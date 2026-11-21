from rag.retrieval.bm25 import BM25Index, BM25Retriever
from rag.retrieval.dense import RetrievedChunk
from rag.retrieval.hybrid import HybridRetriever


class FakeRetriever:
    def __init__(self, results):
        self.results = results

    def retrieve(self, index_id, query, top_k=10):
        return self.results[:top_k]


def item(chunk_id, rank):
    return RetrievedChunk({"chunk_id": chunk_id}, 1.0 / rank, rank)


def test_rrf_fuses_dense_and_lexical_rankings_deterministically():
    dense = FakeRetriever([item("a", 1), item("b", 2)])
    lexical = FakeRetriever([item("b", 1), item("c", 2)])
    results = HybridRetriever(dense, lexical, rrf_k=1).retrieve("idx", "query", 3)
    assert [result.chunk["chunk_id"] for result in results] == ["b", "a", "c"]
    assert [result.rank for result in results] == [1, 2, 3]


def test_rrf_deduplicates_chunks_and_validates_arguments():
    import pytest

    retriever = HybridRetriever(
        FakeRetriever([item("a", 1)]), FakeRetriever([item("a", 1)])
    )
    assert len(retriever.retrieve("idx", "query", 5)) == 1
    with pytest.raises(ValueError):
        retriever.retrieve("idx", " ", 5)


def test_hybrid_retriever_fuses_persistent_bm25_results(tmp_path):
    index_root = tmp_path / "indexes"
    BM25Index.build(
        index_root / "idx" / "bm25.sqlite3",
        [
            {
                "chunk_id": "dispatch",
                "text": "def dispatch_request(): pass",
            }
        ],
    )
    retriever = HybridRetriever(FakeRetriever([]), BM25Retriever(index_root))

    results = retriever.retrieve("idx", "dispatchRequest", top_k=1)

    assert [result.chunk["chunk_id"] for result in results] == ["dispatch"]
    assert results[0].rank == 1
