from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag.embeddings.base import Embedder
from rag.vectorstore.base import VectorStore


@dataclass(frozen=True)
class IndexingResult:
    index_id: str
    chunks_indexed: int
    batches: int


class IndexingService:
    """Embed and upsert an existing chunks.jsonl file in bounded batches."""

    def __init__(self, embedder: Embedder, vector_store: VectorStore) -> None:
        self.embedder = embedder
        self.vector_store = vector_store

    def index_chunks(
        self,
        index_id: str,
        chunks: list[dict[str, Any]],
        *,
        batch_size: int = 32,
    ) -> IndexingResult:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not chunks:
            return IndexingResult(index_id, chunks_indexed=0, batches=0)

        for chunk in chunks:
            if not chunk.get("chunk_id") or not isinstance(chunk.get("text"), str):
                raise ValueError("Each chunk must include chunk_id and text")
            chunk_index_id = chunk.get("index_id")
            if chunk_index_id is not None and chunk_index_id != index_id:
                raise ValueError("Chunk index_id does not match requested index")

        self.vector_store.create_collection(index_id, self.embedder.dim)
        batches = 0
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            vectors = self.embedder.embed_documents([chunk["text"] for chunk in batch])
            if len(vectors) != len(batch):
                raise ValueError("Embedder returned an unexpected number of vectors")
            if any(len(vector) != self.embedder.dim for vector in vectors):
                raise ValueError("Embedder returned an unexpected vector dimension")
            self.vector_store.upsert(index_id, batch, vectors)
            batches += 1

        return IndexingResult(
            index_id=index_id,
            chunks_indexed=len(chunks),
            batches=batches,
        )

    def index_jsonl(
        self,
        index_id: str,
        chunks_path: str | Path,
        *,
        batch_size: int = 32,
    ) -> IndexingResult:
        chunks = []
        with Path(chunks_path).open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid JSON in {chunks_path}:{line_number}"
                    ) from exc
        return self.index_chunks(index_id, chunks, batch_size=batch_size)
