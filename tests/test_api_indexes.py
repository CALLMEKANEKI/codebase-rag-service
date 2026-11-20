import json
import time

from fastapi.testclient import TestClient

import api.main as api_main
from api.main import create_app
from rag.config import Settings


class FakeStore:
    def __init__(self):
        self.deleted = []

    def delete(self, index_id):
        self.deleted.append(index_id)

    def close(self):
        pass


class FakeCache:
    def close(self):
        pass


class FakeEmbedder:
    pass


def test_create_index_status_and_snapshot_file_lifecycle(tmp_path, monkeypatch):
    store = FakeStore()

    def fake_runtime(app):
        runtime = (FakeEmbedder(), store, FakeCache())
        app.state.embedding_runtime = runtime
        app.state.resources.append(("runtime", runtime))
        return runtime

    def fake_indexing(**kwargs):
        root = kwargs["index_root"]
        snapshot = root / "snapshot" / "src"
        snapshot.mkdir(parents=True)
        (snapshot / "app.py").write_text(
            "def first():\n    return 1\n\ndef second():\n    return 2\n",
            encoding="utf-8",
        )
        (root / "snapshot" / "large.py").write_text(
            "".join(f"line {number}\n" for number in range(1, 2002)),
            encoding="utf-8",
        )
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "files": [{"path": "src/app.py"}, {"path": "large.py"}],
                    "stats": {"files": 2, "chunks": 2},
                }
            ),
            encoding="utf-8",
        )
        kwargs["update_status"](
            status="parsing", stage="parsing", total_files=1, files_done=0
        )
        kwargs["update_status"](
            status="ready",
            stage=None,
            total_files=1,
            files_done=1,
            commit_sha="a" * 40,
            files=1,
            chunks=2,
        )

    monkeypatch.setattr(api_main, "_get_embedding_runtime", fake_runtime)
    monkeypatch.setattr(api_main, "run_indexing", fake_indexing)
    settings = Settings(
        rag_api_key="test-key",
        index_storage_path=tmp_path / "indexes",
        index_registry_path=tmp_path / "index_registry.sqlite",
    )
    app = create_app(settings=settings)

    with TestClient(app) as client:
        created = client.post(
            "/v1/indexes",
            headers={"X-API-Key": "test-key", "X-Request-ID": "create-1"},
            json={
                "repo_url": "https://github.com/pallets/flask",
                "ref": "main",
                "client_ref": "repo-42",
            },
        )
        assert created.status_code == 202
        assert created.headers["X-Request-ID"] == "create-1"
        index_id = created.json()["index_id"]

        status = None
        for _ in range(100):
            response = client.get(
                f"/v1/indexes/{index_id}", headers={"X-API-Key": "test-key"}
            )
            status = response.json()
            if status["status"] in {"ready", "failed"}:
                break
            time.sleep(0.01)

        assert status["status"] == "ready"
        assert status["commit_sha"] == "a" * 40
        assert status["stats"] == {"files": 1, "chunks": 2}

        file_response = client.get(
            f"/v1/indexes/{index_id}/files",
            headers={"X-API-Key": "test-key"},
            params={"path": "src/app.py", "start": 2, "end": 99},
        )
        assert file_response.status_code == 200
        assert file_response.json() == {
            "path": "src/app.py",
            "start": 2,
            "end": 5,
            "content": "    return 1\n\ndef second():\n    return 2\n",
            "language": "python",
        }

        traversal = client.get(
            f"/v1/indexes/{index_id}/files",
            headers={"X-API-Key": "test-key"},
            params={"path": "../outside.py"},
        )
        assert traversal.status_code == 404
        assert traversal.json()["error"]["code"] == "FILE_NOT_FOUND"

        invalid_range = client.get(
            f"/v1/indexes/{index_id}/files",
            headers={"X-API-Key": "test-key"},
            params={"path": "large.py", "start": 1, "end": 2001},
        )
        assert invalid_range.status_code == 400
        assert invalid_range.json()["error"]["code"] == "INVALID_RANGE"

        deleted = client.delete(
            f"/v1/indexes/{index_id}", headers={"X-API-Key": "test-key"}
        )
        assert deleted.status_code == 204
        assert store.deleted == [index_id]
        missing = client.get(
            f"/v1/indexes/{index_id}", headers={"X-API-Key": "test-key"}
        )
        assert missing.status_code == 404
        assert (
            client.delete(
                f"/v1/indexes/{index_id}", headers={"X-API-Key": "test-key"}
            ).status_code
            == 204
        )


def test_index_endpoints_require_api_key_and_reject_invalid_repo(tmp_path):
    app = create_app(
        settings=Settings(
            rag_api_key="test-key",
            index_storage_path=tmp_path / "indexes",
            index_registry_path=tmp_path / "index_registry.sqlite",
        )
    )
    with TestClient(app) as client:
        unauthorized = client.post(
            "/v1/indexes",
            json={"repo_url": "https://github.com/pallets/flask"},
        )
        assert unauthorized.status_code == 401
        assert unauthorized.json()["error"]["code"] == "UNAUTHORIZED"

        invalid = client.post(
            "/v1/indexes",
            headers={"X-API-Key": "test-key"},
            json={"repo_url": "https://gitlab.com/pallets/flask"},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_REPO"


def test_file_viewer_rejects_range_start_after_eof(tmp_path):
    index_root = tmp_path / "idx_demo"
    snapshot = index_root / "snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (index_root / "manifest.json").write_text(
        json.dumps({"files": [{"path": "app.py"}]}), encoding="utf-8"
    )

    from api.main import APIError, _read_snapshot_file

    try:
        _read_snapshot_file(index_root, "app.py", 2, None)
    except APIError as exc:
        assert exc.status_code == 400
        assert exc.code == "INVALID_RANGE"
    else:
        raise AssertionError("start beyond EOF should be rejected")
