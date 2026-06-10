from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models import SourceConfig, SourceHealth, SourceStatus
from app.registry import build_source_registry


@pytest.mark.anyio
async def test_build_source_registry_exposes_safe_fields_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_config = SourceConfig.model_validate(
        {
            "source_id": "vehicle_log_primary",
            "display_name": "Vehicle Log - Primary",
            "description": "Personal vehicle operating records.",
            "domain_tags": ["vehicle", "maintenance"],
            "connector": "google_sheets",
            "enabled": True,
            "sensitivity": "low",
            "access_mode": "read_only",
            "connector_config": {
                "spreadsheet_id": "sheet-secret-id",
                "worksheet": "Maintenance",
                "header_row": 1,
            },
            "retrieval": {
                "default_mode": "targeted",
                "max_results": 20,
                "max_bytes": 100000,
                "max_text_chars": 40000,
                "max_context_rows": 250,
                "allow_full_fetch": True,
            },
        }
    )

    class FakeConnector:
        async def check_health(self, source_config: SourceConfig):
            return SourceHealth(
                status=SourceStatus.READY,
                last_checked_at=datetime(2026, 6, 10, tzinfo=UTC),
                last_error=None,
            )

    monkeypatch.setattr("app.registry.get_connector", lambda _: FakeConnector())

    registry = await build_source_registry([source_config])

    entry = registry.list_sources()[0]
    dumped = entry.model_dump(mode="json")

    assert dumped["source_id"] == "vehicle_log_primary"
    assert dumped["display_name"] == "Vehicle Log - Primary"
    assert dumped["domain_tags"] == ["vehicle", "maintenance"]
    assert dumped["capabilities"] == ["profile", "search", "fetch", "context"]
    assert dumped["status"] == "ready"
    assert dumped["last_error"] is None
    assert "connector_config" not in dumped
    assert "sheet-secret-id" not in str(dumped)


@pytest.mark.anyio
async def test_registry_detail_includes_safe_profile_and_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_config = SourceConfig.model_validate(
        {
            "source_id": "calendar_sports",
            "display_name": "Sports Calendar",
            "description": "Sports schedule source.",
            "domain_tags": ["calendar", "sports"],
            "connector": "ics_calendar",
            "enabled": True,
            "sensitivity": "low",
            "access_mode": "read_only",
            "connector_config": {
                "url": "https://example.com/secret.ics",
                "timezone": "America/Toronto",
            },
            "retrieval": {
                "default_mode": "targeted",
                "max_results": 10,
                "max_bytes": 100000,
                "max_text_chars": 40000,
                "lookback_days": 7,
                "lookahead_days": 365,
                "allow_full_fetch": False,
            },
        }
    )

    class FakeConnector:
        async def check_health(self, source_config: SourceConfig):
            return SourceHealth(
                status=SourceStatus.UNAVAILABLE,
                last_checked_at=datetime(2026, 6, 10, tzinfo=UTC),
                last_error="source_unavailable",
            )

    monkeypatch.setattr("app.registry.get_connector", lambda _: FakeConnector())

    registry = await build_source_registry([source_config])

    detail = registry.get_source("calendar_sports")

    assert detail is not None
    assert detail.profile.summary == "ICS calendar source with read-only event retrieval."
    assert detail.retrieval.default_mode.value == "targeted"
    assert detail.status == "unavailable"
    assert detail.last_error == "source_unavailable"
    assert "secret.ics" not in detail.model_dump_json()
    assert detail.display_name == "Sports Calendar"
    assert detail.domain_tags == ["calendar", "sports"]


@pytest.mark.anyio
async def test_disabled_source_returns_disabled_without_connector_check(
    source_config_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_config = source_config_factory(enabled=False)

    class FakeConnector:
        async def check_health(self, source_config: SourceConfig):
            raise AssertionError("disabled sources should not run health checks")

    monkeypatch.setattr("app.registry.get_connector", lambda _: FakeConnector())

    registry = await build_source_registry([source_config])

    entry = registry.list_sources()[0]
    assert entry.status == "disabled"
    assert entry.last_error is None
