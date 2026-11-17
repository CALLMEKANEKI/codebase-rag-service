from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from pathlib import Path

from rag.ingestion.clone import clone_repository, resolve_commit_sha
from rag.ingestion.filters import FilterLimits, enumerate_python_files
from rag.ingestion.snapshot import create_snapshot
from rag.parsing.chunking import chunk_python_bytes

MAX_CHUNKS = 20_000


def ingest(args: argparse.Namespace) -> int:
    index_id = args.index_id or f"idx_{uuid.uuid4().hex[:12]}"
    if not index_id.startswith("idx_") or not index_id.replace("_", "").isalnum():
        raise ValueError("index_id must use the idx_<alphanumeric> format")
    index_root = Path(args.output or Path("storage/indexes") / index_id).resolve()

    with tempfile.TemporaryDirectory(prefix="rag-ingest-") as temporary:
        if args.repo_url:
            source_root = Path(temporary) / "repository"
            commit_sha = clone_repository(args.repo_url, source_root, ref=args.ref)
        else:
            source_root = Path(args.local_path).resolve(strict=True)
            commit_sha = resolve_commit_sha(source_root)

        report = enumerate_python_files(
            source_root,
            FilterLimits(
                max_files=args.max_files,
                max_file_bytes=args.max_file_bytes,
                max_total_bytes=args.max_total_bytes,
            ),
        )
        manifest = create_snapshot(
            source_root,
            index_root,
            repo_url=args.repo_url,
            commit_sha=commit_sha,
            report=report,
        )

    snapshot_root = index_root / "snapshot"
    chunks = []
    for relative in report.files:
        file_path = snapshot_root / relative
        chunks.extend(
            chunk_python_bytes(
                file_path.read_bytes(),
                index_id=index_id,
                commit_sha=commit_sha,
                file_path=relative,
                strategy=args.strategy,
            )
        )
        if len(chunks) > MAX_CHUNKS:
            raise ValueError(f"Repository exceeds max_chunks={MAX_CHUNKS}")

    chunks_path = index_root / "chunks.jsonl"
    temporary_chunks = chunks_path.with_suffix(".jsonl.tmp")
    with temporary_chunks.open("w", encoding="utf-8", newline="\n") as stream:
        for chunk in chunks:
            stream.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
    temporary_chunks.replace(chunks_path)

    manifest["stats"]["chunks"] = len(chunks)
    manifest_path = index_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "index_id": index_id,
        "commit_sha": commit_sha,
        "files": len(report.files),
        "chunks": len(chunks),
        "total_bytes": report.total_bytes,
        "skipped": report.skipped,
        "snapshot": str(snapshot_root),
        "chunks_path": str(chunks_path),
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest_parser = subparsers.add_parser("ingest", help="Create a source snapshot and code chunks")
    source = ingest_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo-url", help="Public GitHub repository URL")
    source.add_argument("--local-path", help="Local Git checkout for development")
    ingest_parser.add_argument("--ref", help="Branch, tag, or commit to index")
    ingest_parser.add_argument("--index-id")
    ingest_parser.add_argument("--output")
    ingest_parser.add_argument("--strategy", choices=("code", "fixed"), default="code")
    ingest_parser.add_argument("--max-files", type=int, default=2_000)
    ingest_parser.add_argument("--max-file-bytes", type=int, default=256 * 1024)
    ingest_parser.add_argument("--max-total-bytes", type=int, default=50 * 1024 * 1024)
    ingest_parser.set_defaults(handler=ingest)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
