# Retrieval experiments: R23–R28

## Scope and guardrails

This protocol defines the comparison only; it does not authorize running the
Eval Runner or evaluating the held-out test split. Use the development split
for iteration. Keep the test split untouched until the explicitly approved
final evaluation stage.

All retrieval variants must use the same pinned corpus snapshots, chunk IDs,
embedding model/cache, query set, and relevance annotations.

## R23–R25: retrieval baselines

Compare these systems:

1. Dense: current embedding + Qdrant retrieval.
2. BM25: code-aware tokenization + persistent SQLite BM25 index.
3. Hybrid: Dense + BM25 fused with Reciprocal Rank Fusion (RRF).

Report Hit@5, Recall@5, Recall@10, MRR@10, nDCG@10, and retrieval latency
(p50/p95). Report per-repository and per-category results as well as macro
averages. Record index build time, index size, and candidate counts.

Default BM25 parameters: k1=1.5, b=0.75. Default RRF constant: k=60.
Treat these as baseline settings, not tuned values.

## R27: reranking

Compare Hybrid top-k against Hybrid candidate retrieval followed by a
cross-encoder reranker. Use a fixed candidate pool (default 4 × requested
top-k, capped at 50) and the same final top-k for both arms. Report the same
retrieval metrics plus reranker-only latency and total latency.

Record the exact reranker model ID, device, package versions, and whether the
model was already warm in memory. Do not compare a warm arm against a cold arm
without reporting both.

## R28: context-header ablation

Hold retrieval rankings constant. Compare context construction with headers
enabled and disabled. The enabled header contains file path, inclusive line
range, symbol name, and the first docstring line when available.

Record context character/token budget, included source count, truncation count,
and downstream answer/citation quality if an approved answer evaluation is
performed. Header ablation must not silently change candidate retrieval.

## Reproducibility

For each run, record timestamp, branch/commit SHA, dataset file and SHA-256,
repo commit SHAs, model identifiers, parameters, device, cache state, and
latency methodology. Store raw per-question results and aggregate metrics.
Never fill result tables with estimates or fabricated values.
