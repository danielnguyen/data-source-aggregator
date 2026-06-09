from __future__ import annotations

import json

from app.errors import ServiceError
from app.models import (
    EffectiveRetrievalBudget,
    ResultEnvelope,
    RetrievalBudget,
    RetrievalBudgetSummary,
    SourceConfig,
)


def build_effective_budget(
    source_configs: list[SourceConfig],
    request_budget: RetrievalBudget | None,
    request_max_results: int | None = None,
) -> EffectiveRetrievalBudget:
    source_max_results = (
        min(source_config.retrieval.max_results for source_config in source_configs)
        if source_configs
        else None
    )
    source_max_bytes = (
        min(source_config.retrieval.max_bytes for source_config in source_configs)
        if source_configs
        else None
    )
    source_max_text_chars = (
        min(source_config.retrieval.max_text_chars for source_config in source_configs)
        if source_configs
        else None
    )

    return EffectiveRetrievalBudget(
        max_results=_minimum(
            source_max_results,
            request_budget.max_results if request_budget else None,
            request_max_results,
        ),
        max_bytes=_minimum(
            source_max_bytes,
            request_budget.max_bytes if request_budget else None,
        ),
        max_text_chars=_minimum(
            source_max_text_chars,
            request_budget.max_text_chars if request_budget else None,
        ),
        max_rows=request_budget.max_rows if request_budget else None,
    )


def enforce_budget(
    result_envelopes: list[ResultEnvelope],
    retrieval_budget: EffectiveRetrievalBudget,
) -> tuple[list[ResultEnvelope], RetrievalBudgetSummary]:
    estimated_bytes = 0
    bounded_results: list[ResultEnvelope] = []
    truncated = False

    for result_envelope in result_envelopes:
        result_bytes = len(json.dumps(result_envelope.model_dump(mode="json")).encode("utf-8"))
        text_chars = len(result_envelope.text)

        if (
            retrieval_budget.max_results is not None
            and len(bounded_results) >= retrieval_budget.max_results
        ):
            truncated = True
            break

        if (
            retrieval_budget.max_bytes is not None
            and estimated_bytes + result_bytes > retrieval_budget.max_bytes
        ):
            truncated = True
            break

        if retrieval_budget.max_text_chars is not None:
            current_text_chars = sum(len(result.text) for result in bounded_results)
            if current_text_chars + text_chars > retrieval_budget.max_text_chars:
                truncated = True
                break

        bounded_results.append(result_envelope)
        estimated_bytes += result_bytes

    if not bounded_results and result_envelopes and truncated:
        raise ServiceError(
            "result_too_large",
            "The retrieval budget is too small for the requested result set.",
            status_code=413,
            details={"max_bytes": retrieval_budget.max_bytes},
        )

    return bounded_results, RetrievalBudgetSummary(
        max_results=retrieval_budget.max_results,
        returned_results=len(bounded_results),
        estimated_bytes=estimated_bytes,
        truncated=truncated,
    )


def _minimum(*values: int | None) -> int | None:
    present_values = [value for value in values if value is not None]
    if not present_values:
        return None
    return min(present_values)
