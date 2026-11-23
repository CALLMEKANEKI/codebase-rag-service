# D40 — Multi-Index API Contract Proposal (Review Draft)

Date: 2026-11-23  
Status: **Proposal for Web/RAG review — NOT approved for implementation or OpenAPI update.**  
Owner decisions: Architecture B, Conversation Scope S1, Repository Focus F2 (soft preference), History H2, Error handling E2.

Related:
- [D40 implementation and acceptance test plan](web-team-d40-implementation-and-test-plan.md)
- [Web team local handoff](web-team-local-handoff.md)
- Historical [Web ↔ RAG local E2E baseline (2026-11-21)](https://github.com/MinhVu309/codebase-rag-web/blob/main/docs/reports/e2e-local-2026-11-21.md)

## 1. Purpose and non-goals

This document proposes the internal Web Backend ↔ RAG contract needed for RAG-owned multi-repository orchestration.

It is a design artifact for review. It does **not** authorize:
- Editing `contracts/openapi.yaml`.
- Changing the existing `POST /v1/query` request/response.
- Implementing either side before the request/response contract and limits are approved.
- Exposing RAG credentials or RAG `index_id` values to the browser.

The existing single-index endpoint should remain backward-compatible for existing callers.

## 2. Verified current-state constraints

### RAG service
- `POST /v1/query` accepts one `index_id`, one question, `top_k`, retrieval options and debug flag.
- The endpoint validates the index state, builds a single-index retrieval service, and performs one generation for that index.
- Existing response contains answer, citations, usage, latency and optional debug information.
- Citation objects currently identify file path, symbol, line range, snippet, score and a per-answer `ref_id`; they do not carry Web repository identity or index/commit provenance.

### Web service
- `IRagClient.QueryAsync(indexId, question, topK, options)` is single-index.
- `ChatService.AskAsync` currently resolves the Project's active repository through `Project.ActiveIndexId`; Conversation has no repository-scope membership.
- The Web message stores a citation snapshot with `RepositoryId` and `CommitSha`, but does not yet persist scope/focus/actual searched repositories/per-repository outcomes.
- The Web API currently accepts `AskRequest(Question, TopK)`.

These are the main changes the proposal must address.

## 3. Responsibilities and trust boundary

### Web Backend is authoritative for
- Authenticated user and Project ownership.
- Conversation Scope membership (the user-managed set of repositories).
- Resolving each in-scope Repository record to its current READY RAG index.
- Validating the user's per-message focus is a subset of Conversation Scope.
- Building the trusted RAG request. The browser must never supply authoritative `index_id` values.
- Persisting H2 message snapshots and translating RAG outcomes to the Web API/UI.

### RAG is authoritative for
- Validating each supplied index exists and is READY at query time.
- Focus-first retrieval and soft-focus fallback within the supplied scope only.
- Evidence sufficiency decision and retrieval diagnostics.
- Evidence-level fusion, safe deduplication, context budgeting and one final generation.
- Citation provenance and per-index execution outcomes.

RAG's service API key authenticates the Web service, not the end user. RAG must treat the supplied repository/index set as the Web Backend's authorized scope; it must never discover or search additional indexes on its own.

## 4. Proposed endpoint

Proposed new endpoint:

`POST /v1/query/multi`

Rationale:
- Keeps existing `POST /v1/query` unchanged.
- Makes the multi-index capability explicit.
- Allows a staged rollout and direct comparison with the single-index path.
- Web can migrate only after contract and compatibility tests pass.

Endpoint name is a proposal, not a final decision. Alternative: versioned `POST /v2/query`. Web/RAG should choose one before OpenAPI approval.

Authentication and common headers:
- `X-API-Key`: required, same service-to-service authentication as existing endpoints.
- `X-Request-ID`: optional; echo it in the response.
- The frontend never calls this endpoint directly.

## 5. Proposed request

Example (illustrative only):

```json
{
  "question": "Trace how the order endpoint response is consumed by the frontend.",
  "scope": [
    {
      "repository_id": "8e9a91c2-3b0e-4e5a-9d8f-2f34e9f75b10",
      "index_id": "idx_backend_a",
      "display_name": "orders-api"
    },
    {
      "repository_id": "4b77e9b4-ae7b-4d9f-a8a5-93b2b36d1210",
      "index_id": "idx_frontend_b",
      "display_name": "orders-web"
    }
  ],
  "focus_repository_ids": [
    "8e9a91c2-3b0e-4e5a-9d8f-2f34e9f75b10"
  ],
  "top_k": 8,
  "options": {
    "mode": "hybrid",
    "rerank": false
  },
  "debug": false
}
```

### Proposed field semantics

| Field | Required | Meaning |
|---|---:|---|
| `question` | Yes | User question; retain current 1–2000 character validation unless separately approved. |
| `scope` | Yes | Non-empty array of repositories that Web has authorized for this conversation and resolved to READY indexes. |
| `scope[].repository_id` | Yes | Stable Web Repository ID. Opaque to RAG except for provenance and response correlation. |
| `scope[].index_id` | Yes | RAG index ID resolved by Web Backend from its database; never accepted from browser input. |
| `scope[].display_name` | Recommended | Human-readable repository label for answer/citation provenance; do not use as an authorization key. |
| `focus_repository_ids` | No | Repository IDs selected by the user for this message. Empty or omitted means no preferred subset; search the full scope. |
| `top_k` | No | Maximum number of evidence chunks passed to final generation, globally across the fused result. Preserve the current 1–20 range unless load tests justify a change. |
| `options` | No | Existing retrieval options (`dense` or `hybrid`; reranking only when supported and explicitly enabled). |
| `debug` | No | Optional bounded diagnostics for internal testing; never expose raw chunk contents or sensitive internals in production logs. |

### Validation rules

1. `scope` must contain at least one entry.
2. `repository_id` values and `index_id` values must each be unique in the request.
3. Every `index_id` must match the accepted RAG index format.
4. `focus_repository_ids` must be a subset of `scope[].repository_id`.
5. Unknown focus IDs, duplicate entries, malformed IDs, empty questions and unsupported options are rejected as invalid requests.
6. Web resolves all IDs from trusted database records after checking ownership and Conversation Scope. It does not forward client-provided index IDs.
7. RAG verifies each index exists and is READY. A source that becomes unavailable between Web resolution and RAG execution is reported in the per-index outcome, not silently dropped.
8. RAG must not search any index absent from `scope`, including when all focused indexes fail.

### Scope size and resource limits

Do not silently truncate the scope array. The contract should have a documented configurable maximum (proposed initial MVP cap: 10 repositories per conversation query, subject to Web/RAG review and load testing). If the cap is exceeded, reject with a clear validation error and tell the user to narrow the Conversation Scope.

The final cap, per-index candidate count, concurrency, and context budget must be confirmed using latency and resource tests before OpenAPI approval.

## 6. Required soft-focus behavior

Conversation Scope is the hard boundary. Focus is only a soft preference.

1. If focus is empty, retrieve across the entire supplied scope.
2. If focus contains one or more repositories, retrieve those indexes first.
3. Evaluate whether focused evidence is sufficient to answer the question.
4. If sufficient, generate from the focused evidence. Non-focused repositories may remain `not_searched` with reason `focus_evidence_sufficient`.
5. If evidence is insufficient, incomplete, or focused indexes are unavailable, expand to the remaining indexes in Conversation Scope.
6. If fallback evidence is used, expose that fact in the response so Web can tell the user which repositories outside the selected focus supplied evidence.
7. If all in-scope repositories are searched and evidence is insufficient, return a grounded no-evidence result. The answer should ask the user to verify the focus/scope or clarify whether the question is outside the indexed codebase.
8. Never search outside Conversation Scope.

### Evidence-sufficiency policy is a required design item

Do not implement fallback as an unobservable arbitrary score comparison. RAG must document and test:
- What constitutes sufficient evidence for a direct symbol/API lookup versus a multi-part/cross-repository question.
- How retrieval scores are normalized or calibrated across indexes and retrieval modes.
- How partial coverage triggers fallback.
- How the system prevents one repository from monopolizing the final context.
- Which diagnostics explain why fallback did or did not happen.

The first version should use a deterministic, inspectable policy calibrated on the agreed fixtures and Dev cases. Do not claim that a similarity score alone proves semantic answerability. If an LLM-based sufficiency classifier is proposed, document its extra latency/cost, failure mode, and evaluation separately before adopting it.

## 7. Proposed response

Example (illustrative only):

```json
{
  "execution_status": "partial",
  "answer_status": "answered",
  "answer": "The backend defines the response field [1], and the frontend reads it in the order summary [2]. The selected focus repository did not contain the full flow; the second part was found in orders-web.",
  "citations": [
    {
      "ref_id": 1,
      "repository_id": "8e9a91c2-3b0e-4e5a-9d8f-2f34e9f75b10",
      "index_id": "idx_backend_a",
      "commit_sha": "abc123...",
      "file_path": "app/routes/orders.py",
      "symbol": "get_order",
      "symbol_kind": "function",
      "start_line": 20,
      "end_line": 42,
      "snippet": "..."
    },
    {
      "ref_id": 2,
      "repository_id": "4b77e9b4-ae7b-4d9f-a8a5-93b2b36d1210",
      "index_id": "idx_frontend_b",
      "commit_sha": "def456...",
      "file_path": "src/orders/OrderSummary.tsx",
      "symbol": "OrderSummary",
      "symbol_kind": "function",
      "start_line": 12,
      "end_line": 38,
      "snippet": "..."
    }
  ],
  "focus": {
    "requested_repository_ids": [
      "8e9a91c2-3b0e-4e5a-9d8f-2f34e9f75b10"
    ],
    "searched_focus_repository_ids": [
      "8e9a91c2-3b0e-4e5a-9d8f-2f34e9f75b10"
    ],
    "fallback_repository_ids": [
      "4b77e9b4-ae7b-4d9f-a8a5-93b2b36d1210"
    ],
    "fallback_triggered": true,
    "fallback_reason": "focused_evidence_insufficient"
  },
  "repository_outcomes": [
    {
      "repository_id": "8e9a91c2-3b0e-4e5a-9d8f-2f34e9f75b10",
      "index_id": "idx_backend_a",
      "commit_sha": "abc123...",
      "outcome": "searched_with_evidence",
      "evidence_count": 3,
      "latency_ms": 45
    },
    {
      "repository_id": "4b77e9b4-ae7b-4d9f-a8a5-93b2b36d1210",
      "index_id": "idx_frontend_b",
      "commit_sha": "def456...",
      "outcome": "searched_with_evidence",
      "evidence_count": 2,
      "latency_ms": 52
    }
  ],
  "usage": null,
  "latency_ms": 920,
  "debug": null
}
```

### Proposed response fields

| Field | Meaning |
|---|---|
| `execution_status` | `completed` when all required execution completed without source failures; `partial` when one or more in-scope sources failed/unavailable but the request still completed usefully. |
| `answer_status` | `answered` when sufficient evidence supports an answer; `no_evidence` when the full eligible scope was searched but no sufficient evidence was found. |
| `answer` | One coherent final answer generated once from the fused evidence. For no-evidence, a grounded clarification/refusal message. |
| `citations` | Global citation list. Each citation includes stable repository/index/commit provenance in addition to existing file/symbol/line/snippet fields. |
| `focus` | Requested focus, focused repositories actually searched, fallback repositories used, and whether/why fallback occurred. |
| `repository_outcomes` | Exactly one outcome for every supplied scope repository. |
| `usage` | Final generation usage when provider reports it. |
| `latency_ms` | Total elapsed time for the complete multi-index request. |
| `debug` | Optional bounded diagnostics for test/development only. |

### Per-repository outcome enum

- `searched_with_evidence`: retrieval completed and produced evidence candidates.
- `searched_no_evidence`: retrieval completed successfully but produced no relevant evidence.
- `unavailable`: index cannot be accessed due to transient infrastructure/service availability.
- `failed`: index-level processing failed for another reason.
- `not_searched`: the orchestrator did not need to search this index (for example, focused evidence was sufficient).

Include a machine-readable `reason` when outcome is `unavailable`, `failed`, or `not_searched`. Never conflate `searched_no_evidence` with an infrastructure failure.

## 8. HTTP and error semantics

| Condition | Proposed HTTP | Machine-readable result |
|---|---:|---|
| Valid request; sufficient evidence; all required sources successful | 200 | `execution_status=completed, answer_status=answered` |
| Valid request; some source unavailable/failed; useful answer still possible | 200 | `execution_status=partial, answer_status=answered` |
| All eligible sources searched; no sufficient evidence | 200 | `execution_status=completed, answer_status=no_evidence` |
| Some sources failed and remaining searched sources have no sufficient evidence | 200 | `execution_status=partial, answer_status=no_evidence` |
| Every supplied index is unavailable/not queryable; no retrieval could run | 503 | `ALL_INDEXES_UNAVAILABLE` with per-repository outcomes in details |
| Invalid request, duplicate IDs, focus not subset of scope, or scope exceeds configured cap | 422 | `INVALID_REQUEST` with field/reason |
| Missing/invalid service API key | 401 | Existing `UNAUTHORIZED` envelope |
| Final generation provider error | 502 | Existing `LLM_ERROR` envelope |
| Whole request exceeds RAG deadline | 504 | Existing `TIMEOUT` envelope |
| Unexpected orchestrator failure | 500 | Existing `INTERNAL_ERROR` envelope |

A source-level `INDEX_NOT_READY` or `INDEX_NOT_FOUND` should be represented in that source's outcome when other indexes can still be queried. If no supplied index can be queried, return the top-level unavailable response. Web should retain the detailed per-repository status in H2.

No automatic retry of the multi-index POST after an ambiguous timeout. Query orchestration is a non-idempotent expensive operation from a cost/latency perspective; Web should not duplicate it silently.

## 9. H2 persistence recommendation (Web database)

Keep Conversation Scope as a persistent relational association, and keep message-time execution details as immutable message snapshots.

### Conversation scope membership

Proposed join entity/table: `ConversationRepository`.

| Field | Purpose |
|---|---|
| `ConversationId` | FK to Conversation |
| `RepositoryId` | FK to CodeRepository |
| `AddedAt` | Audit timestamp |
| `AddedByUserId` | User who explicitly added it, if the team wants an audit field |

Use a unique constraint on (`ConversationId`, `RepositoryId`). Deleting a repository from the current scope removes only the membership, not historical message snapshots.

A new conversation should be created with one READY repository explicitly selected by the user (or selected in the creation flow). Do not derive its scope from the project's latest active repository without a user-visible choice.

### Message execution snapshot

Add a versioned JSON snapshot field on the assistant message, e.g. `RetrievalSnapshot` (JSONB). Suggested shape:

```json
{
  "schema_version": 1,
  "scope": [
    {
      "repository_id": "...",
      "index_id": "idx_backend_a",
      "commit_sha": "abc123..."
    }
  ],
  "focus_repository_ids": ["..."],
  "searched_repository_ids": ["..."],
  "fallback_repository_ids": ["..."],
  "repository_outcomes": [
    {
      "repository_id": "...",
      "index_id": "idx_backend_a",
      "commit_sha": "abc123...",
      "outcome": "searched_with_evidence",
      "reason": null
    }
  ],
  "answer_status": "answered",
  "execution_status": "completed"
}
```

Continue storing citation snapshots with repository ID and commit SHA; extend each citation with `IndexId` so identical repository paths/versions are unambiguous. Prefer storing the index and commit values returned/confirmed by RAG, not values guessed by the frontend.

Do not put the entire scope/focus snapshot only on the user message: the assistant answer and citations must be independently interpretable. The user message may also carry the submitted focus if the UI needs it, but the authoritative execution snapshot belongs to the assistant result.

Repository deletion should preserve snapshot JSON and citation snippets. Live file retrieval can fail with a source-unavailable state.

## 10. Timeout, resource budget and observability

Multi-index retrieval adds work before final generation. The current historical timeout chain (RAG generation/fallback budget 60s, Web→RAG 65s, frontend→Web 75s) must be measured again with the multi-index orchestrator; do not assume the 5-second Web margin is sufficient for multi-index retrieval plus generation.

Required:
- One absolute request deadline propagated through per-index retrieval and final generation.
- Bounded parallelism for index retrieval; never spawn unbounded work proportional to scope size.
- A configurable scope cap, candidate budget, context budget and per-index quota.
- Per-index retrieval latency, status and evidence count.
- Total retrieval/fusion/generation latency.
- Correlation via `X-Request-ID`; no question text, snippets, API keys or provider secrets in logs.
- No silent index omission and no hidden automatic retries of the POST.

## 11. Required contract review questions

Web Backend and RAG owners should respond to these before implementation:

1. Endpoint name: `POST /v1/query/multi` or a versioned `POST /v2/query`?
2. Maximum Conversation Scope size for MVP, justified by load tests?
3. Global `top_k) semantics and per-index candidate budget?
4. Exact deterministic evidence-sufficiency/fallback policy and its fixture-based calibration?
5. Parallelism and deadline budget per request?
6. Exact enum/reason codes for per-index outcomes and top-level partial/unavailable errors?
7. Whether `repository_id`, `index_id`, and `commit_sha` are all returned for every citation and every repository outcome?
8. Which Web migrations/API endpoints are required for Conversation Scope CRUD and per-message focus?
9. How to handle a repository reindexed to a new index while old message snapshots still point to the old index/commit?
10. How are partial answers displayed and how does UI distinguish no-evidence from source failure?

## 12. Acceptance criteria for this contract

- [ ] Existing single-index `POST /v1/query` remains unchanged.
- [ ] Request carries full authorized scope and optional focus subset.
- [ ] Focus is validated as a subset of scope.
- [ ] Empty focus means all scope; selected focus is a soft preference.
- [ ] RAG prioritizes focus, expands only within scope when evidence is insufficient, and exposes the fallback trace.
- [ ] RAG fuses evidence and generates one coherent answer exactly once.
- [ ] Citations carry repository/index/commit/path/line provenance.
- [ ] Every scope repository has an explicit execution outcome, including not-searched.
- [ ] Searched/no-evidence is distinct from unavailable/failed.
- [ ] H2 can persist immutable scope/focus/search/outcome/citation snapshots.
- [ ] Resource limits, timeout budget and evidence sufficiency policy are documented and tested.
- [ ] Web/RAG review all open questions and the project owner explicitly approves the contract.
- [ ] Only after approval may the contract be translated into `contracts/openapi.yaml`.

**Approval gate: This proposal is not an implementation authorization. `contracts/openapi.yaml` must remain unchanged until the project owner explicitly approves the reviewed contract.**
