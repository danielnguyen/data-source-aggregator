from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.errors import ServiceError
from app.models import (
    AvailableContext,
    ContextPackItem,
    ContextRequest,
    MaterialScopeReferences,
    ResultEnvelope,
    RetrievalBudget,
    SourceConfig,
    StructuredFieldValues,
)
from app.services.budget import build_effective_budget, enforce_budget


@pytest.mark.parametrize(
    "scope_refs",
    [
        {"time": "fy2026"},
        {"version": "release-152"},
        {"domain": "credential-management"},
        {"project": "firefox"},
        {"time": "fy2026", "project": "firefox"},
        {
            "time": "fy2026",
            "version": "release-152",
            "domain": "credential-management",
            "project": "firefox",
        },
    ],
)
def test_material_scope_references_accept_exact_configured_subsets(
    scope_refs: dict[str, str],
) -> None:
    references = MaterialScopeReferences.model_validate(scope_refs)

    assert references.model_dump(mode="json") == scope_refs


@pytest.mark.parametrize(
    "scope_refs",
    [
        None,
        [],
        "fy2026",
        2026,
        {},
        {"time": None},
        {"time": 2026},
        {"time": ""},
        {"time": " "},
        {"time": " fy2026"},
        {"time": "fy2026 "},
        {"time": "https://scope.example.test/fy2026"},
        {"time": "fy2026?region=west"},
        {"time": "fy2026/west"},
        {"time": "fy2026!"},
        {"time": "x" * 121},
        {"owner": "operations"},
        {"time": "fy2026", "project": "unsafe project"},
    ],
)
def test_material_scope_references_reject_malformed_objects(
    scope_refs: object,
) -> None:
    with pytest.raises(ValidationError):
        MaterialScopeReferences.model_validate(scope_refs)


def test_source_config_accepts_legacy_absence_and_rejects_explicit_null_scope_refs(
    source_config_factory,
) -> None:
    legacy = source_config_factory()

    assert legacy.scope_refs is None
    with pytest.raises(ValidationError):
        SourceConfig.model_validate(
            {
                **legacy.model_dump(mode="json", exclude={"scope_refs"}),
                "scope_refs": None,
            }
        )


def test_retrieval_budget_requires_at_least_one_field() -> None:
    with pytest.raises(ValueError):
        RetrievalBudget()


def test_context_request_accepts_exact_configured_field_name() -> None:
    request = ContextRequest(
        source_ref="google_sheets:vehicle_log_primary:Maintenance!A2:E2",
        context_mode="configured_field_values",
        field_name="Fuel (L)",
    )

    assert request.field_name == "Fuel (L)"


def test_context_request_accepts_direct_configured_field_values_source() -> None:
    request = ContextRequest(
        source_id="configured_measurements",
        context_mode="configured_field_values",
        field_name="Fuel (L)",
    )

    assert request.source_id == "configured_measurements"
    assert request.source_ref is None
    assert "source_ref" not in request.model_dump(mode="json")


@pytest.mark.parametrize(
    "context_mode",
    ["nearby_rows", "configured_worksheet", "upcoming_events"],
)
def test_context_request_preserves_source_ref_for_other_modes(
    context_mode: str,
) -> None:
    request = ContextRequest(
        source_ref="google_sheets:configured_measurements:Measurements!A2:D2",
        context_mode=context_mode,
    )

    assert request.source_ref is not None
    assert request.source_id is None
    assert "source_id" not in request.model_dump(mode="json")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "context_mode": "configured_field_values",
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "source_id": "vehicle_log_primary",
            "context_mode": "configured_field_values",
            "field_name": "Fuel (L)",
        },
        {
            "context_mode": "configured_field_values",
            "field_name": "Fuel (L)",
        },
        {
            "source_ref": None,
            "source_id": "vehicle_log_primary",
            "context_mode": "configured_field_values",
            "field_name": "Fuel (L)",
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "source_id": None,
            "context_mode": "configured_field_values",
            "field_name": "Fuel (L)",
        },
        {
            "source_id": "vehicle_log_primary",
            "context_mode": "configured_field_values",
            "field_name": None,
        },
        {
            "source_id": "invalid source",
            "context_mode": "configured_field_values",
            "field_name": "Fuel (L)",
        },
        {
            "source_id": "vehicle_log_primary",
            "context_mode": "nearby_rows",
        },
        {
            "source_id": "vehicle_log_primary",
            "context_mode": "configured_worksheet",
        },
        {
            "source_id": "vehicle_log_primary",
            "context_mode": "upcoming_events",
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "context_mode": "nearby_rows",
            "field_name": "Fuel (L)",
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "context_mode": "configured_worksheet",
            "field_name": "Fuel (L)",
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "context_mode": "nearby_rows",
            "field_name": None,
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "context_mode": "sheet_profile",
            "field_name": "Fuel (L)",
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "context_mode": "configured_field_values",
            "field_name": "",
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "context_mode": "configured_field_values",
            "field_name": " Fuel (L)",
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "context_mode": "configured_field_values",
            "field_name": "Fuel (L) ",
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "context_mode": "configured_field_values",
            "field_name": "Fuel\x00(L)",
        },
        {
            "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            "context_mode": "configured_field_values",
            "field_name": "x" * 121,
        },
    ],
)
def test_context_request_rejects_invalid_field_name_contract(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ContextRequest.model_validate(payload)


def test_structured_field_values_are_typed_bounded_and_ordered() -> None:
    structured_data = StructuredFieldValues(
        kind="field_values",
        field_name="Fuel (L)",
        record_count=4,
        non_empty_value_count=3,
        values=["42.1", None, "38.7", "42.1"],
    )

    assert structured_data.model_dump(mode="json") == {
        "kind": "field_values",
        "field_name": "Fuel (L)",
        "record_count": 4,
        "non_empty_value_count": 3,
        "values": ["42.1", None, "38.7", "42.1"],
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"record_count": 1},
        {"non_empty_value_count": 2},
        {"values": ["1"] * 251, "record_count": 251, "non_empty_value_count": 251},
        {"values": [1], "record_count": 1, "non_empty_value_count": 1},
        {"kind": "rows"},
    ],
)
def test_structured_field_values_reject_incoherent_or_unbounded_data(
    updates: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "kind": "field_values",
        "field_name": "Fuel (L)",
        "record_count": 2,
        "non_empty_value_count": 1,
        "values": ["42.1", None],
    }
    payload.update(updates)

    with pytest.raises(ValidationError):
        StructuredFieldValues.model_validate(payload)


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
    assert "structured_data" not in dumped


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
