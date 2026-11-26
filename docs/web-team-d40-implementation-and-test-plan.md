# D40 — Web implementation handoff and acceptance test plan

Date: 2026-11-23  
Status: **Product decisions confirmed; implementation and E2E verification pending.**  
Related historical baseline: [Web ↔ real RAG local E2E report (2026-11-21)](https://github.com/MinhVu309/codebase-rag-web/blob/main/docs/reports/e2e-local-2026-11-21.md)  
Architecture decision: D40 in Notion page **03 — Quy ước & kiến trúc chung**.

## 1. Purpose and baseline

The 2026-11-21 E2E report tested the previous active-repository behavior. Keep it unchanged as historical evidence; it is not an acceptance report for D40.

This document is the authoritative Web-team implementation request and acceptance-test plan for D40. It records the owner's final choices and expected behavior. It does not claim that implementation or tests have been completed.

## 2. Final product decisions

### 2.1 Conversation Scope — S1: user-managed, persistent, hard boundary

- A new conversation starts with exactly one READY repository selected.
- The user can explicitly add or remove READY repositories from that conversation.
- This set is the persistent **Conversation Scope**. It is the maximum repository universe that the conversation is allowed to search.
- A newly imported repository is not automatically added to existing conversations. When it becomes READY, show a non-blocking notification/suggestion; add it only after the user confirms.
- Dismissing the suggestion leaves all existing scopes unchanged.
- Never infer scope from the latest imported repository or a project-level active repository.
- A repository outside Conversation Scope must never be searched for that conversation, even if a client submits its ID.

### 2.2 Per-question Repository Focus — F2: arbitrary multi-select, soft preference

The composer provides a multi-select control listing repositories already in the current Conversation Scope.

**Focus is a preference/hint, not a hard retrieval filter.** It tells the system and LLM which repositories the user expects to be most relevant. It must not prevent the system from searching other repositories that are already inside Conversation Scope when the focused repositories do not provide enough evidence.

Expected semantics:

1. No repositories selected: search the full Conversation Scope without a preferred subset.
2. One or more repositories selected: prioritize retrieval from those repositories and clearly tell the answer-generation step that they are the user's intended focus.
3. If focused repositories provide sufficient evidence, answer primarily from that evidence.
4. If evidence is missing, weak, or only partially answers the question, RAG may expand retrieval to the remaining repositories in Conversation Scope.
5. If useful evidence is found outside the selected focus, the answer must identify the repository where it was found and state that the focused repositories did not provide sufficient evidence. Where appropriate, ask whether the user intended to select a different focus.
6. If no repository in the entire Conversation Scope provides sufficient evidence, do not fabricate an answer. State that the available repositories do not contain enough evidence and ask the user to check the repository selection or clarify whether the question is outside the indexed codebase.
7. Focus applies only to the current message. It must not add/remove repositories from Conversation Scope. The next message may use a different focus.

**Important distinction:** Conversation Scope is the hard authorization/search boundary; Repository Focus is a soft ranking/priority signal within that boundary. Prompt wording alone is insufficient: focus must influence retrieval prioritization, evidence allocation, or staged expansion in RAG.

### 2.3 Multi-index architecture — B: RAG owns orchestration

The owner selected **RAG Multi-Index Orchestrator**. RAG is responsible for retrieving from multiple authorized indexes, merging evidence, preserving provenance, and performing one final generation.

- Web Backend remains the source of truth for user authorization, Project/Repository records, Conversation Scope, and the per-message focus selection.
- Web Backend resolves repository records to READY `index_id` values server-side and passes the authorized index set plus focus metadata to the RAG multi-index capability.
- RAG retrieves from focused indexes with priority, then expands to other supplied indexes in the same scope when evidence is insufficient.
- RAG performs evidence-level fusion/deduplication and one coherent final generation. Do not fan out to the existing single-index endpoint and concatenate independently generated answers.
- Every evidence item and citation must retain repository/index provenance.
- Legacy `POST /v1/query` remains single-index and backward-compatible. The owner-approved additive multi-index contract is `POST /v1/query/multi`; `contracts/openapi.yaml` was updated to v0.3.0 on 2026-11-25.

### 2.4 History and citations — H2: immutable per-message snapshot

Persist enough information to explain each historical answer independently of the current scope:

- Conversation Scope at send time.
- User-selected focus at send time (including an empty selection).
- Actual indexes/repositories searched, including any fallback expansion.
- Repository/index identity and indexed commit/ref.
- Citation provenance: repository ID, index ID, path, line range, snippet, and other stable source metadata required by the viewer.
- Per-repository query outcome: searched with evidence, searched with no evidence, unavailable/failed, or not searched.

Removing a repository from scope or deleting it later must not rewrite old messages or erase their citation snapshots. A historical citation should retain its snapshot/snippet; live file navigation may show that the source is no longer available.

### 2.5 Error and no-evidence behavior — E2: partial result with explicit status

- If one repository is unavailable but other in-scope repositories can be searched, return a partial result when useful evidence exists and clearly disclose which source was unavailable.
- A successfully searched repository with no relevant evidence is **not** an error. Distinguish “searched, no evidence” from “not searched / unavailable / failed”.
- Never silently omit an index that the system was expected to query.
- If all eligible repositories fail/unavailable, return a clear error state rather than a fabricated answer.
- If all repositories are searched successfully but no sufficient evidence is found, return a no-evidence response and invite the user to verify the selected focus/scope or clarify whether the question is outside the indexed codebase.



### RAG audit update (2026-11-26)

The RAG-only audit fixes are now present on `work/wp6-retrieval-reranking` at commit `69e6fb358876a100eda5712347ead79e5b1e897a`:

- Repaired the malformed `MultiQueryScopeItem` and `MultiCitation` OpenAPI schema fragments and added an OpenAPI validator to CI.
- Added bounded admission for orchestration and retrieval workers. A timed-out running worker retains its capacity slot until it actually exits, preventing unbounded queued work and silent worker-pool exhaustion.
- Applied the per-index deadline across registry/vector-store preflight and retrieval; source timeouts are reported independently and can still permit fallback within Scope.
- Kept the deterministic two-non-empty-chunk Focus sufficiency heuristic and documented it as a measurable proxy, not semantic proof.
- Added opt-in `debug` diagnostics: candidate count, fused count, final context-source count, and provenance/rank/score for chunks included in the final model context.
- Expanded tests for concurrent retrieval, focused retrieval timeout and fallback, preflight timeout and fallback, empty-evidence semantics, diagnostics, and multi-index OpenAPI provenance.

Validation on commit `69e6fb3`:
- OpenAPI validation: passed.
- Ruff lint and format: passed.
- Pytest: **178 passed**.
- Dataset validation and pinned Flask ingestion smoke test: passed.
- CI run: [#227](https://github.com/CALLMEKANEKI/codebase-rag-service/actions/runs/45747217680).

This is not D40 E2E acceptance, Eval Runner execution, Test-set evaluation, retrieval benchmark, merge, or deployment. Those remain outside this validation run and require their own approval.

## 3. Required implementation changes and ownership

### RAG implementation note (2026-11-25)

The first RAG implementation pass is now present on `work/wp6-retrieval-reranking`:
- Additive `POST /v1/query/multi`; legacy `POST /v1/query` remains in place.
- Scope is bounded to 1–5 repositories; duplicate repository/index IDs and Focus outside Scope are rejected.
- Focused indexes are queried first. The initial deterministic sufficiency heuristic is **at least two non-empty retrieved chunks across Focus**; below that threshold, RAG searches the remaining Scope repositories. This is a retrieval-expansion heuristic, not a claim of semantic certainty.
- Evidence is fused across repositories, context is interleaved to reduce single-repository crowding, and one final generation is performed.
- Citations carry repository ID, RAG index ID and commit SHA alongside existing file/line provenance.
- Per-repository outcomes distinguish evidence, no evidence, unavailable, failed and not searched; all-source unavailability maps to 503.
- The RAG endpoint applies one shared 60-second deadline to bounded retrieval tasks and passes the remaining generation budget to OpenRouter/Gemini. Timed-out retrieval futures are cancelled when possible; a running native/provider call may finish in its worker after the request has returned.

This was an implementation-in-progress note at the time it was written, not a test or acceptance claim. The Web Backend and Frontend implementation and D40 E2E remain separate workstreams.

## 3. Required implementation changes and ownership

### Web Frontend

1. Conversation creation selects one READY repository by default.
2. Add Conversation Scope management (add/remove repositories) outside the composer.
3. Add arbitrary multi-select Repository Focus beside the composer.
4. Make the distinction explicit in labels/help text:
   - “Conversation Scope”: repositories available to this conversation.
   - “Focus for this question”: repositories to prioritize for this message; other repositories in scope may still be searched if needed.
5. Empty focus must be visibly described as “Search all repositories in this conversation”.
6. Keep focus selection per message; do not persist it as a change to Conversation Scope. Show the actual repositories searched on the resulting message, not only the user's initial focus.
7. Show non-blocking “repository ready — add to conversation?” notification. Confirm adds; dismiss leaves scope unchanged.
8. Show partial/unavailable source status and distinguish it from a successful search with no evidence.
9. Display repository identity with each citation so identical paths in different repositories remain distinguishable.

### Web Backend

1. Persist normalized Conversation–Repository N:N membership (or an equivalent model).
2. Accept optional repository IDs for the current message's focus; never accept client-supplied RAG `index_id` as authoritative.
3. Authorize the user and verify all scope/focus repositories belong to the correct Project and user.
4. Validate selected focus as a subset of Conversation Scope. Reject out-of-scope IDs and forged repository/index identifiers before contacting RAG.
5. Resolve repository → current READY index on the server. Pass the full authorized Conversation Scope index set plus the selected focus subset to RAG, so RAG can perform soft-focus fallback without escaping scope.
6. Persist immutable message snapshots described in §2.4, including actual searched indexes and per-repository outcomes returned by RAG.
7. Preserve old message/citation snapshots after repository removal/deletion.
8. Map RAG partial/unavailable/no-evidence states to explicit Web API/UI states. Do not silently drop source failures.
9. Do not automatically retry a query POST after it may have reached RAG.

### RAG Service

1. Design and implement a Multi-Index Orchestrator that accepts a bounded list of authorized index IDs and an optional focus subset supplied by Web Backend.
2. Validate all supplied indexes exist and are READY; report per-index failures/statuses instead of silently ignoring them.
3. Implement soft-focus retrieval:
   - focused indexes receive first priority / preferred evidence budget;
   - evaluate whether retrieved evidence is sufficient for the question;
   - expand to the remaining in-scope indexes when focused evidence is insufficient or incomplete;
   - never search an index not supplied in the authorized Conversation Scope set.
4. Define a measurable, testable evidence-sufficiency policy. Start with deterministic test fixtures and retrieval diagnostics; document thresholds/heuristics and avoid claiming semantic certainty from a score alone.
5. Fuse evidence across indexes, deduplicate safely, balance context so one repository cannot crowd out all others, and retain repository/index/commit/path/line provenance.
6. Run one final generation over the fused evidence. Prompt must explain user focus, which repositories were actually searched, which evidence came from fallback repositories, and which sources failed or had no evidence.
7. Return answer, citations, per-index query outcomes, actual searched indexes, focus metadata, and enough diagnostics for reproducible tests.
8. If evidence is found only outside focus, say so explicitly. If no evidence exists in the entire scope, return a grounded no-evidence response that asks the user to verify selection or clarify out-of-scope intent.

## 4. API and contract gate

The reviews from Web Backend (2026-11-23) and Frontend (2026-11-24) have been consolidated in [D40 Multi-Index API Contract Review v2](d40-multi-index-api-contract-review-v2.md). It supersedes the unresolved open-question list in the original proposal for the purpose of final owner review; the original proposal remains as review history. The consolidated draft adopts `POST /v1/query/multi`, a default Scope cap of 5, browser-safe DTOs with no `index_id`, Python-only paired fixtures, the fixed outcome/reason enums, and the 60/65/75-second timeout chain.

**Contract approval gate closed on 2026-11-25:** the project owner approved the complete Contract v2 and the repository-ready notification dismissal decision. The additive RAG OpenAPI update is complete. RAG implementation has begun on `work/wp6-retrieval-reranking`; Web implementation, tests/E2E, merge and deployment remain separate work/gates.


The current single-index `POST /v1/query` is insufficient for this architecture. Before implementation of the Web↔RAG request shape, Web and RAG must prepare a short contract proposal covering:

- Request fields: question, authorized scope index list, optional focused index list, retrieval options, request/correlation ID.
- Response fields: one answer, citations with repository/index provenance, actual searched indexes, per-index outcomes, focus/fallback disclosure, latency/usage and error/partial status.
- Validation: focus must be a subset of scope; index IDs resolved and authorized by Web Backend; RAG validates READY status.
- Failure semantics: partial success, all-source failure, no evidence, invalid/not-ready index.
- Size limits, ordering/budget policy, timeout and retry behavior.

The project owner approved the consolidated Contract v2 on 2026-11-25. The approved OpenAPI change is recorded on the RAG branch; implementation and validation status are tracked in §3 and §11 below.

## 5. Acceptance test fixture and execution method

### 5.1 Prepare deterministic repositories

Use two small public test repositories or controlled fixtures with known, intentionally distinct evidence:

- **Repo A (Backend):** a uniquely named endpoint such as `/api/orders/{order_id}`, its handler, and a distinctive response field such as `total_after_discount`.
- **Repo B (Frontend):** a uniquely named API client call to that endpoint and the component/function that reads `total_after_discount`.
- Add a control symbol with the same name in both repositories but different behavior, to test provenance and disambiguation.
- Optionally add **Repo C** after the conversation has been created, to test the notification and explicit-add flow.
- Record exact Git commit SHAs and the expected file/line ranges before running tests.

Do not rely on general questions whose answers are guessed from common programming knowledge. Each test question must have a known expected source in the fixture.

### 5.2 Execution layers

- **Unit tests:** scope/focus validation, soft-focus policy, evidence sufficiency/fallback decisions, provenance-preserving deduplication, per-index outcome classification, snapshot serialization.
- **RAG integration tests:** create READY indexes for A/B, send multi-index requests, inspect actual queried indexes, fused evidence, answer and citations.
- **Web API integration tests:** verify authorization, scope membership, server-side index resolution, snapshots and error mapping.
- **Browser-driven E2E:** execute user-visible create/manage conversation, focus selection, send messages, view actual searched repositories/citations, import notification, history and deletion flows.
- Capture request/response traces with secrets redacted, application logs/correlation IDs, screenshots for UI cases, test output and exact commit SHAs.

### 5.3 Test cases — Conversation Scope

| ID | Setup and steps | Expected result / evidence |
|---|---|---|
| S01 | Create conversation while A is READY. | Scope contains A only; persisted Conversation–Repository membership is verified. |
| S02 | Add READY B from the scope manager. | Scope becomes A+B; both are queryable. |
| S03 | Remove B, then send a question whose only evidence is in B. | B is not searched for the new message; prior message snapshots/citations remain unchanged. |
| S04 | Import C after conversation creation and wait until C is READY. | Existing scope remains A+B; notification/suggestion appears. |
| S05 | Dismiss C notification and refresh/reopen conversation. | C remains outside scope; no implicit membership is created. |
| S06 | Confirm “Add C” for this conversation. | C is added only to this conversation; other conversations are unchanged. |
| S07 | Try adding an indexing, failed, deleted or unavailable repository. | UI/backend explains state; it is not treated as READY or silently substituted. |
| S08 | Import a new repository while multiple conversations exist. | Each conversation's scope remains unchanged until the user explicitly adds the repository to that conversation. |

### 5.4 Test cases — Per-question Focus (soft preference)

| ID | Setup and steps | Expected result / evidence |
|---|---|---|
| F01 | Scope A+B; leave focus empty; ask a question whose evidence is split across A and B. | Both indexes are searched; one coherent answer cites both. |
| F02 | Scope A+B; focus A; ask a question with sufficient evidence only in A. | A is prioritized and answer is grounded in A; B need not be searched if sufficiency policy says A is enough. Actual searched-index trace is stored. |
| F03 | Scope A+B; focus B; repeat with evidence only in B. | B is prioritized; answer/citations identify B. |
| F04 | Scope A+B; focus A+B; ask a cross-repository trace question. | Both focused indexes are searched; one final answer synthesizes evidence from both. |
| F05 | Scope A+B; focus A, but put the answer only in B. | A is attempted first; insufficient evidence triggers fallback to B. Answer states that A did not provide sufficient evidence and the relevant evidence was found in B. |
| F06 | Scope A+B; focus A; put related partial evidence in A and decisive evidence in B. | System expands to B when A is insufficient; final answer distinguishes which claim is supported by which repository. |
| F07 | Scope A+B; focus A; put no answer evidence in either repo. | Both are searched according to the fallback policy; no fabricated answer; response asks user to verify selection or clarify out-of-scope intent. |
| F08 | Scope A+B; focus A for message 1, then B for message 2. | Message 1 and 2 retain different focus snapshots; scope remains A+B. |
| F09 | Scope A+B; focus A; put evidence in both A and B. | A receives priority, but B may be used when needed; response and provenance do not misrepresent B as part of the initial focus. |
| F10 | Scope A+B; focus A; ask about a concept found only in out-of-scope Repo C. | C is never searched. Response says no sufficient evidence exists in this conversation's scope and suggests adding the intended repository. |
| F11 | Submit a repository outside scope as focus via a tampered request. | Web Backend rejects the request before RAG is called. |
| F12 | Submit forged `index_id` values or alter repository-to-index mapping client-side. | Web Backend ignores/rejects client-supplied index IDs and resolves current READY indexes from trusted server-side records. |
| F13 | Reopen conversation history after several different focus selections. | Each message shows the user's focus and the repositories actually searched; these are not conflated. |

### 5.5 Test cases — Multi-index retrieval, answer and citations

| ID | Setup and steps | Expected result / evidence |
|---|---|---|
| R01 | Ask which frontend function calls the backend endpoint and how its response is consumed. | One final answer uses evidence from A and B; each claim cites the correct repository. |
| R02 | Put same file path or symbol name in A and B with different content. | Citation identity includes repository and index; opening each citation reaches the correct repository/file/lines. |
| R03 | Evidence exists only in A; B is successfully searched but has no evidence. | Outcome for B is “searched/no evidence”, not “failed”; answer does not imply B confirms the claim. |
| R04 | Make B unavailable while A is searchable and has useful evidence. | Partial answer is returned when useful; B is explicitly marked unavailable; no silent omission. |
| R05 | Make focused A unavailable while non-focused B is READY and has evidence. | A's failure is disclosed; fallback to B is explicit; answer cites B correctly. |
| R06 | Remove B from scope after a prior answer cited B. | Old answer, snapshot and citations remain readable; new queries do not search B. |
| R07 | Delete B after a prior answer cited B. | Historical snippet/provenance remains; live file navigation clearly reports source unavailable. |
| R08 | Return overlapping/duplicate chunks from A and B. | Deduplication is safe; distinct repository provenance is not lost. |
| R09 | Force all supplied indexes to fail/unavailable. | Explicit all-source failure state; no fabricated answer and no false “no evidence” classification. |
| R10 | All indexes complete successfully but none has relevant evidence. | Grounded no-evidence response; distinguish this from infrastructure failure. |
| R11 | Ask a question whose answer requires combining two facts from different repos. | Final generation combines both facts and cites each; no independent per-repo answer concatenation. |

### 5.6 Test cases — Security, concurrency, timeout and recovery

| ID | Setup and steps | Expected result / evidence |
|---|---|---|
| Q01 | User 1 tries to add/focus a repository owned by User 2. | Authorization failure; no metadata leak and no RAG call. |
| Q02 | Two users concurrently query different conversation scopes. | No cross-user evidence, citations, focus or snapshot leakage. |
| Q03 | Run two repository indexing jobs while a conversation is active. | Scope does not change unless user confirms adding the new READY repository. |
| Q04 | Trigger RAG LLM timeout/error after query POST has been sent. | Web maps the error clearly; no automatic duplicate query POST. |
| Q05 | Inspect RAG, Web HTTP client, reverse proxy and frontend timeout values. | Actual chain respects D39: RAG OpenRouter 60s total fallback deadline; Web→RAG 65s; Frontend→Web 75s. |
| Q06 | Restart RAG while indexes are READY and while an index is being built. | READY index recovery and in-progress job failure/recovery behavior are explicit; conversation scope is not silently rewritten. |
| Q07 | Test maximum supported number of indexes and a repository near/over configured size limits. | Limits, latency, resource use and error mapping are recorded; no unbounded fan-out. |

### 5.7 Evidence required for every test run

For each test case, record:

- Test ID, date/time, environment and pass/fail/blocked status.
- Exact Web commit SHA, RAG commit SHA, fixture repository URLs and commit SHAs.
- Conversation ID, scope membership at send time, user focus selection.
- Index IDs actually queried and per-index outcome (evidence / no evidence / unavailable / failed / not searched).
- Retrieval trace sufficient to prove focused-first and fallback behavior.
- Final answer, citation repository/index/path/line, and whether each citation supports the claim.
- Latency per stage and total latency; concurrency conditions where relevant.
- Screenshot for browser/UI tests; redacted request/response and logs for API/integration tests.
- Known limitations, skipped cases and reason.

A citation opening at the correct path/line proves navigation integrity only; it does not by itself prove semantic relevance or answer correctness.

## 5.1 Final owner UX decisions (2026-11-25)

- Repository-ready suggestion dismissal is persisted in browser storage keyed by `userId + conversationId + repositoryId`; it survives reloads and revisits.
- Dismissal only hides that suggestion. It does not modify Conversation Scope or Focus.
- A newly imported repository with a new Web Repository ID is a new suggestion.
- The Chat Header / Conversation Scope bar MUST always show a persistent **+ Add repository** control, regardless of whether a suggestion exists or has been dismissed.
- The + control opens the Scope manager. It remains visible and clickable at the Scope cap; the manager explains the maximum (default 5) and prevents adding another repository.
- Dismissing a suggestion MUST NOT hide, disable or replace the + Add repository control.
- Scope manager should provide a way to view/reopen dismissed suggestions; this does not replace the always-visible + control.

## 6. Definition of done

- [ ] Conversation starts with one READY repository; user can manually add/remove scope members.
- [ ] New repository notification is non-blocking and requires explicit confirmation; dismissal persists per user/conversation/repository across reloads.
- [ ] A persistent + Add repository control is always visible in the Chat Header / Scope bar, even after notification dismissal and at the Scope cap; clicking it opens the Scope manager.
- [ ] Focus supports none / one / arbitrary multiple repositories and is per-message.
- [ ] Scope is the hard boundary; focus is a soft retrieval priority within that boundary.
- [ ] Focused-first retrieval and fallback to remaining in-scope repositories are demonstrated by traces/tests.
- [ ] No evidence in the whole scope produces a grounded clarification/no-evidence response.
- [ ] RAG Multi-Index Orchestrator performs evidence-level fusion and one final generation.
- [ ] Citations preserve repository/index/commit/path/line provenance.
- [ ] H2 message snapshots persist scope, selected focus, actual searched indexes and per-index outcomes.
- [ ] Partial source failures are explicit; no silent omission.
- [ ] Authorization and server-side index resolution are tested.
- [ ] Browser-driven E2E and API/integration tests pass for the required cases.
- [ ] Any OpenAPI proposal is reviewed and separately approved before editing the contract.
- [ ] A dated D40 E2E report is published with exact commits, fixture SHAs, evidence and limitations.

## 7. Relationship to the 2026-11-21 baseline

Keep the 2026-11-21 report unchanged. Its 18-query latency and 82/82 citation-opening results remain historical baseline observations only; they do not validate D40.

**Status:** an initial RAG multi-index implementation and focused unit-test cases are present on the RAG branch. The tests have not been run; Web implementation and D40 E2E acceptance are still pending.
