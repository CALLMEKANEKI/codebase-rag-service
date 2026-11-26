from rag.retrieval.dense import RetrievedChunk
from rag.retrieval.reranker import CrossEncoderReranker


class FakeScorer:
    def predict(self, pairs):
        assert "dispatch_request" in pairs[0][1]
        return [0.1, 0.9]


def test_cross_encoder_reranker_reorders_candidates():
    candidates = [
        RetrievedChunk({"chunk_id": "a", "text": "dispatch_request"}, 0.9, 1),
        RetrievedChunk({"chunk_id": "b", "text": "other"}, 0.8, 2),
    ]
    reranker = CrossEncoderReranker(scorer=FakeScorer())
    results = reranker.rerank("how dispatch works", candidates, top_k=2)
    assert [item.chunk["chunk_id"] for item in results] == ["b", "a"]
    assert [item.rank for item in results] == [1, 2]
