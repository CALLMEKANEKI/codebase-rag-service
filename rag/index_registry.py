from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


class IndexRegistry:
    """Small persistent SQLite registry for index jobs and their public status."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS indexes (
                    index_id TEXT PRIMARY KEY,
                    repo_url TEXT NOT NULL,
                    requested_ref TEXT,
                    client_ref TEXT,
                    status TEXT NOT NULL,
                    stage TEXT,
                    files_done INTEGER,
                    total_files INTEGER,
                    commit_sha TEXT,
                    files INTEGER,
                    chunks INTEGER,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create(
        self, index_id: str, repo_url: str, ref: str | None, client_ref: str | None
    ) -> None:
        now = _now()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO indexes (
                    index_id, repo_url, requested_ref, client_ref, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (index_id, repo_url, ref, client_ref, now, now),
            )
            connection.commit()

    def update(self, index_id: str, **values: Any) -> None:
        allowed = {
            "status", "stage", "files_done", "total_files", "commit_sha",
            "files", "chunks", "error_code", "error_message",
        }
        fields = {key: value for key, value in values.items() if key in allowed}
        if not fields:
            return
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._lock, self._connection() as connection:
            connection.execute(
                f"UPDATE indexes SET {assignments} WHERE index_id = ?",
                (*fields.values(), index_id),
            )
            connection.commit()

    def get(self, index_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM indexes WHERE index_id = ?", (index_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete(self, index_id: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM indexes WHERE index_id = ?", (index_id,))
            connection.commit()

    def mark_interrupted_jobs_failed(self) -> None:
        now = _now()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE indexes
                SET status = 'failed', stage = CASE
                    WHEN status = 'pending' THEN NULL ELSE stage END,
                    error_code = 'INTERNAL_ERROR',
                    error_message = 'Indexing was interrupted by service restart',
                    updated_at = ?
                WHERE status IN ('pending', 'cloning', 'parsing', 'embedding')
                """,
                (now,),
            )
            connection.commit()

    @staticmethod
    def to_api(row: dict[str, Any]) -> dict[str, Any]:
        status = row["status"]
        stage = row["stage"] if status in {"cloning", "parsing", "embedding", "failed"} else None
        progress = None
        if row["total_files"] is not None:
            progress = {
                "files_done": int(row["files_done"] or 0),
                "total_files": int(row["total_files"]),
            }
        stats = None
        if status == "ready":
            stats = {"files": int(row["files"] or 0), "chunks": int(row["chunks"] or 0)}
        error = None
        if status == "failed":
            error = {
                "code": row["error_code"] or "INTERNAL_ERROR",
                "message": row["error_message"] or "Indexing failed",
            }
        return {
            "index_id": row["index_id"],
            "status": status,
            "stage": stage,
            "progress": progress,
            "commit_sha": row["commit_sha"],
            "stats": stats,
            "error": error,
        }
