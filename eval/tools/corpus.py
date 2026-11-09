import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PROFILES = Path(__file__).parents[1] / "corpus_profiles.yaml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PREFIX_RE = re.compile(r"^[a-z]{2}$")


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class CorpusProfile:
    commit_sha: str
    id_prefix: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]


def _validate_prefixes(value: Any, label: str, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{label} must be a list of path prefixes")
    if required and not value:
        raise ConfigurationError(f"{label} must not be empty")
    for prefix in value:
        if (
            not isinstance(prefix, str)
            or not prefix
            or not prefix.endswith("/")
            or prefix.startswith("/")
            or "\\" in prefix
        ):
            raise ConfigurationError(
                f"{label} entries must be non-empty relative paths ending in '/': "
                f"{prefix!r}"
            )
    return tuple(value)


def load_profiles(path: str | Path = DEFAULT_PROFILES) -> dict[str, CorpusProfile]:
    try:
        with Path(path).open(encoding="utf-8") as profile_file:
            document = yaml.safe_load(profile_file)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot load profiles {path}: {exc}") from exc

    if not isinstance(document, dict):
        raise ConfigurationError("profile document must be a mapping")
    version = document.get("version")
    if type(version) is not int or version != 1:
        raise ConfigurationError("profile version must be integer 1")
    repos = document.get("repos")
    if not isinstance(repos, dict):
        raise ConfigurationError("profile repos must be a mapping")

    profiles: dict[str, CorpusProfile] = {}
    prefixes: set[str] = set()
    for repo_id, repo_value in repos.items():
        if not isinstance(repo_id, str) or not isinstance(repo_value, dict):
            raise ConfigurationError("each repo profile must be a mapping")
        sha = repo_value.get("commit_sha")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise ConfigurationError(
                f"{repo_id}: commit_sha must be 40 lowercase hexadecimal characters"
            )
        id_prefix = repo_value.get("id_prefix")
        if not isinstance(id_prefix, str) or not PREFIX_RE.fullmatch(id_prefix):
            raise ConfigurationError(
                f"{repo_id}: id_prefix must be two lowercase letters"
            )
        if id_prefix in prefixes:
            raise ConfigurationError(f"{repo_id}: duplicate id_prefix {id_prefix!r}")
        prefixes.add(id_prefix)

        profiles_value = repo_value.get("profiles")
        if not isinstance(profiles_value, dict) or not isinstance(
            profiles_value.get("src_only"), dict
        ):
            raise ConfigurationError(f"{repo_id}: profiles.src_only is required")
        src_only = profiles_value["src_only"]
        include = _validate_prefixes(
            src_only.get("include"), f"{repo_id}: src_only.include", required=True
        )
        exclude = _validate_prefixes(
            src_only.get("exclude", []), f"{repo_id}: src_only.exclude", required=False
        )
        profiles[repo_id] = CorpusProfile(sha, id_prefix, include, exclude)
    return profiles


def normalize_path(path: str | os.PathLike[str]) -> str:
    return os.fspath(path).replace("\\", "/")


def is_corpus_path(path: str, profile: CorpusProfile) -> bool:
    return (
        path.endswith(".py")
        and any(path.startswith(prefix) for prefix in profile.include)
        and not any(path.startswith(prefix) for prefix in profile.exclude)
    )


def enumerate_corpus(repo_root: str | Path, profile: CorpusProfile) -> list[str]:
    root = Path(repo_root)
    paths: set[str] = set()
    for current, dirs, filenames in os.walk(root, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if name != ".git" and not (Path(current) / name).is_symlink()
        ]
        for filename in filenames:
            file_path = Path(current) / filename
            if file_path.is_symlink() or not file_path.is_file():
                continue
            relative = normalize_path(file_path.relative_to(root))
            if is_corpus_path(relative, profile):
                paths.add(relative)
    return sorted(paths)


def _git(
    repo_root: Path,
    args: list[str],
    *,
    input_bytes: bytes | None = None,
    cwd: Path | None = None,
) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            input=input_bytes,
            capture_output=True,
            cwd=cwd,
            check=False,
        )
    except OSError as exc:
        raise ConfigurationError(f"cannot run git: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise ConfigurationError(
            f"git {' '.join(args)} failed with exit {result.returncode}: {detail}"
        )
    return result.stdout


def _display_paths(paths: list[str], limit: int = 50) -> str:
    shown = paths[:limit]
    lines = [f"  {path}" for path in shown]
    if len(paths) > limit:
        lines.append(f"  ... và {len(paths) - limit} file khác")
    return "\n".join(lines)


def verify_checkout(
    repo_root: str | Path, pinned_sha: str, profile: CorpusProfile
) -> str:
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise ConfigurationError(f"repo_root is not a directory: {root}")

    top_level = _git(root, ["rev-parse", "--show-toplevel"]).decode().strip()
    try:
        is_root = os.path.normcase(os.path.realpath(top_level)) == os.path.normcase(
            os.path.realpath(root)
        )
    except OSError:
        is_root = False
    if not is_root:
        raise ConfigurationError(
            f"repo_root must be the Git repository root (not a subdirectory): {root}"
        )

    head = _git(root, ["rev-parse", "HEAD"]).decode().strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head) or head != pinned_sha:
        raise ConfigurationError(f"HEAD mismatch: expected {pinned_sha}, got {head}")

    tree = _git(root, ["ls-tree", "-r", "-z", pinned_sha])
    snapshot: dict[str, tuple[str, str]] = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, _entry_type, blob_sha = metadata.decode("ascii").split()
        path = normalize_path(os.fsdecode(raw_path))
        if not is_corpus_path(path, profile):
            continue
        if mode not in {"100644", "100755"}:
            raise ConfigurationError(
                f"unsupported entry in corpus: {path} (mode {mode})"
            )
        snapshot[path] = (mode, blob_sha)

    working_paths = enumerate_corpus(root, profile)
    working_set = set(working_paths)
    snapshot_set = set(snapshot)
    missing = sorted(snapshot_set - working_set)
    extra = sorted(working_set - snapshot_set)
    modified: list[str] = []

    common = sorted(snapshot_set & working_set)
    if common:
        hashes = (
            _git(
                root,
                ["hash-object", "--stdin-paths"],
                input_bytes=("".join(f"{path}\n" for path in common)).encode(),
                cwd=root,
            )
            .decode()
            .splitlines()
        )
        if len(hashes) != len(common):
            raise ConfigurationError(
                "git hash-object returned an unexpected number of hashes"
            )
        modified = [
            path for path, digest in zip(common, hashes) if digest != snapshot[path][1]
        ]

    differences = []
    for label, paths in (
        ("MISSING", missing),
        ("EXTRA", extra),
        ("MODIFIED", modified),
    ):
        if paths:
            differences.append(f"{label} ({len(paths)}):\n{_display_paths(paths)}")
    if differences:
        raise ConfigurationError("\n".join(differences))

    return f"Snapshot OK: HEAD={head}, {len(snapshot)} file corpus khớp"
