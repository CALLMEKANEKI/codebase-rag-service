from fastapi.testclient import TestClient

from api.main import create_app
from rag.config import Settings
from rag.generation.service import QueryResult


class FakeQueryService:
    def query(self, index_id, question, *, top_k=5, debug=False):
        return QueryResult(
            answer="Found in code [1].",
            citations=[{
                "ref_id": 1, "file_path": "src/app.py", "symbol": "run",
                "symbol_kind": "function", "start_line": 1, "end_line": 2,
                "snippet": "def run(): pass", "score": 0.9,
            }],
            usage={"input_tokens": 10, "output_tokens": 4},
            debug={"mode": "dense", "retrieved": []} if debug else None,
        )


def make_client(tmp_path):
    settings = Settings(
        rag_api_key="test-key",
        index_storage_path=tmp_path / "indexes",
    )
    index_root = tmp_path / "indexes" / "idx_demo"
    index_root.mkdir(parents=True)
    (index_root / "manifest.json").write_text("{}", encoding="utf-8")
    return TestClient(create_app(query_service=FakeQueryService(), settings=settings))


def test_health_is_public_and_query_requires_api_key(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/v1/health").json() == {"status": "ok"}
    response = client.post("/v1/query", json={
        "index_id": "idx_demo", "question": "What is this?"
    })
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_query_response_matches_contract_and_echoes_request_id(tmp_path):
    client = make_client(tmp_path)
    response = client.post(
        "/v1/query",
        headers={"X-API-Key": "test-key", "X-Request-ID": "req-123"},
        json={"index_id": "idx_demo", "question": "What is this?", "debug": True},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-123"
    body = response.json()
    assert set(body) == {"answer", "citations", "usage", "latency_ms", "debug"}
    assert body["citations"][0]["ref_id"] == 1
    assert body["debug"]["mode"] == "dense"


def test_query_rejects_unsupported_retrieval_options(tmp_path):
    client = make_client(tmp_path)
    response = client.post(
        "/v1/query",
        headers={"X-API-Key": "test-key"},
        json={
            "index_id": "idx_demo", "question": "What is this?",
            "options": {"mode": "hybrid", "rerank": False},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
