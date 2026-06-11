from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from app.audit import AuditLogWriter
from app.connectors import base as connector_base
from app.errors import ServiceError
from app.models import (
    AuditEvent,
    AuditStatus,
    ContextPackItem,
    ContextPackRequest,
    ContextPackResponse,
    ResultEnvelope,
    RetrievalBudgetSummary,
    SearchRequest,
)
from app.registry import SourceRegistry
from app.services.budget import build_effective_budget


async def run_context_pack(
    request: ContextPackRequest,
    source_registry: SourceRegistry,
    audit_log_writer: AuditLogWriter,
) -> ContextPackResponse:
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
            operation="context_pack",
            source_ids=request.source_ids,
            query=request.query,
            error=error,
        )
        raise error

    search_request = SearchRequest(
        query=request.query,
        source_ids=request.source_ids,
        domain_tags=request.domain_tags,
        retrieval_mode=request.retrieval_mode,
        allowed_sensitivity=request.allowed_sensitivity,
        budget=request.budget,
        include_raw=False,
    )
    result_envelopes: list[ResultEnvelope] = []
    for source_config in selected_sources:
        connector = connector_base.get_connector(source_config.connector)
        result_envelopes.extend(await connector.search(search_request, source_config))

    effective_budget = build_effective_budget(selected_sources, request.budget)
    items = [_build_context_pack_item(result_envelope) for result_envelope in result_envelopes]
    bounded_items, budget_summary = _enforce_item_budget(items, effective_budget)
    response = ContextPackResponse(
        query_id=query_id,
        query=request.query,
        sources_used=[source.source_id for source in selected_sources],
        items=bounded_items,
        budget=budget_summary,
    )
    audit_log_writer.write_event(
        AuditEvent(
            event_id=_event_id(),
            timestamp=datetime.now(UTC),
            operation="context_pack",
            source_ids=response.sources_used,
            query=request.query,
            result_count=len(response.items),
            estimated_bytes=response.budget.estimated_bytes,
            status=AuditStatus.SUCCESS,
        )
    )
    return response


def _build_context_pack_item(result_envelope: ResultEnvelope) -> ContextPackItem:
    return ContextPackItem(
        result_id=result_envelope.result_id,
        source_type=result_envelope.source_type,
        source_id=result_envelope.source_id,
        source_name=result_envelope.source_name,
        source_ref=result_envelope.source_ref,
        retrieved_at=result_envelope.retrieved_at,
        source_modified_at=result_envelope.source_modified_at,
        title=result_envelope.title,
        content_type=result_envelope.content_type,
        text=result_envelope.text,
        confidence=result_envelope.confidence,
        warnings=list(result_envelope.warnings),
    )


def _enforce_item_budget(
    items: list[ContextPackItem],
    effective_budget,
) -> tuple[list[ContextPackItem], RetrievalBudgetSummary]:
    estimated_bytes = 0
    bounded_items: list[ContextPackItem] = []
    truncated = False
    current_text_chars = 0

    for item in items:
        item_bytes = len(json.dumps(item.model_dump(mode="json")).encode("utf-8"))
        item_text_chars = len(item.text)

        if (
            effective_budget.max_results is not None
            and len(bounded_items) >= effective_budget.max_results
        ):
            truncated = True
            break

        if (
            effective_budget.max_bytes is not None
            and estimated_bytes + item_bytes > effective_budget.max_bytes
        ):
            truncated = True
            break

        if (
            effective_budget.max_text_chars is not None
            and current_text_chars + item_text_chars > effective_budget.max_text_chars
        ):
            truncated = True
            break

        bounded_items.append(item)
        estimated_bytes += item_bytes
        current_text_chars += item_text_chars

    if not bounded_items and items and truncated:
        raise ServiceError(
            "result_too_large",
            "The retrieval budget is too small for the requested result set.",
            status_code=413,
            details={"max_bytes": effective_budget.max_bytes},
        )

    return bounded_items, RetrievalBudgetSummary(
        max_results=effective_budget.max_results,
        returned_results=len(bounded_items),
        estimated_bytes=estimated_bytes,
        truncated=truncated,
    )


def _write_failure_event(
    audit_log_writer: AuditLogWriter,
    *,
    operation: str,
    source_ids: list[str],
    query: str,
    error: ServiceError,
) -> None:
    audit_log_writer.write_event(
        AuditEvent(
            event_id=_event_id(),
            timestamp=datetime.now(UTC),
            operation=operation,
            source_ids=source_ids,
            query=query,
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
