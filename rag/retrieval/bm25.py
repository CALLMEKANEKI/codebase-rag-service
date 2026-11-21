from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from threading import RLock
from typing import Any

from rag.retrieval.dense import RetrievedChunk

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*|[0-9]+|[^\\W_]+", re.UNICODE)
_CAMEL_BOUNDARY_1 = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_BOUNDARY_2 = re.compile(r"([A-Z]+)([A-Z][a-z])")


def tokenize_code(text: str) -> list[str]:
    """Tokenize source code identifiers while retaining full and split forms."""
    normalized = _CAMEL_BOUNDARY_2.sub(r"\\1 \\2", text)
    normalized = _CAMEL_BOUNDARY_1.sub(r"\\1 \\2", normalized)
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(normalized):
        token = match.group(0).lower()
        if not token:
            continue
        tokens.append(token)
        if "_" in token:
            tokens.extend(part for part in token.split("_") if part)
    return tokens


class BM25Index:
    """Small persistent BM25 index backed by SQLite; no external search service."""

    def __init__(self, path: str | Path, *, k1: float = 1.5, b: float = 0.75):
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("BM25 parameters require k1 > 0 and 0 <= b <= 1")
        self.path = Path(path)
        self.k1 = k1
        self.b = b
        self._lock = RLock()
        self._documents: list[tuple[dict[str, Any], list[str], int]] = []
        self._document_frequency: Counter[str] = Counter()
        self._average_length = 0.0
        self._load()

    @classmethod
    def build(
        cls,
        path: str | Path,
        chunks: list[dict[str, Any]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> "BM25Index":
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(target)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS documents ("
                "chunk_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                "tokens TEXT NOT NULL, token_length INTEGER NOT NULL)"
            )
            with connection:
                connection.execute("DELETE FROM documents")
                for chunk in chunks:
                    chunk_id = str(chunk.get("chunk_id") or "")
                    body = str(chunk.get("text") or "")
                    if not chunk_id or not body.strip():
                        continue
                    searchable = " ".join(
                        (
                            body,
                            str(chunk.get("file_path") or ""),
                            str(chunk.get("qualname") or ""),
                            str(chunk.get("symbol_name") or ""),
                            str(chunk.get("symbol_kind") or ""),
                        )
                    )
                    tokens = tokenize_code(searchable)
                    if not tokens:
                        continue
                    connection.execute(
                        "INSERT OR REPLACE INTO documents "
                        "(chunk_id, payload, tokens, token_length) VALUES (?, ?, ?, ?)",
                        (
                            chunk_id,
                            json.dumps(chunk, ensure_ascii=False),
                            json.dumps(tokens, ensure_ascii=False),
                            len(tokens),
                        ),
                    )
        finally:
            connection.close()
        return cls(target, k1=k1, b=b)

    def _load(self) -> None:
        if not self.path.is_file():
            self._documents = []
            return
        connection = sqlite3.connect(self.path)
        try:
            rows = connection.execute(
                "SELECT payload, tokens, token_length FROM documents"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            connection.close()
        documents = []
        frequencies: Counter[str] = Counter()
        total_length = 0
        for payload_json, tokens_json, length in rows:
            payload = json.loads(payload_json)
            tokens = json.loads(tokens_json)
            documents.append((payload, tokens, int(length)))
            frequencies.update(set(tokens))
            total_length += int(length)
        self._documents = documents
        self._document_frequency = frequencies
        self._average_length = total_length / len(documents) if documents else 0.0

    def retrieve(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        with self._lock:
            documents = self._documents
            frequencies = self._document_frequency
            average_length = self._average_length
            if not documents:
                return []
            query_terms = set(tokenize_code(query))
            if not query_terms:
                return []
            count = len(documents)
            scored: list[tuple[float, dict[str, Any]]] = []
            for payload, tokens, length in documents:
                term_frequencies = Counter(tokens)
                score = 0.0
                for term in query_terms:
                    frequency = term_frequencies.get(term, 0)
                    if not frequency:
                        continue
                    document_frequency = frequencies[term]
                    inverse_frequency = math.log(
                        1.0 + (count - document_frequency + 0.5) / (document_frequency + 0.5)
                    )
                    denominator = frequency + self.k1 * (
                        1.0 - self.b + self.b * length / max(average_length, 1.0)
                    )
                    score += inverse_frequency * (
                        frequency * (self.k1 + 1.0) / denominator
                    )
                if score > 0:
                    scored.append((score, payload))
            scored.sort(
                key=lambda item: (
                    -item[0],
                    str(item[1].get("chunk_id") or ""),
                )
            )
            return [
                RetrievedChunk(chunk=payload, score=score, rank=rank)
                for rank, (score, payload) in enumerate(scored[:top_k], start=1)
            ]


class BM25Retriever:
    """Loads one persistent BM25 index per indexed repository."""

    def __init__(self, index_root: str | Path, *, filename: str = "bm25.sqlite3"):
        self.index_root = Path(index_root)
        self.filename = filename
        self._indexes: dict[str, BM25Index] = {}
        self._lock = RLock()

    def retrieve(
        self, index_id: str, query: str, top_k: int = 10
    ) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        path = self.index_root / index_id / self.filename
        with self._lock:
            index = self._indexes.get(index_id)
            if index is None or index.path != path:
                index = BM25Index(path)
                self._indexes[index_id] = index
        return index.retrieve(query, top_k)
