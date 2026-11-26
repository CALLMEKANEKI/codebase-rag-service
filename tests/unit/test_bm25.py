from rag.retrieval.bm25 import BM25Index, tokenize_code


def test_tokenizer_splits_snake_camel_dotted_and_acronyms():
    tokens = tokenize_code(
        "dispatch_request camelCase PascalCase HTTPClient "
        "MyHTTPClient.handleRequest module.ClassName Session.request"
    )
    assert "dispatch_request" in tokens
    assert "session.request" in tokens
    assert "myhttpclient.handlerequest" in tokens
    assert {
        "dispatch",
        "request",
        "camel",
        "case",
        "pascal",
        "http",
        "client",
        "my",
        "handle",
        "module",
        "class",
        "name",
    } <= set(tokens)
    assert all(not any(char.isspace() for char in token) for token in tokens)


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
    assert results
    assert results[0].chunk["chunk_id"] == "a"
    reopened = BM25Index(path).retrieve("dispatch request", top_k=1)
    assert reopened[0].chunk["chunk_id"] == "a"


def test_bm25_exact_qualified_method_beats_parent_class_and_neighbor_methods(
    tmp_path,
):
    path = tmp_path / "bm25.sqlite3"
    chunks = [
        {
            "chunk_id": "parent",
            "file_path": "requests/sessions.py",
            "qualname": "Session",
            "symbol_kind": "class",
            "text": "class Session: manages persistent settings and cookies",
        },
        {
            "chunk_id": "target",
            "file_path": "requests/sessions.py",
            "qualname": "Session.request",
            "symbol_kind": "method",
            "text": "def request(self, method, url, **kwargs): send a request",
        },
        {
            "chunk_id": "neighbor",
            "file_path": "requests/sessions.py",
            "qualname": "Session.send",
            "symbol_kind": "method",
            "text": "def send(self, request, **kwargs): send prepared request",
        },
        {
            "chunk_id": "other",
            "file_path": "requests/api.py",
            "qualname": "request",
            "symbol_kind": "function",
            "text": "def request(method, url, **kwargs): convenience API",
        },
    ]
    index = BM25Index.build(path, chunks)

    results = index.retrieve("How does Session.request send a request?", top_k=4)

    assert results[0].chunk["chunk_id"] == "target"


def test_bm25_validates_query_and_top_k(tmp_path):
    index = BM25Index.build(tmp_path / "bm25.sqlite3", [])
    import pytest

    with pytest.raises(ValueError):
        index.retrieve(" ")
    with pytest.raises(ValueError):
        index.retrieve("query", 0)
