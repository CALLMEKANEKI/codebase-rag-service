from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models


class QdrantVectorStore:
    """Qdrant adapter supporting in-memory and persistent local modes."""

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        client: QdrantClient | None = None,
    ) -> None:
        if client is not None and path is not None:
            raise ValueError("Pass either client or path, not both")
        self._owns_client = client is None
        if client is not None:
            self._client = client
        elif path is None:
            self._client = QdrantClient(":memory:")
        else:
            local_path = Path(path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(local_path))

    @staticmethod
    def collection_name(index_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", index_id):
            raise ValueError("index_id must contain only letters, digits, '_' or '-'")
        return f"idx_{index_id}"

    def create_collection(self, index_id: str, vector_size: int) -> None:
        if vector_size < 1:
            raise ValueError("vector_size must be positive")
        name = self.collection_name(index_id)
        if self._client.collection_exists(name):
            return
        self._client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )

    def upsert(
        self,
        index_id: str,
        chunks: list[dict[str, Any]],
        vectors: list[list[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        if not chunks:
            return
        dimension = len(vectors[0])
        if dimension < 1 or any(len(vector) != dimension for vector in vectors):
            raise ValueError("all vectors must have the same positive dimension")
        self.create_collection(index_id, dimension)

        points = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk_id = str(chunk["chunk_id"])
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=chunk,
                )
            )
        self._client.upsert(
            collection_name=self.collection_name(index_id),
            points=points,
            wait=True,
        )

    def search(
        self,
        index_id: str,
        vector: list[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        name = self.collection_name(index_id)
        if not self._client.collection_exists(name):
            return []
        result = self._client.query_points(
            collection_name=name,
            query=vector,
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )
        return [
            {
                "payload": point.payload or {},
                "score": float(point.score or 0.0),
            }
            for point in result.points
        ]

    def delete(self, index_id: str) -> None:
        name = self.collection_name(index_id)
        if self._client.collection_exists(name):
            self._client.delete_collection(name)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
