import json
import os
import subprocess
from pathlib import Path, PureWindowsPath

import pytest
import yaml

from eval.tools.build_symbol_index import main as build_index_main
from eval.tools.corpus import (
    ConfigurationError,
    CorpusProfile,
    enumerate_corpus,
    load_profiles,
    normalize_path,
    verify_checkout,
)
from eval.tools.symbols import (
    SourceError,
    build_symbols,
    count_lines,
    decode_source,
    symbols_for_source,
)
from eval.tools.validate_dataset import (
    main as validate_dataset_main,
)
from eval.tools.validate_dataset import (
    validate_dataset_file,
    validate_records,
)

REPO = "example/repo"
PINNED_SHA = "1" * 40
PROFILE = CorpusProfile(PINNED_SHA, "ex", ("pkg/",), ("pkg/vendor/",))


def write_profile(path: Path, sha: str = PINNED_SHA, **overrides) -> Path:
    document = {
        "version": 1,
        "repos": {
            REPO: {
                "commit_sha": sha,
                "id_prefix": "ex",
                "profiles": {
                    "src_only": {
                        "include": ["pkg/"],
                        "exclude": ["pkg/vendor/"],
                    }
                },
            }
        },
    }
    for key, value in overrides.items():
        document["repos"][REPO]["profiles"]["src_only"][key] = value
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def isolated_git_env(monkeypatch) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return env


def configure_git_repo(root: Path, env: dict[str, str], autocrlf: bool) -> None:
    for key, value in (
        ("user.name", "Eval Test"),
        ("user.email", "eval@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "true" if autocrlf else "false"),
    ):
        git(root, "config", key, value, env=env)


def git(root: Path, *args: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env=env,
    )
    return result.stdout.decode().strip()


def make_git_repo(
    root: Path, monkeypatch, *, autocrlf: bool = False
) -> tuple[str, dict[str, str]]:
    root.mkdir(parents=True, exist_ok=True)
    env = isolated_git_env(monkeypatch)
    git(root, "init", "-q", env=env)
    configure_git_repo(root, env, autocrlf)
    files = {
        "pkg/a.py": "def alpha():\n    return 1\n",
        "pkg/sub/b.py": "def beta():\n    return 2\n",
        "pkg/vendor/x.py": "def vendor():\n    return 3\n",
        "pkg/readme.md": "before\n",
        "other/f.py": "def outside():\n    return 4\n",
        ".gitignore": "pkg/ignored_*.py\n",
    }
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="")
    git(root, "add", ".", env=env)
    git(root, "commit", "-m", "fixture", "-q", env=env)
    return git(root, "rev-parse", "HEAD", env=env), env


def make_empty_corpus_git_repo(root: Path, monkeypatch) -> tuple[str, dict[str, str]]:
    root.mkdir(parents=True, exist_ok=True)
    env = isolated_git_env(monkeypatch)
    git(root, "init", "-q", env=env)
    configure_git_repo(root, env, autocrlf=False)
    (root / "README.txt").write_text("no Python corpus\n", encoding="utf-8")
    git(root, "add", ".", env=env)
    git(root, "commit", "-m", "empty corpus fixture", "-q", env=env)
    return git(root, "rev-parse", "HEAD", env=env), env


def snapshot_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def test_reference_profile_loads_with_expected_data(tmp_path):
    profile_file = tmp_path / "profiles.yaml"
    profile_file.write_text(
        """version: 1
repos:
  fastapi/fastapi:
    commit_sha: "79cab5bed28b24d92c185d236feb33f5783dfeb3"
    id_prefix: "fa"
    profiles:
      src_only:
        include: ["fastapi/", "scripts/"]
        exclude: ["tests/", "docs_src/", "docs/"]
  pallets/flask:
    commit_sha: "d086db856be187255b8ec61ef409357393020f32"
    id_prefix: "fl"
    profiles:
      src_only:
        include: ["src/flask/"]
        exclude: ["tests/", "examples/", "docs/"]
""",
        encoding="utf-8",
    )
    profiles = load_profiles(profile_file)
    assert profiles["fastapi/fastapi"].id_prefix == "fa"
    assert profiles["pallets/flask"].include == ("src/flask/",)


@pytest.mark.parametrize(
    ("replace", "message"),
    [
        (lambda doc: doc.update(version=True), "version"),
        (lambda doc: doc["repos"][REPO].update(commit_sha="abc"), "commit_sha"),
        (lambda doc: doc["repos"][REPO].update(id_prefix="A1"), "id_prefix"),
        (
            lambda doc: doc["repos"][REPO]["profiles"]["src_only"].update(include=[]),
            "include",
        ),
        (
            lambda doc: doc["repos"][REPO]["profiles"]["src_only"].update(
                include=["pkg"]
            ),
            "ending in",
        ),
        (
            lambda doc: doc["repos"][REPO]["profiles"]["src_only"].update(
                include="pkg/"
            ),
            "list",
        ),
        (
            lambda doc: doc["repos"][REPO]["profiles"]["src_only"].update(
                exclude=[None]
            ),
            "ending in",
        ),
        (
            lambda doc: doc["repos"][REPO]["profiles"]["src_only"].update(
                exclude="tests/"
            ),
            "list",
        ),
    ],
)
def test_invalid_profile_configuration_is_rejected(tmp_path, replace, message):
    document = {
        "version": 1,
        "repos": {
            REPO: {
                "commit_sha": PINNED_SHA,
                "id_prefix": "ex",
                "profiles": {"src_only": {"include": ["pkg/"], "exclude": ["tests/"]}},
            }
        },
    }
    replace(document)
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message):
        load_profiles(path)


def test_duplicate_profile_prefix_is_rejected(tmp_path):
    document = {
        "version": 1,
        "repos": {
            "a/b": {
                "commit_sha": PINNED_SHA,
                "id_prefix": "ab",
                "profiles": {"src_only": {"include": ["a/"], "exclude": []}},
            },
            "c/d": {
                "commit_sha": "2" * 40,
                "id_prefix": "ab",
                "profiles": {"src_only": {"include": ["c/"], "exclude": []}},
            },
        },
    }
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="duplicate id_prefix"):
        load_profiles(path)


@pytest.mark.parametrize(
    ("field", "message"),
    [("commit_sha", "commit_sha"), ("id_prefix", "id_prefix")],
)
def test_missing_required_repo_profile_fields_are_rejected(tmp_path, field, message):
    repo = {
        "commit_sha": PINNED_SHA,
        "id_prefix": "ex",
        "profiles": {"src_only": {"include": ["pkg/"], "exclude": []}},
    }
    del repo[field]
    path = tmp_path / "profiles.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "repos": {REPO: repo}}), encoding="utf-8"
    )
    with pytest.raises(ConfigurationError, match=message):
        load_profiles(path)


def test_missing_src_only_is_rejected(tmp_path):
    path = tmp_path / "profiles.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "repos": {
                    REPO: {
                        "commit_sha": PINNED_SHA,
                        "id_prefix": "ex",
                        "profiles": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="profiles.src_only"):
        load_profiles(path)


def test_enumerate_corpus_is_prefix_based_sorted_and_includes_empty(tmp_path):
    files = [
        "pkg/a.py",
        "pkg/sub/b.py",
        "pkg/readme.md",
        "pkg/c.pyi",
        "pkg/d.pyc",
        "pkg/e.py.txt",
        "setup.py",
        "other/f.py",
        "tests/g.py",
        "pkg/vendor/x.py",
        "pkg/vendor/deep/y.py",
        "pkg_extra/m.py",
        "pkg/vendor_extra/z.py",
        "pkg/tests/h.py",
        "pkg/empty.py",
    ]
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    profile = CorpusProfile(PINNED_SHA, "ex", ("pkg/",), ("pkg/vendor/", "tests/"))

    listed = enumerate_corpus(tmp_path, profile)

    assert listed == [
        "pkg/a.py",
        "pkg/empty.py",
        "pkg/sub/b.py",
        "pkg/tests/h.py",
        "pkg/vendor_extra/z.py",
    ]
    assert listed == sorted(set(listed))
    assert all(not path.startswith(("./", "/")) for path in listed)
    assert normalize_path(PureWindowsPath("pkg\\sub\\b.py")) == "pkg/sub/b.py"


def test_enumerate_skips_symlink_files_and_directories(tmp_path):
    target = tmp_path / "actual.py"
    target.write_text("x = 1\n", encoding="utf-8")
    link = tmp_path / "pkg" / "link.py"
    link.parent.mkdir()
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")
    (tmp_path / "pkg" / "subdir").symlink_to(tmp_path, target_is_directory=True)
    assert (
        enumerate_corpus(tmp_path, CorpusProfile(PINNED_SHA, "ex", ("pkg/",), ())) == []
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", 0),
        ("\n", 1),
        ("a\nb", 2),
        ("a\nb\n", 2),
        ("a\r\nb\r", 2),
        ("a\fb\n", 1),
    ],
)
def test_count_lines_matches_ast_newline_semantics(source, expected):
    assert count_lines(source) == expected


def test_decode_source_handles_bom_and_declared_encoding():
    assert decode_source(b"\xef\xbb\xbfvalue = 1\n") == "value = 1\n"
    encoded = "# coding: latin-1\nname = 'caf\xe9'\n".encode("latin-1")
    assert "café" in decode_source(encoded)


def test_symbols_record_nested_definitions_control_flow_and_docstrings():
    source = '''"""\\n    Module docs.\\n\\n    More.\\n"""
value = 1
callback = lambda: None
@decorate
class Outer:
    """   """
    class Inner:
        def run(self):
            """\\n                Run it.\\n            """
            def deep():
                pass
    if condition:
        def run(self):
            pass
    else:
        def run(self):
            pass
def helper():
    """Helper docs."""
    def inner():
        pass
    class Local:
        def go(self):
            pass
async def fetch():
    """Fetch docs."""
    pass
'''
    records = symbols_for_source(source, REPO, "src/flask/example.py")
    by_qualname: dict[str, list[dict]] = {}
    for record in records:
        by_qualname.setdefault(record["qualname"], []).append(record)

    assert records[0]["qualname"] == "flask.example"
    assert records[0]["doc_first_line"] == "Module docs."
    assert by_qualname["Outer"][0]["kind"] == "class"
    assert by_qualname["Outer"][0]["doc_first_line"] is None
    assert by_qualname["Outer.Inner"][0]["parent"] == "Outer"
    assert by_qualname["Outer.Inner.run"][0]["kind"] == "method"
    assert by_qualname["Outer.Inner.run"][0]["parent"] == "Outer.Inner"
    assert by_qualname["Outer.Inner.run"][0]["doc_first_line"] == "Run it."
    assert by_qualname["Outer.Inner.run.deep"][0]["kind"] == "function"
    assert by_qualname["Outer.Inner.run.deep"][0]["parent"] == "Outer.Inner.run"
    assert len(by_qualname["Outer.run"]) == 2
    assert all(item["kind"] == "method" for item in by_qualname["Outer.run"])
    assert by_qualname["helper"][0]["parent"] is None
    assert by_qualname["helper"][0]["doc_first_line"] == "Helper docs."
    assert by_qualname["helper.inner"][0]["parent"] == "helper"
    assert by_qualname["helper.Local"][0]["kind"] == "class"
    assert by_qualname["helper.Local"][0]["parent"] == "helper"
    assert by_qualname["helper.Local.go"][0]["kind"] == "method"
    assert by_qualname["fetch"][0]["doc_first_line"] == "Fetch docs."
    assert by_qualname["fetch"][0]["kind"] == "function"
    assert all(record["name"] not in {"value", "callback"} for record in records)
    assert all("<locals>" not in record["qualname"] for record in records)


def test_module_names_and_decorated_definition_lines():
    records = symbols_for_source(
        "@decorator\nclass Example:\n    pass\n", REPO, "fastapi/routing.py"
    )
    assert records[0]["qualname"] == "fastapi.routing"
    assert records[1]["start_line"] == 1
    assert records[1]["end_line"] == 3
    assert (
        symbols_for_source("x = 1\n", REPO, "src/flask/__init__.py")[0]["qualname"]
        == "flask"
    )
    assert (
        symbols_for_source("x = 1\n", REPO, "fastapi/__init__.py")[0]["qualname"]
        == "fastapi"
    )


def test_control_blocks_are_transparent_for_symbol_parentage():
    source = """class Example:
    if condition:
        def run(self): pass
        class Nested:
            def go(self): pass
    else:
        def run(self): pass
def outer():
    try:
        def inner(): pass
    except ValueError:
        class Err: pass
    finally:
        async def final(): pass
    for item in values:
        def looped(): pass
    while condition:
        def whiled(): pass
    with ctx():
        def with_block(): pass
    async with async_ctx():
        def async_block(): pass
    match item:
        case 1:
            def matched(): pass
with ctx():
    def at_module(): pass
"""
    records = symbols_for_source(source, REPO, "pkg/controls.py")
    groups: dict[str, list[dict]] = {}
    for item in records:
        groups.setdefault(item["qualname"], []).append(item)
    assert len(groups["Example.run"]) == 2
    assert all(
        item["kind"] == "method" and item["parent"] == "Example"
        for item in groups["Example.run"]
    )
    assert groups["Example.Nested"][0]["parent"] == "Example"
    assert groups["Example.Nested.go"][0]["parent"] == "Example.Nested"
    for name in (
        "inner",
        "Err",
        "final",
        "looped",
        "whiled",
        "with_block",
        "async_block",
        "matched",
    ):
        assert groups[f"outer.{name}"][0]["parent"] == "outer"
    assert groups["outer.Err"][0]["kind"] == "class"
    assert groups["at_module"][0]["parent"] is None


def test_empty_source_has_no_symbols_but_whitespace_has_module_record():
    assert symbols_for_source("", REPO, "pkg/empty.py") == []
    records = symbols_for_source("\n\n", REPO, "pkg/blank.py")
    assert len(records) == 1
    assert (records[0]["start_line"], records[0]["end_line"]) == (1, 2)
    comment = symbols_for_source("# only a comment\n", REPO, "pkg/comment.py")
    assert (comment[0]["start_line"], comment[0]["end_line"]) == (1, 1)


def test_source_parse_errors_are_explicit(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/broken.py").write_bytes(b"\xff")
    records, _, _, errors = build_symbols(tmp_path, REPO, ["pkg/broken.py"])
    assert records == []
    assert "cannot decode" in errors[0]
    with pytest.raises(SourceError, match="cannot parse"):
        symbols_for_source("def broken(:\n", REPO, "pkg/broken.py")


def test_build_cli_decode_failure_exits_one_and_lists_file(tmp_path, capsys):
    root = tmp_path / "broken"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg/broken.py").write_bytes(b"\xff")
    profile_path = write_profile(tmp_path / "profiles.yaml")
    code = build_index_main(
        [
            "--repo",
            REPO,
            "--repo-root",
            str(root),
            "--profiles",
            str(profile_path),
            "--skip-sha-check",
            "--out",
            str(tmp_path / "symbols.jsonl"),
            "--outline",
            str(tmp_path / "outline.txt"),
        ]
    )
    assert code == 1
    assert "pkg/broken.py" in capsys.readouterr().err


def test_snapshot_clean_checkout_reports_ok(tmp_path, monkeypatch):
    sha, _ = make_git_repo(tmp_path / "repo", monkeypatch)
    output = verify_checkout(tmp_path / "repo", sha, PROFILE)
    assert output == f"Snapshot OK: HEAD={sha}, 2 file corpus khớp"


@pytest.mark.parametrize(
    ("mutation", "expected_section", "expected_path"),
    [
        ("modify", "MODIFIED", "pkg/a.py"),
        ("new", "EXTRA", "pkg/new.py"),
        ("ignored", "EXTRA", "pkg/ignored_1.py"),
        ("delete", "MISSING", "pkg/sub/b.py"),
        ("assume", "MODIFIED", "pkg/a.py"),
        ("skip_worktree", "MODIFIED", "pkg/a.py"),
    ],
)
def test_snapshot_detects_worktree_differences_without_modifying_them(
    tmp_path, monkeypatch, mutation, expected_section, expected_path
):
    root = tmp_path / "repo"
    sha, env = make_git_repo(root, monkeypatch)
    target = root / expected_path
    if mutation in {"modify", "assume", "skip_worktree"}:
        if mutation == "assume":
            git(root, "update-index", "--assume-unchanged", "pkg/a.py", env=env)
        if mutation == "skip_worktree":
            git(root, "update-index", "--skip-worktree", "pkg/a.py", env=env)
        target.write_text("def changed():\n    return 9\n", encoding="utf-8")
    elif mutation in {"new", "ignored"}:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def added():\n    return 1\n", encoding="utf-8")
    elif mutation == "delete":
        target.unlink()

    before = snapshot_bytes(root)
    with pytest.raises(ConfigurationError) as error:
        verify_checkout(root, sha, PROFILE)
    message = str(error.value)
    assert expected_section in message
    assert expected_path in message
    assert snapshot_bytes(root) == before


def test_snapshot_ignores_changes_outside_corpus_and_exclude(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    sha, _ = make_git_repo(root, monkeypatch)
    (root / "other/f.py").write_text("changed outside\n", encoding="utf-8")
    (root / "pkg/readme.md").write_text("changed non-Python\n", encoding="utf-8")
    (root / "pkg/new.txt").write_text("added non-Python\n", encoding="utf-8")
    (root / "pkg/vendor/x.py").write_text("changed excluded\n", encoding="utf-8")
    (root / "pkg/vendor/new.py").write_text("added excluded\n", encoding="utf-8")
    assert verify_checkout(root, sha, PROFILE).startswith("Snapshot OK:")


def test_snapshot_error_lists_only_first_50_paths(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    sha, _ = make_git_repo(root, monkeypatch)
    for index in range(51):
        (root / "pkg" / f"extra_{index:02}.py").write_text(
            "value = 1\n", encoding="utf-8"
        )
    with pytest.raises(ConfigurationError) as error:
        verify_checkout(root, sha, PROFILE)
    message = str(error.value)
    assert "EXTRA (51)" in message
    assert "pkg/extra_00.py" in message
    assert "pkg/extra_49.py" in message
    assert "pkg/extra_50.py" not in message
    assert "... và 1 file khác" in message


@pytest.mark.parametrize("pin_mode", ["wrong_head", "missing_commit"])
def test_snapshot_rejects_wrong_or_missing_pinned_commit(
    tmp_path, monkeypatch, pin_mode
):
    root = tmp_path / "repo"
    sha, env = make_git_repo(root, monkeypatch)
    if pin_mode == "wrong_head":
        (root / "other.txt").write_text("second commit\n", encoding="utf-8")
        git(root, "add", "other.txt", env=env)
        git(root, "commit", "-m", "second", "-q", env=env)
        actual = git(root, "rev-parse", "HEAD", env=env)
        with pytest.raises(ConfigurationError) as error:
            verify_checkout(root, sha, PROFILE)
        assert sha in str(error.value)
        assert actual in str(error.value)
    else:
        missing_sha = "f" * 40
        with pytest.raises(ConfigurationError, match=missing_sha):
            verify_checkout(root, missing_sha, PROFILE)


def test_snapshot_wrong_head_rejection_does_not_restore_files(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    first_sha, env = make_git_repo(root, monkeypatch)
    (root / "other.txt").write_text("second commit\n", encoding="utf-8")
    git(root, "add", "other.txt", env=env)
    git(root, "commit", "-m", "second", "-q", env=env)
    before = snapshot_bytes(root)
    with pytest.raises(ConfigurationError):
        verify_checkout(root, first_sha, PROFILE)
    assert snapshot_bytes(root) == before


def test_snapshot_rejects_non_git_and_nested_repo_roots(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    isolated_git_env(monkeypatch)
    with pytest.raises(ConfigurationError, match="rev-parse"):
        verify_checkout(plain, PINNED_SHA, PROFILE)
    root = tmp_path / "repo"
    sha, _ = make_git_repo(root, monkeypatch)
    with pytest.raises(ConfigurationError, match="not a subdirectory"):
        verify_checkout(root / "pkg", sha, PROFILE)


def test_snapshot_autocrlf_filters_working_tree_before_hashing(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    env = isolated_git_env(monkeypatch)
    git(root, "init", "-q", env=env)
    configure_git_repo(root, env, autocrlf=True)
    (root / "pkg").mkdir()
    (root / "pkg/a.py").write_bytes(b"def alpha():\r\n    return 1\r\n")
    git(root, "add", ".", env=env)
    git(root, "commit", "-m", "crlf fixture", "-q", env=env)
    sha = git(root, "rev-parse", "HEAD", env=env)
    assert verify_checkout(root, sha, PROFILE).startswith("Snapshot OK:")
    (root / "pkg/a.py").write_bytes(b"def alpha():\r\n    return 2\r\n")
    with pytest.raises(ConfigurationError, match="MODIFIED"):
        verify_checkout(root, sha, PROFILE)


@pytest.mark.skipif(
    os.name == "nt", reason="symlink entry modes are not portable on Windows"
)
def test_snapshot_rejects_symlink_entry_in_corpus(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    env = isolated_git_env(monkeypatch)
    git(root, "init", "-q", env=env)
    configure_git_repo(root, env, autocrlf=False)
    (root / "pkg").mkdir()
    target = root / "pkg/target.py"
    target.write_text("x = 1\n", encoding="utf-8")
    (root / "outside.py").write_text("x = 2\n", encoding="utf-8")
    (root / "pkg/link.py").symlink_to(Path("../outside.py"))
    git(root, "add", ".", env=env)
    git(root, "commit", "-m", "symlink fixture", "-q", env=env)
    sha = git(root, "rev-parse", "HEAD", env=env)
    with pytest.raises(ConfigurationError, match="unsupported entry in corpus"):
        verify_checkout(root, sha, PROFILE)


def _plain_fixture(tmp_path: Path):
    root = tmp_path / "plain-repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg/a.py").write_text(
        '"""Module documentation."""\n\ndef alpha():\n    """Alpha docs."""\n    return 1\n',
        encoding="utf-8",
    )
    (root / "pkg/b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    (root / "pkg/empty.py").write_bytes(b"")
    profile = CorpusProfile(PINNED_SHA, "ex", ("pkg/",), ())
    paths = enumerate_corpus(root, profile)
    all_symbols, by_file, empty_files, errors = build_symbols(root, REPO, paths)
    assert not errors
    line_counts = {
        path: count_lines(decode_source((root / path).read_bytes(), path))
        for path in paths
    }
    return (
        root,
        {REPO: profile},
        {REPO: set(paths)},
        {REPO: line_counts},
        {REPO: by_file},
        empty_files,
        all_symbols,
    )


def _valid_record(
    profile: CorpusProfile,
    *,
    record_id: str = "ex-sym-001",
    category: str = "symbol_lookup",
    split: str = "dev",
    answerable: bool = True,
    file_path: str = "pkg/a.py",
    start_line: int = 3,
    end_line: int = 5,
    symbol: str | None = "alpha",
    relevant: list[dict] | None = None,
) -> dict:
    if relevant is None:
        relevant = (
            [
                {
                    "role": "primary",
                    "file_path": file_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "symbol": symbol,
                }
            ]
            if answerable
            else []
        )
    return {
        "id": record_id,
        "schema_version": 1,
        "repo": REPO,
        "commit_sha": profile.commit_sha,
        "split": split,
        "category": category,
        "lexical_overlap": "high",
        "question": f"How does {symbol or 'the code'} work in this example {record_id}?",
        "answerable": answerable,
        "relevant": relevant,
        "key_points": ["First point", "Second point"] if answerable else [],
        "unanswerable_type": None if answerable else "not_in_repo",
    }


def _run_records(records, context, *, phase="all", require_complete=False):
    _, profiles, corpus, line_counts, symbols, _, _ = context
    return validate_records(
        [(index, record) for index, record in enumerate(records, 1)],
        profiles,
        corpus,
        line_counts,
        symbols,
        phase=phase,
        require_complete=require_complete,
    )


def test_validator_accepts_each_category_and_both_unanswerable_types(tmp_path, capsys):
    context = _plain_fixture(tmp_path)
    profile = context[1][REPO]
    records = [
        _valid_record(profile, record_id="ex-sym-001"),
        _valid_record(profile, record_id="ex-und-001", category="code_understanding"),
        _valid_record(
            profile,
            record_id="ex-xfile-001",
            category="cross_file",
            relevant=[
                {
                    "role": "primary",
                    "file_path": "pkg/a.py",
                    "start_line": 3,
                    "end_line": 5,
                    "symbol": "alpha",
                },
                {
                    "role": "primary",
                    "file_path": "pkg/b.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "beta",
                },
            ],
        ),
        _valid_record(
            profile,
            record_id="ex-flow-001",
            category="code_flow",
            relevant=[
                {
                    "role": "primary",
                    "file_path": "pkg/a.py",
                    "start_line": 3,
                    "end_line": 5,
                    "symbol": "alpha",
                    "order": 1,
                },
                {
                    "role": "primary",
                    "file_path": "pkg/b.py",
                    "start_line": 1,
                    "end_line": 2,
                    "symbol": "beta",
                    "order": 2,
                },
                {
                    "role": "primary",
                    "file_path": "pkg/a.py",
                    "start_line": 1,
                    "end_line": 5,
                    "symbol": "pkg.a",
                    "order": 3,
                },
            ],
        ),
        _valid_record(profile, record_id="ex-cfg-001", category="configuration"),
        _valid_record(
            profile,
            record_id="ex-unans-001",
            category="unanswerable",
            answerable=False,
        ),
        _valid_record(
            profile,
            record_id="ex-unans-002",
            category="unanswerable",
            answerable=False,
        ),
    ]
    records[-1]["unanswerable_type"] = "false_premise"
    errors, _, _ = _run_records(records, context)
    assert errors == []
    capsys.readouterr()


@pytest.mark.parametrize(
    ("field", "bad_values", "path"),
    [
        ("schema_version", [True, False, 1.0, "1"], None),
        ("start_line", [True, False, 1.0, "3"], "relevant"),
        ("end_line", [True, False, 1.0, "5"], "relevant"),
        ("order", [True, False, 1.0, "1"], "order"),
    ],
)
def test_integer_fields_reject_bool_float_and_numeric_string(
    tmp_path, capsys, field, bad_values, path
):
    context = _plain_fixture(tmp_path)
    profile = context[1][REPO]
    for index, value in enumerate(bad_values):
        record = _valid_record(profile)
        if field == "schema_version":
            record[field] = value
        else:
            if field == "order":
                record["category"] = "code_flow"
                record["id"] = f"ex-flow-{index:03d}"
                record["relevant"] = [
                    {
                        "role": "primary",
                        "file_path": "pkg/a.py",
                        "start_line": 3,
                        "end_line": 5,
                        "symbol": "alpha",
                        "order": 1,
                    },
                    {
                        "role": "primary",
                        "file_path": "pkg/b.py",
                        "start_line": 1,
                        "end_line": 2,
                        "symbol": "beta",
                        "order": 2,
                    },
                    {
                        "role": "primary",
                        "file_path": "pkg/a.py",
                        "start_line": 3,
                        "end_line": 5,
                        "symbol": "alpha",
                        "order": 3,
                    },
                ]
                record["relevant"][0]["order"] = value
            else:
                record["relevant"][0][field] = value
        errors, _, _ = _run_records([record], context)
        assert any(field in error and "integer" in error for error in errors)
        capsys.readouterr()


def test_integer_fields_accept_real_integers(tmp_path, capsys):
    context = _plain_fixture(tmp_path)
    record = _valid_record(context[1][REPO])
    errors, _, _ = _run_records([record], context)
    assert not any("integer" in error for error in errors)
    capsys.readouterr()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda row: row.pop("unanswerable_type"), "unanswerable_type"),
        (lambda row: row.update(category="unanswerable"), "inconsistent"),
        (lambda row: row.update(answerable=False), "inconsistent"),
        (
            lambda row: row.update(unanswerable_type="false_premise"),
            "unanswerable_type=null",
        ),
        (lambda row: row.update(expected_files=[]), "unknown field"),
        (lambda row: row["relevant"][0].update(file_path=3), "file_path"),
        (lambda row: row.update(answerable=0), "boolean"),
        (lambda row: row.update(answerable=1), "boolean"),
        (lambda row: row.update(id="ex-und-001"), "group code"),
    ],
)
def test_schema_and_consistency_errors_are_reported(
    tmp_path, capsys, mutation, expected
):
    context = _plain_fixture(tmp_path)
    row = _valid_record(context[1][REPO])
    mutation(row)
    errors, _, _ = _run_records([row], context)
    assert any(expected in error for error in errors), errors
    capsys.readouterr()


@pytest.mark.parametrize("lexical_overlap", [[], {}], ids=["list", "mapping"])
def test_validator_rejects_unhashable_lexical_overlap_without_traceback(
    tmp_path, capsys, lexical_overlap
):
    root, profiles, *_ = _plain_fixture(tmp_path)
    profile = profiles[REPO]
    profile_path = write_profile(tmp_path / "profiles.yaml", profile.commit_sha)
    row = _valid_record(
        profile,
        start_line=3,
        end_line=5,
    )
    row["lexical_overlap"] = lexical_overlap
    dataset = tmp_path / "invalid-lexical.jsonl"
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")

    code = validate_dataset_main(
        [
            "--dataset",
            str(dataset),
            "--repo-root",
            f"{REPO}={root}",
            "--profiles",
            str(profile_path),
            "--skip-sha-check",
        ]
    )
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == 1
    assert "lexical_overlap must be high or low" in output
    assert "Traceback" not in output


@pytest.mark.parametrize(
    "question",
    [" " * 20, f"   {'short question'[:14]}   "],
    ids=["whitespace-only", "short-after-strip"],
)
def test_validator_requires_15_question_characters_after_strip(
    tmp_path, capsys, question
):
    context = _plain_fixture(tmp_path)
    row = _valid_record(context[1][REPO])
    row["question"] = question

    errors, _, _ = _run_records([row], context)

    assert len(errors) == 1
    assert (
        "question must be a string of at least 15 characters after stripping whitespace"
        in errors[0]
    )
    capsys.readouterr()


def test_question_normalization_removes_punctuation_and_collapses_whitespace(
    tmp_path, capsys
):
    context = _plain_fixture(tmp_path)
    profile = context[1][REPO]
    first = _valid_record(profile, record_id="ex-sym-001")
    first["question"] = "Where is the alpha-route registered today?"
    second = _valid_record(profile, record_id="ex-sym-002")
    second["question"] = "Where is the alpharoute registered today."
    errors, _, _ = _run_records([first, second], context)
    assert any("duplicates line 1 after normalization" in error for error in errors)
    capsys.readouterr()


def test_relevant_item_must_be_object_and_file_path_string(tmp_path, capsys):
    context = _plain_fixture(tmp_path)
    row = _valid_record(context[1][REPO])
    row["relevant"][0] = "not-object"
    errors, _, _ = _run_records([row], context)
    assert any("relevant[0] must be an object" in error for error in errors)
    capsys.readouterr()

    row = _valid_record(context[1][REPO])
    row["relevant"][0]["file_path"] = 42
    errors, _, _ = _run_records([row], context)
    assert any(
        "file_path must be a non-empty relative POSIX path" in error for error in errors
    )
    capsys.readouterr()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda row: row["relevant"][0].update(order=1), "only allowed"),
        (
            lambda row: row["relevant"][0].update(start_line=30, end_line=40),
            "exceeds",
        ),
        (lambda row: row["relevant"][0].update(symbol="missing"), "does not match"),
        (lambda row: row.update(category="cross_file"), "invalid relevant shape"),
        (lambda row: row["relevant"][0].update(file_path="pkg/empty.py"), "file rỗng"),
        (
            lambda row: row.update(
                category="unanswerable", id="ex-unans-001", answerable=False
            ),
            "require relevant=[]",
        ),
    ],
)
def test_source_group_and_order_errors_are_reported(
    tmp_path, capsys, mutation, expected
):
    context = _plain_fixture(tmp_path)
    row = _valid_record(context[1][REPO])
    mutation(row)
    errors, _, _ = _run_records([row], context)
    assert any(expected in error for error in errors), errors
    capsys.readouterr()


def test_order_requires_contiguous_distinct_flow_primaries(tmp_path, capsys):
    context = _plain_fixture(tmp_path)
    profile = context[1][REPO]
    relevant = [
        {
            "role": "primary",
            "file_path": "pkg/a.py",
            "start_line": 3,
            "end_line": 5,
            "symbol": "alpha",
            "order": 1,
        },
        {
            "role": "primary",
            "file_path": "pkg/b.py",
            "start_line": 1,
            "end_line": 2,
            "symbol": "beta",
            "order": 3,
        },
        {
            "role": "primary",
            "file_path": "pkg/a.py",
            "start_line": 1,
            "end_line": 5,
            "symbol": "pkg.a",
            "order": 3,
        },
    ]
    row = _valid_record(
        profile, record_id="ex-flow-001", category="code_flow", relevant=relevant
    )
    errors, _, _ = _run_records([row], context)
    assert any("unique" in error for error in errors)
    assert any("continuous" in error for error in errors)
    capsys.readouterr()


def test_unknown_dataset_repo_and_wrong_commit_are_rejected_with_skip(tmp_path, capsys):
    context = _plain_fixture(tmp_path)
    row = _valid_record(context[1][REPO], record_id="ex-sym-001")
    row["commit_sha"] = "2" * 40
    errors, _, _ = _run_records([row], context)
    assert any("commit_sha" in error for error in errors)
    capsys.readouterr()

    row = _valid_record(context[1][REPO], record_id="ex-sym-002")
    row["repo"] = "other/repo"
    errors, _, _ = _run_records([row], context)
    assert any("repo is not in profiles" in error for error in errors)
    capsys.readouterr()


def test_validate_dataset_skip_sha_runs_dataset_checks_on_non_git_root(
    tmp_path, capsys
):
    context = _plain_fixture(tmp_path)
    root, profiles, *_ = context
    profile_path = write_profile(tmp_path / "profiles.yaml", profiles[REPO].commit_sha)
    dataset = tmp_path / "valid.jsonl"
    row = _valid_record(profiles[REPO])
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    code = validate_dataset_file(
        dataset,
        {REPO: root},
        profile_path,
        phase="all",
        skip_sha_check=True,
        require_complete=False,
    )
    assert code == 0
    capsys.readouterr()

    row["relevant"][0]["end_line"] = 99
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    code = validate_dataset_file(
        dataset,
        {REPO: root},
        profile_path,
        phase="all",
        skip_sha_check=True,
        require_complete=False,
    )
    assert code == 1
    assert "end_line exceeds" in capsys.readouterr().out


def test_skip_sha_does_not_skip_pinned_sha_or_profile_membership(tmp_path, capsys):
    context = _plain_fixture(tmp_path)
    root, profiles, *_ = context
    profile_path = write_profile(tmp_path / "profiles.yaml", profiles[REPO].commit_sha)
    row = _valid_record(profiles[REPO])
    row["commit_sha"] = "2" * 40
    dataset = tmp_path / "bad-sha.jsonl"
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    code = validate_dataset_file(
        dataset,
        {REPO: root},
        profile_path,
        phase="all",
        skip_sha_check=True,
        require_complete=False,
    )
    assert code == 1
    assert "commit_sha" in capsys.readouterr().out

    row["repo"] = "unknown/repo"
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    code = validate_dataset_file(
        dataset,
        {REPO: root},
        profile_path,
        phase="all",
        skip_sha_check=True,
        require_complete=False,
    )
    assert code == 1
    assert "not in profiles" in capsys.readouterr().out


def test_skip_sha_ignores_dirty_snapshot_but_validates_dataset(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "repo"
    sha, _ = make_git_repo(root, monkeypatch)
    profile_path = write_profile(tmp_path / "profiles.yaml", sha)
    row = _valid_record(
        CorpusProfile(sha, "ex", ("pkg/",), ("pkg/vendor/",)),
        file_path="pkg/a.py",
        start_line=1,
        end_line=2,
        symbol="alpha",
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    (root / "pkg/a.py").write_text("def alpha():\n    return 9\n", encoding="utf-8")
    (root / "pkg/extra.py").write_text("def extra(): pass\n", encoding="utf-8")
    code = validate_dataset_file(
        dataset,
        {REPO: root},
        profile_path,
        phase="all",
        skip_sha_check=True,
        require_complete=False,
    )
    assert code == 0
    capsys.readouterr()


def test_snapshot_failure_precedes_dataset_errors(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    sha, _ = make_git_repo(root, monkeypatch)
    profile_path = write_profile(tmp_path / "profiles.yaml", sha)
    dataset = tmp_path / "bad.jsonl"
    dataset.write_text(json.dumps({"repo": REPO}) + "\n", encoding="utf-8")
    (root / "pkg/a.py").write_text("modified\n", encoding="utf-8")
    code = validate_dataset_main(
        [
            "--dataset",
            str(dataset),
            "--repo-root",
            f"{REPO}={root}",
            "--profiles",
            str(profile_path),
        ]
    )
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code == 2
    assert "MODIFIED" in output
    assert "missing required field" not in output


def test_relative_repo_root_works_for_validator_and_symbol_index(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "repo"
    sha, _ = make_git_repo(root, monkeypatch)
    profile_path = write_profile(tmp_path / "profiles.yaml", sha)
    row = _valid_record(
        CorpusProfile(sha, "ex", ("pkg/",), ("pkg/vendor/",)),
        start_line=1,
        end_line=2,
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    validator_code = validate_dataset_main(
        [
            "--dataset",
            str(dataset),
            "--repo-root",
            f"{REPO}=repo",
            "--profiles",
            str(profile_path),
        ]
    )
    validator_output = capsys.readouterr().out
    assert validator_code == 0
    assert "Snapshot OK:" in validator_output

    build_code = build_index_main(
        [
            "--repo",
            REPO,
            "--repo-root",
            "repo",
            "--profiles",
            str(profile_path),
            "--out",
            str(tmp_path / "symbols.jsonl"),
            "--outline",
            str(tmp_path / "outline.txt"),
        ]
    )
    build_output = capsys.readouterr().out
    assert build_code == 0
    assert "Snapshot OK:" in build_output


@pytest.mark.parametrize("skip_sha_check", [False, True])
def test_build_cli_rejects_nonexistent_repo_root(tmp_path, capsys, skip_sha_check):
    profile_path = write_profile(tmp_path / "profiles.yaml")
    args = [
        "--repo",
        REPO,
        "--repo-root",
        str(tmp_path / "missing"),
        "--profiles",
        str(profile_path),
        "--out",
        str(tmp_path / "symbols.jsonl"),
        "--outline",
        str(tmp_path / "outline.txt"),
    ]
    if skip_sha_check:
        args.append("--skip-sha-check")

    code = build_index_main(args)

    output = capsys.readouterr().err
    assert code == 2
    assert "repo_root is not a directory" in output


@pytest.mark.parametrize("skip_sha_check", [False, True])
def test_build_cli_rejects_corpus_without_python_files(
    tmp_path, monkeypatch, capsys, skip_sha_check
):
    root = tmp_path / "repo"
    sha, _ = make_empty_corpus_git_repo(root, monkeypatch)
    profile_path = write_profile(tmp_path / "profiles.yaml", sha)
    args = [
        "--repo",
        REPO,
        "--repo-root",
        str(root),
        "--profiles",
        str(profile_path),
        "--out",
        str(tmp_path / "symbols.jsonl"),
        "--outline",
        str(tmp_path / "outline.txt"),
    ]
    if skip_sha_check:
        args.append("--skip-sha-check")

    code = build_index_main(args)

    output = capsys.readouterr().err
    assert code == 2
    assert "src_only corpus contains no Python files" in output


@pytest.mark.parametrize("skip_sha_check", [False, True])
def test_validator_rejects_corpus_without_python_files(
    tmp_path, monkeypatch, capsys, skip_sha_check
):
    root = tmp_path / "empty-corpus"
    sha, _ = make_empty_corpus_git_repo(root, monkeypatch)
    profile_path = write_profile(tmp_path / "profiles.yaml", sha)
    row = _valid_record(
        CorpusProfile(sha, "ex", ("pkg/",), ("pkg/vendor/",)),
        start_line=1,
        end_line=2,
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")

    args = [
        "--dataset",
        str(dataset),
        "--repo-root",
        f"{REPO}={root}",
        "--profiles",
        str(profile_path),
    ]
    if skip_sha_check:
        args.append("--skip-sha-check")
    code = validate_dataset_main(args)

    output = capsys.readouterr().err
    assert code == 2
    assert "src_only corpus contains no Python files" in output


def test_empty_python_files_do_not_count_as_an_empty_corpus(tmp_path, capsys):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg/empty.py").write_bytes(b"")
    profile_path = write_profile(tmp_path / "profiles.yaml")

    build_code = build_index_main(
        [
            "--repo",
            REPO,
            "--repo-root",
            str(root),
            "--profiles",
            str(profile_path),
            "--skip-sha-check",
            "--out",
            str(tmp_path / "symbols.jsonl"),
            "--outline",
            str(tmp_path / "outline.txt"),
        ]
    )
    output = capsys.readouterr().out
    assert build_code == 0
    assert "Corpus files: 1" in output
    assert "Empty files: 1" in output

    (root / "pkg/a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    row = _valid_record(
        CorpusProfile(PINNED_SHA, "ex", ("pkg/",), ("pkg/vendor/",)),
        start_line=1,
        end_line=2,
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    validator_code = validate_dataset_file(
        dataset,
        {REPO: root},
        profile_path,
        phase="all",
        skip_sha_check=True,
        require_complete=False,
    )
    assert validator_code == 0
    capsys.readouterr()


def test_phase_filters_completeness_only_not_dataset_validation(tmp_path, capsys):
    context = _plain_fixture(tmp_path)
    profile = context[1][REPO]
    row = _valid_record(profile, split="test")
    row["relevant"][0]["end_line"] = 99
    errors, _, _ = _run_records([row], context, phase="dev")
    assert any("end_line exceeds" in error for error in errors)
    output = capsys.readouterr().out
    assert "dev:" in output and "test:" not in output


def test_completeness_only_errors_when_requested_and_for_over_or_under_target(
    tmp_path, capsys
):
    context = _plain_fixture(tmp_path)
    profile = context[1][REPO]
    row = _valid_record(profile)
    errors, _, _ = _run_records([row], context, phase="dev", require_complete=False)
    assert errors == []
    capsys.readouterr()
    errors, _, _ = _run_records([row], context, phase="dev", require_complete=True)
    assert any("expected 2" in error for error in errors)
    capsys.readouterr()
    two = [_valid_record(profile), _valid_record(profile, record_id="ex-sym-002")]
    errors, _, _ = _run_records(two, context, phase="dev", require_complete=True)
    assert not any("symbol_lookup dev completeness" in error for error in errors)
    capsys.readouterr()
    three = two + [_valid_record(profile, record_id="ex-sym-003")]
    errors, _, _ = _run_records(three, context, phase="dev", require_complete=True)
    assert any(
        "symbol_lookup dev completeness is 3, expected 2" in error for error in errors
    )
    capsys.readouterr()


def test_symbol_lookup_accepts_names_and_rejects_non_overlapping_symbols(
    tmp_path, capsys
):
    context = _plain_fixture(tmp_path)
    profile = context[1][REPO]
    row = _valid_record(profile)
    row["relevant"][0].update(start_line=4, end_line=5, symbol="pkg.a.alpha")
    errors, _, _ = _run_records([row], context)
    assert any("does not match" in error for error in errors)
    capsys.readouterr()


def test_lexical_overlap_warnings_obey_identifier_boundaries(tmp_path, capsys):
    context = _plain_fixture(tmp_path)
    profile = context[1][REPO]
    row = _valid_record(profile)
    row["question"] = "How does alpha work here?"
    row["lexical_overlap"] = "low"
    _, warnings, _ = _run_records([row], context)
    assert any(
        "low but a primary identifier appears" in warning for warning in warnings
    )
    capsys.readouterr()

    row["question"] = "How does target behavior work here?"
    row["relevant"][0]["symbol"] = "get"
    row["lexical_overlap"] = "low"
    _, warnings, _ = _run_records([row], context)
    assert not any("primary identifier appears" in warning for warning in warnings)
    capsys.readouterr()


def test_short_identifier_requires_backticks_call_or_dotted_token(tmp_path, capsys):
    from eval.tools.validate_dataset import _identifier_mentions

    assert not _identifier_mentions("How does target work?", "get", "get")
    assert _identifier_mentions("How does `get` work?", "get", "get")
    assert _identifier_mentions("How does get() work?", "get", "get")

    context = _plain_fixture(tmp_path)
    profile = context[1][REPO]
    row = _valid_record(profile)
    row["question"] = "How does `alpha` behave in the code?"
    row["lexical_overlap"] = "low"
    _, warnings, _ = _run_records([row], context)
    assert any("primary identifier appears" in warning for warning in warnings)
    capsys.readouterr()


def test_lexical_overlap_name_boundary_and_qualname_matching():
    from eval.tools.validate_dataset import _identifier_mentions

    assert not _identifier_mentions("target response", "get", "get")
    assert _identifier_mentions("call get() now", "get", "get")
    assert _identifier_mentions("use `get` now", "get", "get")
    assert not _identifier_mentions(
        "make_response_class", "make_response", "make_response"
    )
    assert _identifier_mentions(
        "Use Flask.make_response here", "make_response", "Flask.make_response"
    )


def test_unanswerable_balance_warns_per_repo_and_split(tmp_path, capsys):
    context = _plain_fixture(tmp_path)
    profile = context[1][REPO]
    rows = []
    for index, kind in enumerate(["not_in_repo"] * 3 + ["false_premise"], start=1):
        row = _valid_record(
            profile,
            record_id=f"ex-unans-{index:03d}",
            category="unanswerable",
            answerable=False,
        )
        row["unanswerable_type"] = kind
        row["question"] = f"Unanswerable question number {index}?"
        rows.append(row)
    _, warnings, _ = _run_records(rows, context)
    assert any("imbalance" in warning for warning in warnings)
    capsys.readouterr()


def test_build_cli_outputs_empty_file_and_jsonl(tmp_path, capsys):
    root, *_ = _plain_fixture(tmp_path)
    profile_path = write_profile(tmp_path / "profiles.yaml")
    out = tmp_path / "symbols.jsonl"
    outline = tmp_path / "outline.txt"
    code = build_index_main(
        [
            "--repo",
            REPO,
            "--repo-root",
            str(root),
            "--profiles",
            str(profile_path),
            "--skip-sha-check",
            "--out",
            str(out),
            "--outline",
            str(outline),
        ]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "Snapshot check skipped" in output
    assert "Empty files: 1" in output
    assert "pkg/empty.py (empty file, 0 lines, no symbols)" in outline.read_text()
    assert all(
        "pkg/empty.py" not in json.loads(line)["file_path"]
        for line in out.read_text().splitlines()
    )


def test_build_cli_parse_failure_exits_one_and_lists_file(tmp_path, capsys):
    root = tmp_path / "broken"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg/broken.py").write_text("def broken(:\n", encoding="utf-8")
    profile_path = write_profile(tmp_path / "profiles.yaml")
    code = build_index_main(
        [
            "--repo",
            REPO,
            "--repo-root",
            str(root),
            "--profiles",
            str(profile_path),
            "--skip-sha-check",
            "--out",
            str(tmp_path / "symbols.jsonl"),
            "--outline",
            str(tmp_path / "outline.txt"),
        ]
    )
    assert code == 1
    assert "pkg/broken.py" in capsys.readouterr().err
