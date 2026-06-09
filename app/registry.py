from __future__ import annotations

from datetime import UTC, datetime

from app.connectors.base import capabilities_for_connector
from app.models import (
    Sensitivity,
    SourceConfig,
    SourceProfile,
    SourceRegistryDetail,
    SourceRegistryEntry,
)


class SourceRegistry:
    def __init__(
        self,
        entries: list[SourceRegistryDetail],
        source_configs: list[SourceConfig],
    ) -> None:
        self._entries = {entry.source_id: entry for entry in entries}
        self._source_configs = {
            source_config.source_id: source_config for source_config in source_configs
        }

    def list_sources(self) -> list[SourceRegistryEntry]:
        return [
            SourceRegistryEntry.model_validate(entry.model_dump())
            for entry in self._entries.values()
        ]

    def get_source(self, source_id: str) -> SourceRegistryDetail | None:
        return self._entries.get(source_id)

    def get_source_config(self, source_id: str) -> SourceConfig | None:
        return self._source_configs.get(source_id)

    def select_sources(
        self,
        *,
        source_ids: list[str] | None = None,
        domain_tags: list[str] | None = None,
        allowed_sensitivity: Sensitivity,
        required_capability: str,
    ) -> list[SourceConfig]:
        if source_ids:
            selected: list[SourceConfig] = []
            for source_id in source_ids:
                source_config = self._source_configs.get(source_id)
                entry = self._entries.get(source_id)
                if source_config is None or entry is None or not entry.enabled:
                    continue
                if required_capability not in entry.capabilities:
                    continue
                if not _sensitivity_allowed(entry.sensitivity, allowed_sensitivity):
                    continue
                selected.append(source_config)
            return selected

        matched: list[SourceConfig] = []
        requested_tags = set(domain_tags or [])
        for source_id, entry in self._entries.items():
            if not entry.enabled or required_capability not in entry.capabilities:
                continue
            if not _sensitivity_allowed(entry.sensitivity, allowed_sensitivity):
                continue
            if requested_tags and requested_tags.isdisjoint(entry.domain_tags):
                continue
            matched.append(self._source_configs[source_id])

        return matched


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

    return SourceRegistry(entries, source_configs)


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


def _sensitivity_allowed(source_sensitivity: Sensitivity, allowed_sensitivity: Sensitivity) -> bool:
    order = {
        Sensitivity.LOW: 0,
        Sensitivity.MEDIUM: 1,
        Sensitivity.HIGH: 2,
        Sensitivity.RESTRICTED: 3,
    }
    return order[source_sensitivity] <= order[allowed_sensitivity]
