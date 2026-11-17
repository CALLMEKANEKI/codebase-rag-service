from __future__ import annotations

import ast
import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from rag.parsing.python_ast import SourceError, decode_source, symbols_for_source

CODE_CHUNK_LINES = 120
WINDOW_LINES = 60
WINDOW_OVERLAP = 10
CHUNKER_VERSION = "python-ast-v1"


@dataclass(frozen=True)
class CodeChunk:
    chunk_id: str
    index_id: str
    commit_sha: str
    file_path: str
    symbol_name: str | None
    qualname: str | None
    symbol_kind: str
    parent: str | None
    start_line: int
    end_line: int
    text: str
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _make_chunk(
    source_lines: list[str],
    *,
    index_id: str,
    commit_sha: str,
    file_path: str,
    start: int,
    end: int,
    symbol_name: str | None,
    qualname: str | None,
    symbol_kind: str,
    parent: str | None,
    strategy: str,
) -> CodeChunk:
    if start < 1 or end < start or end > len(source_lines):
        raise ValueError(f"Invalid chunk range {start}-{end} for {file_path}")
    text = "".join(source_lines[start - 1 : end])
    content_hash = _hash(text)
    identity = "|".join((
        index_id, commit_sha, file_path, str(start), str(end),
        content_hash, strategy, CHUNKER_VERSION,
    ))
    return CodeChunk(
        chunk_id=_hash(identity),
        index_id=index_id,
        commit_sha=commit_sha,
        file_path=file_path,
        symbol_name=symbol_name,
        qualname=qualname,
        symbol_kind=symbol_kind,
        parent=parent,
        start_line=start,
        end_line=end,
        text=text,
        content_hash=content_hash,
    )


def _windows(
    source_lines: list[str], start: int, end: int, *, window: int, overlap: int
) -> list[tuple[int, int]]:
    if end - start + 1 <= window:
        return [(start, end)]
    step = window - overlap
    ranges = []
    cursor = start
    while cursor <= end:
        last = min(cursor + window - 1, end)
        ranges.append((cursor, last))
        if last == end:
            break
        cursor += step
    return ranges


def _ast_chunks(source: str, file_path: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise SourceError(f"{file_path}: cannot parse Python source: {exc}") from exc

    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    ranges: list[dict[str, Any]] = []
    line_count = len(source.splitlines())
    if not line_count:
        return ranges

    first_symbol = min(
        (min([n.lineno, *(d.lineno for d in getattr(n, "decorator_list", []))]) for n in nodes),
        default=line_count + 1,
    )
    if first_symbol > 1:
        ranges.append({
            "start": 1, "end": first_symbol - 1, "name": None,
            "qualname": None, "kind": "module", "parent": None,
        })

    for node in nodes:
        start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        end = node.end_lineno or node.lineno
        name = node.name
        if isinstance(node, ast.ClassDef) and end - start + 1 > CODE_CHUNK_LINES:
            methods = [
                child for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            first_method = min(
                (min([m.lineno, *(d.lineno for d in getattr(m, "decorator_list", []))]) for m in methods),
                default=end + 1,
            )
            if first_method > start:
                ranges.append({
                    "start": start, "end": first_method - 1, "name": name,
                    "qualname": name, "kind": "class", "parent": None,
                })
            for method in methods:
                method_start = min([
                    method.lineno,
                    *(d.lineno for d in getattr(method, "decorator_list", [])),
                ])
                method_end = method.end_lineno or method.lineno
                ranges.extend({
                    "start": left, "end": right, "name": method.name,
                    "qualname": f"{name}.{method.name}", "kind": "method",
                    "parent": name,
                } for left, right in _windows(
                    source.splitlines(keepends=True),
                    method_start, method_end,
                    window=WINDOW_LINES, overlap=WINDOW_OVERLAP,
                ))
        else:
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            ranges.extend({
                "start": left, "end": right, "name": name,
                "qualname": name, "kind": kind, "parent": None,
            } for left, right in _windows(
                source.splitlines(keepends=True), start, end,
                window=WINDOW_LINES, overlap=WINDOW_OVERLAP,
            ))
    return ranges


def chunk_python_source(
    source: str,
    *,
    index_id: str,
    commit_sha: str,
    file_path: str,
    strategy: str = "code",
) -> list[CodeChunk]:
    if strategy not in {"code", "fixed"}:
        raise ValueError("strategy must be 'code' or 'fixed'")
    source_lines = source.splitlines(keepends=True)
    if not source_lines:
        return []
    if strategy == "fixed":
        ranges = [
            {
                "start": start, "end": end, "name": None, "qualname": None,
                "kind": "other", "parent": None,
            }
            for start, end in _windows(
                source_lines, 1, len(source_lines),
                window=WINDOW_LINES, overlap=WINDOW_OVERLAP,
            )
        ]
    else:
        ranges = _ast_chunks(source, file_path)
        if not ranges:
            ranges = [{
                "start": 1, "end": len(source_lines), "name": None,
                "qualname": None, "kind": "module", "parent": None,
            }]

    chunks = [
        _make_chunk(
            source_lines,
            index_id=index_id,
            commit_sha=commit_sha,
            file_path=file_path,
            start=item["start"],
            end=item["end"],
            symbol_name=item["name"],
            qualname=item["qualname"],
            symbol_kind=item["kind"],
            parent=item["parent"],
            strategy=strategy,
        )
        for item in ranges
        if item["end"] >= item["start"]
    ]
    return chunks


def chunk_python_bytes(
    data: bytes,
    *,
    index_id: str,
    commit_sha: str,
    file_path: str,
    strategy: str = "code",
) -> list[CodeChunk]:
    source = decode_source(data, file_path)
    # Build symbol records as a shared parser consistency check.
    symbols_for_source(source, repo="", file_path=file_path)
    return chunk_python_source(
        source,
        index_id=index_id,
        commit_sha=commit_sha,
        file_path=file_path,
        strategy=strategy,
    )
