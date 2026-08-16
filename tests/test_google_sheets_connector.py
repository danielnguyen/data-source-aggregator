from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.connectors import base as connector_base
from app.connectors.base import StubConnector, get_connector
from app.connectors.google_sheets import (
    CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
    CONFIGURED_WORKSHEET_CONTEXT_DESCRIPTION,
    CONFIGURED_WORKSHEET_CONTEXT_MODE,
    NEARBY_ROWS_CONTEXT_DESCRIPTION,
    NEARBY_ROWS_CONTEXT_MODE,
    GoogleSheetsClient,
    GoogleSheetsConnector,
    parse_google_sheets_source_ref,
)
from app.connectors.ics_calendar import IcsCalendarConnector
from app.credentials import CredentialConfig, CredentialRegistry, CredentialType
from app.errors import ServiceError
from app.models import ContextRequest, FetchRequest, SearchRequest


class FakeGoogleSheetsClient(GoogleSheetsClient):
    def __init__(self, values_by_range: dict[str, list[list[str]]]) -> None:
        self.values_by_range = values_by_range
        self.calls: list[tuple[str, str]] = []

    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        assert spreadsheet_id == "sheet-id"
        self.calls.append((spreadsheet_id, range_name))
        return self.values_by_range[range_name]


class FailingGoogleSheetsClient(GoogleSheetsClient):
    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        raise self.error


class FakeGoogleHttpError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.resp = type("Resp", (), {"status": status_code})()


@pytest.fixture
def google_sheets_source_config(source_config_factory):
    return source_config_factory(
        source_id="vehicle_log_primary",
        display_name="Vehicle Log - Primary",
        description="Personal vehicle operating records.",
        domain_tags=["vehicle", "maintenance"],
        connector_config={
            "spreadsheet_id": "sheet-id",
            "worksheet": "Maintenance",
            "header_row": 1,
            "credentials_ref": "google_sheets_readonly",
        },
        result_text={
            "title_from": "Task",
            "include_fields": ["Task", "Date", "Odometer", "Notes"],
        },
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "max_context_rows": 5,
            "allow_full_fetch": True,
        },
    )


@pytest.fixture
def fake_sheet_values():
    rows = [
        ["Date", "Odometer", "Category", "Task", "Notes"],
        [
            "2026-05-12",
            "123456",
            "Electrical",
            "Battery replacement",
            "Replaced after slow crank",
        ],
        ["2026-05-01", "123200", "Oil", "Oil change", "Routine"],
        [
            "2026-05-15",
            "123560",
            "Oil",
            "Transfer case service",
            "Engine oil and transfer case fluid service",
        ],
        [
            "2026-05-20",
            "123700",
            "Inspection",
            "Vehicle inspection",
            "Vehicle inspection and tire rotation",
        ],
        [
            "2026-05-14",
            "123500",
            "Electrical",
            "Battery terminal clean",
            "Cleaned corrosion",
        ],
    ]
    return {
        "Maintenance": rows,
        "Maintenance!A1:A1": [rows[0]],
    }


def test_get_connector_returns_real_google_sheets_connector() -> None:
    connector = get_connector("google_sheets")

    assert isinstance(connector, GoogleSheetsConnector)


def test_ics_calendar_returns_real_connector() -> None:
    connector = get_connector("ics_calendar")

    assert isinstance(connector, IcsCalendarConnector)


@pytest.mark.anyio
async def test_google_sheets_health_returns_ready_for_readable_sheet(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    client = FakeGoogleSheetsClient(fake_sheet_values)
    connector = GoogleSheetsConnector(client_factory=lambda _: client)

    health = await connector.check_health(google_sheets_source_config)

    assert health.status.value == "ready"
    assert health.last_error is None
    assert client.calls == [("sheet-id", "Maintenance!A1:A1")]


@pytest.mark.anyio
async def test_google_sheets_health_returns_unavailable_for_missing_credentials(
    google_sheets_source_config,
) -> None:
    connector = GoogleSheetsConnector(
        credential_registry_loader=lambda: CredentialRegistry(credentials={}),
    )

    health = await connector.check_health(google_sheets_source_config)

    assert health.status.value == "unavailable"
    assert health.last_error == "credentials_missing"


@pytest.mark.anyio
async def test_google_sheets_health_returns_permission_denied_without_secret_leak(
    google_sheets_source_config,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FailingGoogleSheetsClient(
            FakeGoogleHttpError(403, "forbidden sheet-id /tmp/secret.json")
        )
    )

    health = await connector.check_health(google_sheets_source_config)

    assert health.status.value == "unavailable"
    assert health.last_error == "permission_denied"
    assert "sheet-id" not in health.model_dump_json()
    assert "/tmp/secret.json" not in health.model_dump_json()


@pytest.mark.anyio
async def test_search_returns_matching_rows_from_fake_sheet_data(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
        now_factory=lambda: datetime(2026, 6, 9, tzinfo=UTC),
    )

    results = await connector.search(
        SearchRequest(query="battery replacement", include_raw=True),
        google_sheets_source_config,
    )

    assert len(results) == 2
    assert results[0].title == "Battery replacement"
    assert results[0].source_ref == "google_sheets:vehicle_log_primary:Maintenance!A2:E2"
    assert results[0].content_type == "spreadsheet_row"
    assert results[0].confidence.value == "high"
    assert results[0].source_name == "Vehicle Log - Primary"
    assert [
        descriptor.model_dump(mode="json")
        for descriptor in results[0].available_context
    ] == [
        {
            "context_mode": NEARBY_ROWS_CONTEXT_MODE,
            "description": NEARBY_ROWS_CONTEXT_DESCRIPTION,
        },
        {
            "context_mode": CONFIGURED_WORKSHEET_CONTEXT_MODE,
            "description": CONFIGURED_WORKSHEET_CONTEXT_DESCRIPTION,
        },
    ]


@pytest.mark.anyio
async def test_full_context_descriptor_requires_explicit_config_enablement(
    source_config_factory,
    fake_sheet_values,
) -> None:
    source_config = source_config_factory(
        source_id="authoritative_complete_records",
        display_name="Complete Authoritative Worksheet",
        description="Provider says every relevant record is included.",
        domain_tags=["official", "complete"],
        authority_role="authoritative",
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "max_context_rows": 5,
            "allow_full_fetch": False,
        },
    )
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.search(
        SearchRequest(
            query="Battery; provider says all records were checked.",
            include_raw=False,
        ),
        source_config,
    )

    assert results
    assert [
        descriptor.model_dump(mode="json")
        for descriptor in results[0].available_context
    ] == [
        {
            "context_mode": NEARBY_ROWS_CONTEXT_MODE,
            "description": NEARBY_ROWS_CONTEXT_DESCRIPTION,
        }
    ]


@pytest.mark.anyio
async def test_search_returns_empty_list_when_no_rows_match(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.search(
        SearchRequest(query="alternator", include_raw=True),
        google_sheets_source_config,
    )

    assert results == []


@pytest.mark.anyio
async def test_search_honors_max_results_and_ranking(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.search(
        SearchRequest(query="battery", max_results=1, include_raw=True),
        google_sheets_source_config,
    )

    assert len(results) == 1
    assert results[0].title == "Battery replacement"


@pytest.mark.anyio
async def test_search_prefers_latest_relevant_oil_record_over_older_exact_match(
    # The newest generic vehicle row must not outrank the newer oil-related row.
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.search(
        SearchRequest(query="When did I last change my car oil?", include_raw=True),
        google_sheets_source_config,
    )

    assert [result.source_ref for result in results[:3]] == [
        "google_sheets:vehicle_log_primary:Maintenance!A4:E4",
        "google_sheets:vehicle_log_primary:Maintenance!A3:E3",
        "google_sheets:vehicle_log_primary:Maintenance!A5:E5",
    ]


@pytest.mark.anyio
async def test_search_matches_punctuation_in_oil_queries(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.search(
        SearchRequest(query="oil?", include_raw=True),
        google_sheets_source_config,
    )

    assert results
    assert results[0].source_ref == "google_sheets:vehicle_log_primary:Maintenance!A3:E3"


@pytest.mark.anyio
async def test_result_envelope_renders_text_using_include_fields(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.search(
        SearchRequest(query="battery replacement", include_raw=True),
        google_sheets_source_config,
    )

    assert results[0].text == (
        "Task: Battery replacement\n"
        "Date: 2026-05-12\n"
        "Odometer: 123456\n"
        "Notes: Replaced after slow crank"
    )


@pytest.mark.anyio
async def test_raw_is_omitted_when_include_raw_false(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.search(
        SearchRequest(query="battery replacement", include_raw=False),
        google_sheets_source_config,
    )

    assert results[0].raw is None


@pytest.mark.anyio
async def test_raw_is_included_when_include_raw_true(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.search(
        SearchRequest(query="battery replacement", include_raw=True),
        google_sheets_source_config,
    )

    assert results[0].raw is not None
    assert results[0].raw["row_number"] == 2
    assert "spreadsheet_id" not in results[0].raw
    assert "sheet-id" not in str(results[0].raw)


@pytest.mark.anyio
async def test_fetch_by_valid_row_source_ref_returns_expected_row(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.fetch(
        FetchRequest(
            source_ref="google_sheets:vehicle_log_primary:Maintenance!A2:E2",
            include_raw=True,
            budget={"max_bytes": 100000},
        ),
        google_sheets_source_config,
    )

    assert len(results) == 1
    assert results[0].title == "Battery replacement"
    assert results[0].source_name == "Vehicle Log - Primary"


@pytest.mark.anyio
async def test_fetch_by_valid_range_source_ref_hides_spreadsheet_id(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.fetch(
        FetchRequest(
            source_ref="google_sheets:vehicle_log_primary:Maintenance!A2:E3",
            include_raw=True,
            budget={"max_bytes": 100000},
        ),
        google_sheets_source_config,
    )

    assert len(results) == 1
    assert results[0].source_name == "Vehicle Log - Primary"
    assert results[0].raw is not None
    assert "spreadsheet_id" not in results[0].raw
    assert "sheet-id" not in str(results[0].raw)


@pytest.mark.anyio
async def test_fetch_by_malformed_source_ref_returns_invalid_source_ref(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    with pytest.raises(ServiceError, match="invalid"):
        await connector.fetch(
            FetchRequest(
                source_ref="google_sheets:vehicle_log_primary:not-a-range",
                include_raw=True,
                budget={"max_bytes": 100000},
            ),
            google_sheets_source_config,
        )


@pytest.mark.anyio
def test_parse_google_sheets_source_ref_rejects_bad_locator() -> None:
    with pytest.raises(ServiceError, match="invalid"):
        parse_google_sheets_source_ref("google_sheets:vehicle_log_primary:not-a-range")


@pytest.mark.anyio
async def test_fetch_rejects_mismatched_source_id(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    with pytest.raises(ServiceError) as error_info:
        await connector.fetch(
            FetchRequest(
                source_ref="google_sheets:other_source:Maintenance!A2:E2",
                include_raw=True,
                budget={"max_bytes": 100000},
            ),
            google_sheets_source_config,
        )

    assert error_info.value.code == "invalid_source_ref"


@pytest.mark.anyio
async def test_context_nearby_rows_returns_neighboring_rows(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.context(
        ContextRequest(
            source_ref="google_sheets:vehicle_log_primary:Maintenance!A3:E3",
            context_mode="nearby_rows",
            budget={"max_rows": 3, "max_bytes": 100000},
        ),
        google_sheets_source_config,
    )

    assert [result.title for result in results] == [
        "Battery replacement",
        "Oil change",
        "Transfer case service",
    ]
    assert all(result.source_name == "Vehicle Log - Primary" for result in results)


@pytest.mark.anyio
@pytest.mark.parametrize("seed_row", [2, 4, 6])
async def test_configured_worksheet_returns_the_same_complete_ordered_range(
    google_sheets_source_config,
    fake_sheet_values,
    seed_row: int,
) -> None:
    client = FakeGoogleSheetsClient(fake_sheet_values)
    connector = GoogleSheetsConnector(
        client_factory=lambda _: client,
        now_factory=lambda: datetime(2026, 6, 10, tzinfo=UTC),
    )

    results = await connector.context(
        ContextRequest(
            source_ref=(
                "google_sheets:vehicle_log_primary:"
                f"Maintenance!A{seed_row}:E{seed_row}"
            ),
            context_mode=CONFIGURED_WORKSHEET_CONTEXT_MODE,
            budget={
                "max_rows": 5,
                "max_bytes": 100000,
                "max_text_chars": 40000,
            },
        ),
        google_sheets_source_config,
    )

    assert len(results) == 1
    result = results[0]
    assert result.source_ref == (
        "google_sheets:vehicle_log_primary:Maintenance!A2:E6"
    )
    assert result.content_type == "spreadsheet_range"
    assert result.title == "Maintenance rows 2-6"
    assert result.raw is None
    assert "structured_data" not in result.model_dump(mode="json")
    assert result.available_context == []
    assert [
        result.text.index(title)
        for title in (
            "Battery replacement",
            "Oil change",
            "Transfer case service",
            "Vehicle inspection",
            "Battery terminal clean",
        )
    ] == sorted(
        result.text.index(title)
        for title in (
            "Battery replacement",
            "Oil change",
            "Transfer case service",
            "Vehicle inspection",
            "Battery terminal clean",
        )
    )
    assert "sheet-id" not in result.model_dump_json()
    assert "google_sheets_readonly" not in result.model_dump_json()
    assert client.calls == [("sheet-id", "Maintenance")]


@pytest.mark.anyio
@pytest.mark.parametrize("seed_row", [2, 3, 5])
async def test_configured_field_values_returns_complete_ordered_projection(
    source_config_factory,
    seed_row: int,
) -> None:
    rows = [
        ["Date", "Task", "Fuel (L)", "Notes", "Private Column"],
        ["2026-05-01", "Fuel stop", "42.1", "north", "PRIVATE-ROW-A"],
        ["2026-05-02", "Fuel stop", "", "missing", "PRIVATE-ROW-B"],
        ["2026-05-03", "Fuel stop", "38.7", "south", "PRIVATE-ROW-C"],
        ["2026-05-04", "Fuel stop", "42.1", "repeat", "PRIVATE-ROW-D"],
    ]
    source_config = source_config_factory(
        result_text={
            "title_from": "Task",
            "include_fields": ["Date", "Task", "Fuel (L)", "Notes"],
        },
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "max_context_rows": 10,
            "allow_full_fetch": True,
        },
    )
    client = FakeGoogleSheetsClient({"Maintenance": rows})
    connector = GoogleSheetsConnector(
        client_factory=lambda _: client,
        now_factory=lambda: datetime(2026, 6, 10, tzinfo=UTC),
    )

    results = await connector.context(
        ContextRequest(
            source_ref=(
                "google_sheets:vehicle_log_primary:"
                f"Maintenance!A{seed_row}:E{seed_row}"
            ),
            context_mode=CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
            field_name="Fuel (L)",
            budget={"max_rows": 10, "max_bytes": 100000},
        ),
        source_config,
    )

    assert len(results) == 1
    result = results[0]
    assert result.source_ref == (
        "google_sheets:vehicle_log_primary:Maintenance!A2:E5"
    )
    assert result.content_type == "structured_field_values"
    assert result.title == "Configured values for Fuel (L)"
    assert result.text == "Retrieved 4 configured records for the requested field."
    assert result.raw is None
    assert result.available_context == []
    assert result.structured_data is not None
    assert result.structured_data.model_dump(mode="json") == {
        "kind": "field_values",
        "field_name": "Fuel (L)",
        "record_count": 4,
        "non_empty_value_count": 3,
        "values": ["42.1", None, "38.7", "42.1"],
    }
    serialized = result.model_dump_json()
    assert "PRIVATE-ROW" not in serialized
    assert "north" not in serialized
    assert "42.1" not in result.text
    assert client.calls == [("sheet-id", "Maintenance")]


@pytest.mark.anyio
async def test_configured_field_values_supports_direct_exact_source_without_search(
    source_config_factory,
) -> None:
    rows = [
        ["Entry", "Reading"],
        ["alpha", "10.125"],
        ["beta", "20.25"],
        ["gamma", ""],
    ]
    source_config = source_config_factory(
        source_id="metrics_archive",
        connector_config={
            "spreadsheet_id": "sheet-id",
            "worksheet": "Measurements",
            "header_row": 1,
            "credentials_ref": "google_sheets_readonly",
        },
        result_text={
            "title_from": "Entry",
            "include_fields": ["Entry", "Reading"],
        },
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "max_context_rows": 10,
            "allow_full_fetch": True,
        },
    )
    client = FakeGoogleSheetsClient({"Measurements": rows})
    connector = GoogleSheetsConnector(client_factory=lambda _: client)

    results = await connector.context(
        ContextRequest(
            source_id="metrics_archive",
            context_mode=CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
            field_name="Reading",
            budget={"max_rows": 10, "max_bytes": 100000},
        ),
        source_config,
    )

    assert len(results) == 1
    result = results[0]
    assert result.source_id == "metrics_archive"
    assert result.source_ref == (
        "google_sheets:metrics_archive:Measurements!A2:B4"
    )
    assert result.raw is None
    assert result.available_context == []
    assert result.structured_data is not None
    assert result.structured_data.model_dump(mode="json") == {
        "kind": "field_values",
        "field_name": "Reading",
        "record_count": 3,
        "non_empty_value_count": 2,
        "values": ["10.125", "20.25", None],
    }
    assert client.calls == [("sheet-id", "Measurements")]


@pytest.mark.anyio
async def test_direct_configured_field_values_rejects_source_config_mismatch(
    source_config_factory,
    fake_sheet_values,
) -> None:
    source_config = source_config_factory(source_id="source_b")
    client = FakeGoogleSheetsClient(fake_sheet_values)
    connector = GoogleSheetsConnector(client_factory=lambda _: client)

    with pytest.raises(ServiceError) as error_info:
        await connector.context(
            ContextRequest(
                source_id="source_a",
                context_mode=CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
                field_name="Task",
            ),
            source_config,
        )

    assert error_info.value.code == "invalid_source_ref"
    assert client.calls == []


@pytest.mark.anyio
async def test_direct_and_legacy_configured_field_values_are_equivalent(
    source_config_factory,
) -> None:
    rows = [
        ["Entry", "Reading"],
        ["alpha", "10.125"],
        ["beta", ""],
    ]
    source_config = source_config_factory(
        source_id="metrics_archive",
        connector_config={
            "spreadsheet_id": "sheet-id",
            "worksheet": "Measurements",
            "header_row": 1,
            "credentials_ref": "google_sheets_readonly",
        },
        result_text={"include_fields": ["Entry", "Reading"]},
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "max_context_rows": 10,
            "allow_full_fetch": True,
        },
    )
    client = FakeGoogleSheetsClient({"Measurements": rows})
    connector = GoogleSheetsConnector(client_factory=lambda _: client)

    direct = await connector.context(
        ContextRequest(
            source_id="metrics_archive",
            context_mode=CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
            field_name="Reading",
        ),
        source_config,
    )
    legacy = await connector.context(
        ContextRequest(
            source_ref="google_sheets:metrics_archive:Measurements!A2:B2",
            context_mode=CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
            field_name="Reading",
        ),
        source_config,
    )

    assert direct[0].source_id == legacy[0].source_id
    assert direct[0].source_ref == legacy[0].source_ref
    assert direct[0].content_type == legacy[0].content_type
    assert direct[0].structured_data == legacy[0].structured_data
    assert client.calls == [
        ("sheet-id", "Measurements"),
        ("sheet-id", "Measurements"),
    ]


@pytest.mark.anyio
async def test_direct_configured_field_values_preserves_empty_source_semantics(
    source_config_factory,
) -> None:
    source_config = source_config_factory(
        source_id="metrics_archive",
        connector_config={
            "spreadsheet_id": "sheet-id",
            "worksheet": "Measurements",
            "header_row": 1,
            "credentials_ref": "google_sheets_readonly",
        },
        result_text={"include_fields": ["Entry", "Reading"]},
    )
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(
            {"Measurements": [["Entry", "Reading"]]}
        )
    )

    results = await connector.context(
        ContextRequest(
            source_id="metrics_archive",
            context_mode=CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
            field_name="Reading",
        ),
        source_config,
    )

    assert results == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field_name", "result_text"),
    [
        ("fuel (l)", {"include_fields": ["Fuel (L)"]}),
        ("Fuel", {"include_fields": ["Fuel (L)"]}),
        ("Category", {"include_fields": ["Fuel (L)"]}),
        ("Fuel (L)", None),
        ("Fuel (L)", {}),
        ("Fuel (L)", {"include_fields": None}),
        ("Fuel (L)", {"include_fields": ["Fuel (L)", 7]}),
    ],
    ids=[
        "case-mismatch",
        "partial-name",
        "unconfigured-header",
        "missing-result-text",
        "missing-include-fields",
        "null-include-fields",
        "malformed-include-fields",
    ],
)
async def test_configured_field_values_requires_exact_configured_field(
    source_config_factory,
    fake_sheet_values,
    field_name: str,
    result_text: object,
) -> None:
    source_config = source_config_factory(result_text=result_text)
    client = FakeGoogleSheetsClient(fake_sheet_values)
    connector = GoogleSheetsConnector(client_factory=lambda _: client)

    with pytest.raises(ServiceError) as error_info:
        await connector.context(
            ContextRequest(
                source_ref=(
                    "google_sheets:vehicle_log_primary:Maintenance!A2:E2"
                ),
                context_mode=CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
                field_name=field_name,
                budget={"max_rows": 10, "max_bytes": 100000},
            ),
            source_config,
        )

    assert error_info.value.code == "unsupported_operation"
    assert error_info.value.details == {
        "context_mode": CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
        "operation": "context",
    }
    assert client.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("record_count", "configured_limit", "requested_limit", "succeeds"),
    [
        (5, 5, 10, True),
        (6, 5, 10, False),
        (6, 10, 5, False),
        (251, 300, 300, False),
    ],
    ids=["exact-limit", "configured-limit", "request-limit", "absolute-limit"],
)
async def test_configured_field_values_enforces_complete_record_limits(
    source_config_factory,
    record_count: int,
    configured_limit: int,
    requested_limit: int,
    succeeds: bool,
) -> None:
    rows = [["Task", "Fuel (L)"]] + [
        [f"Record {index}", str(index)] for index in range(record_count)
    ]
    source_config = source_config_factory(
        result_text={"include_fields": ["Task", "Fuel (L)"]},
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 1000000,
            "max_text_chars": 40000,
            "max_context_rows": configured_limit,
            "allow_full_fetch": True,
        },
    )
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient({"Maintenance": rows}),
    )
    request = ContextRequest(
        source_id="vehicle_log_primary",
        context_mode=CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
        field_name="Fuel (L)",
        budget={"max_rows": requested_limit, "max_bytes": 1000000},
    )

    if succeeds:
        results = await connector.context(request, source_config)
        assert results[0].structured_data is not None
        assert results[0].structured_data.record_count == record_count
        return

    with pytest.raises(ServiceError) as error_info:
        await connector.context(request, source_config)
    assert error_info.value.code == "result_too_large"
    assert error_info.value.status_code == 413
    assert error_info.value.details == {
        "max_rows": min(configured_limit, requested_limit, 250)
    }


@pytest.mark.anyio
async def test_configured_field_values_requires_full_fetch_permission(
    source_config_factory,
    fake_sheet_values,
) -> None:
    source_config = source_config_factory(
        result_text={"include_fields": ["Task"]},
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "max_context_rows": 20,
            "allow_full_fetch": False,
        },
    )
    client = FakeGoogleSheetsClient(fake_sheet_values)
    connector = GoogleSheetsConnector(client_factory=lambda _: client)

    with pytest.raises(ServiceError) as error_info:
        await connector.context(
            ContextRequest(
                source_ref=(
                    "google_sheets:vehicle_log_primary:Maintenance!A2:E2"
                ),
                context_mode=CONFIGURED_FIELD_VALUES_CONTEXT_MODE,
                field_name="Task",
                budget={"max_rows": 20, "max_bytes": 100000},
            ),
            source_config,
        )

    assert error_info.value.code == "unsupported_operation"
    assert client.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("configured_limit", "requested_limit"),
    [(5, 10), (10, 5)],
)
async def test_configured_worksheet_accepts_an_exact_effective_row_ceiling(
    source_config_factory,
    fake_sheet_values,
    configured_limit: int,
    requested_limit: int,
) -> None:
    source_config = source_config_factory(
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "max_context_rows": configured_limit,
            "allow_full_fetch": True,
        },
    )
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.context(
        ContextRequest(
            source_ref=(
                "google_sheets:vehicle_log_primary:Maintenance!A4:E4"
            ),
            context_mode=CONFIGURED_WORKSHEET_CONTEXT_MODE,
            budget={
                "max_rows": requested_limit,
                "max_bytes": 100000,
                "max_text_chars": 40000,
            },
        ),
        source_config,
    )

    assert len(results) == 1
    assert results[0].source_ref.endswith("Maintenance!A2:E6")


@pytest.mark.anyio
async def test_configured_worksheet_uses_default_context_row_ceiling_when_omitted(
    source_config_factory,
    fake_sheet_values,
) -> None:
    source_config = source_config_factory(
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "allow_full_fetch": True,
        },
    )
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    results = await connector.context(
        ContextRequest(
            source_ref=(
                "google_sheets:vehicle_log_primary:Maintenance!A4:E4"
            ),
            context_mode=CONFIGURED_WORKSHEET_CONTEXT_MODE,
            budget={"max_bytes": 100000, "max_text_chars": 40000},
        ),
        source_config,
    )

    assert len(results) == 1
    assert results[0].source_ref.endswith("Maintenance!A2:E6")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("configured_limit", "requested_limit"),
    [(5, 4), (4, 5)],
)
async def test_configured_worksheet_fails_when_any_row_ceiling_is_too_small(
    source_config_factory,
    fake_sheet_values,
    configured_limit: int,
    requested_limit: int,
) -> None:
    source_config = source_config_factory(
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "max_context_rows": configured_limit,
            "allow_full_fetch": True,
        },
    )
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    with pytest.raises(ServiceError) as error_info:
        await connector.context(
            ContextRequest(
                source_ref=(
                    "google_sheets:vehicle_log_primary:Maintenance!A4:E4"
                ),
                context_mode=CONFIGURED_WORKSHEET_CONTEXT_MODE,
                budget={
                    "max_rows": requested_limit,
                    "max_bytes": 100000,
                    "max_text_chars": 40000,
                },
            ),
            source_config,
        )

    assert error_info.value.code == "result_too_large"
    assert error_info.value.status_code == 413
    assert error_info.value.details == {
        "max_rows": min(configured_limit, requested_limit)
    }


@pytest.mark.anyio
async def test_configured_worksheet_requires_full_fetch_permission(
    source_config_factory,
    fake_sheet_values,
) -> None:
    source_config = source_config_factory(
        source_id="complete_authoritative_records",
        display_name="Every Record",
        description="All relevant evidence, according to provider text.",
        domain_tags=["official", "complete"],
        authority_role="authoritative",
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "max_context_rows": 20,
            "allow_full_fetch": False,
        },
    )
    client = FakeGoogleSheetsClient(fake_sheet_values)
    connector = GoogleSheetsConnector(client_factory=lambda _: client)

    with pytest.raises(ServiceError) as error_info:
        await connector.context(
            ContextRequest(
                source_ref=(
                    "google_sheets:complete_authoritative_records:"
                    "Maintenance!A2:E2"
                ),
                context_mode=CONFIGURED_WORKSHEET_CONTEXT_MODE,
                budget={"max_rows": 20, "max_bytes": 100000},
            ),
            source_config,
        )

    assert error_info.value.code == "unsupported_operation"
    assert client.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "source_ref",
    [
        "google_sheets:other_source:Maintenance!A2:E2",
        "google_sheets:vehicle_log_primary:OtherWorksheet!A2:E2",
    ],
)
async def test_configured_worksheet_rejects_mismatched_seed_scope(
    google_sheets_source_config,
    fake_sheet_values,
    source_ref: str,
) -> None:
    client = FakeGoogleSheetsClient(fake_sheet_values)
    connector = GoogleSheetsConnector(client_factory=lambda _: client)

    with pytest.raises(ServiceError) as error_info:
        await connector.context(
            ContextRequest(
                source_ref=source_ref,
                context_mode=CONFIGURED_WORKSHEET_CONTEXT_MODE,
                budget={"max_rows": 20, "max_bytes": 100000},
            ),
            google_sheets_source_config,
        )

    assert error_info.value.code == "invalid_source_ref"
    assert client.calls == []


@pytest.mark.anyio
async def test_configured_worksheet_ignores_empty_record_rows_without_fabrication(
    google_sheets_source_config,
) -> None:
    rows = [
        ["Date", "Odometer", "Category", "Task", "Notes"],
        [],
        ["", "", "", "", ""],
    ]
    client = FakeGoogleSheetsClient(
        {
            "Maintenance": rows,
            "Maintenance!A1:A1": [rows[0]],
        }
    )
    connector = GoogleSheetsConnector(client_factory=lambda _: client)

    results = await connector.context(
        ContextRequest(
            source_ref=(
                "google_sheets:vehicle_log_primary:Maintenance!A2:E2"
            ),
            context_mode=CONFIGURED_WORKSHEET_CONTEXT_MODE,
            budget={"max_rows": 5, "max_bytes": 100000},
        ),
        google_sheets_source_config,
    )

    assert results == []


@pytest.mark.anyio
async def test_context_rejects_mismatched_source_id(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    with pytest.raises(ServiceError) as error_info:
        await connector.context(
            ContextRequest(
                source_ref="google_sheets:other_source:Maintenance!A3:E3",
                context_mode="nearby_rows",
                budget={"max_rows": 3, "max_bytes": 100000},
            ),
            google_sheets_source_config,
        )

    assert error_info.value.code == "invalid_source_ref"


@pytest.mark.anyio
async def test_context_rejects_unknown_mode(
    google_sheets_source_config,
    fake_sheet_values,
) -> None:
    connector = GoogleSheetsConnector(
        client_factory=lambda _: FakeGoogleSheetsClient(fake_sheet_values),
    )

    with pytest.raises(ServiceError, match="not supported"):
        await connector.context(
            ContextRequest(
                source_ref="google_sheets:vehicle_log_primary:Maintenance!A3:E3",
                context_mode="sheet_profile",
                budget={"max_rows": 3, "max_bytes": 100000},
            ),
            google_sheets_source_config,
        )


def test_connector_factory_map_can_be_overridden(monkeypatch) -> None:
    fake_connector = StubConnector("google_sheets")
    monkeypatch.setitem(
        connector_base.CONNECTOR_FACTORIES,
        "google_sheets",
        lambda: fake_connector,
    )

    connector = get_connector("google_sheets")

    assert connector is fake_connector


@pytest.mark.anyio
async def test_missing_credential_does_not_expose_credential_ref(
    google_sheets_source_config,
) -> None:
    connector = GoogleSheetsConnector(
        credential_registry_loader=lambda: CredentialRegistry(credentials={}),
    )

    with pytest.raises(ServiceError) as error_info:
        await connector.search(
            SearchRequest(query="battery", include_raw=True),
            google_sheets_source_config,
        )

    error_payload = {
        "code": error_info.value.code,
        "message": error_info.value.message,
        "details": error_info.value.details,
    }

    assert "google_sheets_readonly" not in str(error_payload)
    assert "credential_ref" not in str(error_payload)


@pytest.mark.anyio
async def test_unsupported_credential_type_does_not_expose_credential_ref(
    google_sheets_source_config,
) -> None:
    connector = GoogleSheetsConnector(
        credential_registry_loader=lambda: CredentialRegistry(
            credentials={
                "google_sheets_readonly": CredentialConfig(
                    type=CredentialType.TOKEN_FILE,
                    path="secrets/github_readonly_token",
                )
            }
        ),
    )

    with pytest.raises(ServiceError) as error_info:
        await connector.search(
            SearchRequest(query="battery", include_raw=True),
            google_sheets_source_config,
        )

    error_payload = {
        "code": error_info.value.code,
        "message": error_info.value.message,
        "details": error_info.value.details,
    }

    assert "google_sheets_readonly" not in str(error_payload)
    assert "credential_ref" not in str(error_payload)


@pytest.mark.anyio
async def test_credential_initialization_failure_does_not_expose_credential_ref(
    google_sheets_source_config,
    monkeypatch,
) -> None:
    connector = GoogleSheetsConnector(
        credential_registry_loader=lambda: CredentialRegistry(
            credentials={
                "google_sheets_readonly": CredentialConfig(
                    type=CredentialType.GOOGLE_SERVICE_ACCOUNT_FILE,
                    path="/tmp/missing-service-account.json",
                )
            }
        ),
    )

    class FakeServiceAccountCredentials:
        @staticmethod
        def from_service_account_file(path: str, scopes: list[str]):
            raise RuntimeError(f"failed to initialize {path}")

    def fake_google_auth_default(*, scopes):
        return object(), None

    def fake_build(*args, **kwargs):
        return object()

    monkeypatch.setitem(
        __import__("sys").modules,
        "google.oauth2.service_account",
        type("service_account", (), {"Credentials": FakeServiceAccountCredentials}),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "google.auth",
        type("google_auth", (), {"default": fake_google_auth_default}),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "googleapiclient.discovery",
        type("googleapiclient_discovery", (), {"build": fake_build}),
    )

    with pytest.raises(ServiceError) as error_info:
        await connector.search(
            SearchRequest(query="battery", include_raw=True),
            google_sheets_source_config,
        )

    error_payload = {
        "code": error_info.value.code,
        "message": error_info.value.message,
        "details": error_info.value.details,
    }

    assert "google_sheets_readonly" not in str(error_payload)
    assert "credential_ref" not in str(error_payload)
