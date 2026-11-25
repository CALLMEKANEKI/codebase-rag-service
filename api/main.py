from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from rag.config import Settings, get_settings
from rag.embeddings.cache import CachedEmbedder, SQLiteEmbeddingCache
from rag.embeddings.sentence_transformer import SentenceTransformerEmbedder
from rag.generation.base import LLMError
from rag.generation.factory import build_language_model
from rag.generation.service import QueryService
from rag.generation.multi_query import AllIndexesUnavailable, run_multi_query
from rag.index_registry import IndexRegistry
from rag.indexing_pipeline import IndexCancelled, run_indexing
from rag.ingestion.clone import validate_repo_url
from rag.parsing.chunking import CHUNKER_VERSION
from rag.retrieval.bm25 import BM25Retriever
from rag.retrieval.dense import DenseRetriever
from rag.retrieval.hybrid import HybridRetriever
from rag.retrieval.reranker import CrossEncoderReranker, RerankingRetriever
from rag.vectorstore.qdrant_store import QdrantVectorStore


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


class QueryOptions(BaseModel):
    mode: str = "dense"
    rerank: bool = False


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index_id: str = Field(pattern=r"^idx_[A-Za-z0-9_-]+$")
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    options: QueryOptions = Field(default_factory=QueryOptions)
    debug: bool = False

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class MultiQueryOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "hybrid"
    rerank: bool = False


class MultiQueryScopeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_id: str = Field(min_length=1, max_length=200)
    index_id: str = Field(pattern=r"^idx_[A-Za-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=300)


class MultiQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    scope: list[MultiQueryScopeItem] = Field(min_length=1, max_length=5)
    focus_repository_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=8, ge=1, le=20)
    options: MultiQueryOptions = Field(default_factory=MultiQueryOptions)
    debug: bool = False

    @field_validator("question")
    @classmethod
    def multi_question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value

    @field_validator("focus_repository_ids")
    @classmethod
    def focus_ids_not_blank(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("focus repository IDs must not be blank")
        return value


class CreateIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_url: str = Field(min_length=1, max_length=300)
    ref: str | None = Field(default=None, min_length=1, max_length=255)
    client_ref: str | None = Field(default=None, max_length=200)

    @field_validator("ref")
    @classmethod
    def ref_must_be_a_git_reference(cls, value: str | None) -> str | None:
        if value is not None:
            normalized = value.strip()
            if (
                not normalized
                or normalized.startswith("-")
                or any(character.isspace() for character in normalized)
            ):
                raise ValueError("ref must be a valid branch, tag, or commit reference")
            return normalized
        return value


def _error(
    code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _get_embedding_runtime(app: FastAPI):
    runtime = app.state.embedding_runtime
    if runtime is not None:
        return runtime
    with app.state.runtime_lock:
        runtime = app.state.embedding_runtime
        if runtime is None:
            settings: Settings = app.state.settings
            base_embedder = SentenceTransformerEmbedder(
                model_name=settings.embedding_model,
                device=settings.embedding_device,
            )
            cache = SQLiteEmbeddingCache(
                settings.embedding_cache_path,
                chunker_version=CHUNKER_VERSION,
                header_enabled=False,
            )
            embedder = CachedEmbedder(base_embedder, cache)
            store = QdrantVectorStore(path=settings.qdrant_path)
            runtime = (embedder, store, cache)
            app.state.embedding_runtime = runtime
            app.state.resources.append(("runtime", runtime))
    return runtime


def _get_query_service(app: FastAPI):
    if app.state.query_service is not None:
        return app.state.query_service
    with app.state.runtime_lock:
        if app.state.query_service is not None:
            return app.state.query_service
        settings: Settings = app.state.settings
        embedder, store, _cache = _get_embedding_runtime(app)
        try:
            model = build_language_model(settings)
        except ValueError as exc:
            raise APIError(
                502, "LLM_ERROR", "The configured language model is unavailable"
            ) from exc
        app.state.resources.append(("model", model))
        app.state.query_service = QueryService(DenseRetriever(embedder, store), model)
        return app.state.query_service


def _run_index_job(
    app: FastAPI,
    index_id: str,
    repo_url: str,
    ref: str | None,
    cancelled: threading.Event,
) -> None:
    registry: IndexRegistry = app.state.registry
    index_root = Path(app.state.settings.index_storage_path) / index_id

    def update_status(**values: Any) -> None:
        registry.update(index_id, **values)

    try:
        embedder, store, _cache = _get_embedding_runtime(app)
        run_indexing(
            index_id=index_id,
            repo_url=repo_url,
            ref=ref,
            index_root=index_root,
            embedder=embedder,
            store=store,
            update_status=update_status,
            cancelled=cancelled,
        )
    except IndexCancelled:
        shutil.rmtree(index_root, ignore_errors=True)
    except Exception:  # noqa: BLE001 - persist a safe failed state for any worker crash.
        # The pipeline stores a safe, user-facing failure state. Do not leak
        # repository paths, subprocess output, credentials, or provider errors.
        current = registry.get(index_id)
        if current and current["status"] != "failed":
            registry.update(
                index_id,
                status="failed",
                error_code="INTERNAL_ERROR",
                error_message="Indexing failed due to an internal processing error",
            )


def _read_snapshot_file(
    index_root: Path, relative_path: str, start: int, end: int | None
) -> dict[str, Any]:
    if not relative_path or "\\" in relative_path:
        raise APIError(
            404,
            "FILE_NOT_FOUND",
            "File not found in index snapshot",
            {"path": relative_path},
        )
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise APIError(
            404,
            "FILE_NOT_FOUND",
            "File not found in index snapshot",
            {"path": relative_path},
        )

    manifest_path = index_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise APIError(404, "INDEX_NOT_FOUND", "Index not found") from exc
    allowed = {item.get("path") for item in manifest.get("files", [])}
    normalized = relative.as_posix()
    if normalized not in allowed:
        raise APIError(
            404,
            "FILE_NOT_FOUND",
            "File not found in index snapshot",
            {"path": relative_path},
        )

    snapshot_root = (index_root / "snapshot").resolve()
    file_path = index_root / "snapshot" / Path(*relative.parts)
    try:
        current = index_root / "snapshot"
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise FileNotFoundError
        resolved = file_path.resolve(strict=True)
        resolved.relative_to(snapshot_root)
        if not resolved.is_file():
            raise FileNotFoundError
        content = resolved.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise APIError(
            404,
            "FILE_NOT_FOUND",
            "File not found in index snapshot",
            {"path": relative_path},
        ) from exc

    lines = content.splitlines(keepends=True)
    total = len(lines)
    if start > total or (end is not None and start > end):
        raise APIError(
            400,
            "INVALID_RANGE",
            "Requested line range is invalid",
            {"start": start, "end": end, "total_lines": total},
        )
    last = min(end if end is not None else start + 1999, total)
    if last - start + 1 > 2000:
        raise APIError(
            400,
            "INVALID_RANGE",
            "Requested line range exceeds 2000 lines",
            {"start": start, "end": last},
        )
    return {
        "path": normalized,
        "start": start,
        "end": last,
        "content": "".join(lines[start - 1 : last]),
        "language": "python",
    }


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    settings: Settings = request.app.state.settings
    if not settings.rag_api_key or x_api_key != settings.rag_api_key:
        raise APIError(401, "UNAUTHORIZED", "Missing or invalid API key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.registry.mark_interrupted_jobs_failed()
    yield
    app.state.executor.shutdown(wait=True, cancel_futures=True)
    for kind, resource in reversed(app.state.resources):
        if kind == "runtime":
            _embedder, store, cache = resource
            store.close()
            cache.close()
        elif kind == "model":
            resource.close()


def create_app(
    *,
    query_service: QueryService | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    app = FastAPI(title="Codebase RAG Service", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.state.query_service = query_service
    app.state.embedding_runtime = None
    app.state.runtime_lock = threading.RLock()
    app.state.resources = []
    app.state.executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="rag-index"
    )
    app.state.futures: dict[str, Future] = {}
    app.state.cancel_events: dict[str, threading.Event] = {}
    app.state.registry = IndexRegistry(app.state.settings.index_registry_path)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID")
        response = await call_next(request)
        if request_id:
            response.headers["X-Request-ID"] = request_id[:128]
        return response

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        location = first.get("loc", [])
        field = str(location[-1]) if location else "request"
        return JSONResponse(
            status_code=422,
            content=_error(
                "INVALID_REQUEST",
                "Validation failed",
                {"field": field, "reason": str(first.get("msg", "invalid value"))},
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=_error("INTERNAL_ERROR", "Unexpected server error"),
        )

    @app.get("/v1/health")
    async def health():
        return {"status": "ok"}

    @app.post("/v1/indexes", status_code=202, dependencies=[Depends(require_api_key)])
    async def create_index(payload: CreateIndexRequest, request: Request):
        try:
            repo_url = validate_repo_url(payload.repo_url)
        except ValueError as exc:
            raise APIError(
                422,
                "INVALID_REPO",
                "Only public GitHub repositories (https://github.com/<owner>/<repo>) are supported",
                {"repo_url": payload.repo_url},
            ) from exc

        index_id = f"idx_{uuid.uuid4().hex[:12]}"
        registry: IndexRegistry = request.app.state.registry
        registry.create(index_id, repo_url, payload.ref, payload.client_ref)
        cancelled = threading.Event()
        request.app.state.cancel_events[index_id] = cancelled
        future = request.app.state.executor.submit(
            _run_index_job,
            request.app,
            index_id,
            repo_url,
            payload.ref,
            cancelled,
        )
        request.app.state.futures[index_id] = future
        return {"index_id": index_id, "status": "pending"}

    @app.get(
        "/v1/indexes/{index_id}",
        dependencies=[Depends(require_api_key)],
    )
    async def get_index(index_id: str, request: Request):
        if not re.fullmatch(r"idx_[A-Za-z0-9_-]+", index_id):
            raise APIError(
                404, "INDEX_NOT_FOUND", "Index not found", {"index_id": index_id}
            )
        row = request.app.state.registry.get(index_id)
        if row is None:
            raise APIError(
                404, "INDEX_NOT_FOUND", "Index not found", {"index_id": index_id}
            )
        return request.app.state.registry.to_api(row)

    @app.delete(
        "/v1/indexes/{index_id}",
        status_code=204,
        dependencies=[Depends(require_api_key)],
    )
    async def delete_index(index_id: str, request: Request):
        if not re.fullmatch(r"idx_[A-Za-z0-9_-]+", index_id):
            return Response(status_code=204)
        future = request.app.state.futures.get(index_id)
        cancelled = request.app.state.cancel_events.get(index_id)
        if cancelled is not None:
            cancelled.set()
        if future is not None and not future.cancel():
            await run_in_threadpool(future.result)

        runtime = request.app.state.embedding_runtime
        if runtime is not None:
            _embedder, store, _cache = runtime
            store.delete(index_id)
        index_root = Path(request.app.state.settings.index_storage_path) / index_id
        shutil.rmtree(index_root, ignore_errors=True)
        request.app.state.registry.delete(index_id)
        request.app.state.futures.pop(index_id, None)
        request.app.state.cancel_events.pop(index_id, None)
        return Response(status_code=204)

    @app.get(
        "/v1/indexes/{index_id}/files",
        dependencies=[Depends(require_api_key)],
    )
    async def get_index_file(
        index_id: str,
        request: Request,
        path: str = Query(..., min_length=1, max_length=1024),
        start: int = Query(1, ge=1),
        end: int | None = Query(None, ge=1),
    ):
        row = request.app.state.registry.get(index_id)
        if row is None:
            raise APIError(
                404, "INDEX_NOT_FOUND", "Index not found", {"index_id": index_id}
            )
        if row["status"] != "ready":
            raise APIError(
                409,
                "INDEX_NOT_READY",
                "Index is not ready",
                {"status": row["status"]},
            )
        if end is not None and end < start:
            raise APIError(
                400,
                "INVALID_RANGE",
                "Requested line range is invalid",
                {"start": start, "end": end},
            )
        return _read_snapshot_file(
            Path(request.app.state.settings.index_storage_path) / index_id,
            path,
            start,
            end,
        )

    @app.post("/v1/query", dependencies=[Depends(require_api_key)])
    async def query(payload: QueryRequest, request: Request):
        if payload.options.mode not in {"dense", "hybrid"}:
            raise APIError(
                422,
                "INVALID_REQUEST",
                "Retrieval mode must be dense or hybrid",
                {"field": "options.mode"},
            )
        if payload.options.rerank and payload.options.mode != "hybrid":
            raise APIError(
                422,
                "INVALID_REQUEST",
                "Reranking requires hybrid retrieval",
                {"field": "options.rerank"},
            )

        index_root = (
            Path(request.app.state.settings.index_storage_path) / payload.index_id
        )
        row = request.app.state.registry.get(payload.index_id)
        if row is not None and row["status"] != "ready":
            raise APIError(
                409,
                "INDEX_NOT_READY",
                "Index is not ready",
                {"status": row["status"]},
            )
        manifest_path = index_root / "manifest.json"
        if not manifest_path.is_file():
            raise APIError(
                404,
                "INDEX_NOT_FOUND",
                "Index not found",
                {"index_id": payload.index_id},
            )

        service = request.app.state.query_service
        if service is None:
            service = _get_query_service(request.app)
        if payload.options.mode == "hybrid":
            bm25_path = index_root / "bm25.sqlite3"
            if not bm25_path.is_file():
                raise APIError(
                    409,
                    "INDEX_NOT_READY",
                    "The lexical index is not available for this repository",
                    {"status": "bm25_missing"},
                )
            hybrid = HybridRetriever(
                service.retriever,
                BM25Retriever(Path(request.app.state.settings.index_storage_path)),
            )
            retriever = hybrid
            if payload.options.rerank:
                retriever = RerankingRetriever(hybrid, CrossEncoderReranker())
            service = QueryService(retriever, service.language_model)
        runtime = request.app.state.embedding_runtime
        store = runtime[1] if runtime is not None else None
        if store is not None and not store.collection_exists(payload.index_id):
            raise APIError(
                409,
                "INDEX_NOT_READY",
                "Index is not ready",
                {"status": "embedding"},
            )

        started = time.perf_counter()
        try:
            result = await run_in_threadpool(
                service.query,
                payload.index_id,
                payload.question,
                top_k=payload.top_k,
                debug=payload.debug,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise APIError(504, "TIMEOUT", "The request timed out") from exc
        except LLMError as exc:
            raise APIError(
                502, "LLM_ERROR", "The language model request failed"
            ) from exc

        return {
            "answer": result.answer,
            "citations": result.citations,
            "usage": result.usage,
            "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
            "debug": result.debug,
        }

    @app.post("/v1/query/multi", dependencies=[Depends(require_api_key)])
    async def query_multiple(payload: MultiQueryRequest, request: Request):
        scope = [item.model_dump() for item in payload.scope]
        repository_ids = [item["repository_id"] for item in scope]
        index_ids = [item["index_id"] for item in scope]
        focus_ids = payload.focus_repository_ids

        if len(set(repository_ids)) != len(repository_ids) or len(set(index_ids)) != len(index_ids):
            raise APIError(
                422,
                "INVALID_REQUEST",
                "Scope repository_id and index_id values must be unique",
            )
        if len(set(focus_ids)) != len(focus_ids):
            raise APIError(
                422,
                "INVALID_REQUEST",
                "focus_repository_ids must not contain duplicates",
            )
        if not set(focus_ids).issubset(repository_ids):
            raise APIError(
                422,
                "INVALID_REQUEST",
                "Every focused repository must belong to Scope",
                {"field": "focus_repository_ids"},
            )
        if payload.options.mode not in {"dense", "bm25", "hybrid"}:
            raise APIError(
                422,
                "INVALID_REQUEST",
                "Retrieval mode must be dense, bm25, or hybrid",
                {"field": "options.mode"},
            )
        if payload.options.rerank and payload.options.mode != "hybrid":
            raise APIError(
                422,
                "INVALID_REQUEST",
                "Reranking requires hybrid retrieval",
                {"field": "options.rerank"},
            )

        service = request.app.state.query_service
        if service is None:
            service = _get_query_service(request.app)
        runtime = request.app.state.embedding_runtime
        vector_store = runtime[1] if runtime is not None else None
        started = time.perf_counter()
        try:
            result = await run_in_threadpool(
                run_multi_query,
                question=payload.question,
                scope=scope,
                focus_repository_ids=focus_ids,
                top_k=payload.top_k,
                mode=payload.options.mode,
                rerank=payload.options.rerank,
                query_service=service,
                registry=request.app.state.registry,
                index_storage_path=request.app.state.settings.index_storage_path,
                vector_store=vector_store,
                timeout_seconds=60.0,
            )
        except AllIndexesUnavailable as exc:
            raise APIError(
                503,
                "ALL_INDEXES_UNAVAILABLE",
                "No supplied index is available for querying",
                {"repository_outcomes": exc.repository_outcomes},
            ) from exc
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise APIError(504, "TIMEOUT", "The request timed out") from exc
        except LLMError as exc:
            raise APIError(
                502, "LLM_ERROR", "The language model request failed"
            ) from exc
        except ValueError as exc:
            raise APIError(
                422, "INVALID_REQUEST", str(exc)
            ) from exc

        # Keep the public latency tied to the complete endpoint execution.
        result["latency_ms"] = max(0, int((time.perf_counter() - started) * 1000))
        return result

    return app


app = create_app()
