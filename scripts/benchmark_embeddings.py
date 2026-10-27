
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil
import torch
from sentence_transformers import SentenceTransformer

MODELS = {
    "minilm": {
        "id": "sentence-transformers/all-MiniLM-L6-v2",
        "trust_remote_code": False,
    },
    "jina-code": {
        "id": "jinaai/jina-embeddings-v2-base-code",
        "trust_remote_code": True,
    },
}

SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__",
    "node_modules", "vendor", "dist", "build", ".tox",
}


def discover_python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise SystemExit(f"Repository directory not found: {root}")

    files = []

    for path in root.rglob("*.py"):
        parts = {part.lower() for part in path.relative_to(root).parts}

        if parts & SKIP_DIRS:
            continue

        try:
            if not path.is_file() or path.stat().st_size > 256 * 1024:
                continue
        except OSError:
            continue

        files.append(path)

    return sorted(files)


def collect_code_chunks(
    roots: list[Path],
    max_files: int,
    max_chunks: int,
    chunk_chars: int,
) -> list[str]:
    groups = [(root, discover_python_files(root)) for root in roots]
    candidates = []

    # Interleave files so both repositories can contribute samples.
    longest_group = max((len(files) for _, files in groups), default=0)

    for i in range(longest_group):
        for root, files in groups:
            if i < len(files):
                candidates.append((root, files[i]))

    chunks = []

    for root, path in candidates[:max_files]:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if not source.strip():
            continue

        relative_path = path.relative_to(root).as_posix()
        chunks_from_file = 0

        # Fixed-size character slices isolate embedding throughput
        # before the production chunker has been implemented.
        for offset in range(0, len(source), chunk_chars):
            snippet = source[offset:offset + chunk_chars].strip()

            if not snippet:
                continue

            chunks.append(f"File: {relative_path}\n{snippet}")
            chunks_from_file += 1

            # Prevent a large file from dominating the sample.
            if chunks_from_file >= 4 or len(chunks) >= max_chunks:
                break

        if len(chunks) >= max_chunks:
            break

    if not chunks:
        raise SystemExit(
            "No Python source chunks found. Check the --input-dir paths."
        )

    return chunks


def monitor_rss(process, stop_event, peak_holder):
    while not stop_event.wait(0.05):
        try:
            peak_holder[0] = max(
                peak_holder[0],
                process.memory_info().rss,
            )
        except psutil.Error:
            return


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark local code embedding throughput and memory."
    )
    parser.add_argument(
        "--input-dir",
        action="append",
        required=True,
        help="Repository root; repeat to include multiple repositories.",
    )
    parser.add_argument("--model", choices=MODELS, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--max-files", type=int, default=40)
    parser.add_argument("--max-chunks", type=int, default=64)
    parser.add_argument("--chunk-chars", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-seq-length", type=int, default=256)
    parser.add_argument("--cpu-threads", type=int, default=0)
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument(
        "--output",
        default="reports/embedding_benchmark.csv",
        help="CSV output path; results are appended.",
    )
    args = parser.parse_args()

    if min(args.max_files, args.max_chunks, args.chunk_chars) < 1:
        raise SystemExit("File, chunk, and character limits must be positive.")

    if min(args.batch_size, args.max_seq_length) < 1:
        raise SystemExit("Batch size and sequence length must be positive.")

    if args.cpu_threads < 0:
        raise SystemExit("--cpu-threads cannot be negative.")

    if args.device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device = args.device

    if device == "cuda" and not torch.cuda.is_available():
        raise SystemExit(
            "CUDA was requested but torch.cuda.is_available() is False. "
            "Check the GPU, driver, and PyTorch installation."
        )

    if args.fp16 and device != "cuda":
        raise SystemExit("--fp16 can only be used with --device cuda.")

    if args.cpu_threads > 0:
        torch.set_num_threads(args.cpu_threads)

    roots = [Path(item).expanduser().resolve() for item in args.input_dir]
    chunks = collect_code_chunks(
        roots,
        args.max_files,
        args.max_chunks,
        args.chunk_chars,
    )

    config = MODELS[args.model]
    process = psutil.Process()
    rss_start = process.memory_info().rss
    peak_rss = [rss_start]
    stop_event = threading.Event()

    monitor_thread = threading.Thread(
        target=monitor_rss,
        args=(process, stop_event, peak_rss),
        daemon=True,
    )
    monitor_thread.start()

    try:
        # First run may include downloading model weights.
        load_start = time.perf_counter()
        model = SentenceTransformer(
            config["id"],
            device=device,
            trust_remote_code=config["trust_remote_code"],
        )
        model.max_seq_length = args.max_seq_length

        if args.fp16:
            model.half()

        model_load_seconds = time.perf_counter() - load_start

        # Warm-up is excluded from the measured embedding time.
        model.encode(
            chunks[:min(args.batch_size, len(chunks))],
            batch_size=args.batch_size,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        if device == "cuda":
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()

        start = time.perf_counter()
        model.encode(
            chunks,
            batch_size=args.batch_size,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        if device == "cuda":
            torch.cuda.synchronize()

        embedding_seconds = time.perf_counter() - start

        if device == "cuda":
            vram_allocated = torch.cuda.max_memory_allocated() / (1024 ** 2)
            vram_reserved = torch.cuda.max_memory_reserved() / (1024 ** 2)
            gpu_name = torch.cuda.get_device_name(0)
        else:
            vram_allocated = ""
            vram_reserved = ""
            gpu_name = ""

    finally:
        stop_event.set()
        monitor_thread.join()

        try:
            peak_rss[0] = max(
                peak_rss[0],
                process.memory_info().rss,
            )
        except psutil.Error:
            pass

    row = {
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "model_key": args.model,
        "model_id": config["id"],
        "device": device,
        "dtype": "float16" if args.fp16 else "float32",
        "fp16": args.fp16,
        "dataset_roots": "+".join(root.name for root in roots),
        "chunks": len(chunks),
        "chunk_chars": args.chunk_chars,
        "max_seq_length": args.max_seq_length,
        "batch_size": args.batch_size,
        "model_load_seconds_including_download": round(model_load_seconds, 3),
        "embedding_seconds": round(embedding_seconds, 3),
        "chunks_per_second": round(len(chunks) / embedding_seconds, 3),
        "ram_start_mb": round(rss_start / (1024 ** 2), 2),
        "ram_peak_mb": round(peak_rss[0] / (1024 ** 2), 2),
        "ram_peak_delta_mb": round(
            max(0, peak_rss[0] - rss_start) / (1024 ** 2), 2
        ),
        "vram_peak_allocated_mb": (
            round(vram_allocated, 2) if vram_allocated != "" else ""
        ),
        "vram_peak_reserved_mb": (
            round(vram_reserved, 2) if vram_reserved != "" else ""
        ),
        "gpu_name": gpu_name,
        "cpu_name": platform.processor() or platform.machine(),
        "logical_cpu_cores": os.cpu_count(),
        "torch_cpu_threads": torch.get_num_threads(),
        "torch_version": torch.__version__,
        "python_version": platform.python_version(),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output.exists() or output.stat().st_size == 0

    with output.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))

        if write_header:
            writer.writeheader()

        writer.writerow(row)

    print(json.dumps(row, indent=2, ensure_ascii=False))
    print(f"\nSaved result to: {output.resolve()}")


if __name__ == "__main__":
    main()
