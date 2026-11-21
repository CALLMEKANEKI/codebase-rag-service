# WP6 feature-freeze and deployment decision gate

Status: **Development retrieval benchmark completed; feature-freeze and deployment decision remain pending.**

## Required evidence

- [x] R23 BM25 tokenizer/index unit tests pass.
- [x] R24 RRF unit tests pass.
- [x] R25 development-set Dense vs BM25 vs Hybrid results are recorded.
- [x] R27 Hybrid vs Hybrid + reranker results are recorded, including latency.
- [ ] R28 context-header ablation results are recorded; the fixed-ranking ablation implementation and CI tests are complete, while the Dev experiment is pending.
- [x] Existing API contract conformance remains unchanged.
- [x] CI, lint, formatting, and unit tests pass.
- [ ] Local startup, index lifecycle, and query smoke tests pass.
- [ ] Deployment target, public-link requirement, operational budget, and secret
      management are explicitly decided.

## R25–R27 development retrieval results

Authoritative run: [GitHub Actions run #2](https://github.com/CALLMEKANEKI/codebase-rag-service/actions/runs/44865442996).
Artifact: [retrieval-evaluation-dev-vi](https://github.com/CALLMEKANEKI/codebase-rag-service/actions/runs/44865442996/artifacts/14691214203).

The run evaluated 24 Vietnamese Dev questions: 20 answerable and 4
unanswerable. Relevance-ranking metrics below average only the 20 answerable
questions; unanswerable questions are counted separately and excluded from
Hit/Recall/MRR/nDCG averages.

| Variant | Hit@5 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | Retrieval p50/p95 (ms) |
|---|---:|---:|---:|---:|---:|---:|
| Dense | 0.500 | 0.300 | 0.375 | 0.239 | 0.222 | 18.8 / 20.3 |
| BM25 | 0.600 | 0.388 | 0.557 | 0.384 | 0.383 | 8.2 / 12.5 |
| Hybrid (RRF) | 0.650 | 0.429 | 0.483 | 0.388 | 0.358 | 9.7 / 13.9 |
| Hybrid + Reranker | 0.600 | 0.425 | 0.558 | 0.326 | 0.362 | 9.9 / 14.4 |

For Hybrid + Reranker, reranker-only latency was 2,331.6 ms p50 / 2,484.8 ms
p95, giving total latency of 2,341.1 ms p50 / 2,498.0 ms p95. Cross-encoder
model loading took 4,346.7 ms before timed queries.

### Per-repository results

| Repository | Variant | Hit@5 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---:|---:|---:|---:|---:|
| FastAPI | Dense | 0.400 | 0.200 | 0.300 | 0.183 | 0.164 |
| FastAPI | BM25 | 0.600 | 0.342 | 0.467 | 0.350 | 0.328 |
| FastAPI | Hybrid | 0.700 | 0.392 | 0.467 | 0.410 | 0.341 |
| FastAPI | Hybrid + Reranker | 0.600 | 0.400 | 0.517 | 0.263 | 0.306 |
| Flask | Dense | 0.600 | 0.400 | 0.450 | 0.295 | 0.279 |
| Flask | BM25 | 0.600 | 0.433 | 0.648 | 0.419 | 0.438 |
| Flask | Hybrid | 0.600 | 0.467 | 0.500 | 0.367 | 0.374 |
| Flask | Hybrid + Reranker | 0.600 | 0.450 | 0.600 | 0.389 | 0.418 |

### Per-category results

| Category | Variant | Hit@5 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---:|---:|---:|---:|---:|
| Code flow | Dense | 0.250 | 0.042 | 0.042 | 0.125 | 0.048 |
| Code flow | BM25 | 0.500 | 0.083 | 0.286 | 0.236 | 0.174 |
| Code flow | Hybrid | 0.500 | 0.125 | 0.125 | 0.133 | 0.100 |
| Code flow | Hybrid + Reranker | 0.500 | 0.167 | 0.250 | 0.167 | 0.159 |
| Code understanding | Dense | 0.750 | 0.750 | 0.750 | 0.258 | 0.379 |
| Code understanding | BM25 | 0.750 | 0.750 | 0.750 | 0.750 | 0.750 |
| Code understanding | Hybrid | 0.750 | 0.750 | 0.750 | 0.583 | 0.625 |
| Code understanding | Hybrid + Reranker | 1.000 | 0.875 | 0.875 | 0.750 | 0.754 |
| Configuration | Dense | 0.750 | 0.208 | 0.333 | 0.500 | 0.271 |
| Configuration | BM25 | 0.750 | 0.354 | 0.500 | 0.625 | 0.443 |
| Configuration | Hybrid | 0.750 | 0.271 | 0.417 | 0.750 | 0.429 |
| Configuration | Hybrid + Reranker | 0.500 | 0.208 | 0.542 | 0.275 | 0.292 |
| Cross-file | Dense | 0.500 | 0.250 | 0.375 | 0.146 | 0.197 |
| Cross-file | BM25 | 0.250 | 0.125 | 0.250 | 0.050 | 0.110 |
| Cross-file | Hybrid | 0.500 | 0.375 | 0.375 | 0.175 | 0.233 |
| Cross-file | Hybrid + Reranker | 0.000 | 0.000 | 0.250 | 0.042 | 0.106 |
| Symbol lookup | Dense | 0.250 | 0.250 | 0.375 | 0.167 | 0.212 |
| Symbol lookup | BM25 | 0.750 | 0.625 | 1.000 | 0.260 | 0.439 |
| Symbol lookup | Hybrid | 0.750 | 0.625 | 0.750 | 0.300 | 0.402 |
| Symbol lookup | Hybrid + Reranker | 1.000 | 0.875 | 0.875 | 0.396 | 0.500 |

The complete raw per-question rankings and timings are in the run artifact.
The four unanswerable questions are excluded from the relevance metric tables.

### Reproducibility metadata

- Runner commit: `adfdfd2c367fbe8a8d11ea382acfc6cb4f160049`.
- Dataset: `eval/datasets/dev/E2_Dev_24.jsonl`.
- Dataset SHA-256: `4ffdf9cd5cd9c06d4367023ab618c2632739ac25774b1f237dc58bc6e5a9a272`.
- FastAPI corpus: `79cab5bed28b24d92c185d236feb33f5783dfeb3`.
- Flask corpus: `d086db856be187255b8ec61ef409357393020f32`.
- Embedding: `sentence-transformers/all-MiniLM-L6-v2`, CPU.
- Reranker: `cross-encoder/ms-marco-MiniLM-L-6-v2`, CPU; warm during timed queries.
- Parameters: top-k=10, BM25 k1=1.5/b=0.75, RRF k=60, reranker candidate pool 4×top-k capped at 50.
- Header: disabled. Chunker: `python-ast-v1`.
- Corpus/index build: FastAPI 85 files / 937 chunks / 29.0 s; Flask 24 files / 359 chunks / 11.5 s.
- BM25 SQLite size: FastAPI 3.75 MB; Flask 1.36 MB.

The first benchmark attempt was discarded because its nDCG implementation counted
multiple overlapping chunks against the same ground-truth range and produced
values above 1. The metric was corrected to count each ground-truth range once;
the authoritative figures above come from the subsequent successful run.

## Interpretation and provisional direction

- Dense is the weakest of the four variants on this Dev set.
- BM25 has the best nDCG@10 and Recall@10 while also having the lowest retrieval
  latency among the non-reranked variants.
- Hybrid has the best Hit@5, Recall@5, and MRR@10. It is a reasonable candidate
  for the next stage when early-rank retrieval is prioritized, but it does not
  dominate BM25 on every metric.
- Reranking raises Recall@10 versus Hybrid (0.558 vs 0.483), but lowers Hit@5,
  MRR@10, and nDCG@10 relative to Hybrid, and adds about 2.33 seconds p50
  reranker latency. It should not be enabled by default on this evidence.

These are descriptive results from a small Dev set, not statistical proof or a
production deployment decision. Keep BM25 and Hybrid as candidates for further
validation; do not enable the cross-encoder by default.

## R28 and deployment decision

R28 implementation is present in `rag/generation/context.py` and
`eval/runner.py`. For each Dev question, the runner obtains one Hybrid top-k
ranking and builds both variants from that exact ranking:
- Header disabled: citation markers only (`[1]`, `[2]`, ...).
- Header enabled: citation marker plus file path, inclusive line range, symbol,
  and first docstring line when available.

Per-question measurements are written to `context_ablation_raw.jsonl`;
aggregate character/source/truncation measurements are included in
`retrieval_summary.json`. Default context budget is 48,000 characters.
Token counts are not measured by this runner. The current workflow execution
is pending; do not report R28 measurements until its artifact is available.
No LLM answer or citation-quality evaluation is performed.

Do not claim that Hybrid or reranking improves all retrieval quality measures.
Keep the held-out Test split reserved for the separately approved final
evaluation. Deployment target, public-link requirement, operational budget,
secret management, local startup, and end-to-end smoke tests remain pending.

## Decision

**No feature freeze or deployment approval yet.** Provisional direction:
continue with Hybrid and BM25 as candidates; keep reranking optional/off by
default. Complete R28 and obtain project-owner approval before selecting a
deployment target or declaring feature freeze.
