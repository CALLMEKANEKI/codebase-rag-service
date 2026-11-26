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

The ablation must use one fixed Hybrid top-k ranking per question for both
arms. The disabled arm retains only numbered citation markers (for example
`[1]`) and removes file path, line range, symbol, and docstring metadata; the
enabled arm includes those metadata fields. This isolates metadata headers
without removing the citation mechanism.

Record the character budget, actual context character count, included source
count, and truncation count for each question. Token counts are not currently
measured by this retrieval-only runner. Downstream answer/citation quality
requires a separately approved LLM evaluation and must not be inferred from
context length alone. Header ablation must not silently change candidate
retrieval.

## Reproducibility

For each run, record timestamp, branch/commit SHA, dataset file and SHA-256,
repo commit SHAs, model identifiers, parameters, device, cache state, and
latency methodology. Store raw per-question results and aggregate metrics.
Never fill result tables with estimates or fabricated values.


## Running the development Eval Runner

Install the development and ML dependencies, including the selected CPU or GPU
PyTorch runtime:

```powershell
python -m pip install -r requirements-dev.txt
python -m pip install -r requirements-torch-cpu.txt
python -m pip install -r requirements-ml.txt
```

Check out the two corpus snapshots at the exact commits in
`eval/corpus_profiles.yaml`, then run from the repository root:

```powershell
python -m eval.runner `
  --fastapi-root "C:\GitHub\fastapi" `
  --flask-root "C:\GitHub\flask" `
  --output "eval/results/dev-vi" `
  --device cpu
```

The runner accepts only files resolved under `eval/datasets/dev/` and rejects
records whose `split` is not `dev`. It verifies both repository checkouts
against their pinned commits and corpus snapshots before indexing. It builds
the source-profile chunks and temporary local Qdrant/SQLite indexes, then
compares Dense, BM25, Hybrid, and Hybrid + Reranker. The default dataset is
`eval/datasets/dev/E2_Dev_24.jsonl`; select
`--dataset eval/datasets/dev/E2_Dev_24_en.jsonl` with a distinct output
directory (for example `--output eval/results/dev-en`) to run the English
variant as a separate experiment without overwriting the Vietnamese run.

Outputs are written to `eval/results/dev/` by default:
`retrieval_raw.jsonl` contains per-question rankings, metrics, candidate
counts, and timings; `retrieval_summary.json` contains macro averages,
per-repository/category summaries, index build metadata, model/configuration
details, and dataset SHA-256. Unanswerable questions are counted but excluded
from relevance-ranking metric averages because they have no positive ground
truth. The runner warms the embedding/BM25 paths and loads the cross-encoder
before timed queries; reranker model-load time is recorded separately.

The runner measures retrieval and performs the R28 context-construction
ablation. It does not call the LLM or score generated answers/citations, and it
does not read the held-out Test split. The R28 comparison uses the same Hybrid
top-k ranking for both header settings and writes per-question measurements to
`context_ablation_raw.jsonl` plus aggregate measurements to
`retrieval_summary.json`. The character budget defaults to 48,000 and can be
changed with `--context-max-chars`. Token counts and downstream answer/citation
quality remain unmeasured. Run the two Dev dataset files separately and retain
their outputs as separate experiment runs rather than combining translated
question sets.
