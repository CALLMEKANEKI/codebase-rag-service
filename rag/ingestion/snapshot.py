from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from rag.ingestion.clone import resolve_commit_sha
from rag.ingestion.filters import FilterReport, enumerate_python_files


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_snapshot(
    source_root: str | Path,
    index_root: str | Path,
    *,
    repo_url: str | None = None,
    commit_sha: str | None = None,
    report: FilterReport | None = None,
) -> dict:
    """Copy the filtered source corpus and write a content-hash manifest."""
    source = Path(source_root).resolve(strict=True)
    destination = Path(index_root).resolve()
    if source == destination or source in destination.parents:
        raise ValueError(
            "Snapshot destination must not be inside the source repository"
        )
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Index directory is not empty: {destination}")
    report = report or enumerate_python_files(source)
    commit_sha = commit_sha or resolve_commit_sha(source)
    snapshot_root = destination / "snapshot"
    snapshot_root.mkdir(parents=True, exist_ok=True)

    entries = []
    for relative in report.files:
        source_file = source / relative
        if (
            source_file.is_symlink()
            or source_file.resolve().parent != source_file.parent.resolve()
        ):
            raise ValueError(f"Unsafe source path: {relative}")
        target = snapshot_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
        entries.append(
            {
                "path": relative,
                "size_bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )

    manifest = {
        "version": 1,
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "files": entries,
        "stats": {
            "files": len(entries),
            "total_bytes": sum(item["size_bytes"] for item in entries),
            "skipped": report.skipped,
        },
    }
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "manifest.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest
