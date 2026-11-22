# Web team local integration handoff

## Current delivery decision

Deployment is intentionally deferred. There is no hosted URL or production environment to integrate with yet.

The backend is available on the open branch `work/wp6-retrieval-reranking` in PR #10. It has not been merged into `main`. Until the project owner approves the merge, clone this branch explicitly. After the PR is merged, the web team can use the repository's default branch instead.

## 1. Clone and install (Windows PowerShell)

```powershell
git clone --branch work/wp6-retrieval-reranking https://github.com/CALLMEKANEKI/codebase-rag-service.git
cd codebase-rag-service

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

# CPU setup; install only one PyTorch runtime (CPU or GPU).
python -m pip install -r requirements-torch-cpu.txt
python -m pip install -r requirements-ml.txt
```

The first embedding/query use downloads the configured SentenceTransformer model if it is not already cached. Allow sufficient disk space and network access. GPU setup is optional; use the repository's GPU requirements instead of the CPU requirements, never both.

## 2. Configure local environment

```powershell
Copy-Item .env.example .env
```

Edit `.env` locally:
- Set a random, private `RAG_API_KEY`.
- Set `OPENROUTER_API_KEY` for the default OpenRouter provider, or set `LLM_PROVIDER=gemini` and `GEMINI_API_KEY` for Gemini.
- Keep `OPENROUTER_MODELS` as a comma-separated fallback list. The default is `openrouter/free`.

Never commit `.env`, put provider keys in frontend code, or expose `RAG_API_KEY` in a browser bundle. For browser-based integration, have the web application's server-side route/proxy call this backend and attach `X-API-Key` server-side.

## 3. Start the backend

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

Health check (does not require an API key):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/health
```

Use `http://127.0.0.1:8000` as the local backend base URL. Keep the backend running while the web application is in use.

## 4. Create an index and query it

Open another PowerShell terminal in the repository and activate the same virtual environment.

```powershell
$base = 'http://127.0.0.1:8000'
$headers = @{ 'X-API-Key' = $env:RAG_API_KEY }
```

Create an index for a public GitHub repository:

```powershell
$create = @{
  repo_url = 'https://github.com/pallets/flask'
  ref = 'd086db856be187255b8ec61ef409357393020f32'
} | ConvertTo-Json

$created = Invoke-RestMethod -Method Post -Uri "$base/v1/indexes" -Headers $headers -ContentType 'application/json' -Body $create
$indexId = $created.index_id
```

Indexing is asynchronous. Poll until `status` is `ready` before querying:

```powershell
Invoke-RestMethod -Method Get -Uri "$base/v1/indexes/$indexId" -Headers $headers
```

Query using Hybrid retrieval (recommended candidate, not a claim that it wins every Dev metric):

```powershell
$query = @{
  index_id = $indexId
  question = 'How does Flask dispatch a request?'
  top_k = 5
  options = @{
    mode = 'hybrid'
    rerank = $false
  }
  debug = $false
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri "$base/v1/query" -Headers $headers -ContentType 'application/json' -Body $query
```

Supported retrieval modes are `dense` and `hybrid`. Reranking is optional, requires `mode=hybrid`, and is off by default. It loads a cross-encoder model and adds substantial latency; do not enable it in the normal web flow without a separate product decision.

## 5. API endpoints available to the web team

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/health` | Health check |
| POST | `/v1/indexes` | Start indexing a public GitHub repository |
| GET | `/v1/indexes/{id}` | Poll index status |
| DELETE | `/v1/indexes/{id}` | Cancel/delete an index |
| GET | `/v1/indexes/{id}/files` | Read a bounded source-file line range |
| POST | `/v1/query` | Ask a question and receive answer/citations |

The API contract source remains `contracts/openapi.yaml`. Do not change it as part of frontend integration without a separate approval.

## 6. Integration constraints to account for

- The backend currently has API-key authentication but no configured CORS middleware. A browser calling port 8000 directly from a different origin may be blocked by the browser. Prefer a same-origin web-server proxy for local integration, or agree on explicit local CORS origins before adding CORS support.
- Do not put `RAG_API_KEY`, OpenRouter keys, or Gemini keys in client-side JavaScript.
- Only public GitHub repositories are accepted for server-side indexing.
- Indexing is a single in-process worker. Keep one backend process for local development; multi-worker coordination and durable external queues are not implemented.
- Index data, vector data, registry, and embedding cache are local files under `storage/`. They are not shared between team members.
- The backend is a local development integration target, not a public deployment. No uptime, public URL, or production security guarantee is implied.
- The current Dev benchmark used 24 Vietnamese questions. The held-out Test split has not been evaluated.

## 7. Request timeout budget

Use a nested timeout budget so the caller does not cancel before the downstream component finishes:

| Hop | Timeout |
|---|---:|
| OpenRouter generation in RAG | 60 seconds total across all configured model fallbacks |
| Web Backend → RAG `POST /v1/query` | 65 seconds |
| Frontend → Web Backend message request | 75 seconds |

The 60-second RAG value is a total deadline for the whole OpenRouter fallback sequence, not 60 seconds per model. Each fallback attempt must use only the remaining time. Do not multiply 60 seconds by the number of configured models.

Verify actual timeout settings in the Web HTTP client, reverse proxy/nginx, and frontend. Environment defaults alone are not proof that the full chain honors the budget. Do not automatically retry a query POST after it may have reached RAG.

## 8. Local handoff checklist

- [ ] Web developer can clone the documented branch and install dependencies.
- [ ] Backend starts and `GET /v1/health` returns `{"status":"ok"}`.
- [ ] A public repository can be indexed and reaches `ready`.
- [ ] Hybrid query returns an answer and citations.
- [ ] Web integration uses a server-side proxy for API-key handling, or an explicitly approved CORS design.
- [ ] Frontend and backend agree on request/response fields from `contracts/openapi.yaml`.

Deployment remains deferred until the project owner chooses a hosting target, budget, public-access policy, and secret-management approach.
