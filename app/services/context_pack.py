from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from uuid import uuid4

from app.audit import AuditLogWriter
from app.connectors import base as connector_base
from app.errors import ServiceError
from app.models import (
    AuditEvent,
    AuditStatus,
    ContextPackDiagnostics,
    ContextPackItem,
    ContextPackRequest,
    ContextPackResponse,
    ContextPackSourceDiagnostic,
    ResultEnvelope,
    RetrievalBudgetSummary,
    SearchRequest,
)
from app.registry import SourceRegistry
from app.services.budget import build_effective_budget
from app.services.relevance import (
    build_query_relevance_profile,
    overlap_score,
    tokenize_text,
)


async def run_context_pack(
    request: ContextPackRequest,
    source_registry: SourceRegistry,
    audit_log_writer: AuditLogWriter,
) -> ContextPackResponse:
    query_id = _query_id()
    selection_mode, selected_sources, source_diagnostics = _select_context_pack_sources(
        request,
        source_registry,
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

    effective_budget = build_effective_budget(selected_sources, request.budget)
    per_source_candidate_limit = (
        max(2, effective_budget.max_results)
        if effective_budget.max_results is not None
        else None
    )
    search_request = SearchRequest(
        query=request.query,
        source_ids=request.source_ids,
        domain_tags=request.domain_tags,
        retrieval_mode=request.retrieval_mode,
        max_results=per_source_candidate_limit,
        allowed_sensitivity=request.allowed_sensitivity,
        budget=request.budget,
        include_raw=False,
    )
    candidate_envelopes_by_source: dict[str, list[ResultEnvelope]] = {}
    for source_config in selected_sources:
        connector = connector_base.get_connector(source_config.connector)
        source_candidates = await connector.search(search_request, source_config)
        if per_source_candidate_limit is not None:
            source_candidates = source_candidates[:per_source_candidate_limit]
        candidate_envelopes_by_source[source_config.source_id] = source_candidates

    ranked_result_envelopes, ranking_mode = _rank_context_pack_candidates(
        request.query,
        selected_sources,
        source_diagnostics,
        candidate_envelopes_by_source,
    )
    items = [
        _build_context_pack_item(result_envelope)
        for result_envelope in ranked_result_envelopes
    ]
    bounded_items, budget_summary = _enforce_item_budget(items, effective_budget)
    response = ContextPackResponse(
        query_id=query_id,
        query=request.query,
        sources_used=[source.source_id for source in selected_sources],
        items=bounded_items,
        budget=budget_summary,
        diagnostics=ContextPackDiagnostics(
            selection_mode=selection_mode,
            considered_source_ids=[diagnostic.source_id for diagnostic in source_diagnostics],
            selected_source_ids=[source.source_id for source in selected_sources],
            source_diagnostics=source_diagnostics,
            ranking_mode=ranking_mode,
            candidate_counts_by_source={
                source_id: len(candidates)
                for source_id, candidates in candidate_envelopes_by_source.items()
            },
            budget_truncated_candidates=budget_summary.truncated,
        ),
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


def _select_context_pack_sources(
    request: ContextPackRequest,
    source_registry: SourceRegistry,
) -> tuple[str, list, list[ContextPackSourceDiagnostic]]:
    if request.source_ids:
        selected_sources = source_registry.select_sources(
            source_ids=request.source_ids,
            domain_tags=request.domain_tags,
            allowed_sensitivity=request.allowed_sensitivity,
            required_capability="search",
        )
        return (
            "explicit_source_ids",
            selected_sources,
            [
                ContextPackSourceDiagnostic(
                    source_id=source.source_id,
                    score=0,
                    score_band="none",
                    reasons=["explicit_source_id"],
                )
                for source in selected_sources
            ],
        )

    selection_mode, selected_sources, source_diagnostics = source_registry.rank_sources_for_query(
        query=request.query,
        allowed_sensitivity=request.allowed_sensitivity,
        required_capability="search",
        domain_tags=request.domain_tags,
    )
    return selection_mode, selected_sources, source_diagnostics


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


def _rank_context_pack_candidates(
    query: str,
    selected_sources,
    source_diagnostics: list[ContextPackSourceDiagnostic],
    candidate_envelopes_by_source: dict[str, list[ResultEnvelope]],
) -> tuple[list[ResultEnvelope], str]:
    query_profile = build_query_relevance_profile(query)
    source_scores = {
        diagnostic.source_id: diagnostic.score for diagnostic in source_diagnostics
    }
    source_rankings: list[tuple[str, int, int]] = []
    ranked_candidates_by_source: dict[str, deque[ResultEnvelope]] = {}

    for source_config in selected_sources:
        source_id = source_config.source_id
        source_candidates = candidate_envelopes_by_source.get(source_id, [])
        if query_profile.wants_latest:
            ranked_candidates = list(enumerate(source_candidates))
        else:
            ranked_candidates = sorted(
                enumerate(source_candidates),
                key=lambda item: (
                    -_score_result_for_query(query, item[1]),
                    item[0],
                ),
            )
        ranked_envelopes = [result_envelope for _, result_envelope in ranked_candidates]
        ranked_candidates_by_source[source_id] = deque(ranked_envelopes)
        top_result_score = (
            _score_result_for_query(query, ranked_envelopes[0]) if ranked_envelopes else -1
        )
        source_rankings.append((source_id, source_scores.get(source_id, 0), top_result_score))

    if len(selected_sources) <= 1:
        only_source_id = selected_sources[0].source_id if selected_sources else ""
        return (
            list(ranked_candidates_by_source.get(only_source_id, deque())),
            "single_source_relevance_then_recency"
            if query_profile.wants_latest
            else "single_source",
        )

    ranked_source_ids = [
        source_id
        for source_id, _, _ in sorted(
            source_rankings,
            key=lambda item: (-item[1], -item[2], item[0]),
        )
    ]

    interleaved_results: list[ResultEnvelope] = []
    while True:
        appended = False
        for source_id in ranked_source_ids:
            remaining_candidates = ranked_candidates_by_source[source_id]
            if not remaining_candidates:
                continue
            interleaved_results.append(remaining_candidates.popleft())
            appended = True
        if not appended:
            break

    return (
        interleaved_results,
        "round_robin_by_source_relevance_then_recency"
        if query_profile.wants_latest
        else "round_robin_by_source_relevance",
    )


def _score_result_for_query(query: str, result_envelope: ResultEnvelope) -> int:
    query_tokens = tokenize_text(query)
    result_tokens = tokenize_text(
        " ".join(
            [
                result_envelope.source_id,
                result_envelope.source_name,
                result_envelope.title,
                result_envelope.content_type,
                result_envelope.text,
            ]
        )
    )
    score, _ = overlap_score(query_tokens, result_tokens, weight=1)
    return score


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
