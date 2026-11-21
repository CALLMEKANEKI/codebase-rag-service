from __future__ import annotations

import json
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from rag.embeddings.base import Embedder
from rag.indexing import IndexingService
from rag.ingestion.clone import CloneError, clone_repository
from rag.ingestion.filters import (
    FilterLimits,
    RepositoryLimitError,
    enumerate_python_files,
)
from rag.ingestion.snapshot import create_snapshot
from rag.parsing.chunking import chunk_python_bytes
from rag.retrieval.bm25 import BM25Index
from rag.vectorstore.base import VectorStore

MAX_CHUNKS = 20_000


class IndexCancelled(RuntimeError):
    pass


def run_indexing(
    *,
    index_id: str,
    repo_url: str,
    ref: str | None,
    index_root: Path,
    embedder: Embedder,
    store: VectorStore,
    update_status: Callable[..., None],
    cancelled: threading.Event,
) -> None:
    def check_cancelled() -> None:
        if cancelled.is_set():
            raise IndexCancelled("Indexing was cancelled")

    try:
        update_status(status="cloning", stage="cloning", files_done=0, total_files=None)
        with tempfile.TemporaryDirectory(prefix="rag-index-") as temporary:
            source_root = Path(temporary) / "repository"
            commit_sha = clone_repository(repo_url, source_root, ref=ref)
            check_cancelled()

            update_status(status="parsing", stage="parsing", commit_sha=commit_sha)
            report = enumerate_python_files(
                source_root,
                FilterLimits(
                    max_files=2_000,
                    max_file_bytes=256 * 1024,
                    max_total_bytes=50 * 1024 * 1024,
                ),
            )
            update_status(total_files=len(report.files), files_done=0)
            manifest = create_snapshot(
                source_root,
                index_root,
                repo_url=repo_url,
                commit_sha=commit_sha,
                report=report,
            )
            chunks = []
            chunks_path = index_root / "chunks.jsonl"
            with chunks_path.open("w", encoding="utf-8", newline="\n") as stream:
                for position, relative in enumerate(report.files, start=1):
                    check_cancelled()
                    path = index_root / "snapshot" / relative
                    chunks.extend(
                        chunk_python_bytes(
                            path.read_bytes(),
                            index_id=index_id,
                            commit_sha=commit_sha,
                            file_path=relative,
                            strategy="code",
                        )
                    )
                    if len(chunks) > MAX_CHUNKS:
                        raise RepositoryLimitError(
                            f"Repository exceeds max_chunks={MAX_CHUNKS}"
                        )
                    update_status(files_done=position)
                for chunk in chunks:
                    stream.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")

            manifest["stats"]["chunks"] = len(chunks)
            (index_root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            check_cancelled()
            update_status(status="embedding", stage="embedding")

        check_cancelled()
        if not chunks:
            raise ValueError("Repository contains no indexable Python chunks")
        IndexingService(embedder, store).index_jsonl(
            index_id, index_root / "chunks.jsonl", batch_size=32
        )
        BM25Index.build(
            index_root / "bm25.sqlite3",
            [
                json.loads(line)
                for line in (index_root / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ],
        )
        check_cancelled()

        update_status(
            status="ready",
            stage=None,
            files_done=len(report.files),
            total_files=len(report.files),
            commit_sha=commit_sha,
            files=len(report.files),
            chunks=len(chunks),
            error_code=None,
            error_message=None,
        )
    except IndexCancelled:
        raise
    except RepositoryLimitError:
        update_status(
            status="failed",
            stage="parsing",
            error_code="REPO_TOO_LARGE",
            error_message="Repository exceeds the configured size or file limit",
        )
        raise
    except CloneError as exc:
        message = str(exc)
        code = "REPO_NOT_FOUND" if "not found" in message.lower() else "INTERNAL_ERROR"
        update_status(
            status="failed",
            stage="cloning",
            error_code=code,
            error_message="Unable to clone the requested public GitHub repository",
        )
        raise
    except Exception:
        update_status(
            status="failed",
            stage="embedding",
            error_code="INTERNAL_ERROR",
            error_message="Indexing failed due to an internal processing error",
        )
        raise
