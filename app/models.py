from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    display_name: str = Field(min_length=1)
    connector: str = Field(min_length=1)
    enabled: bool
    description: str | None = None
    domain_tags: list[str] = Field(min_length=1)
    sensitivity: Sensitivity
    access_mode: AccessMode
    connector_config: dict[str, object]
    retrieval: RetrievalConfig
    result_text: dict[str, object] | None = None

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
    status: str
    last_checked_at: datetime | None
    last_error: str | None = None


class SourceProfile(BaseModel):
    summary: str
    content_types: list[str]


class SourceRegistryDetail(SourceRegistryEntry):
    retrieval: RetrievalConfig
    profile: SourceProfile


class SourceListResponse(BaseModel):
    sources: list[SourceRegistryEntry]


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


class AvailableContext(BaseModel):
    context_mode: str
    description: str


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
    available_context: list[AvailableContext] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RetrievalBudgetSummary(BaseModel):
    max_results: int | None = None
    returned_results: int
    estimated_bytes: int
    truncated: bool


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
