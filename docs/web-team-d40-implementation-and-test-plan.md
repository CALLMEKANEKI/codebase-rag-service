# D40 — Web implementation handoff and test plan

Date: 2026-11-23  
Status: **Product behavior decided; implementation and E2E verification pending.**  
Related baseline: [Web ↔ real RAG local E2E report (2026-11-21)](https://github.com/MinhVu309/codebase-rag-web/blob/main/docs/reports/e2e-local-2026-11-21.md)  
Architecture decision: D40 in Notion, page **03 — Quy ước & kiến trúc chung**.

## 1. Why this follow-up exists

The 2026-11-21 E2E report tested the previous behavior in which a Project effectively had one active repository and chat followed the latest imported/active repository. That report remains a valid historical baseline. It is **not** an acceptance report for the new multi-repository conversation behavior.

This document defines the implementation responsibilities and acceptance tests for D40. It does not claim that the new behavior has been implemented or tested.

## 2. Product behavior — authoritative decisions

### 2.1 Conversation repository scope

- Creating a conversation starts with exactly one selected repository whose index is READY.
- A user may later add or remove repositories from that conversation's scope.
- The scope is persistent across messages until the user changes it.
- A repository newly imported into a Project is **not** silently added to existing conversations.
- When a new repository becomes READY, the Web app shows a notification/suggestion to add it to a relevant conversation. The user must explicitly confirm.
- Do not infer conversation scope from the most recently imported repository or a mutable project-level active repository.

### 2.2 Per-question repository focus

Add a multi-select repository control next to the question composer.

- Available choices are only repositories already included in the current conversation scope and READY for querying.
- No explicit selection means query the entire conversation scope.
- Selecting one repository focuses the current question on that repository.
- Selecting multiple repositories focuses the current question on the selected set, including questions that compare or trace behavior across repositories.
- Focus applies only to the current message. It must not mutate the persistent conversation scope.
- After sending, the UI should retain enough state to show which repositories were used for that message; the next question may choose a different focus.

The selected repository names/identities are semantic metadata for the current question. They must guide both retrieval scope and the LLM prompt. **Prompt wording alone is not multi-repository retrieval.**

## 3. Ownership and implementation requirements

### Web Frontend

1. On conversation creation, choose one READY repository by default.
2. Provide conversation-scope management to add/remove READY repositories.
3. Add a multi-select repository-focus control beside the question input.
4. Make the distinction visible in the UI:
   - conversation scope = repositories available to this conversation;
   - question focus = repositories selected for this message only.
5. If no focus is selected, communicate that all repositories in the conversation scope will be searched.
6. When a new repository becomes READY, show a non-blocking notification/suggestion. Add it only after explicit user confirmation.
7. Display repository/index provenance for each assistant message and its citations.

### Web Backend

1. Persist a many-to-many Conversation–Repository relationship (or an equivalent normalized model).
2. On message creation, accept optional repository focus identifiers, not client-supplied RAG `index_id` values.
3. Authorize the user and verify every focused repository belongs to the conversation scope and the user's Project.
4. Resolve repository → current READY index on the server. Reject invalid/out-of-scope selections; never trust a client-provided index identifier.
5. If focus is empty, derive the effective focus from the full conversation scope.
6. Persist immutable per-message snapshots:
   - conversation scope used;
   - effective repository focus used;
   - repository IDs and index IDs actually queried;
   - citation provenance (repository ID, index ID/commit, path, line range, snippet).
7. Do not silently omit an index that is failed, indexing, deleted, or unavailable. Return a clear partial/unavailable-source indication or fail the query according to an explicitly documented policy.
8. Preserve historical messages and citation snapshots if a repository is later removed from the conversation or deleted.

### Multi-index retrieval and answer assembly — architecture decision required

The current RAG `POST /v1/query` contract accepts **one `index_id` per request** and returns a generated answer plus citations. Therefore Web/RAG must agree on a correct orchestration strategy before implementation.

Required behavior:
- Search every repository in the effective focus (not just the most recently imported one).
- Merge/deduplicate candidate evidence across repositories.
- Preserve repository/index provenance on every evidence item and citation.
- Assemble a balanced context so one large/noisy repository cannot silently crowd out all other selected repositories.
- For cross-repository questions, generate one coherent answer from evidence across the selected repositories.
- If evidence exists in only one of several selected repositories, state that limitation instead of implying all repositories were checked successfully.

Possible approaches to review (not yet selected):
1. Web fan-out to the existing single-index endpoint followed by a new evidence/context assembly stage. The current endpoint returns generated answers, so simply concatenating per-index answers is not an adequate substitute for evidence-level fusion.
2. A dedicated multi-index orchestration capability in RAG that owns retrieval fusion and one final generation step.

The teams must compare complexity, latency, citation correctness, error handling, and ownership, then document the selected approach. Do not change `contracts/openapi.yaml` until the contract proposal has been reviewed and explicitly approved by the project owner.

## 4. Required acceptance tests

### A. Conversation scope

| ID | Scenario | Expected result |
|---|---|---|
| S01 | Create a conversation with Repository A READY | Scope contains A only |
| S02 | Add Repository B to the conversation | Scope contains A and B |
| S03 | Remove B from scope | Future messages no longer search B; prior message snapshots remain unchanged |
| S04 | Import Repository C and wait until READY | Existing conversation scope remains A/B; UI offers an explicit add-C action |
| S05 | Dismiss the notification | C is not added |
| S06 | Confirm add-C | C is added to that conversation only |
| S07 | Repository is indexing/failed/deleted | It cannot be queried as READY; UI/backend explain its state |

### B. Per-question focus

| ID | Scenario | Expected result |
|---|---|---|
| F01 | Scope A/B, no focus selected | Query uses A and B |
| F02 | Scope A/B, focus A | Query uses A only |
| F03 | Scope A/B, focus B | Query uses B only |
| F04 | Scope A/B, focus A+B | Query uses both and can synthesize cross-repository evidence |
| F05 | Ask next message with focus A after prior A+B | Only current message uses A; conversation scope remains A/B |
| F06 | Send a repository outside scope as focus | Backend rejects it; no query is sent to that index |
| F07 | Supply a forged index ID from the client | Backend ignores/rejects it and resolves index IDs server-side |
| F08 | Reopen conversation history | Each message shows its own effective focus/scope snapshot |

### C. Retrieval, answer and citations

| ID | Scenario | Expected result |
|---|---|---|
| R01 | Cross-repo question where evidence exists in A and B | Final answer uses evidence from both, with citations attributed to the correct repository |
| R02 | Similar file paths/symbol names exist in A and B | Citations remain unambiguous and open the correct repository's file |
| R03 | Only A has evidence; B has none | Answer states the evidence limitation; it does not imply B confirmed the claim |
| R04 | B fails or becomes unavailable while A is queryable | UI/answer clearly reports partial/unavailable source status; no silent omission |
| R05 | Remove B from scope after an answer cited B | Old answer and citation snapshot remain readable |
| R06 | Delete B after an answer cited B | Historical snippet/provenance remains; live file access reports source unavailable |
| R07 | Duplicate/overlapping chunks appear across indexes | Deduplication does not erase repository provenance or collapse distinct repository evidence incorrectly |

### D. Security, isolation and resilience

- A user cannot add a repository from another user's Project.
- A focus set must be a subset of the conversation scope.
- Two concurrent users querying different scopes must not receive mixed evidence/citations.
- Two concurrent index jobs must not change an existing conversation's scope.
- Query POST is not automatically retried after it may have reached RAG.
- Verify the nested timeout budget from D39: RAG OpenRouter 60 seconds total across fallback attempts; Web → RAG 65 seconds; Frontend → Web 75 seconds. Verify actual HTTP client and proxy settings, not only environment defaults.

## 5. Test dataset recommendation

Use at least two repositories with intentionally related code, for example:
- Backend repository: defines API endpoints/contracts.
- Frontend repository: calls those endpoints and maps response fields.

Prepare questions in three groups:
1. Single-repository controls: questions whose answer exists only in A or only in B.
2. Cross-repository trace: “Which frontend function calls this backend endpoint, and how are the response fields consumed?”
3. Conflict/absence controls: same symbol name in both repos, or a claim present in only one repo.

For each question, record expected repository evidence, returned repository/index IDs, citations, answer correctness, latency, and any unavailable-source state. Do not treat a citation opening successfully as proof that the cited source is semantically relevant.

## 6. Definition of done

- [ ] Conversation starts with one READY repository.
- [ ] User can expand and shrink scope.
- [ ] New repository notification requires explicit user action.
- [ ] Composer focus supports none / one / multiple repositories.
- [ ] Focus is per-message and does not mutate persistent scope.
- [ ] Server-side authorization and index resolution are enforced.
- [ ] Cross-repository retrieval produces one evidence-grounded answer with correct citations.
- [ ] Per-message scope/focus snapshots are persisted and shown in history.
- [ ] Unavailable repositories are not silently skipped.
- [ ] Browser-driven E2E passes for the scenarios above.
- [ ] Contract changes, if any, are separately reviewed and approved.

## 7. Relationship to the 2026-11-21 baseline report

Keep the existing report unchanged as historical evidence for the old implementation. Its 18-query latency and 82/82 citation-opening results remain baseline observations only. They do not validate D40.

After implementation, publish a separate dated E2E report with:
- exact Web and RAG commit SHAs;
- exact repository refs/index IDs used;
- actual conversation scope and per-question focus;
- test cases and pass/fail evidence;
- citations and source provenance;
- latency per query and concurrency conditions;
- all skipped tests and known limitations.

**No D40 implementation or E2E pass is claimed by this document.**
