from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.errors import ServiceError
from app.models import ResultEnvelope, RetrievalBudget
from app.services.budget import build_effective_budget, enforce_budget


def test_retrieval_budget_requires_at_least_one_field() -> None:
    with pytest.raises(ValueError):
        RetrievalBudget()


def test_result_envelope_defaults_are_stable() -> None:
    result_envelope = ResultEnvelope(
        result_id="r_123",
        source_type="google_sheets",
        source_id="vehicle_log_primary",
        source_name="Vehicle Log - Primary",
        source_ref="google_sheets:vehicle_log_primary:Maintenance!A44:H44",
        retrieved_at=datetime.now(UTC),
        title="Battery replacement",
        content_type="spreadsheet_row",
        text="Battery replacement.",
        raw={},
    )

    dumped = result_envelope.model_dump(mode="json")

    assert dumped["cache_status"] == "unknown"
    assert dumped["confidence"] == "none"
    assert dumped["available_context"] == []
    assert dumped["raw"] == {}


def test_retrieval_budget_enforcement_truncates_results(source_config_factory) -> None:
    source_config = source_config_factory()
    result_envelopes = [
        ResultEnvelope(
            result_id="r_1",
            source_type="google_sheets",
            source_id="vehicle_log_primary",
            source_name="Vehicle Log - Primary",
            source_ref="google_sheets:vehicle_log_primary:Maintenance!A1:H1",
            retrieved_at=datetime.now(UTC),
            title="One",
            content_type="spreadsheet_row",
            text="One",
            raw={},
        ),
        ResultEnvelope(
            result_id="r_2",
            source_type="google_sheets",
            source_id="vehicle_log_primary",
            source_name="Vehicle Log - Primary",
            source_ref="google_sheets:vehicle_log_primary:Maintenance!A2:H2",
            retrieved_at=datetime.now(UTC),
            title="Two",
            content_type="spreadsheet_row",
            text="Two",
            raw={},
        ),
    ]

    bounded_results, budget_summary = enforce_budget(
        result_envelopes,
        build_effective_budget([source_config], RetrievalBudget(max_results=1)),
    )

    assert len(bounded_results) == 1
    assert budget_summary.truncated is True


def test_retrieval_budget_enforcement_rejects_too_small_budget(source_config_factory) -> None:
    source_config = source_config_factory()
    result_envelope = ResultEnvelope(
        result_id="r_1",
        source_type="google_sheets",
        source_id="vehicle_log_primary",
        source_name="Vehicle Log - Primary",
        source_ref="google_sheets:vehicle_log_primary:Maintenance!A1:H1",
        retrieved_at=datetime.now(UTC),
        title="One",
        content_type="spreadsheet_row",
        text="One",
        raw={},
    )

    with pytest.raises(ServiceError, match="budget"):
        enforce_budget(
            [result_envelope],
            build_effective_budget([source_config], RetrievalBudget(max_bytes=1)),
        )
