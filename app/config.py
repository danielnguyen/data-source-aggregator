from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any

import yaml
from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError

from app.connectors.base import validate_connector_config
from app.credentials import CredentialRegistry, load_credential_registry
from app.errors import SourceConfigValidationError
from app.models import SourceConfig

DEFAULT_SOURCE_CONFIG_DIR = Path("config/sources")


def get_source_config_dir() -> Path:
    _load_local_dotenv()
    configured = os.getenv("SOURCE_CONFIG_DIR")
    if configured:
        return Path(configured)
    return DEFAULT_SOURCE_CONFIG_DIR


def load_source_configs(config_dir: Path | None = None) -> list[SourceConfig]:
    _load_local_dotenv()
    directory = (config_dir or get_source_config_dir()).resolve()
    if not directory.exists():
        return []

    credential_registry = load_credential_registry()
    configs: list[SourceConfig] = []
    for path in sorted(_iter_source_files(directory)):
        data = _load_yaml_file(path)
        try:
            resolved = _resolve_env_references(data)
            source_config = SourceConfig.model_validate(resolved)
            validate_connector_config(source_config.connector, source_config.connector_config)
            validate_source_credentials(source_config, credential_registry)
        except (ValidationError, SourceConfigValidationError) as exc:
            if _is_enabled_value(data):
                message = f"Invalid enabled source config '{path.name}': {exc}"
                raise SourceConfigValidationError(message) from exc

            warnings.warn(
                f"Ignoring invalid disabled source config '{path.name}': {exc}",
                stacklevel=2,
            )
            continue

        configs.append(source_config)

    return configs


def _iter_source_files(directory: Path) -> list[Path]:
    return [*directory.glob("*.yaml"), *directory.glob("*.yml")]


def _load_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    if not isinstance(payload, dict):
        raise SourceConfigValidationError(f"Config '{path.name}' must contain a YAML mapping.")
    return payload


def _resolve_env_references(value: Any) -> Any:
    if isinstance(value, dict):
        resolved: dict[str, Any] = {}
        for key, nested_value in value.items():
            if key.endswith("_env") and isinstance(nested_value, str):
                resolved_key = key[:-4]
                resolved[resolved_key] = _read_required_env(nested_value)
                continue

            resolved[key] = _resolve_env_references(nested_value)
        return resolved

    if isinstance(value, list):
        return [_resolve_env_references(item) for item in value]

    return value


def _read_required_env(name: str) -> str:
    value = os.getenv(name)
    if value in (None, ""):
        raise SourceConfigValidationError(
            f"Environment variable '{name}' is required but not set."
        )
    return value


def _is_enabled_value(data: dict[str, Any]) -> bool:
    return bool(data.get("enabled"))


def validate_source_credentials(
    source_config: SourceConfig,
    credential_registry: CredentialRegistry,
) -> None:
    credentials_ref = source_config.connector_config.get("credentials_ref")
    if not isinstance(credentials_ref, str) or not credentials_ref:
        return

    if credentials_ref not in credential_registry.credentials:
        raise SourceConfigValidationError(
            f"Source '{source_config.source_id}' references unknown credential_ref "
            f"'{credentials_ref}'."
        )


def _load_local_dotenv() -> None:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path, override=False)
