# Codebase RAG Service

Codebase RAG Service is intended to index source repositories and answer
questions about their code with citations. It provides an API contract and
development tooling for the planned service.

## Status

- OpenAPI contract v0.2 is available in `contracts/openapi.yaml`.
- A Prism mock server is available for local API integration.
- An embedding benchmark script is available in `scripts/benchmark_embeddings.py`.
- WP2 baseline is implemented: public GitHub clone/snapshot, Python source filtering,
  shared AST parsing, code-aware chunking, and a local ingestion CLI.
- WP3 baseline is implemented: SentenceTransformer / Fake embedders, SQLite
  embedding cache, local Qdrant vector store, and dense retrieval CLI.
- WP4 baseline is implemented: Gemini generation, citation validation, query API,
  SQLite index registry, serialized background indexing jobs, index status/deletion,
  and snapshot-backed file viewing.

The source of truth for the API contract is `contracts/openapi.yaml`.

## Requirements

- Python 3.11 (standard project Python version; CI uses Python 3.11).
- Node.js is required only to run the Prism mock server.

## Setup

Create and activate a virtual environment, then install development
dependencies:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` includes the core packages from `requirements.txt`.
For embedding/model work, choose exactly one PyTorch runtime:

```powershell
# CPU
python -m pip install -r requirements-torch-cpu.txt

# Or GPU; do not install both CPU and GPU requirements.
python -m pip install -r requirements-torch-gpu.txt
```

Then install the ML packages when needed:

```powershell
python -m pip install -r requirements-ml.txt
```

The GPU requirements use the CUDA 13.2 PyTorch wheel index. This configuration
matches the command previously used on a laptop, but has not been verified on
the current machine because CUDA is unavailable.


## Local RAG CLI (development baseline)

The CLI runs ingestion, embedding/indexing, and dense retrieval as separate
steps. WP4 adds the first live FastAPI query path (`/v1/health` and `/v1/query`);
index lifecycle and file-viewer endpoints remain future work.

### 1. Ingest a repository

Use a local Git checkout:

\`\`\`powershell
python -m rag.cli ingest --local-path "C:\GitHub\flask" --index-id idx_flask
\`\`\`

Or clone a public GitHub repository (optionally at a branch, tag, or commit):

\`\`\`powershell
python -m rag.cli ingest --repo-url https://github.com/pallets/flask --ref d086db856be187255b8ec61ef409357393020f32 --index-id idx_flask
\`\`\`

The command writes a source snapshot and \`manifest.json\` under
\`storage/indexes/<index_id>/\`, plus \`chunks.jsonl\` containing code-aware chunks.
The current baseline indexes Python files only and applies the configured file,
size, sensitive-name, and directory exclusions.

### 2. Embed chunks and write to local Qdrant

Install the selected CPU or GPU PyTorch runtime and \`requirements-ml.txt\` first.
The first run downloads the configured model if it is not already cached.

\`\`\`powershell
python -m rag.cli index --index-id idx_flask --chunks-path storage/indexes/idx_flask/chunks.jsonl
\`\`\`

The default model is \`sentence-transformers/all-MiniLM-L6-v2\`. Override it with
\`--model\` or \`EMBEDDING_MODEL\`; choose \`auto\`, \`cpu\`, or \`cuda\` with \`--device\`
or \`EMBEDDING_DEVICE\`. Qdrant uses its local persistent mode, so Docker is not
required. Vector data is stored under \`storage/qdrant\`; embedding cache data is
stored in \`storage/embedding_cache.sqlite\`.

### 3. Run dense retrieval

\`\`\`powershell
python -m rag.cli retrieve --index-id idx_flask --query "How does Flask dispatch a request?" --top-k 5
\`\`\`

The command returns ranked chunks with scores, file paths, line ranges, symbols,
and source text. The deterministic \`FakeEmbedder\` is used in tests and CI; it is
not a substitute for semantic embeddings in actual retrieval.

## Checks

Run the project checks from the repository root:

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

## Live query API (WP4 baseline)

Install the core requirements and the selected PyTorch/ML requirements above.
Configure a local `.env` from `.env.example` with `RAG_API_KEY` and
`GEMINI_API_KEY`. The Gemini model defaults to `gemini-2.5-flash` and can be
changed with `GEMINI_MODEL`. The model request timeout defaults to 30 seconds.

First ingest and index a repository using the CLI commands above. Then start
the service:

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Call the implemented query endpoint (the index must already have a snapshot
manifest and a Qdrant collection):

```powershell
$headers = @{ 'X-API-Key' = 'your-local-rag-key' }
$body = @{
  index_id = 'idx_flask'
  question = 'How does Flask dispatch a request?'
  top_k = 5
  options = @{ mode = 'dense'; rerank = $false }
  debug = $true
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/v1/query' -Headers $headers -ContentType 'application/json' -Body $body
```

The query path uses dense retrieval, bounded numbered context, Gemini generation,
and citation validation against the chunks actually supplied to the model.
Unsupported citation markers are discarded; an answer without a valid citation
fails closed to the insufficient-evidence response. Retrieved repository text
is explicitly treated as untrusted data in the generation instructions.

Current live-service scope is intentionally partial: `GET /v1/health` and
`POST /v1/query` are implemented. Index creation/status/deletion and snapshot
file retrieval remain pending; create and index repositories with the CLI.
The OpenAPI contract remains unchanged.

## Mock server (Prism)

### Startup and API key

The mock server requires Node.js. It was tested with Node.js `v24.22.0` and
Prism CLI `5.16.0`. From the repository root, start Prism with the pinned CLI
version:

```powershell
npx @stoplight/prism-cli@5.16.0 mock contracts/openapi.yaml -p 4010
```

The base URL is `http://localhost:4010`.

For local requests, use `X-API-Key: test-key`. In the tested Prism mock, an
empty key was rejected with `401`, but the non-empty value `wrong` was accepted
with `202`. Prism therefore does not verify the key's value; this mock behavior
is for local development only and is not authentication.

### PowerShell examples

Run these examples in another PowerShell terminal while Prism is running:

```powershell
$baseUrl = 'http://localhost:4010'
$headers = @{ 'X-API-Key' = 'test-key' }
$createBody = @{ repo_url = 'https://github.com/pallets/flask' } | ConvertTo-Json -Compress
$queryBody = @{ index_id = 'idx_3f9a1c'; question = 'JWT o dau?' } | ConvertTo-Json -Compress
```

| Operation | PowerShell command |
|---|---|
| Health | `Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/health"` |
| Create index | `Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/indexes" -Headers $headers -ContentType 'application/json' -Body $createBody` |
| Get index | `Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/indexes/idx_3f9a1c" -Headers $headers` |
| Delete index | `Invoke-RestMethod -Method Delete -Uri "$baseUrl/v1/indexes/idx_3f9a1c" -Headers $headers` |
| Read file lines 34–36 | `Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/indexes/idx_3f9a1c/files?path=src%2Fflask%2Fapp.py&start=34&end=36" -Headers $headers` |
| Query | `Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/query" -Headers $headers -ContentType 'application/json' -Body $queryBody` |

In Windows PowerShell 5.1, `Invoke-RestMethod` and `Invoke-WebRequest` throw on
4xx/5xx responses, so the error body is not printed by default. To see an error
body, use `curl.exe`, or `Invoke-WebRequest -SkipHttpErrorCheck` in PowerShell 7.

### Selecting response examples

Use the `Prefer` header to select a documented index state:

```powershell
Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/indexes/idx_3f9a1c" -Headers ($headers + @{ Prefer = 'example=ready' })
Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/indexes/idx_3f9a1c" -Headers ($headers + @{ Prefer = 'example=embedding' })
Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/indexes/idx_3f9a1c" -Headers ($headers + @{ Prefer = 'example=failed' })
```

To select a defined error response, use `Prefer: code=<status>`. For example,
the query operation returned `409` and `504` when requested with
`Prefer: code=409` and `Prefer: code=504`, respectively.

### Observed behavior and known limitations

The following behavior was observed with Prism CLI `5.16.0` against this
contract:

- The examples are fixed: creating an index returned `idx_3f9a1c` with
  `pending`; selecting `example=embedding` or `example=failed` returned those
  fixed examples.
- Requests do not share index lifecycle state. After create returned
  `idx_3f9a1c`, delete returned `204`; a subsequent GET still returned the fixed
  `ready` example.
- The mock does not authenticate API-key values: the empty key returned `401`,
  while `wrong` returned `202`. A request with no X-API-Key header also returned
  401.
- Error responses defined in the contract return a JSON body that follows
  `ErrorResponse` (`error.code`, `error.message`, `error.details`). For example,
  `Prefer: code=409` on `POST /v1/query` returns `INDEX_NOT_READY`, and
  `Prefer: code=504` returns `TIMEOUT`.
- When a request fails validation and the contract defines a `400` or `422`
  response for that operation, Prism returns that response's fixed example
  regardless of the real cause. For example, omitting the required `path` query
  parameter on `GET /v1/indexes/{id}/files` returns the fixed `400`
  `INVALID_RANGE` example, and posting a non-GitHub URL to `POST /v1/indexes`
  returns the fixed `422` `INVALID_REPO` example. The real service may return a
  different code for the same input, so do not rely on the mock to learn
  validation behavior.
- `IndexStatus.error` (returned by `GET /v1/indexes/{id}` when `status` is
  `failed`) has only `code` and `message`. It is a different shape from the HTTP
  `ErrorResponse` envelope, which always includes `details`.
- All sample data (index ID, commit SHA, file content such as the `verify_token`
  snippet shown for `src/flask/app.py`) is fictional and only illustrates the
  response shapes.
- An invalid index ID (`bad-id`) returned Prism's generated `422` response
  with `application/problem+json`, not the contract's `ErrorResponse` shape.
- These mock responses do not exercise the service's actual authentication,
  repository validation, index lifecycle, file lookup, or query logic.

## FastAPI service (WP4)

Configure `.env` from `.env.example`. Set a private `RAG_API_KEY` and a
`GEMINI_API_KEY` before using the query endpoint. The embedding model is loaded
on the first indexing or query request; its first use may download model files.

Start the API:

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

The implemented API endpoints are:

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/health` | Public health check |
| POST | `/v1/indexes` | Queue indexing for an allowlisted public GitHub repository |
| GET | `/v1/indexes/{id}` | Read persisted indexing status and progress |
| DELETE | `/v1/indexes/{id}` | Cancel queued/running work and remove snapshot/vector data |
| GET | `/v1/indexes/{id}/files` | Read a bounded line range from the indexed snapshot |
| POST | `/v1/query` | Ask a question using dense retrieval and Gemini |

Index jobs run in-process with a single worker. The SQLite registry survives
service restarts; unfinished jobs are marked failed on startup rather than being
silently reported as active. A restart does not automatically resume indexing.
File viewing is restricted to manifest-listed snapshot files, rejects traversal
and symlinks, and returns at most 2,000 lines per request.

Example:

```powershell
$baseUrl = 'http://127.0.0.1:8000'
$headers = @{ 'X-API-Key' = $env:RAG_API_KEY }
$body = @{
  repo_url = 'https://github.com/pallets/flask'
  ref = 'd086db856be187255b8ec61ef409357393020f32'
} | ConvertTo-Json -Compress

$created = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/indexes" -Headers $headers -ContentType 'application/json' -Body $body
$indexId = $created.index_id
# Poll GET /v1/indexes/$indexId until status is ready or failed.
Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/indexes/$indexId" -Headers $headers
```

The service requires local Git, the dependencies in `requirements-ml.txt`,
and a working Gemini API key for generation. It is a single-process MVP:
multiple API workers/replicas, durable external job queues, automatic job
resumption, and distributed cancellation are out of scope.
