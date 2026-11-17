from rag.index_registry import IndexRegistry


def test_registry_persists_status_and_marks_interrupted_jobs_failed(tmp_path):
    database = tmp_path / "indexes.sqlite"
    registry = IndexRegistry(database)
    registry.create(
        "idx_persist",
        "https://github.com/pallets/flask",
        "main",
        "web-repo-17",
    )
    registry.update(
        "idx_persist",
        status="embedding",
        stage="embedding",
        files_done=8,
        total_files=20,
        commit_sha="d" * 40,
    )

    restarted = IndexRegistry(database)
    restarted.mark_interrupted_jobs_failed()
    row = restarted.get("idx_persist")

    assert row is not None
    assert row["repo_url"] == "https://github.com/pallets/flask"
    assert row["client_ref"] == "web-repo-17"
    api_status = restarted.to_api(row)
    assert api_status["status"] == "failed"
    assert api_status["stage"] == "embedding"
    assert api_status["progress"] == {"files_done": 8, "total_files": 20}
    assert api_status["error"]["code"] == "INTERNAL_ERROR"
