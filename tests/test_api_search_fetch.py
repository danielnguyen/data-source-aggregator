from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app import registry as registry_module
from app.connectors.google_sheets import GoogleSheetsClient, GoogleSheetsConnector
from app.errors import ServiceError
from app.main import create_app
from app.models import (
    Confidence,
    ContextRequest,
    FetchRequest,
    ResultEnvelope,
    SearchRequest,
    SourceConfig,
    SourceHealth,
    SourceStatus,
)
from app.services import context_pack as context_pack_service
from app.services import fetch as fetch_service
from app.services import search as search_service


def _write_source_config(source_dir: Path) -> None:
    (source_dir / "source.yaml").write_text(
        """
source_id: vehicle_log_primary
display_name: Vehicle Log - Primary
description: Personal vehicle operating records.
domain_tags: [vehicle, maintenance]
connector: google_sheets
enabled: true
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: sheet-secret-id
  worksheet: Maintenance
  header_row: 1
  credentials_ref: google_sheets_readonly
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  max_context_rows: 250
  allow_full_fetch: true
""",
        encoding="utf-8",
    )


def _write_ics_source_config(source_dir: Path) -> None:
    (source_dir / "calendar.yaml").write_text(
        """
source_id: calendar_sports
display_name: Sports Calendar
description: Family sports schedule.
domain_tags: [calendar, sports]
connector: ics_calendar
enabled: true
sensitivity: low
access_mode: read_only
connector_config:
  url: https://private.example.test/calendar.ics
  timezone: America/Toronto
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
""",
        encoding="utf-8",
    )


def _write_multi_source_configs(source_dir: Path, *, reverse_order: bool = False) -> None:
    sources = [
        (
            "01_vehicle.yaml",
            """
source_id: vehicle_service_log
display_name: Vehicle Service Log
description: >
  Combustion vehicle maintenance log with oil changes, repairs,
  and upcoming service reminders.
domain_tags: [vehicle, maintenance, repair, oil]
connector: google_sheets
enabled: true
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: sheet-vehicle
  worksheet: Maintenance
  header_row: 1
  credentials_ref: google_sheets_readonly
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  max_context_rows: 250
  allow_full_fetch: true
""",
        ),
        (
            "02_ev.yaml",
            """
source_id: electric_vehicle_history
display_name: Electric Vehicle Service History
description: Electric vehicle service, charging, and inspection history.
domain_tags: [vehicle, electric, ev, charging, service]
connector: google_sheets
enabled: true
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: sheet-ev
  worksheet: Maintenance
  header_row: 1
  credentials_ref: google_sheets_readonly
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  max_context_rows: 250
  allow_full_fetch: true
""",
        ),
        (
            "03_personal.yaml",
            """
source_id: personal_calendar_agenda
display_name: Personal Calendar Agenda
description: Personal calendar of appointments, schedule, and upcoming events.
domain_tags: [calendar, appointment, schedule]
connector: ics_calendar
enabled: true
sensitivity: low
access_mode: read_only
connector_config:
  url: https://example.test/personal.ics
  timezone: America/Toronto
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
""",
        ),
        (
            "04_family.yaml",
            """
source_id: family_calendar_agenda
display_name: Family Calendar Agenda
description: Family calendar with appointments and shared schedule items.
domain_tags: [calendar, family, appointment, schedule]
connector: ics_calendar
enabled: true
sensitivity: low
access_mode: read_only
connector_config:
  url: https://example.test/family.ics
  timezone: America/Toronto
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
""",
        ),
        (
            "05_holidays.yaml",
            """
source_id: public_holiday_calendar
display_name: National Summer Holiday Calendar
description: Public holiday and observance calendar with summer holiday dates.
domain_tags: [calendar, holiday, summer]
connector: ics_calendar
enabled: true
sensitivity: low
access_mode: read_only
connector_config:
  url: https://example.test/holidays.ics
  timezone: America/Toronto
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
""",
        ),
    ]

    for filename, content in (list(reversed(sources)) if reverse_order else sources):
        (source_dir / filename).write_text(content, encoding="utf-8")


def _write_credentials_config(tmp_path: Path, monkeypatch) -> None:
    credentials_path = tmp_path / "credentials.yaml"
    credentials_path.write_text(
        """
credentials:
  google_sheets_readonly:
    type: google_service_account_file
    path: secrets/google_sheets_readonly.json
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))


class FakeApiConnector:
    async def search(
        self,
        request: SearchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        return []

    async def fetch(
        self,
        request: FetchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        raise ServiceError(
            "unsupported_operation",
            "Connector does not support fetch in this test.",
            status_code=501,
            details={"connector": source_config.connector, "operation": "fetch"},
        )

    async def context(
        self,
        request: ContextRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        raise ServiceError(
            "unsupported_operation",
            "Connector does not support context in this test.",
            status_code=501,
            details={"connector": source_config.connector, "operation": "context"},
        )

    async def check_health(self, source_config: SourceConfig) -> SourceHealth:
        return SourceHealth(
            status=SourceStatus.READY,
            last_checked_at=datetime(2026, 6, 10, tzinfo=UTC),
        )


class FakeContextPackConnector:
    async def search(
        self,
        request: SearchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        assert request.include_raw is False
        if source_config.source_id == "vehicle_log_primary":
            return [
                ResultEnvelope(
                    result_id="r_vehicle_1",
                    source_type="google_sheets",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="google_sheets:vehicle_log_primary:'Form responses 1'!A13:I13",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="09/03/2026",
                    content_type="spreadsheet_row",
                    text=(
                        "Date: 09/03/2026\n"
                        "Kilometers: 83061\n"
                        "Comments/Repair Notes: Engine oil and transfer case service."
                    ),
                    confidence=Confidence.HIGH,
                    raw={
                        "spreadsheet_id": "sheet-secret-id",
                        "values_by_header": {"Comments/Repair Notes": "Engine oil"},
                    },
                    warnings=["maintenance_summary"],
                ),
                ResultEnvelope(
                    result_id="r_vehicle_2",
                    source_type="google_sheets",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="google_sheets:vehicle_log_primary:'Form responses 1'!A14:I14",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="09/20/2026",
                    content_type="spreadsheet_row",
                    text="Date: 09/20/2026\nComments/Repair Notes: Brake inspection.",
                    confidence=Confidence.MEDIUM,
                ),
            ]

        if source_config.source_id == "calendar_sports":
            return [
                ResultEnvelope(
                    result_id="r_calendar_1",
                    source_type="ics_calendar",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="ics_calendar:calendar_sports:event:sports-team-home-20261010",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    source_modified_at=datetime(2026, 6, 1, tzinfo=UTC),
                    title="Home Game vs Rivals",
                    content_type="calendar_event",
                    text=(
                        "Summary: Home Game vs Rivals\n"
                        "Start: 2026-10-10T19:00:00-04:00\n"
                        "Location: Main Arena"
                    ),
                    confidence=Confidence.HIGH,
                    raw={"url": "https://private.example.test/calendar.ics"},
                    warnings=["timezone_inferred"],
                )
            ]

        return []

    async def fetch(
        self,
        request: FetchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        raise ServiceError(
            "unsupported_operation",
            "Connector does not support fetch in this test.",
            status_code=501,
            details={"connector": source_config.connector, "operation": "fetch"},
        )

    async def context(
        self,
        request: ContextRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        raise ServiceError(
            "unsupported_operation",
            "Connector does not support context in this test.",
            status_code=501,
            details={"connector": source_config.connector, "operation": "context"},
        )

    async def check_health(self, source_config: SourceConfig) -> SourceHealth:
        return SourceHealth(
            status=SourceStatus.READY,
            last_checked_at=datetime(2026, 6, 10, tzinfo=UTC),
        )


class FakeMultiSourceContextPackConnector:
    async def search(
        self,
        request: SearchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        results_by_source = {
            "vehicle_service_log": [
                ResultEnvelope(
                    result_id="r_vehicle_oil_recent",
                    source_type="google_sheets",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="google_sheets:vehicle_service_log:Maintenance!A2:F2",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="09/03/2026",
                    content_type="spreadsheet_row",
                    text=(
                        "Date: 09/03/2026\n"
                        "Service Notes: Engine oil; transfer case fluid service completed."
                    ),
                    confidence=Confidence.HIGH,
                    record_date=datetime(2026, 3, 9, tzinfo=UTC).date(),
                ),
                ResultEnvelope(
                    result_id="r_vehicle_oil_older",
                    source_type="google_sheets",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="google_sheets:vehicle_service_log:Maintenance!A3:F3",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="14/08/2025",
                    content_type="spreadsheet_row",
                    text="Date: 14/08/2025\nService Notes: Completed oil change and filter.",
                    confidence=Confidence.HIGH,
                    record_date=datetime(2025, 8, 14, tzinfo=UTC).date(),
                ),
                ResultEnvelope(
                    result_id="r_vehicle_brake",
                    source_type="google_sheets",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="google_sheets:vehicle_service_log:Maintenance!A4:F4",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="20/04/2026",
                    content_type="spreadsheet_row",
                    text="Date: 20/04/2026\nService Notes: Brake inspection and tire rotation.",
                    confidence=Confidence.MEDIUM,
                    record_date=datetime(2026, 4, 20, tzinfo=UTC).date(),
                ),
            ],
            "electric_vehicle_history": [
                ResultEnvelope(
                    result_id="r_ev_service",
                    source_type="google_sheets",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="google_sheets:electric_vehicle_history:Maintenance!A2:F2",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="Electric vehicle service",
                    content_type="spreadsheet_row",
                    text=(
                        "Electric vehicle service history entry with traction battery coolant "
                        "inspection and charging system check on 2026-04-21."
                    ),
                    confidence=Confidence.HIGH,
                ),
            ],
            "personal_calendar_agenda": [
                ResultEnvelope(
                    result_id="r_personal_appointment",
                    source_type="ics_calendar",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="ics_calendar:personal_calendar_agenda:event:personal-dentist-20260625",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="Dentist appointment",
                    content_type="calendar_event",
                    text="Calendar appointment next week on 2026-06-25 for a dentist visit.",
                    confidence=Confidence.HIGH,
                ),
                ResultEnvelope(
                    result_id="r_personal_planning",
                    source_type="ics_calendar",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="ics_calendar:personal_calendar_agenda:event:planning-20260627",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="Planning session",
                    content_type="calendar_event",
                    text="Upcoming calendar planning session on 2026-06-27.",
                    confidence=Confidence.MEDIUM,
                ),
            ],
            "family_calendar_agenda": [
                ResultEnvelope(
                    result_id="r_family_appointment",
                    source_type="ics_calendar",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="ics_calendar:family_calendar_agenda:event:family-checkup-20260626",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="Family checkup",
                    content_type="calendar_event",
                    text="Shared family calendar appointment on 2026-06-26.",
                    confidence=Confidence.HIGH,
                ),
            ],
            "public_holiday_calendar": [
                ResultEnvelope(
                    result_id="r_holiday_1",
                    source_type="ics_calendar",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="ics_calendar:public_holiday_calendar:event:national-summer-holiday",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="National Summer Holiday",
                    content_type="calendar_event",
                    text="Public holiday on 2026-07-01.",
                    confidence=Confidence.HIGH,
                ),
                ResultEnvelope(
                    result_id="r_holiday_2",
                    source_type="ics_calendar",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="ics_calendar:public_holiday_calendar:event:harvest-observance",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="Harvest Observance",
                    content_type="calendar_event",
                    text="Public holiday observance on 2026-09-05.",
                    confidence=Confidence.MEDIUM,
                ),
                ResultEnvelope(
                    result_id="r_holiday_3",
                    source_type="ics_calendar",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="ics_calendar:public_holiday_calendar:event:winter-festival",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="Winter Festival",
                    content_type="calendar_event",
                    text="Seasonal holiday on 2026-12-20.",
                    confidence=Confidence.MEDIUM,
                ),
                ResultEnvelope(
                    result_id="r_holiday_4",
                    source_type="ics_calendar",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="ics_calendar:public_holiday_calendar:event:new-year-observance",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="New Year Observance",
                    content_type="calendar_event",
                    text="Public holiday on 2027-01-01.",
                    confidence=Confidence.MEDIUM,
                ),
                ResultEnvelope(
                    result_id="r_holiday_5",
                    source_type="ics_calendar",
                    source_id=source_config.source_id,
                    source_name=source_config.display_name,
                    source_ref="ics_calendar:public_holiday_calendar:event:spring-observance",
                    retrieved_at=datetime(2026, 6, 10, tzinfo=UTC),
                    title="Spring Observance",
                    content_type="calendar_event",
                    text="Public holiday on 2027-03-21.",
                    confidence=Confidence.MEDIUM,
                ),
            ],
        }
        return results_by_source.get(source_config.source_id, [])

    async def fetch(
        self,
        request: FetchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        raise ServiceError(
            "unsupported_operation",
            "Connector does not support fetch in this test.",
            status_code=501,
            details={"connector": source_config.connector, "operation": "fetch"},
        )

    async def context(
        self,
        request: ContextRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        raise ServiceError(
            "unsupported_operation",
            "Connector does not support context in this test.",
            status_code=501,
            details={"connector": source_config.connector, "operation": "context"},
        )

    async def check_health(self, source_config: SourceConfig) -> SourceHealth:
        return SourceHealth(
            status=SourceStatus.READY,
            last_checked_at=datetime(2026, 6, 10, tzinfo=UTC),
        )


class MultiSourceFakeGoogleSheetsClient(GoogleSheetsClient):
    def __init__(self, values_by_spreadsheet_id: dict[str, dict[str, list[list[str]]]]) -> None:
        self._values_by_spreadsheet_id = values_by_spreadsheet_id

    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        return self._values_by_spreadsheet_id[spreadsheet_id][range_name]


class EmptyCalendarConnector:
    async def search(
        self,
        request: SearchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        return []

    async def fetch(
        self,
        request: FetchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        return []

    async def context(
        self,
        request: ContextRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        return []

    async def check_health(self, source_config: SourceConfig) -> SourceHealth:
        return SourceHealth(
            status=SourceStatus.READY,
            last_checked_at=datetime(2026, 6, 10, tzinfo=UTC),
        )


@pytest.fixture
def fake_api_connector(monkeypatch):
    connector = FakeApiConnector()
    monkeypatch.setattr(search_service.connector_base, "get_connector", lambda _: connector)
    monkeypatch.setattr(fetch_service.connector_base, "get_connector", lambda _: connector)
    monkeypatch.setattr(registry_module, "get_connector", lambda _: connector)
    return connector


@pytest.fixture
def fake_context_pack_connector(monkeypatch):
    connector = FakeContextPackConnector()
    monkeypatch.setattr(search_service.connector_base, "get_connector", lambda _: connector)
    monkeypatch.setattr(context_pack_service.connector_base, "get_connector", lambda _: connector)
    monkeypatch.setattr(registry_module, "get_connector", lambda _: connector)
    return connector


@pytest.fixture
def fake_multi_source_context_pack_connector(monkeypatch):
    connector = FakeMultiSourceContextPackConnector()
    monkeypatch.setattr(search_service.connector_base, "get_connector", lambda _: connector)
    monkeypatch.setattr(context_pack_service.connector_base, "get_connector", lambda _: connector)
    monkeypatch.setattr(registry_module, "get_connector", lambda _: connector)
    return connector


@pytest.fixture
def real_multi_source_context_pack_connectors(monkeypatch):
    vehicle_rows = [
        ["Date", "Odometer", "Category", "Task", "Notes"],
        ["14/08/2025", "80000", "Oil", "Oil change", "Completed oil change and filter."],
        [
            "09/03/2026",
            "83061",
            "Oil",
            "Transfer case service",
            "Engine oil and transfer case fluid service completed.",
        ],
        [
            "20/04/2026",
            "84000",
            "Inspection",
            "Vehicle inspection",
            "Vehicle inspection and tire rotation.",
        ],
    ]
    ev_rows = [
        ["Date", "Odometer", "Category", "Task", "Notes"],
        [
            "21/04/2026",
            "15000",
            "Inspection",
            "Charging system inspection",
            "Traction battery coolant inspection and charging system check.",
        ],
    ]
    google_connector = GoogleSheetsConnector(
        client_factory=lambda source_config: MultiSourceFakeGoogleSheetsClient(
            {
                "sheet-vehicle": {
                    "Maintenance": vehicle_rows,
                    "Maintenance!A1:A1": [vehicle_rows[0]],
                },
                "sheet-ev": {
                    "Maintenance": ev_rows,
                    "Maintenance!A1:A1": [ev_rows[0]],
                },
            }
        ),
        now_factory=lambda: datetime(2026, 6, 10, tzinfo=UTC),
    )
    calendar_connector = EmptyCalendarConnector()

    def get_connector(name: str):
        if name == "google_sheets":
            return google_connector
        if name == "ics_calendar":
            return calendar_connector
        raise AssertionError(f"Unexpected connector: {name}")

    monkeypatch.setattr(search_service.connector_base, "get_connector", get_connector)
    monkeypatch.setattr(context_pack_service.connector_base, "get_connector", get_connector)
    monkeypatch.setattr(registry_module, "get_connector", get_connector)
    return google_connector


@pytest.mark.anyio
async def test_search_route_validates_request_shape(
    tmp_path: Path,
    monkeypatch,
    fake_api_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/search",
            json={"source_ids": ["vehicle_log_primary"]},
        )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"] == "Request validation failed."


@pytest.mark.anyio
async def test_search_route_returns_empty_stub_results_and_writes_audit(
    tmp_path: Path,
    monkeypatch,
    fake_api_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/search",
            json={
                "query": "battery replacement",
                "source_ids": ["vehicle_log_primary"],
                "domain_tags": ["vehicle"],
                "retrieval_mode": "targeted",
                "max_results": 10,
                "allowed_sensitivity": "medium",
                "budget": {
                    "max_results": 10,
                    "max_bytes": 50000,
                    "max_text_chars": 20000,
                },
                "include_raw": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "battery replacement"
    assert payload["results"] == []
    assert payload["budget"]["returned_results"] == 0
    assert "sheet-secret-id" not in json.dumps(payload)

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["operation"] == "search"
    assert audit_event["status"] == "success"
    assert audit_event["result_count"] == 0
    assert "sheet-secret-id" not in json.dumps(audit_event)
    assert "google_sheets_readonly" not in json.dumps(audit_event)


@pytest.mark.anyio
async def test_search_route_without_filters_remains_broadly_compatible(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/search",
            json={
                "query": "vehicle maintenance",
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 10, "max_bytes": 50000, "max_text_chars": 20000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert {result["source_id"] for result in payload["results"]} >= {
        "vehicle_service_log",
        "public_holiday_calendar",
    }


@pytest.mark.anyio
async def test_search_route_audit_does_not_include_api_key(
    tmp_path: Path,
    monkeypatch,
    fake_api_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    monkeypatch.setenv("DSA_API_KEY", "super-secret-dsa-key")
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/search",
            headers={"X-API-Key": "super-secret-dsa-key"},
            json={
                "query": "battery replacement",
                "source_ids": ["vehicle_log_primary"],
                "budget": {
                    "max_results": 10,
                    "max_bytes": 50000,
                    "max_text_chars": 20000,
                },
            },
        )

    assert response.status_code == 200
    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert "super-secret-dsa-key" not in json.dumps(audit_event)


@pytest.mark.anyio
async def test_search_route_returns_stable_unknown_source_error_and_writes_audit(
    tmp_path: Path,
    monkeypatch,
    fake_api_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/search",
            json={"query": "battery", "source_ids": ["unknown_source"]},
        )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == {
        "code": "source_not_found",
        "message": "Source 'unknown_source' is not configured or is disabled.",
        "details": {"source_id": "unknown_source"},
    }

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["status"] == "error"
    assert audit_event["error_code"] == "source_not_found"


@pytest.mark.anyio
async def test_fetch_route_returns_unsupported_operation_and_writes_audit(
    tmp_path: Path,
    monkeypatch,
    fake_api_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/fetch",
            json={
                "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A44:H44",
                "include_raw": True,
                "budget": {"max_bytes": 50000, "max_text_chars": 20000},
            },
        )

    assert response.status_code == 501
    payload = response.json()
    assert payload["error"]["code"] == "unsupported_operation"
    assert payload["error"]["details"]["operation"] == "fetch"

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["operation"] == "fetch"
    assert audit_event["status"] == "error"
    assert audit_event["error_code"] == "unsupported_operation"


@pytest.mark.anyio
async def test_fetch_route_rejects_invalid_source_ref(
    tmp_path: Path,
    monkeypatch,
    fake_api_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/fetch",
            json={"source_ref": "bad-ref", "budget": {"max_bytes": 50000}},
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_source_ref"
    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["operation"] == "fetch"
    assert audit_event["status"] == "error"
    assert audit_event["error_code"] == "invalid_source_ref"


@pytest.mark.anyio
async def test_context_pack_route_returns_compact_google_sheets_items(
    tmp_path: Path,
    monkeypatch,
    fake_context_pack_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "what maintenance did I do recently on the Jeep?",
                "source_ids": ["vehicle_log_primary"],
                "retrieval_mode": "targeted",
                "allowed_sensitivity": "medium",
                "budget": {
                    "max_results": 5,
                    "max_bytes": 50000,
                    "max_text_chars": 12000,
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sources_used"] == ["vehicle_log_primary"]
    assert len(payload["items"]) == 2
    assert (
        payload["items"][0]["source_ref"]
        == "google_sheets:vehicle_log_primary:'Form responses 1'!A13:I13"
    )
    assert "Engine oil and transfer case service." in payload["items"][0]["text"]
    assert payload["items"][0]["warnings"] == ["maintenance_summary"]
    assert "raw" not in payload["items"][0]
    assert "sheet-secret-id" not in json.dumps(payload)

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["operation"] == "context_pack"
    assert audit_event["status"] == "success"
    assert audit_event["result_count"] == 2
    assert "sheet-secret-id" not in json.dumps(audit_event)


@pytest.mark.anyio
async def test_context_pack_route_returns_compact_ics_items_and_hides_private_details(
    tmp_path: Path,
    monkeypatch,
    fake_context_pack_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "when is the next home game?",
                "source_ids": ["calendar_sports"],
                "retrieval_mode": "targeted",
                "allowed_sensitivity": "low",
                "budget": {
                    "max_results": 5,
                    "max_bytes": 50000,
                    "max_text_chars": 12000,
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert (
        payload["items"][0]["source_ref"]
        == "ics_calendar:calendar_sports:event:sports-team-home-20261010"
    )
    assert payload["items"][0]["source_modified_at"] == "2026-06-01T00:00:00Z"
    assert payload["items"][0]["warnings"] == ["timezone_inferred"]
    assert "raw" not in payload["items"][0]
    assert "private.example.test" not in json.dumps(payload)


@pytest.mark.anyio
async def test_context_pack_route_enforces_budget_limits(
    tmp_path: Path,
    monkeypatch,
    fake_context_pack_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "maintenance",
                "source_ids": ["vehicle_log_primary"],
                "allowed_sensitivity": "medium",
                "budget": {
                    "max_results": 1,
                    "max_bytes": 50000,
                    "max_text_chars": 12000,
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["budget"] == {
        "max_results": 1,
        "returned_results": 1,
        "estimated_bytes": payload["budget"]["estimated_bytes"],
        "truncated": True,
    }


@pytest.mark.anyio
async def test_context_pack_route_returns_stable_missing_source_error(
    tmp_path: Path,
    monkeypatch,
    fake_context_pack_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={"query": "oil", "source_ids": ["unknown_source"]},
        )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == {
        "code": "source_not_found",
        "message": "Source 'unknown_source' is not configured or is disabled.",
        "details": {"source_id": "unknown_source"},
    }

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["operation"] == "context_pack"
    assert audit_event["status"] == "error"
    assert audit_event["error_code"] == "source_not_found"


@pytest.mark.anyio
async def test_context_pack_route_filters_disallowed_sensitivity_like_search(
    tmp_path: Path,
    monkeypatch,
    fake_context_pack_connector,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)
    source_path = source_dir / "source.yaml"
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace("sensitivity: low", "sensitivity: high"),
        encoding="utf-8",
    )

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "oil",
                "source_ids": ["vehicle_log_primary"],
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "source_not_found"


@pytest.mark.anyio
async def test_context_pack_route_ranks_latest_relevant_vehicle_record_first(
    tmp_path: Path,
    monkeypatch,
    real_multi_source_context_pack_connectors,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "When did I last change my car oil?",
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["diagnostics"]["selection_mode"] == "query_relevance"
    assert payload["sources_used"][0] == "vehicle_service_log"
    assert payload["items"][0]["source_id"] == "vehicle_service_log"
    assert (
        payload["items"][0]["source_ref"]
        == "google_sheets:vehicle_service_log:Maintenance!A3:E3"
    )
    assert "09/03/2026" in payload["items"][0]["text"]
    vehicle_item_refs = [
        item["source_ref"]
        for item in payload["items"]
        if item["source_id"] == "vehicle_service_log"
    ]
    assert vehicle_item_refs[:3] == [
        "google_sheets:vehicle_service_log:Maintenance!A3:E3",
        "google_sheets:vehicle_service_log:Maintenance!A2:E2",
        "google_sheets:vehicle_service_log:Maintenance!A4:E4",
    ]
    assert all("calendar" not in source_id for source_id in payload["sources_used"])
    assert "public_holiday_calendar" not in payload["sources_used"]
    assert payload["budget"]["returned_results"] >= 3
    assert payload["diagnostics"]["ranking_mode"] == "round_robin_by_source_relevance_then_recency"


@pytest.mark.anyio
async def test_context_pack_route_treats_latest_and_most_recent_as_equivalent_temporal_queries(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        latest_response = await client.post(
            "/v1/context-pack",
            json={
                "query": "When was my latest car oil service?",
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )
        most_recent_response = await client.post(
            "/v1/context-pack",
            json={
                "query": "When was my most recent car oil service?",
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert latest_response.status_code == 200
    assert most_recent_response.status_code == 200
    assert (
        latest_response.json()["items"][0]["source_ref"]
        == "google_sheets:vehicle_service_log:Maintenance!A2:F2"
    )
    assert (
        most_recent_response.json()["items"][0]["source_ref"]
        == "google_sheets:vehicle_service_log:Maintenance!A2:F2"
    )


@pytest.mark.anyio
async def test_context_pack_route_keeps_non_temporal_oil_change_queries_relevance_first(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "oil change",
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert (
        payload["items"][0]["source_ref"]
        == "google_sheets:vehicle_service_log:Maintenance!A3:F3"
    )
    assert payload["diagnostics"]["ranking_mode"] == "single_source"


@pytest.mark.anyio
async def test_context_pack_route_matches_punctuation_in_oil_queries(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "oil?",
                "source_ids": ["vehicle_service_log"],
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sources_used"] == ["vehicle_service_log"]
    assert (
        payload["items"][0]["source_ref"]
        == "google_sheets:vehicle_service_log:Maintenance!A2:F2"
    )


@pytest.mark.anyio
async def test_context_pack_route_uses_query_relevance_for_electric_vehicle_queries(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "When did I last service my electric vehicle?",
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert "electric_vehicle_history" in payload["sources_used"]
    assert any(item["source_id"] == "electric_vehicle_history" for item in payload["items"])
    assert all("calendar" not in item["source_id"] for item in payload["items"])


@pytest.mark.anyio
async def test_context_pack_route_prefers_calendar_sources_for_calendar_queries(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "What is on my calendar next week?",
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert "personal_calendar_agenda" in payload["sources_used"]
    assert any("calendar" in item["source_id"] for item in payload["items"])
    assert all("vehicle" not in item["source_id"] for item in payload["items"])


@pytest.mark.anyio
async def test_context_pack_route_prefers_holiday_source_for_holiday_queries(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "When is the national summer holiday?",
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sources_used"][0] == "public_holiday_calendar"
    assert payload["items"][0]["source_id"] == "public_holiday_calendar"
    assert "National Summer Holiday" in payload["items"][0]["title"]


@pytest.mark.anyio
async def test_context_pack_route_supports_cross_domain_queries_without_starvation(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "What appointments and vehicle maintenance do I have coming up?",
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 4, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    source_ids = [item["source_id"] for item in payload["items"]]
    assert any("calendar" in source_id for source_id in source_ids)
    assert any("vehicle" in source_id for source_id in source_ids)
    assert payload["diagnostics"]["ranking_mode"] == "round_robin_by_source_relevance"


@pytest.mark.anyio
async def test_context_pack_route_falls_back_broadly_for_ambiguous_queries(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "status",
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 3, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["diagnostics"]["selection_mode"] == "broad_fallback"
    assert set(payload["sources_used"]) == {
        "vehicle_service_log",
        "electric_vehicle_history",
        "personal_calendar_agenda",
        "family_calendar_agenda",
        "public_holiday_calendar",
    }
    assert payload["budget"]["returned_results"] == 3


@pytest.mark.anyio
async def test_context_pack_route_honors_explicit_source_override(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "When did I last service my electric vehicle?",
                "source_ids": ["vehicle_service_log"],
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sources_used"] == ["vehicle_service_log"]
    assert payload["diagnostics"]["selection_mode"] == "explicit_source_ids"
    assert all(item["source_id"] == "vehicle_service_log" for item in payload["items"])


@pytest.mark.anyio
async def test_context_pack_route_honors_domain_tag_constraint(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "oil change",
                "domain_tags": ["calendar"],
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 4, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["diagnostics"]["selection_mode"] == "domain_tags"
    assert payload["sources_used"]
    assert all("calendar" in source_id for source_id in payload["sources_used"])
    assert all("calendar" in item["source_id"] for item in payload["items"])


@pytest.mark.anyio
async def test_context_pack_route_is_registry_order_independent(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    first_dir = tmp_path / "sources_first"
    second_dir = tmp_path / "sources_second"
    first_dir.mkdir()
    second_dir.mkdir()
    _write_multi_source_configs(first_dir)
    _write_multi_source_configs(second_dir, reverse_order=True)

    first_transport = httpx.ASGITransport(app=create_app(source_config_dir=first_dir))
    second_transport = httpx.ASGITransport(app=create_app(source_config_dir=second_dir))

    async with httpx.AsyncClient(
        transport=first_transport,
        base_url="http://test",
    ) as first_client:
        first_response = await first_client.post(
            "/v1/context-pack",
            json={
                "query": (
                    "Search my vehicle maintenance log and tell me when I last changed "
                    "the oil in my vehicle."
                ),
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    async with httpx.AsyncClient(
        transport=second_transport,
        base_url="http://test",
    ) as second_client:
        second_response = await second_client.post(
            "/v1/context-pack",
            json={
                "query": (
                    "Search my vehicle maintenance log and tell me when I last changed "
                    "the oil in my vehicle."
                ),
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 12000},
            },
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_payload = first_response.json()
    second_payload = second_response.json()
    assert first_payload["sources_used"] == second_payload["sources_used"]
    assert [item["source_id"] for item in first_payload["items"]] == [
        item["source_id"] for item in second_payload["items"]
    ]


@pytest.mark.anyio
async def test_context_pack_route_enforces_text_char_budget_with_truthful_diagnostics(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "vehicle maintenance",
                "source_ids": ["vehicle_service_log"],
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 50000, "max_text_chars": 130},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["budget"]["returned_results"] == 1
    assert payload["budget"]["truncated"] is True
    assert payload["diagnostics"]["candidate_counts_by_source"] == {"vehicle_service_log": 3}
    assert payload["diagnostics"]["budget_truncated_candidates"] is True


@pytest.mark.anyio
async def test_context_pack_route_preserves_result_too_large_for_oversized_first_item(
    tmp_path: Path,
    monkeypatch,
    fake_multi_source_context_pack_connector,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_multi_source_configs(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack",
            json={
                "query": "vehicle maintenance",
                "source_ids": ["vehicle_service_log"],
                "allowed_sensitivity": "medium",
                "budget": {"max_results": 5, "max_bytes": 40, "max_text_chars": 12000},
            },
        )

    assert response.status_code == 413
    payload = response.json()
    assert payload["error"]["code"] == "result_too_large"
