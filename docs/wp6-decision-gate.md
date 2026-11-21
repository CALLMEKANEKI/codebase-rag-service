# WP6 feature-freeze and deployment decision gate

Status: **Not evaluated — pending approved benchmark runs.**

## Required evidence

- [ ] R23 BM25 tokenizer/index unit tests pass.
- [ ] R24 RRF unit tests pass.
- [ ] R25 development-set Dense vs BM25 vs Hybrid results are recorded.
- [ ] R27 Hybrid vs Hybrid + reranker results are recorded, including latency.
- [ ] R28 context-header ablation is recorded.
- [ ] Existing API contract conformance remains unchanged.
- [ ] CI, lint, formatting, and unit tests pass.
- [ ] Local startup, index lifecycle, and query smoke tests pass.
- [ ] Deployment target, public-link requirement, operational budget, and secret
      management are explicitly decided.

## Decision rules

Do not claim that Hybrid or reranking improves retrieval without measured
development-set evidence. Do not deploy the reranker by default if its quality
gain does not justify its latency, memory, and model-download cost.

The held-out test set remains reserved for the final evaluation stage and must
not be used for tuning.

## Decision

**Pending.** Complete the evidence checklist and obtain project-owner approval
before declaring feature freeze or selecting a public deployment target.
