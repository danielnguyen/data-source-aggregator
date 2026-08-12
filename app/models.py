from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticCustomError

MAX_SOURCE_COUNT = 32


class AccessMode(str, Enum):
    READ_ONLY = "read_only"


class Sensitivity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    RESTRICTED = "restricted"


class RetrievalMode(str, Enum):
    TARGETED = "targeted"
    EXPANDED = "expanded"
    CONTEXT = "context"
    PROFILE = "profile"
    FETCH = "fetch"


class Confidence(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CacheStatus(str, Enum):
    LIVE = "live"
    CACHED = "cached"
    STALE = "stale"
    UNKNOWN = "unknown"


class AuditStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class SourceStatus(str, Enum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class SourceAuthorityRole(str, Enum):
    AUTHORITATIVE = "authoritative"
    SUPPLEMENTAL = "supplemental"
    UNKNOWN = "unknown"


class InventoryScope(str, Enum):
    CONFIGURED_SOURCES = "configured_sources"


class InventoryStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "data-source-aggregator"


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    default_mode: RetrievalMode
    max_results: int = Field(ge=1)
    max_bytes: int = Field(ge=1)
    max_text_chars: int = Field(ge=1)
    allow_full_fetch: bool


MaterialScopeIdentifier = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]

PublicSourceIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]


class MaterialScopeReferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    time: MaterialScopeIdentifier | None = None
    version: MaterialScopeIdentifier | None = None
    domain: MaterialScopeIdentifier | None = None
    project: MaterialScopeIdentifier | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_supplied_values(cls, value: object) -> object:
        if isinstance(value, Mapping):
            if not value:
                raise ValueError("scope_refs must contain at least one value.")
            if any(item is None for item in value.values()):
                raise ValueError("scope_refs values must not be null.")
        return value

    @model_validator(mode="after")
    def validate_non_empty(self) -> "MaterialScopeReferences":
        if not self.model_fields_set:
            raise ValueError("scope_refs must contain at least one value.")
        return self

    @model_serializer(mode="wrap")
    def omit_unsupplied_dimensions(self, handler):
        return {
            key: value
            for key, value in handler(self).items()
            if key in self.model_fields_set
        }


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    display_name: str = Field(min_length=1)
    description: str | None = None
    domain_tags: list[str] = Field(min_length=1)
    connector: str = Field(min_length=1)
    enabled: bool
    authority_role: SourceAuthorityRole = SourceAuthorityRole.UNKNOWN
    sensitivity: Sensitivity
    access_mode: AccessMode
    connector_config: dict[str, object]
    retrieval: RetrievalConfig
    result_text: dict[str, object] | None = None
    scope_refs: MaterialScopeReferences | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_scope_refs_object(cls, value: object) -> object:
        if (
            isinstance(value, Mapping)
            and "scope_refs" in value
            and value["scope_refs"] is None
        ):
            raise ValueError("scope_refs must not be null.")
        return value

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
            raise ValueError("source_id must match [a-z0-9][a-z0-9_-]*.")
        return value

    @field_validator("domain_tags")
    @classmethod
    def validate_domain_tags(cls, value: list[str]) -> list[str]:
        if any(not tag or not tag.strip() for tag in value):
            raise ValueError("domain_tags must not contain empty values.")
        return value


class SourceRegistryEntry(BaseModel):
    source_id: str
    display_name: str
    connector: str
    domain_tags: list[str]
    sensitivity: Sensitivity
    access_mode: AccessMode
    capabilities: list[str]
    enabled: bool
    authority_role: SourceAuthorityRole
    status: str
    last_checked_at: datetime | None
    last_error: str | None = None
    scope_refs: MaterialScopeReferences | None = None

    @model_serializer(mode="wrap")
    def omit_absent_scope_refs(self, handler):
        serialized = handler(self)
        if self.scope_refs is None:
            serialized.pop("scope_refs", None)
        return serialized


class PublicSourceRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: PublicSourceIdentifier
    display_name: str = Field(min_length=1, max_length=240)
    connector: PublicSourceIdentifier
    domain_tags: list[PublicSourceIdentifier] = Field(max_length=8)
    sensitivity: Sensitivity
    access_mode: AccessMode
    capabilities: list[Literal["profile", "search", "fetch", "context"]] = Field(
        max_length=4
    )
    enabled: bool
    authority_role: SourceAuthorityRole
    status: Literal["ready", "unavailable", "disabled", "unknown"]
    last_checked_at: datetime | None
    last_error: str | None = Field(default=None, max_length=240)
    scope_refs: MaterialScopeReferences | None = None

    @field_validator("domain_tags", "capabilities")
    @classmethod
    def validate_unique_items(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise PydanticCustomError("duplicate_items", "Collection items must be unique.")
        return value

    @model_validator(mode="after")
    def validate_scope_refs_presence(self) -> "PublicSourceRegistryEntry":
        if "scope_refs" in self.model_fields_set and self.scope_refs is None:
            raise PydanticCustomError(
                "invalid_value",
                "scope_refs must not be null when supplied.",
            )
        return self

    @model_serializer(mode="wrap")
    def omit_absent_scope_refs(self, handler):
        serialized = handler(self)
        if self.scope_refs is None:
            serialized.pop("scope_refs", None)
        return serialized


class SourceHealth(BaseModel):
    status: SourceStatus
    last_checked_at: datetime
    last_error: str | None = None


class SourceProfile(BaseModel):
    summary: str
    content_types: list[str]


class SourceRegistryDetail(SourceRegistryEntry):
    retrieval: RetrievalConfig
    profile: SourceProfile


class SourceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_scope: InventoryScope
    inventory_status: InventoryStatus
    sources: list[PublicSourceRegistryEntry] = Field(max_length=MAX_SOURCE_COUNT)


class SourceDetailResponse(BaseModel):
    source: SourceRegistryDetail


class RetrievalBudget(BaseModel):
    max_results: int | None = Field(default=None, ge=1)
    max_bytes: int | None = Field(default=None, ge=1)
    max_text_chars: int | None = Field(default=None, ge=1)
    max_rows: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_any_budget(self) -> "RetrievalBudget":
        if (
            self.max_results is None
            and self.max_bytes is None
            and self.max_text_chars is None
            and self.max_rows is None
        ):
            raise ValueError("At least one retrieval budget field must be provided.")
        return self


class EffectiveRetrievalBudget(BaseModel):
    max_results: int | None = None
    max_bytes: int | None = None
    max_text_chars: int | None = None
    max_rows: int | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    source_ids: list[str] | None = None
    domain_tags: list[str] | None = None
    retrieval_mode: RetrievalMode = RetrievalMode.TARGETED
    max_results: int | None = Field(default=None, ge=1)
    allowed_sensitivity: Sensitivity = Sensitivity.LOW
    budget: RetrievalBudget | None = None
    include_raw: bool = True

    @field_validator("source_ids", "domain_tags")
    @classmethod
    def validate_optional_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and not value:
            raise ValueError("List must not be empty when provided.")
        return value


class FetchRequest(BaseModel):
    source_ref: str = Field(min_length=1)
    include_raw: bool = True
    budget: RetrievalBudget | None = None


class ContextRequest(BaseModel):
    source_ref: str = Field(min_length=1)
    context_mode: str = Field(min_length=1)
    budget: RetrievalBudget | None = None


class ContextPackRequest(BaseModel):
    query: str = Field(min_length=1)
    source_ids: list[str] | None = None
    domain_tags: list[str] | None = None
    retrieval_mode: RetrievalMode = RetrievalMode.TARGETED
    allowed_sensitivity: Sensitivity = Sensitivity.LOW
    budget: RetrievalBudget | None = None

    @field_validator("source_ids", "domain_tags")
    @classmethod
    def validate_optional_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and not value:
            raise ValueError("List must not be empty when provided.")
        return value


class AvailableContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    context_mode: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    description: str = Field(min_length=1, max_length=500)


class ResultEnvelope(BaseModel):
    result_id: str
    source_type: str
    source_id: str
    source_name: str
    source_ref: str
    retrieved_at: datetime
    source_modified_at: datetime | None = None
    cache_status: CacheStatus = CacheStatus.UNKNOWN
    title: str
    content_type: str
    text: str
    url: str | None = None
    confidence: Confidence = Confidence.NONE
    raw: dict[str, object] | None = None
    available_context: list[AvailableContext] = Field(default_factory=list, max_length=16)
    warnings: list[str] = Field(default_factory=list)
    record_date: date | None = Field(default=None, exclude=True)

    @field_validator("available_context", mode="before")
    @classmethod
    def validate_available_context_collection(
        cls,
        value: object,
    ) -> object:
        if not isinstance(value, list):
            raise ValueError("Available context must be a list.")
        return value

    @field_validator("available_context")
    @classmethod
    def validate_unique_context_modes(
        cls,
        value: list[AvailableContext],
    ) -> list[AvailableContext]:
        context_modes = [descriptor.context_mode for descriptor in value]
        if len(set(context_modes)) != len(context_modes):
            raise ValueError("Available context modes must be unique.")
        return value


class RetrievalBudgetSummary(BaseModel):
    max_results: int | None = None
    returned_results: int
    estimated_bytes: int
    truncated: bool


class ContextPackItem(BaseModel):
    result_id: str
    source_type: str
    source_id: str
    source_name: str
    source_ref: str
    retrieved_at: datetime
    source_modified_at: datetime | None = None
    title: str
    content_type: str
    text: str
    confidence: Confidence = Confidence.NONE
    available_context: list[AvailableContext] = Field(default_factory=list, max_length=16)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("available_context", mode="before")
    @classmethod
    def validate_available_context_collection(
        cls,
        value: object,
    ) -> object:
        if not isinstance(value, list):
            raise ValueError("Available context must be a list.")
        return value

    @field_validator("available_context")
    @classmethod
    def validate_unique_context_modes(
        cls,
        value: list[AvailableContext],
    ) -> list[AvailableContext]:
        context_modes = [descriptor.context_mode for descriptor in value]
        if len(set(context_modes)) != len(context_modes):
            raise ValueError("Available context modes must be unique.")
        return value


class ContextPackSourceDiagnostic(BaseModel):
    source_id: str
    score: int
    score_band: str
    reasons: list[str] = Field(default_factory=list)


class ContextPackDiagnostics(BaseModel):
    selection_mode: str
    considered_source_ids: list[str]
    selected_source_ids: list[str]
    source_diagnostics: list[ContextPackSourceDiagnostic] = Field(default_factory=list)
    ranking_mode: str
    candidate_counts_by_source: dict[str, int] = Field(default_factory=dict)
    budget_truncated_candidates: bool = False


class SearchResponse(BaseModel):
    query_id: str
    query: str
    answerable: bool
    confidence: Confidence
    retrieval_mode: RetrievalMode
    results: list[ResultEnvelope]
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, object]] = Field(default_factory=list)
    budget: RetrievalBudgetSummary


class FetchResponse(BaseModel):
    query_id: str
    answerable: bool
    confidence: Confidence
    retrieval_mode: RetrievalMode = RetrievalMode.FETCH
    results: list[ResultEnvelope]
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, object]] = Field(default_factory=list)
    budget: RetrievalBudgetSummary


class ContextResponse(BaseModel):
    query_id: str
    answerable: bool
    confidence: Confidence
    retrieval_mode: RetrievalMode = RetrievalMode.CONTEXT
    results: list[ResultEnvelope]
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, object]] = Field(default_factory=list)
    budget: RetrievalBudgetSummary


class ContextPackResponse(BaseModel):
    query_id: str
    query: str
    sources_used: list[str]
    items: list[ContextPackItem]
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, object]] = Field(default_factory=list)
    budget: RetrievalBudgetSummary
    diagnostics: ContextPackDiagnostics | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail


class AuditEvent(BaseModel):
    event_id: str
    timestamp: datetime
    operation: str
    caller: str = "unknown"
    source_ids: list[str] = Field(default_factory=list)
    query: str | None = None
    source_ref: str | None = None
    result_count: int
    estimated_bytes: int
    status: AuditStatus
    error_code: str | None = None
