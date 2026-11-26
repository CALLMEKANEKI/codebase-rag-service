import threading
from pathlib import Path

from rag.indexing_pipeline import IndexCancelled, run_indexing
from rag.retrieval.bm25 import BM25Retriever


class FakeEmbedder:
    dim = 3
    model_name = "fake"

    def embed_documents(self, texts):
        return [[float(len(text)), 1.0, 0.0] for text in texts]


class FakeStore:
    def __init__(self):
        self.collection = None
        self.points = []

    def create_collection(self, index_id, vector_size):
        self.collection = (index_id, vector_size)

    def upsert(self, index_id, chunks, vectors):
        self.points.extend(zip(chunks, vectors, strict=True))

    def search(self, index_id, vector, top_k):
        return []

    def delete(self, index_id):
        self.collection = None


def test_indexing_pipeline_creates_snapshot_chunks_and_vectors(tmp_path, monkeypatch):
    commit_sha = "b" * 40

    def fake_clone(repo_url, destination, ref=None):
        destination = Path(destination)
        destination.mkdir(parents=True)
        (destination / "app.py").write_text(
            "def greet(name):\n    return f'hello {name}'\n",
            encoding="utf-8",
        )
        return commit_sha

    monkeypatch.setattr("rag.indexing_pipeline.clone_repository", fake_clone)
    statuses = []
    store = FakeStore()
    index_root = tmp_path / "indexes" / "idx_test"

    run_indexing(
        index_id="idx_test",
        repo_url="https://github.com/example/repo",
        ref="main",
        index_root=index_root,
        embedder=FakeEmbedder(),
        store=store,
        update_status=lambda **values: statuses.append(values),
        cancelled=threading.Event(),
    )

    assert (index_root / "manifest.json").is_file()
    assert (index_root / "snapshot" / "app.py").is_file()
    assert (index_root / "chunks.jsonl").is_file()
    assert store.collection == ("idx_test", 3)
    assert len(store.points) == 1
    assert statuses[-1]["status"] == "ready"
    assert statuses[-1]["commit_sha"] == commit_sha
    lexical_results = BM25Retriever(index_root.parent).retrieve("idx_test", "greet")
    assert lexical_results
    assert lexical_results[0].chunk["index_id"] == "idx_test"


def test_indexing_pipeline_stops_when_cancelled_after_clone(tmp_path, monkeypatch):
    cancelled = threading.Event()

    def fake_clone(repo_url, destination, ref=None):
        Path(destination).mkdir(parents=True)
        cancelled.set()
        return "c" * 40

    monkeypatch.setattr("rag.indexing_pipeline.clone_repository", fake_clone)

    try:
        run_indexing(
            index_id="idx_cancel",
            repo_url="https://github.com/example/repo",
            ref=None,
            index_root=tmp_path / "idx_cancel",
            embedder=FakeEmbedder(),
            store=FakeStore(),
            update_status=lambda **values: None,
            cancelled=cancelled,
        )
    except IndexCancelled:
        pass
    else:
        raise AssertionError("cancelled job should stop before parsing")
