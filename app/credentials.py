from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.errors import SourceConfigValidationError

DEFAULT_CREDENTIALS_CONFIG_PATH = Path("config/credentials.yaml")


class CredentialType(str, Enum):
    GOOGLE_SERVICE_ACCOUNT_FILE = "google_service_account_file"
    GOOGLE_APPLICATION_DEFAULT = "google_application_default"
    TOKEN_FILE = "token_file"


class CredentialConfig(BaseModel):
    type: CredentialType
    path: str | None = None

    @model_validator(mode="after")
    def validate_path_requirements(self) -> "CredentialConfig":
        if self.type in {
            CredentialType.GOOGLE_SERVICE_ACCOUNT_FILE,
            CredentialType.TOKEN_FILE,
        } and not self.path:
            raise ValueError(f"Credential type '{self.type.value}' requires 'path'.")
        return self


class CredentialRegistry(BaseModel):
    credentials: dict[str, CredentialConfig] = Field(default_factory=dict)

    @field_validator("credentials")
    @classmethod
    def validate_credential_ids(
        cls,
        credentials: dict[str, CredentialConfig],
    ) -> dict[str, CredentialConfig]:
        import re

        for credential_id in credentials:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", credential_id):
                raise ValueError("credential IDs must match [a-z0-9][a-z0-9_-]*.")
        return credentials


def get_credentials_config_path() -> Path:
    _load_local_dotenv()
    configured = os.getenv("CREDENTIALS_CONFIG_PATH")
    if configured:
        return Path(configured)
    return DEFAULT_CREDENTIALS_CONFIG_PATH


def load_credential_registry(credentials_path: Path | None = None) -> CredentialRegistry:
    _load_local_dotenv()
    path = (credentials_path or get_credentials_config_path()).resolve()
    if not path.exists():
        return CredentialRegistry()

    payload = _load_yaml_file(path)
    try:
        return CredentialRegistry.model_validate(payload)
    except ValidationError as exc:
        raise SourceConfigValidationError(
            f"Invalid credentials config '{path.name}': {exc}"
        ) from exc


def _load_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    if not isinstance(payload, dict):
        raise SourceConfigValidationError(
            f"Credentials config '{path.name}' must contain a YAML mapping."
        )
    return payload


def _load_local_dotenv() -> None:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path, override=False)
