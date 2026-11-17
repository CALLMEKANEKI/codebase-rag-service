from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from rag.config import Settings, get_settings
from rag.embeddings.cache import CachedEmbedder, SQLiteEmbeddingCache
from rag.embeddings.sentence_transformer import SentenceTransformerEmbedder
from rag.generation.base import LLMError
from rag.generation.gemini import GeminiLanguageModel
from rag.generation.service import QueryService
from rag.parsing.chunking import CHUNKER_VERSION
from rag.retrieval.dense import DenseRetriever
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


def _error(
    code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _build_query_service(settings: Settings):
    if not settings.gemini_api_key:
        raise APIError(502, "LLM_ERROR", "The language model is not configured")
    embedder_base = SentenceTransformerEmbedder(
        model_name=settings.embedding_model,
        device=settings.embedding_device,
    )
    cache = SQLiteEmbeddingCache(
        settings.embedding_cache_path,
        chunker_version=CHUNKER_VERSION,
        header_enabled=False,
    )
    embedder = CachedEmbedder(embedder_base, cache)
    store = QdrantVectorStore(path=settings.qdrant_path)
    model = GeminiLanguageModel(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_ms=settings.llm_timeout_ms,
    )
    return QueryService(DenseRetriever(embedder, store), model), store, cache, model


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    settings: Settings = request.app.state.settings
    if not settings.rag_api_key or x_api_key != settings.rag_api_key:
        raise APIError(401, "UNAUTHORIZED", "Missing or invalid API key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    resources = getattr(app.state, "resources", None)
    if resources:
        store, cache, model = resources
        store.close()
        cache.close()
        model.close()


def create_app(
    *,
    query_service: QueryService | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    app = FastAPI(title="Codebase RAG Service", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.state.query_service = query_service
    app.state.resources = None

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
                {
                    "field": field,
                    "reason": str(first.get("msg", "invalid value")),
                },
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

    @app.post("/v1/query", dependencies=[Depends(require_api_key)])
    async def query(payload: QueryRequest, request: Request):
        if payload.options.mode != "dense" or payload.options.rerank:
            raise APIError(
                422,
                "INVALID_REQUEST",
                "Only dense retrieval with rerank=false is supported",
                {"field": "options"},
            )

        index_root = (
            Path(request.app.state.settings.index_storage_path) / payload.index_id
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
            service, store, cache, model = _build_query_service(
                request.app.state.settings
            )
            request.app.state.query_service = service
            request.app.state.resources = (store, cache, model)
        store = request.app.state.resources[0] if request.app.state.resources else None
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

    return app


app = create_app()
