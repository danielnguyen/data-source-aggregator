from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app import registry as registry_module
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
