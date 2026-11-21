from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.metrics import hit_at_k, ndcg_at_k, recall_at_k, reciprocal_rank
from eval.tools.corpus import (
    DEFAULT_PROFILES,
    ConfigurationError,
    enumerate_corpus,
    load_profiles,
    verify_checkout,
)
from eval.tools.symbols import decode_source, symbols_for_source
from rag.embeddings.cache import CachedEmbedder, SQLiteEmbeddingCache
from rag.embeddings.sentence_transformer import SentenceTransformerEmbedder
from rag.parsing.chunking import CHUNKER_VERSION, chunk_python_bytes
from rag.retrieval.bm25 import BM25Index, BM25Retriever
from rag.retrieval.dense import DenseRetriever
from rag.retrieval.hybrid import HybridRetriever
from rag.retrieval.reranker import CrossEncoderReranker
from rag.vectorstore.qdrant_store import QdrantVectorStore

VARIANTS = ("dense", "bm25", "hybrid", "hybrid_rerank")
DEFAULT_DATASET = Path(__file__).parent / "datasets" / "dev" / "E2_Dev_24.jsonl"


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _metrics(ranked: list, relevant: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "hit_at_5": hit_at_k(ranked, relevant, 5),
        "recall_at_5": recall_at_k(ranked, relevant, 5),
        "recall_at_10": recall_at_k(ranked, relevant, 10),
        "mrr_at_10": reciprocal_rank(ranked[:10], relevant),
        "ndcg_at_10": ndcg_at_k(ranked, relevant, 10),
    }


def _ranked_payload(ranked: list) -> list[dict[str, Any]]:
    return [
        {
            "rank": item.rank,
            "score": item.score,
            "chunk_id": item.chunk.get("chunk_id"),
            "file_path": item.chunk.get("file_path"),
            "start_line": item.chunk.get("start_line"),
            "end_line": item.chunk.get("end_line"),
            "qualname": item.chunk.get("qualname"),
        }
        for item in ranked
    ]


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = (
        "hit_at_5",
        "recall_at_5",
        "recall_at_10",
        "mrr_at_10",
        "ndcg_at_10",
    )

    def summarize(group: list[dict[str, Any]]) -> dict[str, Any]:
        answerable = [row for row in group if row["answerable"]]
        result: dict[str, Any] = {
            "questions": len(group),
            "answerable_questions": len(answerable),
            "unanswerable_questions": len(group) - len(answerable),
        }
        for metric in metric_names:
            values = [row["metrics"][metric] for row in answerable]
            result[metric] = statistics.fmean(values) if values else None
        for latency_key in ("retrieval_ms", "reranker_ms", "total_ms"):
            values = [row[latency_key] for row in group if row[latency_key] is not None]
            result[f"{latency_key}_p50"] = _percentile(values, 0.50)
            result[f"{latency_key}_p95"] = _percentile(values, 0.95)
        return result

    output: dict[str, Any] = {"overall": summarize(rows), "by_repo": {}, "by_category": {}}
    for key in sorted({row["repo"] for row in rows}):
        output["by_repo"][key] = summarize([row for row in rows if row["repo"] == key])
    for key in sorted({row["category"] for row in rows}):
        output["by_category"][key] = summarize(
            [row for row in rows if row["category"] == key]
        )
    return output


def _load_dev_dataset(path: Path) -> list[dict[str, Any]]:
    dataset = path.resolve(strict=True)
    dev_root = (Path(__file__).parent / "datasets" / "dev").resolve()
    if not dataset.is_relative_to(dev_root):
        raise ConfigurationError(
            "Eval Runner only accepts dataset files located under eval/datasets/dev/"
        )
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with dataset.open(encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ConfigurationError(f"dataset line {line_number} is not an object")
            if record.get("split") != "dev":
                raise ConfigurationError(
                    f"dataset line {line_number} is not marked split=dev"
                )
            if record.get("id") in seen_ids:
                raise ConfigurationError(f"duplicate question id: {record.get('id')}")
            seen_ids.add(record.get("id"))
            records.append(record)
    if not records:
        raise ConfigurationError("development dataset is empty")
    return records


def _build_chunks(repo_id: str, repo_root: Path, profile, index_id: str):
    chunks: list[dict[str, Any]] = []
    file_paths = enumerate_corpus(repo_root, profile)
    if not file_paths:
        raise ConfigurationError(f"src_only corpus is empty for {repo_id}")
    for file_path in file_paths:
        source_bytes = (repo_root / file_path).read_bytes()
        source_text = decode_source(source_bytes, file_path)
        docstrings = {
            item["qualname"]: item["doc_first_line"]
            for item in symbols_for_source(source_text, repo_id, file_path)
            if item.get("doc_first_line")
        }
        chunks.extend(
            {
                **chunk.to_dict(),
                "index_id": index_id,
                "doc_first_line": docstrings.get(chunk.qualname),
            }
            for chunk in chunk_python_bytes(
                source_bytes,
                index_id=index_id,
                commit_sha=profile.commit_sha,
                file_path=file_path,
                strategy="code",
            )
        )
    if not chunks:
        raise ConfigurationError(f"no indexable chunks found for {repo_id}")
    return chunks, len(file_paths)


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = args.dataset
    records = _load_dev_dataset(dataset_path)
    profiles = load_profiles(args.profiles)
    repo_roots = {
        "fastapi/fastapi": args.fastapi_root,
        "pallets/flask": args.flask_root,
    }
    active_repos = sorted({record["repo"] for record in records})
    for repo_id in active_repos:
        if repo_id not in profiles or repo_id not in repo_roots:
            raise ConfigurationError(f"unsupported repo in dataset: {repo_id}")
        if repo_roots[repo_id] is None:
            raise ConfigurationError(f"missing local checkout for {repo_id}")
        verify_checkout(repo_roots[repo_id], profiles[repo_id].commit_sha, profiles[repo_id])

    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "retrieval_raw.jsonl"
    summary_path = output_dir / "retrieval_summary.json"
    index_root = output_dir / "indexes"
    qdrant_path = output_dir / "qdrant"
    cache_path = output_dir / "embedding_cache.sqlite"
    bm25_root = index_root
    embedder_base = SentenceTransformerEmbedder(args.model, args.device)
    cache = SQLiteEmbeddingCache(
        cache_path,
        chunker_version=CHUNKER_VERSION,
        header_enabled=args.header_enabled,
    )
    embedder = CachedEmbedder(embedder_base, cache)
    store = QdrantVectorStore(path=qdrant_path)
    bm25_root.mkdir(parents=True, exist_ok=True)

    index_metadata: dict[str, Any] = {}
    retrievers: dict[str, dict[str, Any]] = {}
    try:
        for repo_id in active_repos:
            profile = profiles[repo_id]
            index_id = "eval_" + profile.id_prefix
            started = time.perf_counter()
            chunks, file_count = _build_chunks(
                repo_id, repo_roots[repo_id], profile, index_id
            )
            BM25Index.build(bm25_root / index_id / "bm25.sqlite3", chunks)
            from rag.indexing import IndexingService

            IndexingService(embedder, store).index_chunks(
                index_id, chunks, batch_size=args.batch_size
            )
            build_ms = (time.perf_counter() - started) * 1000
            dense = DenseRetriever(embedder, store)
            bm25 = BM25Retriever(bm25_root)
            hybrid = HybridRetriever(dense, bm25, rrf_k=args.rrf_k)
            retrievers[repo_id] = {
                "index_id": index_id,
                "dense": dense,
                "bm25": bm25,
                "hybrid": hybrid,
                "reranker": CrossEncoderReranker(args.reranker_model, device=args.reranker_device),
            }
            index_metadata[repo_id] = {
                "index_id": index_id,
                "files": file_count,
                "chunks": len(chunks),
                "build_ms": build_ms,
                "bm25_index_bytes": (bm25_root / index_id / "bm25.sqlite3").stat().st_size,
                "dense_index_bytes": _directory_size(qdrant_path),
                "embedding_model": embedder.model_name,
                "embedding_device": embedder_base.device,
            }

        # Warm lazy BM25 loading and the embedding path before collecting latency.
        first_question_by_repo = {
            repo_id: next(row["question"] for row in records if row["repo"] == repo_id)
            for repo_id in active_repos
        }
        for repo_id, question in first_question_by_repo.items():
            item = retrievers[repo_id]
            index_id = item["index_id"]
            item["dense"].retrieve(index_id, question, top_k=args.top_k)
            item["bm25"].retrieve(index_id, question, top_k=args.top_k)

        # Load the cross-encoder before timing queries so every reranker query is warm.
        reranker_load_started = time.perf_counter()
        for item in retrievers.values():
            item["reranker"]._get_scorer()
        reranker_load_ms = (time.perf_counter() - reranker_load_started) * 1000

        raw_rows: list[dict[str, Any]] = []
        with raw_path.open("w", encoding="utf-8", newline="\n") as raw_file:
            for record in records:
                repo_id = record["repo"]
                components = retrievers[repo_id]
                index_id = components["index_id"]
                query = record["question"]
                relevant = record["relevant"]
                answerable = record["answerable"]
                for variant in VARIANTS:
                    retrieval_started = time.perf_counter()
                    reranker_ms = None
                    candidate_count = 0
                    if variant == "dense":
                        ranked = components["dense"].retrieve(index_id, query, args.top_k)
                        candidate_count = len(ranked)
                    elif variant == "bm25":
                        ranked = components["bm25"].retrieve(index_id, query, args.top_k)
                        candidate_count = len(ranked)
                    elif variant == "hybrid":
                        ranked = components["hybrid"].retrieve(index_id, query, args.top_k)
                        candidate_count = len(ranked)
                    else:
                        candidate_k = min(args.top_k * args.candidate_multiplier, args.max_candidates)
                        candidates = components["hybrid"].retrieve(index_id, query, candidate_k)
                        candidate_count = len(candidates)
                        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
                        rerank_started = time.perf_counter()
                        ranked = components["reranker"].rerank(
                            query, candidates, top_k=args.top_k
                        )
                        reranker_ms = (time.perf_counter() - rerank_started) * 1000
                    if variant != "hybrid_rerank":
                        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
                    total_ms = retrieval_ms + (reranker_ms or 0.0)
                    row = {
                        "question_id": record["id"],
                        "repo": repo_id,
                        "category": record["category"],
                        "answerable": answerable,
                        "variant": variant,
                        "question": query,
                        "metrics": _metrics(ranked, relevant),
                        "retrieval_ms": retrieval_ms,
                        "reranker_ms": reranker_ms,
                        "total_ms": total_ms,
                        "candidate_count": candidate_count,
                        "returned_count": len(ranked),
                        "ranked": _ranked_payload(ranked),
                    }
                    raw_file.write(json.dumps(row, ensure_ascii=False) + "\n")
                    raw_rows.append(row)

        summary = {
            "metadata": {
                "created_at": datetime.now(UTC).isoformat(),
                "git_commit": _git_sha(),
                "dataset": str(dataset_path.resolve()),
                "dataset_sha256": _sha256(dataset_path),
                "split": "dev",
                "question_count": len(records),
                "answerable_question_count": sum(bool(row["answerable"]) for row in records),
                "repo_commits": {
                    repo_id: profiles[repo_id].commit_sha for repo_id in active_repos
                },
                "embedding_model": embedder.model_name,
                "embedding_device": embedder_base.device,
                "reranker_model": args.reranker_model,
                "reranker_device_requested": args.reranker_device,
                "reranker_warm": True,
                "reranker_load_ms": reranker_load_ms,
                "top_k": args.top_k,
                "bm25_k1": 1.5,
                "bm25_b": 0.75,
                "rrf_k": args.rrf_k,
                "candidate_multiplier": args.candidate_multiplier,
                "max_candidates": args.max_candidates,
                "chunker_version": CHUNKER_VERSION,
                "header_enabled": args.header_enabled,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "latency_method": (
                    "time.perf_counter; embedding and BM25 lazy-load paths warmed; "
                    "cross-encoder loaded before timing; sequential per-question execution"
                ),
                "cache_state": "persistent SQLite cache; see output directory",
            },
            "index": index_metadata,
            "results": {
                variant: _aggregate([row for row in raw_rows if row["variant"] == variant])
                for variant in VARIANTS
            },
            "raw_results": str(raw_path),
        }
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return summary
    finally:
        store.close()
        cache.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run retrieval experiments on the development split only."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--fastapi-root", type=Path, required=True)
    parser.add_argument("--flask-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("eval/results/dev"))
    parser.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--reranker-model", default="cross-encoder/ms-marco-MiniLM-L-6-v2")
    parser.add_argument("--reranker-device", default="auto")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-multiplier", type=int, default=4)
    parser.add_argument("--max-candidates", type=int, default=50)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--header-enabled", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.top_k < 1 or args.candidate_multiplier < 1 or args.max_candidates < 1:
            raise ConfigurationError("top-k and candidate-pool settings must be positive")
        summary = run(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ConfigurationError, OSError, RuntimeError, ValueError) as exc:
        print(f"Eval Runner error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
