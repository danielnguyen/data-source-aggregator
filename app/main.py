from __future__ import annotations

import hmac
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.audit import AuditLogWriter
from app.config import get_dsa_api_key, load_source_config_inventory
from app.errors import ServiceError
from app.models import (
    ContextPackRequest,
    ContextPackResponse,
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
from app.registry import SourceRegistry, build_empty_source_registry, build_source_registry
from app.services.context_pack import run_context_pack
from app.services.fetch import run_context, run_fetch
from app.services.search import run_search

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_request_logger = logging.getLogger("uvicorn.error.data_source_aggregator.requests")


def create_app(source_config_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await _refresh_source_registry(app, source_config_dir)
        yield

    app = FastAPI(title="data-source-aggregator", lifespan=lifespan)
    app.state.source_registry = build_empty_source_registry()
    app.state.audit_log_writer = AuditLogWriter()
    app.state.source_config_dir = source_config_dir
    app.state.dsa_api_key = get_dsa_api_key()

    @app.middleware("http")
    async def correlate_request(request: Request, call_next):
        request_id, correlation_state = _request_correlation(
            request.headers.get("X-Request-ID")
        )
        try:
            response = await call_next(request)
        except Exception:
            _log_request_completion(
                request=request,
                request_id=request_id,
                correlation_state=correlation_state,
                status_code=500,
            )
            raise

        if request_id is not None:
            response.headers["X-Request-ID"] = request_id
        _log_request_completion(
            request=request,
            request_id=request_id,
            correlation_state=correlation_state,
            status_code=response.status_code,
        )
        return response

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
        _require_api_key(request)
        await _ensure_source_registry_loaded(request.app)
        registry = _get_registry(request)
        return SourceListResponse(
            inventory_scope="configured_sources",
            inventory_status=registry.inventory_status,
            sources=registry.list_sources(),
        )

    @app.get("/v1/sources/{source_id}", response_model=SourceDetailResponse)
    async def get_source(source_id: str, request: Request) -> SourceDetailResponse:
        _require_api_key(request)
        await _ensure_source_registry_loaded(request.app)
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
        _require_api_key(request)
        await _ensure_source_registry_loaded(request.app)
        return await run_search(
            request_body,
            _get_registry(request),
            _get_audit_log_writer(request),
        )

    @app.post("/v1/sources/fetch", response_model=FetchResponse)
    async def fetch_source(request_body: FetchRequest, request: Request) -> FetchResponse:
        _require_api_key(request)
        await _ensure_source_registry_loaded(request.app)
        return await run_fetch(
            request_body,
            _get_registry(request),
            _get_audit_log_writer(request),
        )

    @app.post("/v1/sources/context", response_model=ContextResponse)
    async def get_context(request_body: ContextRequest, request: Request) -> ContextResponse:
        _require_api_key(request)
        await _ensure_source_registry_loaded(request.app)
        return await run_context(
            request_body,
            _get_registry(request),
            _get_audit_log_writer(request),
        )

    @app.post("/v1/context-pack", response_model=ContextPackResponse)
    async def build_context_pack(
        request_body: ContextPackRequest,
        request: Request,
    ) -> ContextPackResponse:
        _require_api_key(request)
        await _ensure_source_registry_loaded(request.app)
        return await run_context_pack(
            request_body,
            _get_registry(request),
            _get_audit_log_writer(request),
        )

    return app


def _request_correlation(raw_request_id: str | None) -> tuple[str | None, str]:
    if raw_request_id is None:
        return None, "absent"
    if _REQUEST_ID_PATTERN.fullmatch(raw_request_id):
        return raw_request_id, "valid"
    return None, "invalid"


def _log_request_completion(
    *,
    request: Request,
    request_id: str | None,
    correlation_state: str,
    status_code: int,
) -> None:
    route = request.scope.get("route")
    route_template = getattr(route, "path", None)
    if not isinstance(route_template, str) or not route_template.startswith("/"):
        route_template = "unmatched"
    method = request.method if re.fullmatch(r"[A-Z]{1,16}", request.method) else "UNKNOWN"
    if request_id is None:
        _request_logger.info(
            "dsa_request_completed component=data-source-aggregator "
            "correlation_state=%s method=%s route=%s status=%d",
            correlation_state,
            method,
            route_template,
            status_code,
        )
        return
    _request_logger.info(
        "dsa_request_completed component=data-source-aggregator "
        "correlation_state=valid request_id=%s method=%s route=%s status=%d",
        request_id,
        method,
        route_template,
        status_code,
    )


def _get_registry(request: Request) -> SourceRegistry:
    return request.app.state.source_registry


def _get_audit_log_writer(request: Request) -> AuditLogWriter:
    return request.app.state.audit_log_writer


def _require_api_key(request: Request) -> None:
    configured_api_key = request.app.state.dsa_api_key
    if configured_api_key is None:
        return

    provided_api_key = request.headers.get("X-API-Key")
    if provided_api_key and hmac.compare_digest(provided_api_key, configured_api_key):
        return

    raise ServiceError(
        "unauthorized",
        "Invalid or missing API key",
        status_code=401,
        details={},
    )


async def _ensure_source_registry_loaded(app: FastAPI) -> None:
    registry: SourceRegistry = app.state.source_registry
    if registry.loaded:
        return
    await _refresh_source_registry(app, app.state.source_config_dir)


async def _refresh_source_registry(app: FastAPI, source_config_dir: Path | None) -> None:
    load_result = load_source_config_inventory(source_config_dir)
    app.state.source_registry = await build_source_registry(
        load_result.source_configs,
        inventory_status=load_result.inventory_status,
    )


app = create_app()
