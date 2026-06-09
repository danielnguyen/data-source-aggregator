from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.audit import AuditLogWriter
from app.config import load_source_configs
from app.errors import ServiceError
from app.models import (
    ContextRequest,
    ContextResponse,
    ErrorDetail,
    ErrorResponse,
    FetchRequest,
    FetchResponse,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    SourceDetailResponse,
    SourceListResponse,
)
from app.registry import SourceRegistry, build_source_registry
from app.services.fetch import run_context, run_fetch
from app.services.search import run_search


def create_app(source_config_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _refresh_source_registry(app, source_config_dir)
        yield

    app = FastAPI(title="data-source-aggregator", lifespan=lifespan)
    app.state.source_registry = build_source_registry([])
    app.state.audit_log_writer = AuditLogWriter()
    if source_config_dir is not None:
        _refresh_source_registry(app, source_config_dir)

    @app.exception_handler(ServiceError)
    async def handle_service_error(_: Request, error: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=ErrorResponse(
                error=ErrorDetail(
                    code=error.code,
                    message=error.message,
                    details=error.details,
                )
            ).model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        issues = [
            {"location": list(issue["loc"]), "message": issue["msg"]}
            for issue in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="invalid_request",
                    message="Request validation failed.",
                    details={"issues": issues},
                )
            ).model_dump(mode="json"),
        )

    @app.get("/health", response_model=HealthResponse)
    async def get_health() -> HealthResponse:
        return HealthResponse()

    @app.get("/v1/sources", response_model=SourceListResponse)
    async def list_sources(request: Request) -> SourceListResponse:
        registry = _get_registry(request)
        return SourceListResponse(sources=registry.list_sources())

    @app.get("/v1/sources/{source_id}", response_model=SourceDetailResponse)
    async def get_source(source_id: str, request: Request) -> SourceDetailResponse:
        registry = _get_registry(request)
        source = registry.get_source(source_id)
        if source is None:
            raise ServiceError(
                "source_not_found",
                f"Source '{source_id}' is not configured or is disabled.",
                status_code=404,
                details={"source_id": source_id},
            )
        return SourceDetailResponse(source=source)

    @app.post("/v1/sources/search", response_model=SearchResponse)
    async def search_sources(request_body: SearchRequest, request: Request) -> SearchResponse:
        return await run_search(
            request_body,
            _get_registry(request),
            _get_audit_log_writer(request),
        )

    @app.post("/v1/sources/fetch", response_model=FetchResponse)
    async def fetch_source(request_body: FetchRequest, request: Request) -> FetchResponse:
        return await run_fetch(
            request_body,
            _get_registry(request),
            _get_audit_log_writer(request),
        )

    @app.post("/v1/sources/context", response_model=ContextResponse)
    async def get_context(request_body: ContextRequest, request: Request) -> ContextResponse:
        return await run_context(
            request_body,
            _get_registry(request),
            _get_audit_log_writer(request),
        )

    return app


def _get_registry(request: Request) -> SourceRegistry:
    return request.app.state.source_registry


def _get_audit_log_writer(request: Request) -> AuditLogWriter:
    return request.app.state.audit_log_writer


def _refresh_source_registry(app: FastAPI, source_config_dir: Path | None) -> None:
    source_configs = load_source_configs(source_config_dir)
    app.state.source_registry = build_source_registry(source_configs)


app = create_app()
