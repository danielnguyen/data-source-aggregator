from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.connectors.base import capabilities_for_connector, get_connector
from app.models import (
    ContextPackSourceDiagnostic,
    Sensitivity,
    SourceConfig,
    SourceHealth,
    SourceProfile,
    SourceRegistryDetail,
    SourceRegistryEntry,
    SourceStatus,
)
from app.services.relevance import build_query_relevance_profile, overlap_score, tokenize_text


@dataclass(frozen=True)
class RankedSource:
    source_config: SourceConfig
    score: int
    matched_terms: set[str]
    reasons: list[str]

    @property
    def source_id(self) -> str:
        return self.source_config.source_id


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

    def rank_sources_for_query(
        self,
        *,
        query: str,
        allowed_sensitivity: Sensitivity,
        required_capability: str,
        domain_tags: list[str] | None = None,
    ) -> tuple[str, list[SourceConfig], list[ContextPackSourceDiagnostic]]:
        eligible_sources = self.select_sources(
            source_ids=None,
            domain_tags=domain_tags,
            allowed_sensitivity=allowed_sensitivity,
            required_capability=required_capability,
        )
        ranked_sources = [
            self._score_source_for_query(query, source_config)
            for source_config in eligible_sources
        ]
        ranked_sources.sort(
            key=lambda ranked_source: (-ranked_source.score, ranked_source.source_id)
        )

        positive_sources = [
            ranked_source for ranked_source in ranked_sources if ranked_source.score > 0
        ]
        if domain_tags:
            selection_mode = "domain_tags"
            selected_sources = [ranked_source.source_config for ranked_source in ranked_sources]
        elif self._should_use_broad_fallback(eligible_sources, positive_sources):
            selection_mode = "broad_fallback"
            selected_sources = [ranked_source.source_config for ranked_source in ranked_sources]
        else:
            selection_mode = "query_relevance"
            selected_sources = [ranked_source.source_config for ranked_source in positive_sources]

        diagnostics = [
            ContextPackSourceDiagnostic(
                source_id=ranked_source.source_id,
                score=ranked_source.score,
                score_band=_score_band(ranked_source.score),
                reasons=ranked_source.reasons,
            )
            for ranked_source in ranked_sources
        ]
        return selection_mode, selected_sources, diagnostics

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

    def _score_source_for_query(
        self,
        query: str,
        source_config: SourceConfig,
    ) -> RankedSource:
        entry = self._entries[source_config.source_id]
        query_tokens = build_query_relevance_profile(query).tokens
        field_matches: list[tuple[str, int, set[str]]] = [
            (
                "source_id_match",
                *overlap_score(
                    query_tokens,
                    tokenize_text(source_config.source_id),
                    weight=5,
                ),
            ),
            (
                "display_name_match",
                *overlap_score(
                    query_tokens,
                    tokenize_text(source_config.display_name),
                    weight=8,
                ),
            ),
            (
                "domain_tag_match",
                *overlap_score(
                    query_tokens,
                    tokenize_text(" ".join(source_config.domain_tags)),
                    weight=7,
                ),
            ),
            (
                "description_match",
                *overlap_score(
                    query_tokens,
                    tokenize_text(source_config.description),
                    weight=5,
                ),
            ),
            (
                "connector_match",
                *overlap_score(
                    query_tokens,
                    tokenize_text(source_config.connector),
                    weight=4,
                ),
            ),
            (
                "profile_summary_match",
                *overlap_score(query_tokens, tokenize_text(entry.profile.summary), weight=3),
            ),
            (
                "content_type_match",
                *overlap_score(
                    query_tokens,
                    tokenize_text(" ".join(entry.profile.content_types)),
                    weight=3,
                ),
            ),
        ]

        score = 0
        matched_terms: set[str] = set()
        reasons: list[str] = []
        for reason, partial_score, partial_matches in field_matches:
            if partial_score <= 0:
                continue
            score += partial_score
            matched_terms.update(partial_matches)
            reasons.append(reason)

        return RankedSource(
            source_config=source_config,
            score=score,
            matched_terms=matched_terms,
            reasons=reasons,
        )

    def _should_use_broad_fallback(
        self,
        eligible_sources: list[SourceConfig],
        positive_sources: list[RankedSource],
    ) -> bool:
        if not eligible_sources or not positive_sources:
            return True

        strongest_source = positive_sources[0]
        return strongest_source.score < 7 and len(strongest_source.matched_terms) < 2


async def build_source_registry(source_configs: list[SourceConfig]) -> SourceRegistry:
    entries: list[SourceRegistryDetail] = []

    for source_config in source_configs:
        capabilities = capabilities_for_connector(source_config.connector)
        health = await _check_source_health(source_config)
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
                status=health.status.value,
                last_checked_at=health.last_checked_at,
                last_error=health.last_error,
                retrieval=source_config.retrieval,
                profile=_build_source_profile(source_config),
            )
        )

    return SourceRegistry(entries, source_configs)


def build_empty_source_registry() -> SourceRegistry:
    return SourceRegistry([], [])


def _build_source_profile(source_config: SourceConfig) -> SourceProfile:
    if source_config.connector == "google_sheets":
        return SourceProfile(
            summary="Google Sheets source with read-only row and range retrieval.",
            content_types=["spreadsheet_row", "spreadsheet_range"],
        )

    if source_config.connector == "ics_calendar":
        return SourceProfile(
            summary="ICS calendar source with read-only event retrieval.",
            content_types=["calendar_event", "calendar_profile"],
        )

    return SourceProfile(summary="Configured source.", content_types=["source_record"])


async def _check_source_health(source_config: SourceConfig) -> SourceHealth:
    checked_at = datetime.now(UTC)
    if not source_config.enabled:
        return SourceHealth(
            status=SourceStatus.DISABLED,
            last_checked_at=checked_at,
            last_error=None,
        )

    connector = get_connector(source_config.connector)
    return await connector.check_health(source_config)


def _sensitivity_allowed(source_sensitivity: Sensitivity, allowed_sensitivity: Sensitivity) -> bool:
    order = {
        Sensitivity.LOW: 0,
        Sensitivity.MEDIUM: 1,
        Sensitivity.HIGH: 2,
        Sensitivity.RESTRICTED: 3,
    }
    return order[source_sensitivity] <= order[allowed_sensitivity]


def _score_band(score: int) -> str:
    if score >= 14:
        return "high"
    if score >= 7:
        return "medium"
    if score > 0:
        return "low"
    return "none"
