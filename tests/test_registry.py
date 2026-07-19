from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.errors import SourceConfigValidationError
from app.models import InventoryStatus, SourceConfig, SourceHealth, SourceStatus
from app.registry import build_empty_source_registry, build_source_registry


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
    assert dumped["authority_role"] == "unknown"
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
            "authority_role": "authoritative",
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
    assert detail.authority_role.value == "authoritative"
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
    assert registry.inventory_status is InventoryStatus.COMPLETE


@pytest.mark.anyio
async def test_rank_sources_for_query_prefers_vehicle_metadata(
    source_config_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vehicle_source = source_config_factory(
        source_id="vehicle_service_log",
        display_name="Vehicle Service Log",
        description="Combustion vehicle maintenance and oil service records.",
        domain_tags=["vehicle", "maintenance", "repair"],
    )
    holiday_source = source_config_factory(
        source_id="public_holiday_calendar",
        display_name="Public Holiday Calendar",
        description="National holiday and observance calendar.",
        domain_tags=["calendar", "holiday"],
        connector="ics_calendar",
        connector_config={
            "url": "https://example.test/holidays.ics",
            "timezone": "America/Toronto",
        },
    )

    class FakeConnector:
        async def check_health(self, source_config: SourceConfig):
            return SourceHealth(
                status=SourceStatus.READY,
                last_checked_at=datetime(2026, 6, 10, tzinfo=UTC),
                last_error=None,
            )

    monkeypatch.setattr("app.registry.get_connector", lambda _: FakeConnector())

    registry = await build_source_registry([holiday_source, vehicle_source])
    selection_mode, selected_sources, diagnostics = registry.rank_sources_for_query(
        query="Search my vehicle maintenance log and tell me when I last changed the oil.",
        allowed_sensitivity=vehicle_source.sensitivity,
        required_capability="search",
    )

    assert selection_mode == "query_relevance"
    assert [source.source_id for source in selected_sources] == ["vehicle_service_log"]
    assert diagnostics[0].source_id == "vehicle_service_log"
    assert diagnostics[0].score_band in {"medium", "high"}
    assert "domain_tag_match" in diagnostics[0].reasons


@pytest.mark.anyio
async def test_rank_sources_for_query_falls_back_broadly_when_match_is_weak(
    source_config_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vehicle_source = source_config_factory(
        source_id="vehicle_service_log",
        display_name="Vehicle Service Log",
        description="Combustion vehicle maintenance and oil service records.",
        domain_tags=["vehicle", "maintenance", "repair"],
    )
    calendar_source = source_config_factory(
        source_id="personal_calendar_agenda",
        display_name="Personal Calendar",
        description="Personal appointments and schedule.",
        domain_tags=["calendar", "appointment"],
        connector="ics_calendar",
        connector_config={
            "url": "https://example.test/personal.ics",
            "timezone": "America/Toronto",
        },
    )

    class FakeConnector:
        async def check_health(self, source_config: SourceConfig):
            return SourceHealth(
                status=SourceStatus.READY,
                last_checked_at=datetime(2026, 6, 10, tzinfo=UTC),
                last_error=None,
            )

    monkeypatch.setattr("app.registry.get_connector", lambda _: FakeConnector())

    registry = await build_source_registry([vehicle_source, calendar_source])
    selection_mode, selected_sources, diagnostics = registry.rank_sources_for_query(
        query="status",
        allowed_sensitivity=vehicle_source.sensitivity,
        required_capability="search",
    )

    assert selection_mode == "broad_fallback"
    assert {source.source_id for source in selected_sources} == {
        "vehicle_service_log",
        "personal_calendar_agenda",
    }
    assert all(diagnostic.score == 0 for diagnostic in diagnostics)


@pytest.mark.anyio
async def test_registry_order_and_authority_are_configured_not_inferred(
    source_config_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authoritative = source_config_factory(
        source_id="z_authoritative_records",
        display_name="Canonical Authoritative Records",
        description="Official complete source.",
        domain_tags=["official", "authoritative"],
        connector="ics_calendar",
        connector_config={
            "url": "https://private.example.test/authoritative.ics",
            "timezone": "UTC",
        },
    )
    supplemental = source_config_factory(
        source_id="a_supplemental",
        authority_role="supplemental",
    )

    class FakeConnector:
        async def check_health(self, source_config: SourceConfig):
            return SourceHealth(
                status=SourceStatus.UNAVAILABLE,
                last_checked_at=datetime(2026, 6, 10, tzinfo=UTC),
                last_error="source_unavailable",
            )

    monkeypatch.setattr("app.registry.get_connector", lambda _: FakeConnector())

    registry = await build_source_registry([authoritative, supplemental])

    entries = registry.list_sources()
    assert [entry.source_id for entry in entries] == [
        "z_authoritative_records",
        "a_supplemental",
    ]
    assert [entry.authority_role.value for entry in entries] == [
        "unknown",
        "supplemental",
    ]
    assert all(entry.status == "unavailable" for entry in entries)
    assert registry.inventory_status is InventoryStatus.COMPLETE


@pytest.mark.anyio
async def test_registry_rejects_duplicate_and_oversized_source_sets(
    source_config_factory,
) -> None:
    duplicate = source_config_factory(source_id="duplicate_source")
    with pytest.raises(SourceConfigValidationError, match="duplicate source IDs"):
        await build_source_registry([duplicate, duplicate])

    oversized = [
        source_config_factory(source_id=f"source_{index:02d}")
        for index in range(33)
    ]
    with pytest.raises(SourceConfigValidationError, match="exceeds 32 sources"):
        await build_source_registry(oversized)


@pytest.mark.anyio
async def test_registry_loaded_state_distinguishes_initial_and_loaded_empty() -> None:
    initial = build_empty_source_registry()
    loaded_complete = await build_source_registry([])
    loaded_partial = await build_source_registry(
        [],
        inventory_status=InventoryStatus.PARTIAL,
    )
    loaded_unknown = await build_source_registry(
        [],
        inventory_status=InventoryStatus.UNKNOWN,
    )

    assert initial.loaded is False
    assert initial.inventory_status is InventoryStatus.UNKNOWN
    assert loaded_complete.loaded is True
    assert loaded_complete.inventory_status is InventoryStatus.COMPLETE
    assert loaded_partial.loaded is True
    assert loaded_partial.inventory_status is InventoryStatus.PARTIAL
    assert loaded_unknown.loaded is True
    assert loaded_unknown.inventory_status is InventoryStatus.UNKNOWN
