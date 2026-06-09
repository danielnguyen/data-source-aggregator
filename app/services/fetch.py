from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.audit import AuditLogWriter
from app.connectors.base import get_connector
from app.errors import ServiceError
from app.models import (
    AuditEvent,
    AuditStatus,
    Confidence,
    ContextRequest,
    ContextResponse,
    FetchRequest,
    FetchResponse,
)
from app.registry import SourceRegistry
from app.services.budget import build_effective_budget, enforce_budget
from app.services.source_ref import parse_source_ref


async def run_fetch(
    request: FetchRequest,
    source_registry: SourceRegistry,
    audit_log_writer: AuditLogWriter,
) -> FetchResponse:
    try:
        parsed_source_ref = parse_source_ref(request.source_ref)
        source_config = _resolve_source(
            source_registry,
            parsed_source_ref.source_id,
            parsed_source_ref.source_type,
        )
        connector = get_connector(source_config.connector)
        result_envelopes = await connector.fetch(request, source_config)
        bounded_results, budget_summary = enforce_budget(
            result_envelopes,
            build_effective_budget([source_config], request.budget),
        )
    except ServiceError as error:
        _write_failure_event(
            audit_log_writer,
            operation="fetch",
            source_ref=request.source_ref,
            source_ids=_failure_source_ids(request.source_ref),
            error=error,
        )
        raise

    response = FetchResponse(
        query_id=_query_id(),
        answerable=bool(bounded_results),
        confidence=Confidence.NONE if not bounded_results else Confidence.LOW,
        results=bounded_results,
        budget=budget_summary,
    )
    audit_log_writer.write_event(
        AuditEvent(
            event_id=_event_id(),
            timestamp=datetime.now(UTC),
            operation="fetch",
            source_ids=[source_config.source_id],
            source_ref=request.source_ref,
            result_count=len(response.results),
            estimated_bytes=response.budget.estimated_bytes,
            status=AuditStatus.SUCCESS,
        )
    )
    return response


async def run_context(
    request: ContextRequest,
    source_registry: SourceRegistry,
    audit_log_writer: AuditLogWriter,
) -> ContextResponse:
    try:
        parsed_source_ref = parse_source_ref(request.source_ref)
        source_config = _resolve_source(
            source_registry,
            parsed_source_ref.source_id,
            parsed_source_ref.source_type,
        )
        connector = get_connector(source_config.connector)
        result_envelopes = await connector.context(request, source_config)
        bounded_results, budget_summary = enforce_budget(
            result_envelopes,
            build_effective_budget([source_config], request.budget),
        )
    except ServiceError as error:
        _write_failure_event(
            audit_log_writer,
            operation="context",
            source_ref=request.source_ref,
            source_ids=_failure_source_ids(request.source_ref),
            error=error,
        )
        raise

    response = ContextResponse(
        query_id=_query_id(),
        answerable=bool(bounded_results),
        confidence=Confidence.NONE if not bounded_results else Confidence.LOW,
        results=bounded_results,
        budget=budget_summary,
    )
    audit_log_writer.write_event(
        AuditEvent(
            event_id=_event_id(),
            timestamp=datetime.now(UTC),
            operation="context",
            source_ids=[source_config.source_id],
            source_ref=request.source_ref,
            result_count=len(response.results),
            estimated_bytes=response.budget.estimated_bytes,
            status=AuditStatus.SUCCESS,
        )
    )
    return response


def _resolve_source(source_registry: SourceRegistry, source_id: str, source_type: str):
    source_config = source_registry.get_source_config(source_id)
    source_entry = source_registry.get_source(source_id)
    if source_config is None or source_entry is None or not source_entry.enabled:
        raise ServiceError(
            "source_not_found",
            f"Source '{source_id}' is not configured or is disabled.",
            status_code=404,
            details={"source_id": source_id},
        )
    if source_config.connector != source_type:
        raise ServiceError(
            "invalid_source_ref",
            "The provided source_ref does not match the configured source connector.",
            status_code=400,
            details={"source_id": source_id, "source_type": source_type},
        )
    return source_config


def _write_failure_event(
    audit_log_writer: AuditLogWriter,
    *,
    operation: str,
    source_ref: str,
    source_ids: list[str],
    error: ServiceError,
) -> None:
    audit_log_writer.write_event(
        AuditEvent(
            event_id=_event_id(),
            timestamp=datetime.now(UTC),
            operation=operation,
            source_ids=source_ids,
            source_ref=source_ref,
            result_count=0,
            estimated_bytes=0,
            status=AuditStatus.ERROR,
            error_code=error.code,
        )
    )


def _failure_source_ids(source_ref: str) -> list[str]:
    parts = source_ref.split(":", 2)
    if len(parts) >= 2 and parts[1]:
        return [parts[1]]
    return []


def _event_id() -> str:
    return f"evt_{uuid4().hex}"


def _query_id() -> str:
    return f"q_{uuid4().hex}"
