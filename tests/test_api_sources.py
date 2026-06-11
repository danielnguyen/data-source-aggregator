from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.connectors import base as connector_base
from app.models import ContextRequest, FetchRequest, ResultEnvelope, SearchRequest
from app.main import create_app
from app.models import SourceHealth, SourceStatus


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


@pytest.mark.anyio
async def test_health_route(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "data-source-aggregator",
    }


@pytest.mark.anyio
async def test_sources_routes_return_safe_registry_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeHealthyGoogleSheetsConnector:
        async def search(
            self,
            request: SearchRequest,
            source_config,
        ) -> list[ResultEnvelope]:
            return []

        async def fetch(
            self,
            request: FetchRequest,
            source_config,
        ) -> list[ResultEnvelope]:
            return []

        async def context(
            self,
            request: ContextRequest,
            source_config,
        ) -> list[ResultEnvelope]:
            return []

        async def check_health(self, source_config):
            return SourceHealth(
                status=SourceStatus.READY,
                last_checked_at="2026-06-10T00:00:00Z",
                last_error=None,
            )

    monkeypatch.setitem(
        connector_base.CONNECTOR_FACTORIES,
        "google_sheets",
        lambda: FakeHealthyGoogleSheetsConnector(),
    )
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
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

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        list_response = await client.get("/v1/sources")
        detail_response = await client.get("/v1/sources/vehicle_log_primary")

    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["sources"][0]["source_id"] == "vehicle_log_primary"
    assert payload["sources"][0]["display_name"] == "Vehicle Log - Primary"
    assert payload["sources"][0]["domain_tags"] == ["vehicle", "maintenance"]
    assert payload["sources"][0]["status"] == "ready"
    assert payload["sources"][0]["last_checked_at"] == "2026-06-10T00:00:00Z"
    assert payload["sources"][0]["last_error"] is None
    assert payload["sources"][0]["capabilities"] == ["profile", "search", "fetch", "context"]
    assert "connector_config" not in payload["sources"][0]
    assert "sheet-secret-id" not in str(payload)

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["source"]["retrieval"]["default_mode"] == "targeted"
    assert detail_payload["source"]["display_name"] == "Vehicle Log - Primary"
    assert detail_payload["source"]["domain_tags"] == ["vehicle", "maintenance"]
    assert detail_payload["source"]["status"] == "ready"
    assert detail_payload["source"]["last_checked_at"] == "2026-06-10T00:00:00Z"
    assert detail_payload["source"]["last_error"] is None
    assert (
        detail_payload["source"]["profile"]["summary"]
        == "Google Sheets source with read-only row and range retrieval."
    )
    assert "sheet-secret-id" not in str(detail_payload)


@pytest.mark.anyio
async def test_sources_route_reports_unavailable_without_leaking_private_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeUnavailableIcsConnector:
        async def check_health(self, source_config):
            return SourceHealth(
                status=SourceStatus.UNAVAILABLE,
                last_checked_at="2026-06-10T00:00:00Z",
                last_error="source_unavailable",
            )

    monkeypatch.setitem(
        connector_base.CONNECTOR_FACTORIES,
        "ics_calendar",
        lambda: FakeUnavailableIcsConnector(),
    )
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "calendar.yaml").write_text(
        """
source_id: calendar_sports
display_name: Sports Calendar
description: Sports schedule source.
domain_tags: [calendar, sports]
connector: ics_calendar
enabled: true
sensitivity: low
access_mode: read_only
connector_config:
  url: https://private.example.test/sports-calendar.ics
  timezone: America/Toronto
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  lookback_days: 30
  lookahead_days: 365
  allow_full_fetch: true
""",
        encoding="utf-8",
    )

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/sources")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sources"][0]["status"] == "unavailable"
    assert payload["sources"][0]["last_error"] == "source_unavailable"
    assert "private.example.test" not in str(payload)


@pytest.mark.anyio
async def test_get_source_returns_404_for_unknown_source(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/sources/missing_source")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "source_not_found",
        "message": "Source 'missing_source' is not configured or is disabled.",
        "details": {"source_id": "missing_source"},
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/v1/sources", None),
        ("GET", "/v1/sources/vehicle_log_primary", None),
        (
            "POST",
            "/v1/sources/search",
            {
                "query": "battery replacement",
                "source_ids": ["vehicle_log_primary"],
                "budget": {"max_results": 1, "max_bytes": 50000, "max_text_chars": 20000},
            },
        ),
        (
            "POST",
            "/v1/sources/fetch",
            {
                "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A44:H44",
                "budget": {"max_bytes": 50000, "max_text_chars": 20000},
            },
        ),
        (
            "POST",
            "/v1/sources/context",
            {
                "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A44:H44",
                "context_mode": "surrounding_rows",
                "budget": {"max_rows": 5, "max_bytes": 50000, "max_text_chars": 20000},
            },
        ),
        (
            "POST",
            "/v1/context-pack",
            {
                "query": "battery replacement",
                "source_ids": ["vehicle_log_primary"],
                "budget": {"max_results": 1, "max_bytes": 50000, "max_text_chars": 12000},
            },
        ),
    ],
)
async def test_protected_routes_require_api_key_when_configured(
    tmp_path: Path,
    monkeypatch,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    class FakeHealthyGoogleSheetsConnector:
        async def search(
            self,
            request: SearchRequest,
            source_config,
        ) -> list[ResultEnvelope]:
            return []

        async def fetch(
            self,
            request: FetchRequest,
            source_config,
        ) -> list[ResultEnvelope]:
            return []

        async def context(
            self,
            request: ContextRequest,
            source_config,
        ) -> list[ResultEnvelope]:
            return []

        async def check_health(self, source_config):
            return SourceHealth(
                status=SourceStatus.READY,
                last_checked_at="2026-06-10T00:00:00Z",
                last_error=None,
            )

    monkeypatch.setitem(
        connector_base.CONNECTOR_FACTORIES,
        "google_sheets",
        lambda: FakeHealthyGoogleSheetsConnector(),
    )
    monkeypatch.setenv("DSA_API_KEY", "dsa-secret")
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
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

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.request(method, path, json=payload)
        wrong = await client.request(method, path, json=payload, headers={"X-API-Key": "wrong-key"})
        correct = await client.request(
            method,
            path,
            json=payload,
            headers={"X-API-Key": "dsa-secret"},
        )

    assert missing.status_code == 401
    assert missing.json() == {
        "error": {
            "code": "unauthorized",
            "message": "Invalid or missing API key",
            "details": {},
        }
    }
    assert wrong.status_code == 401
    assert wrong.json() == missing.json()
    assert correct.status_code != 401


@pytest.mark.anyio
async def test_health_route_remains_open_when_api_key_is_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DSA_API_KEY", "dsa-secret")
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "data-source-aggregator",
    }
