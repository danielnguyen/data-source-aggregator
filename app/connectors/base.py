from __future__ import annotations

from collections.abc import Mapping

from app.errors import SourceConfigValidationError

CONNECTOR_CAPABILITIES: dict[str, list[str]] = {
    "google_sheets": ["profile", "search", "fetch"],
    "ics_calendar": ["profile", "search", "fetch"],
}


def supported_connectors() -> set[str]:
    return set(CONNECTOR_CAPABILITIES)


def capabilities_for_connector(connector: str) -> list[str]:
    return CONNECTOR_CAPABILITIES.get(connector, ["profile"])


def validate_connector_config(connector: str, connector_config: Mapping[str, object]) -> None:
    if connector not in CONNECTOR_CAPABILITIES:
        raise SourceConfigValidationError(
            f"Unsupported connector '{connector}'. Supported connectors: "
            f"{', '.join(sorted(CONNECTOR_CAPABILITIES))}."
        )

    if connector == "google_sheets":
        _require_any(connector_config, "spreadsheet_id", "spreadsheet_id_env")
        _require(connector_config, "worksheet")
        _require(connector_config, "header_row")
        return

    if connector == "ics_calendar":
        _require_any(connector_config, "url", "url_env")
        _require(connector_config, "timezone")


def _require(config: Mapping[str, object], key: str) -> None:
    value = config.get(key)
    if value in (None, ""):
        raise SourceConfigValidationError(f"Connector config must define '{key}'.")


def _require_any(config: Mapping[str, object], *keys: str) -> None:
    for key in keys:
        value = config.get(key)
        if value not in (None, ""):
            return

    joined = ", ".join(keys)
    raise SourceConfigValidationError(f"Connector config must define one of: {joined}.")

