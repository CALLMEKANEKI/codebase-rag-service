# D40 — Consolidated Multi-Index API Contract Review (v2)

Date: 2026-11-24  
Status: **Approved Contract v2 (2026-11-25); RAG OpenAPI v0.3 updated. Implementation remains a separate approval gate.**  
Supersedes the open review questions in [the original proposal](d40-multi-index-api-contract-proposal.md). The original proposal is retained as review history.

## 1. Inputs and authority

This consolidation combines:
- Owner decisions D40 (Architecture B, Scope S1, Focus F2, History H2, Error handling E2).
- Web Backend review dated 2026-11-23.
- Frontend UX/API review dated 2026-11-24.
- Current RAG and Web source constraints recorded in the original proposal and backend review.

Priority rules:
1. Owner decisions are authoritative.
2. Backend/Frontend feedback is accepted where it does not conflict with owner decisions or security constraints.
3. Any unresolved item is explicitly listed in §12; it is not silently treated as approved.
4. Owner approval on 2026-11-25 authorizes aligning the RAG `contracts/openapi.yaml` with this contract. It does not authorize implementation, merge, deployment, or E2E execution.

## 2. Consolidated decisions

| Area | Consolidated decision |
|---|---|
| Architecture | B — RAG owns multi-index orchestration, evidence fusion and one final generation. |
| Conversation Scope | S1 — user-managed persistent set; hard search boundary. |
| Repository Focus | F2 — arbitrary multi-select per message; soft preference, not a hard filter. |
| History | H2 — immutable per-message snapshot of Scope, Focus, actual sources searched, outcomes and citation provenance. |
| Error handling | E2 — useful partial answers allowed; source failures are explicit and distinct from searched/no-evidence. |
| RAG endpoint | `POST /v1/query/multi`. Keep `POST /v1/query` unchanged and backward-compatible. |
| Web JSON naming | camelCase (e.g. `initialRepositoryId`, `focusRepositoryIds`, `repositoryId`, `filePath`). |
| RAG JSON naming | Preserve the RAG service's existing snake_case convention (e.g. `repository_id`, `index_id`, `focus_repository_ids`). |
| Index identity | `index_id` may travel between Web Backend and RAG and is stored internally by Web. It MUST NOT appear in any browser-facing DTO, error, history response, or citation response. |
| Scope cap | Default maximum 5 repositories per conversation. Configurable, but Web enforces at add time and RAG validates the request cap. No silent truncation. |
| Fixture | Two primary in-scope fixtures, both Python repositories. No TS/TSX fixture because current RAG ingestion is Python-only. |
| Deadline | One absolute RAG deadline ≤60 seconds for the entire multi-index request, including retrieval and all generation/model-fallback attempts. Web→RAG 65s; Frontend→Web 75s. |
| Retry | Do not automatically retry a query POST after it may have reached RAG. |
| Rate limit | Preserve current Web limit: 10 questions/minute/user for multi-index queries. |

## 3. Responsibility and trust boundaries

### 3.1 Web Backend owns
- User authentication, authorization and Project ownership.
- Conversation Scope membership and repository READY checks.
- Server-side Repository → RAG `index_id` resolution.
- Validation that per-message Focus is a subset of current Scope.
- Building the trusted RAG request; never accept a browser-supplied `index_id`.
- Persisting H2 snapshots, including failure snapshots.
- Mapping internal RAG provenance/outcomes into safe browser-facing DTOs.

### 3.2 RAG owns
- Validation of each supplied index and index readiness at query time.
- Focus-first retrieval and fallback only within the supplied Scope.
- Evidence sufficiency and fallback decision/diagnostics.
- Bounded parallel retrieval, evidence-level fusion, deduplication and context allocation.
- One final generation over fused evidence.
- Citation provenance and exactly one outcome per index supplied to the RAG request.

### 3.3 Frontend owns
- Scope manager, Focus selector and repository-ready suggestion.
- Per-message display of answer/execution status, source outcomes and fallback disclosure.
- Display of historical snapshots without recomputing them from current Scope.
- No direct calls to RAG and no knowledge of internal RAG `index_id`.

## 4. RAG API contract — approved

### 4.1 Endpoint and compatibility

`POST /v1/query/multi`

- Auth: existing `X-API-Key`; optional `X-Request-ID` is echoed for tracing.
- Frontend never calls this endpoint.
- Existing `POST /v1/query` remains unchanged.
- RAG receives only the repositories Web Backend has authorized for the current conversation.

### 4.2 Request shape

```json
{
  "question": "Trace how an order response is produced and consumed.",
  "scope": [
    {
      "repository_id": "web-repository-guid-a",
      "index_id": "rag-index-a",
      "display_name": "orders-api"
    },
    {
      "repository_id": "web-repository-guid-b",
      "index_id": "rag-index-b",
      "display_name": "orders-client"
    }
  ],
  "focus_repository_ids": ["web-repository-guid-a"],
  "top_k": 8,
  "options": {
    "mode": "hybrid",
    "rerank": false
  },
  "debug": false
}
```

Field rules:
- `question`: required; retain the current question length validation.
- `scope`: required, non-empty, unique repository/index pairs; maximum defaults to 5.
- `scope[].repository_id`: stable Web Repository ID.
- `scope[].index_id`: trusted RAG index ID resolved from Web DB; never browser-provided.
- `scope[].display_name`: normalized `owner/repo` label derived from the GitHub repository URL; display metadata only, not an authorization key.
- `focus_repository_ids`: optional unique list of Web Repository IDs; must be a subset of scope. Omitted/empty means search the entire Scope without a preferred subset.
- `top_k`: global final evidence-chunk limit after fusion, range 1–20. Per-index candidate counts and allocation are RAG internals; do not divide final topK into independent answers.
- `options`: preserve supported RAG retrieval options. MVP default is hybrid with rerank disabled unless separately configured/approved.
- `debug`: false in normal Web traffic.

Validation:
1. Reject empty Scope, duplicate repository IDs, duplicate index IDs, malformed IDs and invalid question/options.
2. Reject Focus IDs outside Scope.
3. Reject requests over the configured Scope cap; do not truncate.
4. RAG validates each supplied index exists and is READY at query time.
5. RAG must never search an index absent from the request Scope, including when all focused indexes fail.
6. Web Backend performs ownership/project checks and resolves every index ID server-side before calling RAG.

### 4.3 Focus and fallback semantics

1. Empty Focus means search all supplied Scope repositories.
2. Non-empty Focus means retrieve from Focus repositories first.
3. RAG evaluates whether focused evidence sufficiently covers the question. Similarity score alone is not proof of answerability.
4. If focused evidence is sufficient, RAG may stop without searching non-focused repositories. Those repositories receive `not_searched` with reason `focus_evidence_sufficient`.
5. If focused evidence is insufficient/incomplete, or focused sources are unavailable, RAG may search the remaining repositories in Scope.
6. If fallback evidence is used, response metadata and answer must disclose which non-focused repositories supplied evidence.
7. If all eligible repositories are searched and evidence is insufficient, return grounded `no_evidence`; do not invent code behavior.
8. No cross-conversation or out-of-Scope search is permitted.

Evidence-sufficiency policy is RAG-owned and must be deterministic/inspectable for the first MVP, documented with diagnostics and calibrated against the agreed fixtures. Any LLM-based sufficiency classifier requires a separate latency/cost evaluation and approval.

### 4.4 Response shape (RAG → Web Backend only)

```json
{
  "execution_status": "partial",
  "answer_status": "answered",
  "answer": "The API defines the field [1]; the client consumes it in the order summary [2].",
  "focus": {
    "requested_repository_ids": ["web-repository-guid-a"],
    "searched_focus_repository_ids": ["web-repository-guid-a"],
    "fallback_repository_ids": ["web-repository-guid-b"],
    "fallback_triggered": true,
    "fallback_reason": "focused_evidence_insufficient"
  },
  "repository_outcomes": [
    {
      "repository_id": "web-repository-guid-a",
      "index_id": "rag-index-a",
      "commit_sha": "abc123",
      "outcome": "searched_with_evidence",
      "reason": null,
      "evidence_count": 2,
      "latency_ms": 38
    },
    {
      "repository_id": "web-repository-guid-b",
      "index_id": "rag-index-b",
      "commit_sha": "def456",
      "outcome": "searched_with_evidence",
      "reason": null,
      "evidence_count": 1,
      "latency_ms": 42
    }
  ],
  "citations": [
    {
      "ref_id": 1,
      "repository_id": "web-repository-guid-a",
      "index_id": "rag-index-a",
      "commit_sha": "abc123",
      "file_path": "app/routes/orders.py",
      "symbol": "get_order",
      "symbol_kind": "function",
      "start_line": 20,
      "end_line": 42,
      "snippet": "...",
      "score": 0.91
    }
  ],
  "usage": null,
  "latency_ms": 920
}
```

Response requirements:
- `execution_status`: `completed | partial`.
- `answer_status`: `answered | no_evidence`.
- Exactly one `repository_outcomes` entry for every repository in the RAG request Scope.
- Every RAG citation and repository outcome includes `repository_id` and `index_id`; `commit_sha` may be null if RAG cannot determine it.
- Citation keeps existing `ref_id`, path, symbol, line range, snippet and score fields.
- `fallback_reason` is null unless fallback is triggered.
- One coherent answer only; no independent per-repository answer concatenation.

### 4.5 Outcome and reason enums

Per-source `outcome`:
- `searched_with_evidence`
- `searched_no_evidence`
- `unavailable`
- `failed`
- `not_searched`

Accepted `reason` values:
| Outcome | Reason |
|---|---|
| `not_searched` | `focus_evidence_sufficient` |
| `unavailable` | `index_not_found`, `index_not_ready`, `storage_error`, `timeout` (RAG) |
| `failed` | `retrieval_error` |

Web may add Web-originated source outcomes to its stored/UI snapshot when a repository in the conversation Scope cannot be submitted to RAG:
- `unavailable / repository_not_ready`
- `unavailable / repository_deleted`

Accepted `fallback_reason` values:
- `focused_evidence_insufficient`
- `focused_sources_unavailable`

Do not use `failed` or `unavailable` to represent a successful search with no evidence.

### 4.6 HTTP semantics

| Condition | RAG HTTP | RAG result |
|---|---:|---|
| Complete, sufficient evidence | 200 | completed + answered |
| Some sources unavailable/failed, useful answer exists | 200 | partial + answered |
| All eligible sources searched; no sufficient evidence | 200 | completed + no_evidence |
| Some sources failed; remaining searched sources have no sufficient evidence | 200 | partial + no_evidence |
| No supplied index can be queried | 503 | `ALL_INDEXES_UNAVAILABLE` + source outcomes |
| Invalid request / focus outside Scope / duplicate IDs / cap exceeded | 422 | `INVALID_REQUEST` |
| Invalid service key | 401 | Existing `UNAUTHORIZED` |
| Final generation provider error | 502 | Existing `LLM_ERROR` |
| Absolute request deadline exceeded | 504 | Existing `TIMEOUT` |
| Unexpected orchestrator failure | 500 | Existing `INTERNAL_ERROR` |

Web maps RAG 503 `ALL_INDEXES_UNAVAILABLE` to browser-facing 503 `ALL_SOURCES_UNAVAILABLE`. The Web response must not leak index IDs.

## 5. Web API contract — approved design contract

Web API JSON uses camelCase. These routes are the consolidated choice based on Web Backend's route review; they supersede the alternative full-scope PUT route in the FE sketch.

| Method and route | Contract |
|---|---|
| `POST /api/projects/{projectId}/conversations` | Body: `{ "title"?: string, "initialRepositoryId": "..." }`. Repository must belong to the user's project and be READY. Create Conversation and first membership in one transaction. |
| `GET /api/conversations/{id}/repositories` | Return current Scope: `repositoryId`, `displayName`, `repoUrl`, `status`, `commitSha`, `addedAt`. Never return `index_id`. |
| `POST /api/conversations/{id}/repositories` | Body: `{ "repositoryId": "..." }`. Add one READY repository. Duplicate add is idempotent (200 with current Scope). |
| `DELETE /api/conversations/{id}/repositories/{repositoryId}` | Remove current membership; 204 on success. Reject removal of last member with 409 `SCOPE_CANNOT_BE_EMPTY`. |
| `POST /api/conversations/{id}/messages` | Body: `{ "question": "...", "topK"?: 1..20, "focusRepositoryIds"?: ["..."] }`. Empty/omitted Focus means all Scope. |
| `GET /api/conversations/{id}/messages` | Return messages with immutable, browser-safe execution snapshot and citations. No raw `RetrievalSnapshot` JSON and no `index_id`. |

Validation and status mapping:
- Unknown repository, another user's repository or repository outside the user's Project: 404 (avoid information disclosure).
- Initial/add repository not READY: 409 `REPOSITORY_NOT_READY`.
- Add when Scope already has 5 repositories: 409 `SCOPE_LIMIT_REACHED`.
- Remove final Scope member: 409 `SCOPE_CANNOT_BE_EMPTY`.
- Focus ID not in Scope (including forged/foreign IDs): 400 `FOCUS_NOT_IN_SCOPE`; do not reveal whether the ID exists elsewhere.
- Duplicate Focus IDs: 400 `INVALID_REQUEST`.
- Conversation with no eligible repository in Scope: 409 `NO_REPOSITORY_IN_SCOPE`.
- RAG reports all sources unavailable: Web 503 `ALL_SOURCES_UNAVAILABLE`.
- Preserve existing auth/error envelope conventions.

The Scope limit is 5 by default and configurable. Web rejects the sixth repository at add time; RAG independently enforces its configured request cap. Never silently truncate Scope.

### 5.1 Browser-safe message DTO

Illustrative Web-facing shape; Web OpenAPI/client updates remain the Web team's implementation work:

```json
{
  "id": "message-id",
  "role": "assistant",
  "content": "Answer ...",
  "executionStatus": "partial",
  "answerStatus": "answered",
  "fallbackTriggered": true,
  "fallbackReason": "focused_evidence_insufficient",
  "scopeSnapshot": [
    { "repositoryId": "repo-a", "displayName": "orders-api", "commitSha": "abc123" },
    { "repositoryId": "repo-b", "displayName": "orders-client", "commitSha": "def456" }
  ],
  "focusRepositoryIds": ["repo-a"],
  "sources": [
    {
      "repositoryId": "repo-a",
      "displayName": "orders-api",
      "commitSha": "abc123",
      "focused": true,
      "usedAsFallback": false,
      "outcome": "searched_with_evidence",
      "reason": null,
      "evidenceCount": 2,
      "latencyMs": 38
    },
    {
      "repositoryId": "repo-b",
      "displayName": "orders-client",
      "commitSha": "def456",
      "focused": false,
      "usedAsFallback": true,
      "outcome": "searched_with_evidence",
      "reason": null,
      "evidenceCount": 1,
      "latencyMs": 42
    }
  ],
  "citations": [
    {
      "refId": 1,
      "repositoryId": "repo-a",
      "displayName": "orders-api",
      "commitSha": "abc123",
      "filePath": "app/routes/orders.py",
      "symbol": "get_order",
      "symbolKind": "function",
      "startLine": 20,
      "endLine": 42,
      "snippet": "...",
      "score": 0.91
    }
  ]
}
```

Browser-safe DTO rules:
- Do not expose `index_id`, `IndexId`, raw RAG snapshot JSON, RAG API key or internal storage details in any browser response.
- Browser receives Web `repositoryId`, `displayName`, `commitSha`, source outcome/reason and citation file metadata.
- `sources` is the UI projection of the stored snapshot, not a raw passthrough of RAG response.
- `scopeSnapshot` records all Scope members at send time. For repositories not sent to RAG because they were no longer READY/deleted, Web records `unavailable` with `repository_not_ready` or `repository_deleted`.
- A repository that was sent to RAG has the outcome returned by RAG.
- Citations returned to browser include repository identity and commit provenance, but not index identity.

### 5.2 UI status mapping (FE)

Keep the five FE display states, but derive them from the two top-level status fields and HTTP outcomes; do not treat per-source outcomes as message-level states.

| FE badge | Mapping / behavior |
|---|---|
| Answered | HTTP 200, `executionStatus=completed`, `answerStatus=answered`. |
| No Evidence | HTTP 200, `executionStatus=completed`, `answerStatus=no_evidence`. |
| Partial | HTTP 200, `executionStatus=partial`, regardless of `answerStatus`. Show answer if answered; otherwise explain that searched sources did not provide enough evidence and list failed/unavailable sources. |
| Failed | Request-level 500/502/504 or persisted turn failure with no usable RAG result. Show retry action; never auto-retry. |
| Unavailable | Request-level 503 when all supplied sources are unavailable. For mixed/partial results, show Unavailable on the affected source row rather than replacing the whole message badge. |

A successfully searched source with no evidence is `searched_no_evidence`, not Failed or Unavailable. A source not searched because focused evidence was sufficient is `not_searched`.

## 6. Scope, persistence and migration

### 6.1 Database model
- Add `ConversationRepository` with `ConversationId`, `RepositoryId`, `AddedAt` and optional `AddedByUserId`; unique constraint on (ConversationId, RepositoryId).
- Add versioned JSONB `RetrievalSnapshot` to assistant message / execution record.
- Keep internal `IndexId` and `DisplayName` on Web-side citation/source persistence as needed for traceability. Index ID is DB-only and must be filtered from API DTOs.
- Keep citation snapshot fields needed for historical display: repository ID, display name, commit SHA, path, line range, snippet, symbol metadata and score.

### 6.2 Snapshot rules (H2)
Persist a versioned snapshot with:
- `schema_version`.
- Full Scope at send time (repository ID, display name, commit SHA if known; internal index ID in DB only).
- User-selected Focus, including explicit empty Focus.
- Actual searched repositories/indexes.
- Per-source outcome, reason, evidence count and latency where available.
- Fallback triggered/reason and fallback repository set.
- Execution/answer status and citations/provenance.
- Failure code/status if the query turn failed.

Additional rules:
- Store the selected Focus on the user message as submitted.
- Store the authoritative execution snapshot on the assistant result/turn record.
- Persist failure snapshots too, so history can explain failed turns.
- Removing a repository from Scope or deleting it from the Project must not mutate historical snapshots/citations/snippets.
- Scope membership may cascade-delete when the underlying Project Repository is deleted; historical JSON snapshots must not cascade-delete.
- No re-index exists in current Web MVP: re-import creates a new Repository record/index. Snapshot retains the exact repository/commit/index used at answer time.

### 6.3 Legacy conversation migration
- Backfill each existing conversation's initial Scope from the Project's active repository/index if it still exists.
- If no active repository exists, leave the legacy Scope empty and return 409 `NO_REPOSITORY_IN_SCOPE` when the user tries to ask; prompt the user to add a READY repository.
- New conversations always require `initialRepositoryId` and create the first Scope membership atomically.
- After migration/rollout, remove `Project.ActiveIndexId` and the active-repository assumption from DTO/UI. Do this only as part of the approved implementation migration.

## 7. Timeout, concurrency, resource limits and retry

- RAG establishes one monotonic absolute deadline at request entry: **≤60 seconds total** for validation, index access, bounded retrieval, evidence sufficiency/fallback, fusion, prompt construction, generation and all model fallback attempts.
- Each retrieval and generation stage receives only the remaining budget. OpenRouter/model fallback must not restart a fresh 60-second timer per model. Retrieval time consumes part of the same 60-second request budget.
- Web Backend → RAG timeout: 65 seconds.
- Frontend → Web Backend message timeout: 75 seconds.
- Retrieval may run concurrently with a bounded fan-out no greater than configured Scope cap (default 5); enforce connection/resource limits and cancel remaining work when the shared deadline expires.
- Global final `top_k` remains 1–20 after fusion; candidate pool, per-index candidate budget, fairness allocation and context budget are RAG internals documented and measured in load tests.
- Preserve 10 questions/minute/user rate limit.
- No automatic retry of query POST after dispatch; GET operations may follow existing retry rules.

The timeout chain is a required measured configuration, not just environment-variable documentation. Verify HTTP clients, reverse proxy and frontend.

## 8. Deterministic fixtures and acceptance tests

### 8.1 Primary fixtures
Use **two primary Python repositories**:
- Repo A (Backend): FastAPI service defining `/api/orders/{order_id}`, its handler and distinctive response field `total_after_discount`.
- Repo B (Client): Python CLI/SDK client calling that endpoint and consuming `total_after_discount`.
- Include one same-named symbol with different behavior in A and B to test repository-aware citation identity.
- Record exact repository commit SHAs and expected file/line ranges before tests.

Do not use a `.tsx` fixture: current RAG ingestion allows `.py` only. If an out-of-Scope negative-control repository is required, use a small separate Python control fixture; it is not part of the two primary in-Scope fixture pair.

### 8.2 Additional test requirements
Retain S01–S08, F01–F13, R01–R11 and Q01–Q07 from the implementation plan. Add explicit coverage:
- Scope maximum 5; adding a sixth returns 409 `SCOPE_LIMIT_REACHED` and does not mutate Scope.
- Removing the last Scope member returns 409 `SCOPE_CANNOT_BE_EMPTY`.
- Web API responses, history, errors and citations never contain `index_id`/internal `IndexId`.
- Web resolves index IDs server-side; forged client index IDs cannot influence RAG request.
- Source outcomes and reason enums are stable and mapped to FE badges as in §5.2.
- Failed query turns persist immutable Scope/Focus/error snapshots and appear correctly in history.
- Scope includes a repository that becomes not READY/deleted before query; Web records an explicit unavailable source outcome instead of silently dropping it.
- New repository suggestion dismissal behavior is tested after the owner decision in §12.
- Both primary fixtures are Python and actually produce indexed chunks.

For every test, retain exact Web/RAG SHAs, fixture SHAs, Scope/Focus, actual searched indexes, per-source outcomes, retrieval trace, citations and semantic relevance, latency, screenshots and redacted logs. The 2026-11-21 E2E report remains historical baseline only.

## 9. Frontend implementation mapping

Accepted FE proposal:
- Reuse: `Header.tsx`, `AiThinkingIndicator.tsx`, `LucianNotificationPopover.tsx`.
- Modify: `ChatPage.tsx`, `MessageItem.tsx`, `CitationDrawer.tsx`.
- Create: `ConversationScopeModal.tsx`, `RepositoryFocusSelector.tsx`, `QueryStatusBadge.tsx`, `ScopeRecommendationBanner.tsx`.

Behavior:
- Scope manager lists project repositories; only READY repos can be newly added.
- Focus selector supports none/one/multiple. Empty Focus label should explain “search all repositories in this conversation”.
- Fallback callout and citations distinguish focused evidence from fallback evidence.
- Partial result displays the answer when available plus affected source warnings.
- No Evidence is distinct from Failed/Unavailable.
- History renders immutable Scope/Focus/source snapshots per message.
- Repository-ready suggestion is non-blocking; never auto-adds a repository.
- Dismissal is persisted browser-side, scoped by (user, conversation, repository); dismissing a suggestion does not remove the repository from Project or Conversation Scope. It remains dismissed across reloads. A newly imported/re-imported repository with a new Repository ID is a new suggestion.
- The Chat Header / Conversation Scope bar MUST always show a persistent **+ Add repository** control, independent of whether any recommendation banner is visible or dismissed. This is the primary, always-available entry point for adding repositories to Scope; it opens the Scope manager and is not itself a notification.
- The + control remains visible even when Scope is at its 5-repository cap. It opens the Scope manager, which explains the limit and prevents adding a sixth repository; do not hide the control or silently drop a selection. Existing Scope repositories can still be reviewed/removed there, subject to the cannot-remove-last-member rule.
- Dismissing a repository-ready recommendation only dismisses that recommendation; it MUST NOT hide or disable the persistent + Add repository control.
- Use Web repository IDs only in browser state and API payloads.

## 10. Rollout and compatibility

- Keep `POST /v1/query` unchanged.
- Web introduces `RAG_QUERY_API=single|multi`, default `single` until the multi-index path passes required E2E; support rollback to single-index path.
- Web migrations add scope membership and snapshots; backfill legacy conversations as in §6.3.
- After approved contract and rollout, remove Project ActiveIndexId/active-repository semantics.
- Update Web generated client, contract tests and Prism mock only after OpenAPI approval.
- No D40 deployment is implied.

## 11. Approval gate and current status

The project owner explicitly approved the complete consolidated Contract v2 on **2026-11-25**. This approval authorizes updating the RAG `contracts/openapi.yaml` to the approved multi-index contract. The OpenAPI update is recorded on branch `work/wp6-retrieval-reranking`.

Separate gates remain in force:
1. RAG/Web implementation and database migration require implementation authorization.
2. Test/E2E/benchmark execution remains separately controlled; no Eval Runner or benchmark is authorized by this approval.
3. Merge and deployment require separate explicit approval.
4. The existing `POST /v1/query` remains backward-compatible; the multi-index endpoint is additive.

## 12. Owner decision — resolved (2026-11-25)

The owner approved persistent browser-side dismissal for repository-ready suggestions.

Implementation contract:
- Store dismissal in browser storage scoped by `(userId, conversationId, repositoryId)`.
- A dismissed suggestion stays dismissed across reloads and revisits to the same conversation.
- Dismissal affects only the suggestion/banner; it does not add/remove repository membership and does not affect Scope or Focus.
- A newly imported/re-imported repository represented by a new Web Repository ID is treated as a new suggestion.
- Provide a way to explicitly reopen/view dismissed suggestions if the user wants to revisit them; this is separate from the always-visible add control.
- No Backend endpoint or DB field is required for this MVP decision.

**Additional owner UX requirement (2026-11-25):**
- The Chat Header / Conversation Scope bar must always display a persistent **+ Add repository** button.
- This control remains visible even when no repository-ready suggestion is present, when the suggestion has been dismissed, and when the Scope is at its maximum.
- Clicking it opens the Scope manager. At the 5-repository limit, the manager explains the limit and disables further additions, but the + control itself remains visible and usable to open the manager.
- The recommendation banner and the + Add repository control are independent UI elements. Dismissing the banner must never hide the + control.

This owner decision closes the product question previously listed in §12. The complete contract was subsequently approved by the owner on 2026-11-25; see §11.

## 13. Related documents
- [Original D40 Multi-Index API Contract Proposal](d40-multi-index-api-contract-proposal.md) — retained review history.
- [D40 implementation and acceptance test plan](web-team-d40-implementation-and-test-plan.md).
- [Web team local handoff](web-team-local-handoff.md).
- [Historical Web ↔ RAG local E2E baseline (2026-11-21)](https://github.com/MinhVu309/codebase-rag-web/blob/main/docs/reports/e2e-local-2026-11-21.md).

**OpenAPI update:** `contracts/openapi.yaml` now contains the approved additive `POST /v1/query/multi` contract and supporting schemas. Legacy `POST /v1/query` is retained. No implementation, CI, E2E or benchmark has been run as part of this approval/update.
