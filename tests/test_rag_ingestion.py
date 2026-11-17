import argparse
import hashlib
import json
import subprocess

import pytest

from rag.cli import ingest
from rag.ingestion.clone import validate_repo_url
from rag.ingestion.filters import (
    FilterLimits,
    RepositoryLimitError,
    enumerate_python_files,
)
from rag.parsing.chunking import chunk_python_source


def test_repo_url_only_accepts_canonical_public_github_url():
    assert validate_repo_url("https://github.com/pallets/flask") == (
        "https://github.com/pallets/flask"
    )
    for value in (
        "git@github.com:pallets/flask.git",
        "https://github.com/pallets/flask.git",
        "https://github.com/pallets/flask/",
        "https://example.com/pallets/flask",
        "https://github.com/pallets/flask/tree/main",
    ):
        with pytest.raises(ValueError):
            validate_repo_url(value)


def test_filter_includes_only_safe_python_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "app.py").write_text("def app():\n    return 1\n")
    (tmp_path / "src" / ".env").write_text("TOKEN=secret\n")
    (tmp_path / "src" / "private.pem").write_text("private\n")
    (tmp_path / "tests" / "test_app.py").write_text("def test_app():\n    pass\n")
    (tmp_path / "docs" / "example.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("# docs\n")

    report = enumerate_python_files(tmp_path)

    assert report.files == ["src/app.py"]
    assert report.skipped["sensitive_name"] == 2
    assert report.skipped["extension"] == 1


def test_filter_skips_oversized_files_and_enforces_repository_limits(tmp_path):
    (tmp_path / "small.py").write_text("x = 1\n")
    (tmp_path / "large.py").write_text("x" * 20)
    report = enumerate_python_files(
        tmp_path, FilterLimits(max_file_bytes=10, max_total_bytes=10)
    )
    assert report.files == ["small.py"]
    assert report.skipped["file_too_large"] == 1

    with pytest.raises(RepositoryLimitError):
        enumerate_python_files(
            tmp_path, FilterLimits(max_file_bytes=100, max_total_bytes=2)
        )


def test_code_chunk_keeps_small_function_intact_and_exact_lines():
    source = (
        '"""module docs"""\n'
        "import os\n"
        "\n"
        "def small():\n"
        "    value = 1\n"
        "    return value\n"
    )
    chunks = chunk_python_source(
        source,
        index_id="idx_test",
        commit_sha="a" * 40,
        file_path="src/app.py",
    )
    function = next(chunk for chunk in chunks if chunk.qualname == "small")
    assert (function.start_line, function.end_line) == (4, 6)
    assert function.text == "".join(source.splitlines(keepends=True)[3:6])
    assert function.content_hash == hashlib.sha256(function.text.encode()).hexdigest()


def test_long_function_uses_overlapping_windows_and_stable_ids():
    body = "\n".join(f"    value_{i} = {i}" for i in range(140))
    source = f"def long_function():\n{body}\n    return value_139\n"
    kwargs = {
        "index_id": "idx_test",
        "commit_sha": "b" * 40,
        "file_path": "src/long.py",
    }
    first = chunk_python_source(source, **kwargs)
    second = chunk_python_source(source, **kwargs)
    windows = [chunk for chunk in first if chunk.qualname == "long_function"]
    assert len(windows) >= 3
    assert windows[0].end_line - windows[1].start_line + 1 == 10
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert all(chunk.text == "".join(source.splitlines(keepends=True)[chunk.start_line - 1:chunk.end_line]) for chunk in first)


def test_fixed_chunking_uses_sixty_lines_with_ten_line_overlap():
    source = "".join(f"value_{i} = {i}\n" for i in range(130))
    chunks = chunk_python_source(
        source,
        index_id="idx_test",
        commit_sha="c" * 40,
        file_path="src/fixed.py",
        strategy="fixed",
    )
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 60), (51, 110), (101, 130)
    ]


def test_large_class_is_split_into_header_and_methods():
    method_a = "\n".join(f"        a_{i} = {i}" for i in range(65))
    method_b = "\n".join(f"        b_{i} = {i}" for i in range(65))
    source = (
        "class Large:\n"
        "    category = 'large'\n"
        "    def first(self):\n"
        f"{method_a}\n"
        "        return a_64\n"
        "    def second(self):\n"
        f"{method_b}\n"
        "        return b_64\n"
    )
    chunks = chunk_python_source(
        source,
        index_id="idx_test",
        commit_sha="d" * 40,
        file_path="src/large.py",
    )
    assert any(chunk.symbol_kind == "class" for chunk in chunks)
    assert any(chunk.qualname == "Large.first" for chunk in chunks)
    assert any(chunk.qualname == "Large.second" for chunk in chunks)
    assert not any(
        chunk.symbol_kind == "class" and chunk.end_line == len(source.splitlines())
        for chunk in chunks
    )


def test_local_ingest_cli_writes_snapshot_manifest_and_chunks(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "RAG Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "rag@example.invalid"],
        check=True,
    )
    source_file = repository / "src" / "app.py"
    source_file.parent.mkdir()
    source_file.write_text("def answer():\n    return 42\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "fixture", "-q"],
        check=True,
    )

    output = tmp_path / "index"
    args = argparse.Namespace(
        index_id="idx_test",
        output=str(output),
        local_path=str(repository),
        repo_url=None,
        ref=None,
        strategy="code",
        max_files=2_000,
        max_file_bytes=256 * 1024,
        max_total_bytes=50 * 1024 * 1024,
    )

    assert ingest(args) == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    chunks = [
        json.loads(line)
        for line in (output / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert manifest["stats"]["files"] == 1
    assert manifest["stats"]["chunks"] == len(chunks)
    assert manifest["commit_sha"] == chunks[0]["commit_sha"]
    assert chunks[0]["text"] == "def answer():\n    return 42\n"
