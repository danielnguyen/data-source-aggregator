from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError

from app.connectors.base import capabilities_for_connector, get_connector
from app.errors import SourceConfigValidationError
from app.models import (
    MAX_SOURCE_COUNT,
    ContextPackSourceDiagnostic,
    InventoryStatus,
    PublicSourceIdentifier,
    PublicSourceRegistryEntry,
    Sensitivity,
    SourceConfig,
    SourceHealth,
    SourceProfile,
    SourceRegistryDetail,
    SourceStatus,
)
from app.services.relevance import build_query_relevance_profile, overlap_score, tokenize_text

_registry_logger = logging.getLogger("uvicorn.error.data_source_aggregator.registry")
_public_identifier_adapter = TypeAdapter(PublicSourceIdentifier)
_PUBLIC_ENTRY_FIELDS = frozenset(
    {
        "source_id",
        "display_name",
        "connector",
        "domain_tags",
        "sensitivity",
        "access_mode",
        "capabilities",
        "enabled",
        "status",
        "last_checked_at",
        "last_error",
        "authority_role",
        "scope_refs",
        "content_fields",
    }
)
_PUBLIC_VALIDATION_REASONS = {
    "string_pattern_mismatch": "invalid_identifier",
    "string_too_long": "value_too_long",
    "too_long": "collection_too_large",
    "duplicate_items": "duplicate_items",
    "literal_error": "unsupported_value",
}


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
        *,
        inventory_status: InventoryStatus,
        loaded: bool,
    ) -> None:
        self._entries = {entry.source_id: entry for entry in entries}
        self._source_configs = {
            source_config.source_id: source_config
            for source_config in source_configs
        }
        self._public_entries: list[PublicSourceRegistryEntry] = []
        quarantined_count = 0
        for entry in entries:
            public_entry = _project_public_entry(
                entry,
                self._source_configs.get(entry.source_id),
            )
            if public_entry is None:
                quarantined_count += 1
                continue
            self._public_entries.append(public_entry)
        self._base_inventory_status = inventory_status
        self._inventory_status = _effective_public_inventory_status(
            inventory_status,
            quarantined=quarantined_count > 0,
        )
        self._loaded = loaded

    @property
    def inventory_status(self) -> InventoryStatus:
        return self._inventory_status

    @property
    def loaded(self) -> bool:
        return self._loaded

    def list_sources(self) -> list[PublicSourceRegistryEntry]:
        return list(self._public_entries)

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


async def build_source_registry(
    source_configs: list[SourceConfig],
    *,
    inventory_status: InventoryStatus = InventoryStatus.COMPLETE,
) -> SourceRegistry:
    _validate_source_configs(source_configs)
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
                authority_role=source_config.authority_role,
                status=health.status.value,
                last_checked_at=health.last_checked_at,
                last_error=health.last_error,
                scope_refs=source_config.scope_refs,
                retrieval=source_config.retrieval,
                profile=_build_source_profile(source_config),
            )
        )

    return SourceRegistry(
        entries,
        source_configs,
        inventory_status=inventory_status,
        loaded=True,
    )


def build_empty_source_registry() -> SourceRegistry:
    return SourceRegistry(
        [],
        [],
        inventory_status=InventoryStatus.UNKNOWN,
        loaded=False,
    )


def _project_public_entry(
    entry: SourceRegistryDetail,
    source_config: SourceConfig | None,
) -> PublicSourceRegistryEntry | None:
    projection = entry.model_dump()
    if projection.get("scope_refs") is None:
        projection.pop("scope_refs", None)
    projection.pop("retrieval", None)
    projection.pop("profile", None)
    if (
        source_config is not None
        and source_config.connector == "google_sheets"
        and source_config.result_text is not None
        and "include_fields" in source_config.result_text
    ):
        projection["content_fields"] = source_config.result_text["include_fields"]
    try:
        return PublicSourceRegistryEntry.model_validate(projection)
    except ValidationError as error:
        field, reason = _public_validation_diagnostic(error)
        if _is_public_identifier(entry.source_id):
            _registry_logger.warning(
                "public_source_projection_quarantined "
                "component=data-source-aggregator field=%s reason=%s source_id=%s",
                field,
                reason,
                entry.source_id,
            )
        else:
            _registry_logger.warning(
                "public_source_projection_quarantined "
                "component=data-source-aggregator field=%s reason=%s "
                "source_id_state=omitted",
                field,
                reason,
            )
        return None


def _public_validation_diagnostic(error: ValidationError) -> tuple[str, str]:
    issues = error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )
    issue = issues[0] if issues else {}
    location = issue.get("loc", ())
    field = location[0] if location and location[0] in _PUBLIC_ENTRY_FIELDS else "entry"
    reason = _PUBLIC_VALIDATION_REASONS.get(issue.get("type"), "invalid_value")
    return str(field), reason


def _is_public_identifier(value: str) -> bool:
    try:
        _public_identifier_adapter.validate_python(value)
    except ValidationError:
        return False
    return True


def _effective_public_inventory_status(
    base_status: InventoryStatus,
    *,
    quarantined: bool,
) -> InventoryStatus:
    if quarantined and base_status == InventoryStatus.COMPLETE:
        return InventoryStatus.PARTIAL
    return base_status


def _validate_source_configs(source_configs: list[SourceConfig]) -> None:
    if len(source_configs) > MAX_SOURCE_COUNT:
        raise SourceConfigValidationError(
            f"Configured source inventory exceeds {MAX_SOURCE_COUNT} sources."
        )
    source_ids = [source_config.source_id for source_config in source_configs]
    if len(set(source_ids)) != len(source_ids):
        raise SourceConfigValidationError(
            "Configured source inventory contains duplicate source IDs."
        )


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
