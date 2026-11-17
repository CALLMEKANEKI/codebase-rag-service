import pytest

from rag.embeddings.cache import CachedEmbedder, SQLiteEmbeddingCache
from rag.embeddings.fake import FakeEmbedder
from rag.indexing import IndexingService
from rag.retrieval.dense import DenseRetriever
from rag.vectorstore.qdrant_store import QdrantVectorStore


class CountingEmbedder(FakeEmbedder):
    def __init__(self, dim: int = 16) -> None:
        super().__init__(dim)
        self.calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return super().embed_documents(texts)


def make_chunk(chunk_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "file_path": "src/example.py",
        "start_line": 1,
        "end_line": 2,
        "text": text,
        "content_hash": chunk_id,
    }


def test_fake_embedder_is_deterministic_and_normalized():
    embedder = FakeEmbedder(dim=16)
    first = embedder.embed_query("dispatch_request")
    second = embedder.embed_query("dispatch_request")

    assert first == second
    assert len(first) == 16
    assert sum(value * value for value in first) == pytest.approx(1.0)


def test_sqlite_cache_reuses_embeddings_and_is_configuration_sensitive(tmp_path):
    base = CountingEmbedder()
    cache = SQLiteEmbeddingCache(
        tmp_path / "cache.sqlite",
        chunker_version="python-ast-v1",
        header_enabled=True,
    )
    embedder = CachedEmbedder(base, cache)

    first = embedder.embed_documents(["alpha", "beta"])
    second = embedder.embed_documents(["alpha", "beta"])

    assert first == second
    assert base.calls == 1
    assert embedder.embed_query("alpha") == first[0]
    assert base.calls == 1
    assert cache.make_key(base, "alpha") != cache.make_key(
        base, "alpha "
    )

    cache.close()


def test_qdrant_local_store_upserts_searches_and_isolates_indexes():
    store = QdrantVectorStore()
    embedder = FakeEmbedder(dim=16)
    chunks = [
        make_chunk("chunk-alpha", "dispatch_request handles the request"),
        make_chunk("chunk-beta", "response serialization"),
    ]
    vectors = embedder.embed_documents([chunk["text"] for chunk in chunks])
    store.upsert("flask", chunks, vectors)

    retriever = DenseRetriever(embedder, store)
    results = retriever.retrieve(
        "flask",
        "dispatch_request handles the request",
        top_k=2,
    )

    assert results
    assert results[0].chunk["chunk_id"] == "chunk-alpha"
    assert results[0].rank == 1
    assert results[0].score == pytest.approx(1.0, abs=1e-5)
    assert store.search("another-index", vectors[0], 5) == []

    store.delete("flask")
    assert store.search("flask", vectors[0], 5) == []
    store.close()


def test_qdrant_rejects_mismatched_vectors_and_invalid_index_ids():
    store = QdrantVectorStore()
    with pytest.raises(ValueError, match="same length"):
        store.upsert("flask", [make_chunk("one", "text")], [])
    with pytest.raises(ValueError, match="index_id"):
        store.create_collection("../bad", 16)
    store.close()


def test_embedding_cache_persists_between_instances(tmp_path):
    database = tmp_path / "cache.sqlite"
    first_cache = SQLiteEmbeddingCache(
        database,
        chunker_version="v1",
        header_enabled=False,
    )
    embedder = FakeEmbedder(dim=8)
    vector = embedder.embed_query("persistent")
    key = first_cache.make_key(embedder, "persistent")
    first_cache.put(
        key,
        model_name=embedder.model_name,
        dimension=embedder.dim,
        vector=vector,
    )
    first_cache.close()

    second_cache = SQLiteEmbeddingCache(
        database,
        chunker_version="v1",
        header_enabled=False,
    )
    assert second_cache.get(key) == vector
    second_cache.close()



def test_indexing_service_reads_chunks_jsonl_and_builds_dense_index(tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    chunks = [
        {
            **make_chunk("chunk-one", "dispatch_request"),
            "index_id": "flask",
        },
        {
            **make_chunk("chunk-two", "response serialization"),
            "index_id": "flask",
        },
    ]
    chunks_path.write_text(
        "\n".join(json.dumps(chunk) for chunk in chunks) + "\n",
        encoding="utf-8",
    )
    embedder = FakeEmbedder(dim=16)
    store = QdrantVectorStore()
    result = IndexingService(embedder, store).index_jsonl(
        "flask",
        chunks_path,
        batch_size=1,
    )

    assert result.chunks_indexed == 2
    assert result.batches == 2
    retrieved = DenseRetriever(embedder, store).retrieve(
        "flask",
        "dispatch_request",
        top_k=1,
    )
    assert retrieved[0].chunk["chunk_id"] == "chunk-one"
    store.close()
