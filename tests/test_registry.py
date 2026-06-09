from __future__ import annotations

from app.models import SourceConfig
from app.registry import build_source_registry


def test_build_source_registry_exposes_safe_fields_only() -> None:
    source_config = SourceConfig.model_validate(
        {
            "source_id": "vehicle_log_primary",
            "connector": "google_sheets",
            "enabled": True,
            "public_profile": {
                "display_name": "Vehicle Log - Primary",
                "description": "Personal vehicle operating records.",
                "domain_tags": ["vehicle", "maintenance"],
            },
            "private_profile": {
                "display_name": "Primary Vehicle Logs",
                "description": "Private operator description.",
                "domain_tags": ["vehicle_detail", "ownership_cost"],
            },
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

    registry = build_source_registry([source_config])

    entry = registry.list_sources()[0]
    dumped = entry.model_dump(mode="json")

    assert dumped["source_id"] == "vehicle_log_primary"
    assert dumped["display_name"] == "Vehicle Log - Primary"
    assert dumped["domain_tags"] == ["vehicle", "maintenance"]
    assert dumped["capabilities"] == ["profile", "search", "fetch", "context"]
    assert "connector_config" not in dumped
    assert "sheet-secret-id" not in str(dumped)
    assert "Primary Vehicle Logs" not in str(dumped)


def test_registry_detail_includes_safe_profile_and_retrieval() -> None:
    source_config = SourceConfig.model_validate(
        {
            "source_id": "calendar_sports",
            "connector": "ics_calendar",
            "enabled": True,
            "public_profile": {
                "display_name": "Sports Calendar",
                "description": "Sports schedule source.",
                "domain_tags": ["calendar", "sports"],
            },
            "private_profile": {
                "display_name": "Sports Team Calendar",
                "description": "Private subscribed feed.",
                "domain_tags": ["sports", "sports_team"],
            },
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

    registry = build_source_registry([source_config])

    detail = registry.get_source("calendar_sports")

    assert detail is not None
    assert detail.profile.summary == "ICS calendar source with read-only event retrieval."
    assert detail.retrieval.default_mode.value == "targeted"
    assert "secret.ics" not in detail.model_dump_json()
    assert detail.display_name == "Sports Calendar"
    assert detail.domain_tags == ["calendar", "sports"]
