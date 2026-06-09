from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
            raise ValueError(
                "source_id must match [a-z0-9][a-z0-9_-]*."
            )
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

