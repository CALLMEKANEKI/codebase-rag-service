from rag.retrieval.bm25 import BM25Index, tokenize_code


def test_tokenizer_splits_snake_camel_dotted_and_acronyms():
    tokens = tokenize_code("dispatch_request MyHTTPClient.handleRequest")
    assert "dispatch_request" in tokens
    assert {"dispatch", "request", "my", "http", "client", "handle"} <= set(tokens)
    assert "handle" in tokens


def test_bm25_ranks_identifier_match_and_persists(tmp_path):
    path = tmp_path / "bm25.sqlite3"
    chunks = [
        {
            "chunk_id": "a",
            "file_path": "src/app.py",
            "text": "def dispatch_request(): pass",
        },
        {
            "chunk_id": "b",
            "file_path": "src/other.py",
            "text": "serialize response body",
        },
    ]
    index = BM25Index.build(path, chunks)
    results = index.retrieve("dispatchRequest", top_k=2)
    assert results[0].chunk["chunk_id"] == "a"
    reopened = BM25Index(path).retrieve("dispatch request", top_k=1)
    assert reopened[0].chunk["chunk_id"] == "a"


def test_bm25_validates_query_and_top_k(tmp_path):
    index = BM25Index.build(tmp_path / "bm25.sqlite3", [])
    import pytest

    with pytest.raises(ValueError):
        index.retrieve(" ")
    with pytest.raises(ValueError):
        index.retrieve("query", 0)
