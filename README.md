# Codebase RAG Service

Codebase RAG Service is intended to index source repositories and answer
questions about their code with citations. It provides an API contract and
development tooling for the planned service.

## Status

- OpenAPI contract v0.2 is available in `contracts/openapi.yaml`.
- A Prism mock server is available for local API integration.
- An embedding benchmark script is available in `scripts/benchmark_embeddings.py`.
- The actual API and RAG pipeline are not implemented yet.

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

## Checks

Run the project checks from the repository root:

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

## Mock server (Prism)

## Requirements and startup

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

## PowerShell examples

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

## Selecting response examples

Use the `Prefer` header to select a documented index state:

```powershell
Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/indexes/idx_3f9a1c" -Headers ($headers + @{ Prefer = 'example=ready' })
Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/indexes/idx_3f9a1c" -Headers ($headers + @{ Prefer = 'example=embedding' })
Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/indexes/idx_3f9a1c" -Headers ($headers + @{ Prefer = 'example=failed' })
```

To select a defined error response, use `Prefer: code=<status>`. For example,
the query operation returned `409` and `504` when requested with
`Prefer: code=409` and `Prefer: code=504`, respectively.

## Observed behavior and known limitations

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