from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

import app.main as main_module
from app.connectors import base as connector_base
from app.main import create_app
from app.models import (
    ContextRequest,
    FetchRequest,
    InventoryStatus,
    ResultEnvelope,
    RetrievalConfig,
    SearchRequest,
    Sensitivity,
    SourceConfig,
    SourceHealth,
    SourceProfile,
    SourceRegistryDetail,
    SourceStatus,
)
from app.registry import SourceRegistry

REQUEST_LOGGER = "uvicorn.error.data_source_aggregator.requests"
REGISTRY_LOGGER = "uvicorn.error.data_source_aggregator.registry"


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


def _write_ics_source(
    source_dir: Path,
    filename: str,
    *,
    source_id: str,
    enabled: bool = True,
    authority_role: str | None = None,
    description: str = "Configured calendar source.",
    display_name: str = "Configured Calendar",
    domain_tags: list[str] | None = None,
) -> None:
    authority_line = (
        f"authority_role: {authority_role}\n"
        if authority_role is not None
        else ""
    )
    (source_dir / filename).write_text(
        f"""
source_id: {source_id}
display_name: {json.dumps(display_name)}
description: {description}
domain_tags: {json.dumps(domain_tags or ["calendar"])}
connector: ics_calendar
enabled: {str(enabled).lower()}
{authority_line}sensitivity: low
access_mode: read_only
connector_config:
  url: https://private.example.test/{source_id}.ics
  timezone: UTC
retrieval:
  default_mode: targeted
  max_results: 10
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: false
""",
        encoding="utf-8",
    )


def _source_config(
    *,
    source_id: str = "configured_calendar",
    display_name: str = "Configured Calendar",
    domain_tags: list[str] | None = None,
    description: str = "Configured calendar source.",
    enabled: bool = False,
) -> SourceConfig:
    return SourceConfig(
        source_id=source_id,
        display_name=display_name,
        description=description,
        domain_tags=domain_tags or ["calendar"],
        connector="ics_calendar",
        enabled=enabled,
        sensitivity="low",
        access_mode="read_only",
        connector_config={
            "url": "https://private.example.test/calendar.ics",
            "timezone": "UTC",
            "credential": "PRIVATE_CONFIG_SENTINEL",
        },
        retrieval=RetrievalConfig(
            default_mode="targeted",
            max_results=10,
            max_bytes=100000,
            max_text_chars=40000,
            allow_full_fetch=False,
        ),
    )


def _registry_detail(
    source_config: SourceConfig,
    *,
    last_error: str | None = None,
) -> SourceRegistryDetail:
    return SourceRegistryDetail(
        source_id=source_config.source_id,
        display_name=source_config.display_name,
        connector=source_config.connector,
        domain_tags=source_config.domain_tags,
        sensitivity=source_config.sensitivity,
        access_mode=source_config.access_mode,
        capabilities=["profile", "search", "fetch", "context"],
        enabled=source_config.enabled,
        authority_role=source_config.authority_role,
        status="ready" if source_config.enabled else "disabled",
        last_checked_at=datetime(2026, 8, 12, tzinfo=UTC),
        last_error=last_error,
        scope_refs=source_config.scope_refs,
        retrieval=source_config.retrieval,
        profile=SourceProfile(
            summary="Configured source.",
            content_types=["source_record"],
        ),
    )


def _source_registry(
    source_configs: list[SourceConfig],
    *,
    inventory_status: InventoryStatus = InventoryStatus.COMPLETE,
    last_errors: dict[str, str] | None = None,
) -> SourceRegistry:
    errors = last_errors or {}
    return SourceRegistry(
        [
            _registry_detail(
                source_config,
                last_error=errors.get(source_config.source_id),
            )
            for source_config in source_configs
        ],
        source_configs,
        inventory_status=inventory_status,
        loaded=True,
    )


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


def test_request_logger_emits_info_with_default_uvicorn_logging() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    program = """
import copy
import logging.config

from uvicorn.config import LOGGING_CONFIG

import app.main as main_module

assert main_module._request_logger.name == (
    "uvicorn.error.data_source_aggregator.requests"
)
logging.config.dictConfig(copy.deepcopy(LOGGING_CONFIG))
main_module._request_logger.info("DSA_CORRELATION_VISIBILITY_SENTINEL")
"""

    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "DSA_CORRELATION_VISIBILITY_SENTINEL" in (
        completed.stdout + completed.stderr
    )


@pytest.mark.anyio
async def test_valid_request_id_is_echoed_and_logged(tmp_path: Path, caplog) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    request_id = "chat.request_123:attempt-1"
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/sources",
            headers={"X-Request-ID": request_id},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id
    assert request_id in caplog.text
    assert "correlation_state=valid" in caplog.text
    assert "method=GET" in caplog.text
    assert "route=/v1/sources" in caplog.text
    assert "status=200" in caplog.text


@pytest.mark.anyio
async def test_missing_request_id_is_not_fabricated(tmp_path: Path, caplog) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/sources")

    assert response.status_code == 200
    assert "X-Request-ID" not in response.headers
    assert "correlation_state=absent" in caplog.text
    assert "request_id=" not in caplog.text


@pytest.mark.anyio
async def test_invalid_request_id_is_not_echoed_or_logged(tmp_path: Path, caplog) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    malformed = "invalid request id PRIVATE_HEADER_SENTINEL"
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/sources?query=PRIVATE_QUERY_SENTINEL",
            headers={"X-Request-ID": malformed},
        )

    assert response.status_code == 200
    assert "X-Request-ID" not in response.headers
    assert "correlation_state=invalid" in caplog.text
    assert malformed not in caplog.text
    assert "PRIVATE_HEADER_SENTINEL" not in caplog.text
    assert "PRIVATE_QUERY_SENTINEL" not in caplog.text
    assert "route=/v1/sources" in caplog.text


@pytest.mark.anyio
async def test_valid_request_id_survives_validation_error_without_logging_body(
    tmp_path: Path,
    caplog,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    request_id = "validation-request-1"
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/context-pack?query=PRIVATE_QUERY_SENTINEL",
            headers={"X-Request-ID": request_id},
            json={"query": {"private": "PRIVATE_BODY_SENTINEL"}},
        )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == request_id
    assert request_id in caplog.text
    assert "route=/v1/context-pack" in caplog.text
    assert "status=422" in caplog.text
    assert "PRIVATE_QUERY_SENTINEL" not in caplog.text
    assert "PRIVATE_BODY_SENTINEL" not in caplog.text


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
            raise AssertionError("source listing must not execute search")

        async def fetch(
            self,
            request: FetchRequest,
            source_config,
        ) -> list[ResultEnvelope]:
            raise AssertionError("source listing must not execute fetch")

        async def context(
            self,
            request: ContextRequest,
            source_config,
        ) -> list[ResultEnvelope]:
            raise AssertionError("source listing must not execute context")

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
authority_role: authoritative
scope_refs:
  time: fy2026
  version: release-152
  domain: vehicle-maintenance
  project: vehicle-log
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
    assert set(payload) == {"inventory_scope", "inventory_status", "sources"}
    assert payload["inventory_scope"] == "configured_sources"
    assert payload["inventory_status"] == "complete"
    assert payload["sources"][0]["source_id"] == "vehicle_log_primary"
    assert payload["sources"][0]["display_name"] == "Vehicle Log - Primary"
    assert payload["sources"][0]["domain_tags"] == ["vehicle", "maintenance"]
    assert payload["sources"][0]["status"] == "ready"
    assert payload["sources"][0]["last_checked_at"] == "2026-06-10T00:00:00Z"
    assert payload["sources"][0]["last_error"] is None
    assert payload["sources"][0]["capabilities"] == ["profile", "search", "fetch", "context"]
    assert payload["sources"][0]["authority_role"] == "authoritative"
    assert payload["sources"][0]["scope_refs"] == {
        "time": "fy2026",
        "version": "release-152",
        "domain": "vehicle-maintenance",
        "project": "vehicle-log",
    }
    assert "connector_config" not in payload["sources"][0]
    assert "sheet-secret-id" not in str(payload)

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["source"]["retrieval"]["default_mode"] == "targeted"
    assert detail_payload["source"]["display_name"] == "Vehicle Log - Primary"
    assert detail_payload["source"]["domain_tags"] == ["vehicle", "maintenance"]
    assert detail_payload["source"]["authority_role"] == "authoritative"
    assert detail_payload["source"]["scope_refs"] == {
        "time": "fy2026",
        "version": "release-152",
        "domain": "vehicle-maintenance",
        "project": "vehicle-log",
    }
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
authority_role: supplemental
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
        detail_response = await client.get("/v1/sources/calendar_sports")

    assert response.status_code == 200
    payload = response.json()
    assert payload["inventory_scope"] == "configured_sources"
    assert payload["inventory_status"] == "complete"
    assert payload["sources"][0]["authority_role"] == "supplemental"
    assert payload["sources"][0]["status"] == "unavailable"
    assert payload["sources"][0]["last_error"] == "source_unavailable"
    assert "scope_refs" not in payload["sources"][0]
    assert "private.example.test" not in str(payload)
    assert detail_response.status_code == 200
    detail = detail_response.json()["source"]
    assert detail["last_checked_at"] == "2026-06-10T00:00:00Z"
    assert detail["last_error"] == "source_unavailable"
    assert "scope_refs" not in detail


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


@pytest.mark.anyio
async def test_empty_and_missing_source_directories_have_distinct_statuses(
    tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    missing_dir = tmp_path / "missing"

    empty_transport = httpx.ASGITransport(
        app=create_app(source_config_dir=empty_dir)
    )
    missing_transport = httpx.ASGITransport(
        app=create_app(source_config_dir=missing_dir)
    )
    async with (
        httpx.AsyncClient(
            transport=empty_transport,
            base_url="http://empty",
        ) as empty_client,
        httpx.AsyncClient(
            transport=missing_transport,
            base_url="http://missing",
        ) as missing_client,
    ):
        empty_response = await empty_client.get("/v1/sources")
        missing_response = await missing_client.get("/v1/sources")

    assert empty_response.json() == {
        "inventory_scope": "configured_sources",
        "inventory_status": "complete",
        "sources": [],
    }
    assert missing_response.json() == {
        "inventory_scope": "configured_sources",
        "inventory_status": "unknown",
        "sources": [],
    }


@pytest.mark.anyio
async def test_truthfully_loaded_empty_registry_is_not_reloaded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    calls = 0
    original_loader = main_module.load_source_config_inventory

    def counted_loader(config_dir):
        nonlocal calls
        calls += 1
        return original_loader(config_dir)

    monkeypatch.setattr(
        main_module,
        "load_source_config_inventory",
        counted_loader,
    )
    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        first = await client.get("/v1/sources")
        second = await client.get("/v1/sources")

    assert first.json() == second.json()
    assert first.json()["inventory_status"] == "complete"
    assert first.json()["sources"] == []
    assert calls == 1


@pytest.mark.anyio
async def test_partial_empty_inventory_stays_loaded_and_excludes_rejected_config(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "invalid-disabled.yaml",
        source_id="invalid_calendar",
        enabled=False,
        authority_role="owner_declared",
        description="PRIVATE REJECTED SOURCE CONTENT",
    )
    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))

    with pytest.warns(UserWarning, match="invalid-disabled.yaml"):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            first = await client.get("/v1/sources")
            second = await client.get("/v1/sources")

    assert first.json() == {
        "inventory_scope": "configured_sources",
        "inventory_status": "partial",
        "sources": [],
    }
    assert second.json() == first.json()
    assert "PRIVATE REJECTED SOURCE CONTENT" not in first.text


@pytest.mark.anyio
async def test_valid_disabled_source_is_represented_in_complete_inventory(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "disabled.yaml",
        source_id="disabled_calendar",
        enabled=False,
        authority_role="unknown",
    )
    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/sources")

    assert response.status_code == 200
    assert response.json()["inventory_status"] == "complete"
    assert response.json()["sources"][0]["source_id"] == "disabled_calendar"
    assert response.json()["sources"][0]["status"] == "disabled"
    assert response.json()["sources"][0]["authority_role"] == "unknown"


def test_all_valid_public_inventory_preserves_values_order_and_complete_status() -> None:
    first = _source_config(
        source_id="calendar_alpha",
        display_name="Calendar Alpha",
        domain_tags=["calendar", "alpha"],
    )
    second = _source_config(
        source_id="calendar_beta",
        display_name="Calendar Beta",
        domain_tags=["calendar", "beta"],
    )

    registry = _source_registry([first, second])

    public_entries = registry.list_sources()
    assert registry.inventory_status == InventoryStatus.COMPLETE
    assert [entry.source_id for entry in public_entries] == [
        "calendar_alpha",
        "calendar_beta",
    ]
    assert public_entries[0].display_name == first.display_name
    assert public_entries[0].domain_tags == first.domain_tags
    assert public_entries[0].connector == first.connector
    assert public_entries[0].capabilities == ["profile", "search", "fetch", "context"]


@pytest.mark.anyio
async def test_malformed_domain_tag_is_quarantined_without_poisoning_neighbor(
    tmp_path: Path,
    caplog,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    malformed_tag = "proxima centauri server"
    _write_ics_source(
        source_dir,
        "01-valid.yaml",
        source_id="valid_calendar",
        enabled=False,
        domain_tags=["calendar", "valid"],
    )
    _write_ics_source(
        source_dir,
        "02-malformed.yaml",
        source_id="malformed_calendar",
        enabled=False,
        domain_tags=[malformed_tag],
    )
    caplog.set_level(logging.WARNING, logger=REGISTRY_LOGGER)
    app = create_app(source_config_dir=source_dir)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/sources")

    assert response.status_code == 200
    assert response.json()["inventory_status"] == "partial"
    assert [source["source_id"] for source in response.json()["sources"]] == [
        "valid_calendar"
    ]
    assert malformed_tag not in response.text
    assert "proxima-centauri-server" not in response.text
    registry = app.state.source_registry
    assert registry.get_source("malformed_calendar") is not None
    assert registry.get_source_config("malformed_calendar") is not None
    assert registry.get_source("malformed_calendar").domain_tags == [malformed_tag]
    assert registry.get_source_config("malformed_calendar").domain_tags == [
        malformed_tag
    ]
    assert malformed_tag not in caplog.text
    assert (
        "public_source_projection_quarantined "
        "component=data-source-aggregator field=domain_tags "
        "reason=invalid_identifier source_id=malformed_calendar"
    ) in caplog.text


def test_public_quarantine_does_not_change_internal_selection() -> None:
    malformed_tag = "proxima centauri server"
    source_config = _source_config(
        source_id="operational_calendar",
        domain_tags=[malformed_tag],
        enabled=True,
    )
    registry = _source_registry([source_config])

    assert registry.list_sources() == []
    assert registry.get_source(source_config.source_id) is not None
    assert registry.get_source_config(source_config.source_id) is source_config
    assert registry.get_source(source_config.source_id).domain_tags == [malformed_tag]
    assert registry.select_sources(
        source_ids=[source_config.source_id],
        allowed_sensitivity=Sensitivity.LOW,
        required_capability="search",
    ) == [source_config]


@pytest.mark.parametrize(
    ("config_overrides", "last_error", "expected_field", "expected_reason"),
    [
        pytest.param(
            {"source_id": "s" * 121},
            None,
            "source_id",
            "value_too_long",
            id="source-id-too-long",
        ),
        pytest.param(
            {"display_name": "D" * 241},
            None,
            "display_name",
            "value_too_long",
            id="display-name-too-long",
        ),
        pytest.param(
            {"domain_tags": ["invalid domain tag"]},
            None,
            "domain_tags",
            "invalid_identifier",
            id="domain-tag-invalid-identifier",
        ),
        pytest.param(
            {"domain_tags": ["d" * 121]},
            None,
            "domain_tags",
            "value_too_long",
            id="domain-tag-too-long",
        ),
        pytest.param(
            {"domain_tags": [f"tag_{index}" for index in range(9)]},
            None,
            "domain_tags",
            "collection_too_large",
            id="too-many-domain-tags",
        ),
        pytest.param(
            {"domain_tags": ["calendar", "calendar"]},
            None,
            "domain_tags",
            "duplicate_items",
            id="duplicate-domain-tags",
        ),
        pytest.param(
            {},
            "E" * 241,
            "last_error",
            "value_too_long",
            id="last-error-too-long",
        ),
    ],
)
def test_each_supported_consumer_mismatch_is_quarantined(
    config_overrides: dict[str, object],
    last_error: str | None,
    expected_field: str,
    expected_reason: str,
    caplog,
) -> None:
    caplog.set_level(logging.WARNING, logger=REGISTRY_LOGGER)
    source_config = _source_config(**config_overrides)

    registry = _source_registry(
        [source_config],
        last_errors=(
            {source_config.source_id: last_error}
            if last_error is not None
            else None
        ),
    )

    assert registry.list_sources() == []
    assert registry.inventory_status == InventoryStatus.PARTIAL
    assert registry.get_source(source_config.source_id) is not None
    assert registry.get_source_config(source_config.source_id) is source_config
    assert f"field={expected_field}" in caplog.text
    assert f"reason={expected_reason}" in caplog.text


def test_multiple_malformed_public_entries_preserve_valid_neighbors_and_order() -> None:
    valid_first = _source_config(source_id="valid_first", domain_tags=["first"])
    malformed_tag = _source_config(
        source_id="malformed_tag",
        domain_tags=["private malformed tag"],
    )
    valid_second = _source_config(source_id="valid_second", domain_tags=["second"])
    malformed_name = _source_config(
        source_id="malformed_name",
        display_name="N" * 241,
    )

    registry = _source_registry(
        [valid_first, malformed_tag, valid_second, malformed_name]
    )

    assert [entry.source_id for entry in registry.list_sources()] == [
        "valid_first",
        "valid_second",
    ]
    assert registry.inventory_status == InventoryStatus.PARTIAL
    assert registry.get_source("malformed_tag") is not None
    assert registry.get_source_config("malformed_tag") is malformed_tag
    assert registry.get_source("malformed_name") is not None
    assert registry.get_source_config("malformed_name") is malformed_name


@pytest.mark.anyio
async def test_all_malformed_public_entries_return_partial_empty_response(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "01-malformed-tag.yaml",
        source_id="malformed_tag",
        enabled=False,
        domain_tags=["private malformed tag"],
    )
    _write_ics_source(
        source_dir,
        "02-malformed-name.yaml",
        source_id="malformed_name",
        enabled=False,
        display_name="N" * 241,
    )
    app = create_app(source_config_dir=source_dir)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/sources")

    assert response.status_code == 200
    assert response.json() == {
        "inventory_scope": "configured_sources",
        "inventory_status": "partial",
        "sources": [],
    }
    registry = app.state.source_registry
    assert registry.get_source("malformed_tag") is not None
    assert registry.get_source_config("malformed_tag") is not None
    assert registry.get_source("malformed_name") is not None
    assert registry.get_source_config("malformed_name") is not None


@pytest.mark.parametrize(
    "base_status",
    [InventoryStatus.PARTIAL, InventoryStatus.UNKNOWN, InventoryStatus.UNAVAILABLE],
)
def test_public_quarantine_does_not_promote_base_inventory_uncertainty(
    base_status: InventoryStatus,
) -> None:
    source_config = _source_config(domain_tags=["private malformed tag"])

    registry = _source_registry(
        [source_config],
        inventory_status=base_status,
    )

    assert registry.list_sources() == []
    assert registry.inventory_status == base_status


def test_quarantine_diagnostics_are_closed_and_do_not_expose_raw_values(
    caplog,
) -> None:
    private_tag = "PRIVATE DOMAIN TAG SENTINEL"
    private_display_name = "PRIVATE_DISPLAY_SENTINEL_" + "D" * 241
    private_source_id = "private_source_id_sentinel_" + "s" * 121
    private_description = "PRIVATE_DESCRIPTION_SENTINEL"
    malformed_tag = _source_config(
        source_id="safe_source",
        domain_tags=[private_tag],
        description=private_description,
    )
    malformed_name = _source_config(
        source_id="safe_display_source",
        display_name=private_display_name,
        description=private_description,
    )
    malformed_id = _source_config(
        source_id=private_source_id,
        description=private_description,
    )
    caplog.set_level(logging.WARNING, logger=REGISTRY_LOGGER)

    registry = _source_registry([malformed_tag, malformed_name, malformed_id])

    assert registry.list_sources() == []
    assert private_tag not in caplog.text
    assert private_display_name not in caplog.text
    assert private_source_id not in caplog.text
    assert private_description not in caplog.text
    assert "PRIVATE_CONFIG_SENTINEL" not in caplog.text
    assert "String should" not in caplog.text
    assert "input_value" not in caplog.text
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "public_source_projection_quarantined "
        "component=data-source-aggregator field=domain_tags "
        "reason=invalid_identifier source_id=safe_source",
        "public_source_projection_quarantined "
        "component=data-source-aggregator field=display_name "
        "reason=value_too_long source_id=safe_display_source",
        "public_source_projection_quarantined "
        "component=data-source-aggregator field=source_id "
        "reason=value_too_long source_id_state=omitted",
    ]
