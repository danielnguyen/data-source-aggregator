from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from app.config import load_source_configs
from app.models import (
    HealthResponse,
    SourceDetailResponse,
    SourceListResponse,
)
from app.registry import SourceRegistry, build_source_registry


def create_app(source_config_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _refresh_source_registry(app, source_config_dir)
        yield

    app = FastAPI(title="data-source-aggregator", lifespan=lifespan)
    app.state.source_registry = build_source_registry([])
    if source_config_dir is not None:
        _refresh_source_registry(app, source_config_dir)

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
            raise HTTPException(status_code=404, detail="source_not_found")
        return SourceDetailResponse(source=source)

    return app


def _get_registry(request: Request) -> SourceRegistry:
    return request.app.state.source_registry


def _refresh_source_registry(app: FastAPI, source_config_dir: Path | None) -> None:
    source_configs = load_source_configs(source_config_dir)
    app.state.source_registry = build_source_registry(source_configs)


app = create_app()
