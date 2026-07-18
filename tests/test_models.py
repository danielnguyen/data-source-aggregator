from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.errors import ServiceError
from app.models import AvailableContext, ContextPackItem, ResultEnvelope, RetrievalBudget
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


def test_available_context_descriptor_is_strict_and_bounded() -> None:
    descriptor = AvailableContext(
        context_mode="nearby_rows:window.1",
        description="Fetch nearby rows.",
    )

    assert descriptor.model_dump(mode="json") == {
        "context_mode": "nearby_rows:window.1",
        "description": "Fetch nearby rows.",
    }


@pytest.mark.parametrize(
    "descriptor",
    [
        {"description": "Fetch nearby rows."},
        {"context_mode": "nearby_rows"},
        {"context_mode": "", "description": "Fetch nearby rows."},
        {"context_mode": "nearby rows", "description": "Fetch nearby rows."},
        {
            "context_mode": "https://private.example.test/context",
            "description": "Fetch nearby rows.",
        },
        {"context_mode": "nearby_rows?window=1", "description": "Fetch nearby rows."},
        {"context_mode": "x" * 121, "description": "Fetch nearby rows."},
        {"context_mode": "nearby_rows", "description": ""},
        {"context_mode": "nearby_rows", "description": "x" * 501},
        {
            "context_mode": "nearby_rows",
            "description": "Fetch nearby rows.",
            "credentials": "credential-secret",
        },
        {
            "context_mode": "nearby_rows",
            "description": "Fetch nearby rows.",
            "arguments": {"window": 5},
        },
        {
            "context_mode": "nearby_rows",
            "description": "Fetch nearby rows.",
            "source_config": {"private": True},
        },
        {
            "context_mode": "nearby_rows",
            "description": "Fetch nearby rows.",
            "raw": {"private": "content"},
        },
    ],
)
def test_available_context_descriptor_rejects_unsafe_or_unbounded_values(
    descriptor: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AvailableContext.model_validate(descriptor)


def test_available_context_descriptor_requires_string_fields() -> None:
    with pytest.raises(ValidationError):
        AvailableContext.model_validate(
            {"context_mode": 7, "description": "Fetch nearby rows."}
        )
    with pytest.raises(ValidationError):
        AvailableContext.model_validate(
            {"context_mode": "nearby_rows", "description": 7}
        )


@pytest.mark.parametrize(
    "available_context",
    [
        {"context_mode": "nearby_rows", "description": "Fetch nearby rows."},
        ("nearby_rows",),
        ["nearby_rows"],
        [
            {
                "context_mode": "nearby_rows",
                "description": "Fetch nearby rows.",
            }
        ]
        * 17,
        [
            {
                "context_mode": "nearby_rows",
                "description": "Fetch nearby rows.",
            },
            {
                "context_mode": "nearby_rows",
                "description": "Fetch nearby rows again.",
            },
        ],
    ],
)
def test_result_envelope_rejects_malformed_available_context_collections(
    available_context: object,
) -> None:
    with pytest.raises(ValidationError):
        ResultEnvelope(
            result_id="r_123",
            source_type="google_sheets",
            source_id="vehicle_log_primary",
            source_name="Vehicle Log - Primary",
            source_ref="google_sheets:vehicle_log_primary:Maintenance!A44:H44",
            retrieved_at=datetime.now(UTC),
            title="Battery replacement",
            content_type="spreadsheet_row",
            text="Battery replacement.",
            available_context=available_context,
        )


def test_context_pack_item_preserves_unique_descriptor_order_and_explicit_empty_list() -> None:
    item = ContextPackItem(
        result_id="r_123",
        source_type="generic",
        source_id="source_primary",
        source_name="Primary Source",
        source_ref="generic:source_primary:item-1",
        retrieved_at=datetime.now(UTC),
        title="Bounded record",
        content_type="text",
        text="Bounded record.",
        available_context=[
            {
                "context_mode": "before",
                "description": "Fetch preceding context.",
            },
            {
                "context_mode": "after",
                "description": "Fetch following context.",
            },
        ],
    )
    empty_item = item.model_copy(update={"available_context": []})

    assert [
        descriptor["context_mode"]
        for descriptor in item.model_dump(mode="json")["available_context"]
    ] == ["before", "after"]
    assert empty_item.model_dump(mode="json")["available_context"] == []


def test_context_pack_item_rejects_duplicate_context_modes() -> None:
    with pytest.raises(ValidationError):
        ContextPackItem(
            result_id="r_123",
            source_type="generic",
            source_id="source_primary",
            source_name="Primary Source",
            source_ref="generic:source_primary:item-1",
            retrieved_at=datetime.now(UTC),
            title="Bounded record",
            content_type="text",
            text="Bounded record.",
            available_context=[
                {
                    "context_mode": "nearby",
                    "description": "Fetch nearby context.",
                },
                {
                    "context_mode": "nearby",
                    "description": "Fetch nearby context again.",
                },
            ],
        )


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
