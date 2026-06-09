from __future__ import annotations

from datetime import UTC, datetime

from app.connectors.base import capabilities_for_connector
from app.models import SourceConfig, SourceProfile, SourceRegistryDetail, SourceRegistryEntry


class SourceRegistry:
    def __init__(self, entries: list[SourceRegistryDetail]) -> None:
        self._entries = {entry.source_id: entry for entry in entries}

    def list_sources(self) -> list[SourceRegistryEntry]:
        return [
            SourceRegistryEntry.model_validate(entry.model_dump())
            for entry in self._entries.values()
        ]

    def get_source(self, source_id: str) -> SourceRegistryDetail | None:
        return self._entries.get(source_id)


def build_source_registry(source_configs: list[SourceConfig]) -> SourceRegistry:
    checked_at = datetime.now(UTC)
    entries: list[SourceRegistryDetail] = []

    for source_config in source_configs:
        capabilities = capabilities_for_connector(source_config.connector)
        status = "ready" if source_config.enabled else "disabled"
        entries.append(
            SourceRegistryDetail(
                source_id=source_config.source_id,
                display_name=source_config.display_name,
                connector=source_config.connector,
                domain_tags=source_config.domain_tags,
                sensitivity=source_config.sensitivity,
                access_mode=source_config.access_mode,
                capabilities=capabilities,
                enabled=source_config.enabled,
                status=status,
                last_checked_at=checked_at,
                last_error=None,
                retrieval=source_config.retrieval,
                profile=_build_source_profile(source_config),
            )
        )

    return SourceRegistry(entries)


def _build_source_profile(source_config: SourceConfig) -> SourceProfile:
    if source_config.connector == "google_sheets":
        worksheet = str(source_config.connector_config.get("worksheet", "worksheet"))
        return SourceProfile(
            summary=f"Google Sheet source using worksheet {worksheet}.",
            content_types=["spreadsheet_row", "spreadsheet_range"],
        )

    if source_config.connector == "ics_calendar":
        return SourceProfile(
            summary="ICS calendar source with read-only event retrieval.",
            content_types=["calendar_event", "calendar_profile"],
        )

    return SourceProfile(summary="Configured source.", content_types=["source_record"])

