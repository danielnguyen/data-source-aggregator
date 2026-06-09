from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.audit import AuditLogWriter
from app.connectors import base as connector_base
from app.errors import ServiceError
from app.models import (
    AuditEvent,
    AuditStatus,
    Confidence,
    SearchRequest,
    SearchResponse,
)
from app.registry import SourceRegistry
from app.services.budget import build_effective_budget, enforce_budget


async def run_search(
    request: SearchRequest,
    source_registry: SourceRegistry,
    audit_log_writer: AuditLogWriter,
) -> SearchResponse:
    query_id = _query_id()
    selected_sources = source_registry.select_sources(
        source_ids=request.source_ids,
        domain_tags=request.domain_tags,
        allowed_sensitivity=request.allowed_sensitivity,
        required_capability="search",
    )
    if request.source_ids and len(selected_sources) != len(request.source_ids):
        missing_source_ids = sorted(
            set(request.source_ids) - {source.source_id for source in selected_sources}
        )
        error = ServiceError(
            "source_not_found",
            f"Source '{missing_source_ids[0]}' is not configured or is disabled.",
            status_code=404,
            details={"source_id": missing_source_ids[0]},
        )
        _write_failure_event(
            audit_log_writer,
            operation="search",
            source_ids=request.source_ids,
            query=request.query,
            error=error,
        )
        raise error

    effective_budget = build_effective_budget(
        selected_sources,
        request.budget,
        request.max_results,
    )

    result_envelopes = []
    for source_config in selected_sources:
        connector = connector_base.get_connector(source_config.connector)
        result_envelopes.extend(await connector.search(request, source_config))

    bounded_results, budget_summary = enforce_budget(result_envelopes, effective_budget)
    response = SearchResponse(
        query_id=query_id,
        query=request.query,
        answerable=bool(bounded_results),
        confidence=Confidence.NONE if not bounded_results else Confidence.LOW,
        retrieval_mode=request.retrieval_mode,
        results=bounded_results,
        budget=budget_summary,
    )
    audit_log_writer.write_event(
        AuditEvent(
            event_id=_event_id(),
            timestamp=datetime.now(UTC),
            operation="search",
            source_ids=[source.source_id for source in selected_sources],
            query=request.query,
            result_count=len(response.results),
            estimated_bytes=response.budget.estimated_bytes,
            status=AuditStatus.SUCCESS,
        )
    )
    return response


def _write_failure_event(
    audit_log_writer: AuditLogWriter,
    *,
    operation: str,
    source_ids: list[str],
    query: str | None = None,
    source_ref: str | None = None,
    error: ServiceError,
) -> None:
    audit_log_writer.write_event(
        AuditEvent(
            event_id=_event_id(),
            timestamp=datetime.now(UTC),
            operation=operation,
            source_ids=source_ids,
            query=query,
            source_ref=source_ref,
            result_count=0,
            estimated_bytes=0,
            status=AuditStatus.ERROR,
            error_code=error.code,
        )
    )


def _event_id() -> str:
    return f"evt_{uuid4().hex}"


def _query_id() -> str:
    return f"q_{uuid4().hex}"
