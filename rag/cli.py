from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path

from rag.embeddings.cache import CachedEmbedder, SQLiteEmbeddingCache
from rag.embeddings.sentence_transformer import SentenceTransformerEmbedder
from rag.indexing import IndexingService
from rag.ingestion.clone import clone_repository, resolve_commit_sha
from rag.ingestion.filters import FilterLimits, enumerate_python_files
from rag.ingestion.snapshot import create_snapshot
from rag.parsing.chunking import CHUNKER_VERSION, chunk_python_bytes
from rag.retrieval.dense import DenseRetriever
from rag.vectorstore.qdrant_store import QdrantVectorStore

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
    print(
        json.dumps(
            {
                "index_id": index_id,
                "commit_sha": commit_sha,
                "files": len(report.files),
                "chunks": len(chunks),
                "total_bytes": report.total_bytes,
                "skipped": report.skipped,
                "snapshot": str(snapshot_root),
                "chunks_path": str(chunks_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0



def _embedding_runtime(args):
    base_embedder = SentenceTransformerEmbedder(
        model_name=args.model,
        device=args.device,
    )
    cache = SQLiteEmbeddingCache(
        args.cache_path,
        chunker_version=CHUNKER_VERSION,
        header_enabled=args.header_enabled,
    )
    return CachedEmbedder(base_embedder, cache), cache


def index_chunks(args: argparse.Namespace) -> int:
    embedder, cache = _embedding_runtime(args)
    store = QdrantVectorStore(path=args.qdrant_path)
    try:
        result = IndexingService(embedder, store).index_jsonl(
            args.index_id,
            args.chunks_path,
            batch_size=args.batch_size,
        )
        print(
            json.dumps(
                {
                    "index_id": result.index_id,
                    "chunks_indexed": result.chunks_indexed,
                    "batches": result.batches,
                    "model": embedder.model_name,
                    "dimension": embedder.dim,
                    "qdrant_path": args.qdrant_path,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        store.close()
        cache.close()
    return 0


def retrieve(args: argparse.Namespace) -> int:
    embedder, cache = _embedding_runtime(args)
    store = QdrantVectorStore(path=args.qdrant_path)
    try:
        results = DenseRetriever(embedder, store).retrieve(
            args.index_id,
            args.query,
            top_k=args.top_k,
        )
        print(
            json.dumps(
                [
                    {
                        "rank": item.rank,
                        "score": item.score,
                        "chunk_id": item.chunk.get("chunk_id"),
                        "file_path": item.chunk.get("file_path"),
                        "start_line": item.chunk.get("start_line"),
                        "end_line": item.chunk.get("end_line"),
                        "qualname": item.chunk.get("qualname"),
                        "text": item.chunk.get("text"),
                    }
                    for item in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        store.close()
        cache.close()
    return 0


def add_embedding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=os.getenv(
            "EMBEDDING_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        ),
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default=os.getenv("EMBEDDING_DEVICE", "auto"),
    )
    parser.add_argument(
        "--qdrant-path",
        default="storage/qdrant",
    )
    parser.add_argument(
        "--cache-path",
        default="storage/embedding_cache.sqlite",
    )
    parser.add_argument("--header-enabled", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest_parser = subparsers.add_parser(
        "ingest", help="Create a source snapshot and code chunks"
    )
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

    index_parser = subparsers.add_parser(
        "index", help="Embed chunks and upsert them into local Qdrant"
    )
    index_parser.add_argument("--index-id", required=True)
    index_parser.add_argument("--chunks-path", required=True)
    index_parser.add_argument("--batch-size", type=int, default=32)
    add_embedding_arguments(index_parser)
    index_parser.set_defaults(handler=index_chunks)

    retrieve_parser = subparsers.add_parser(
        "retrieve", help="Run dense vector retrieval against a local Qdrant index"
    )
    retrieve_parser.add_argument("--index-id", required=True)
    retrieve_parser.add_argument("--query", required=True)
    retrieve_parser.add_argument("--top-k", type=int, default=10)
    add_embedding_arguments(retrieve_parser)
    retrieve_parser.set_defaults(handler=retrieve)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
