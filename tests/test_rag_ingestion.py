import hashlib

import pytest

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
