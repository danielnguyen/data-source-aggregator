from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_source_configs
from app.errors import SourceConfigValidationError


def test_load_source_configs_resolves_env_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHEET_ID", "sheet-secret-id")
    credentials_path = tmp_path / "credentials.yaml"
    credentials_path.write_text(
        """
credentials:
  google_sheets_readonly:
    type: google_service_account_file
    path: secrets/google_sheets_readonly.json
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "source.yaml").write_text(
        """
source_id: jeep_wj_maintenance
display_name: Jeep WJ Maintenance Log
connector: google_sheets
enabled: true
domain_tags: [vehicle, maintenance]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id_env: SHEET_ID
  worksheet: Maintenance
  header_row: 1
  credentials_ref: google_sheets_readonly
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
""",
        encoding="utf-8",
    )

    configs = load_source_configs(source_dir)

    assert len(configs) == 1
    assert configs[0].connector_config["spreadsheet_id"] == "sheet-secret-id"
    assert "spreadsheet_id_env" not in configs[0].connector_config


def test_invalid_enabled_source_config_fails_loudly(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "invalid.yaml").write_text(
        """
source_id: Invalid Source Id
display_name: Invalid
connector: google_sheets
enabled: true
domain_tags: [vehicle]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: abc123
  worksheet: Maintenance
  header_row: 1
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
""",
        encoding="utf-8",
    )

    with pytest.raises(SourceConfigValidationError):
        load_source_configs(source_dir)


def test_invalid_disabled_source_config_is_ignored_with_warning(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "invalid-disabled.yaml").write_text(
        """
source_id: Invalid Source Id
display_name: Invalid
connector: google_sheets
enabled: false
domain_tags: [vehicle]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: abc123
  worksheet: Maintenance
  header_row: 1
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
""",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning):
        configs = load_source_configs(source_dir)

    assert configs == []


def test_enabled_config_with_missing_env_var_fails_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "credentials.yaml"
    credentials_path.write_text(
        """
credentials:
  google_sheets_readonly:
    type: google_service_account_file
    path: secrets/google_sheets_readonly.json
""",
        encoding="utf-8",
    )
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "missing-env.yaml").write_text(
        """
source_id: jeep_wj_maintenance
display_name: Jeep WJ Maintenance Log
connector: google_sheets
enabled: true
domain_tags: [vehicle]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id_env: MISSING_SHEET_ID
  worksheet: Maintenance
  header_row: 1
  credentials_ref: google_sheets_readonly
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))
    with pytest.raises(SourceConfigValidationError, match="MISSING_SHEET_ID"):
        load_source_configs(source_dir)


def test_disabled_config_with_missing_env_var_is_ignored_with_warning(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "missing-env-disabled.yaml").write_text(
        """
source_id: leafs_calendar
display_name: Toronto Maple Leafs Calendar
connector: ics_calendar
enabled: false
domain_tags: [sports]
sensitivity: low
access_mode: read_only
connector_config:
  url_env: MISSING_ICS_URL
  timezone: America/Toronto
retrieval:
  default_mode: targeted
  max_results: 10
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: false
""",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="missing-env-disabled.yaml"):
        configs = load_source_configs(source_dir)

    assert configs == []


def test_load_source_configs_reads_local_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    credentials_path = project_dir / "config" / "credentials.yaml"
    source_dir = project_dir / "config" / "sources"
    source_dir.mkdir(parents=True)
    credentials_path.write_text(
        """
credentials:
  google_sheets_readonly:
    type: google_service_account_file
    path: secrets/google_sheets_readonly.json
""",
        encoding="utf-8",
    )
    (project_dir / ".env").write_text(
        "DOTENV_SHEET_ID=sheet-from-dotenv\n",
        encoding="utf-8",
    )
    (source_dir / "source.yaml").write_text(
        """
source_id: jeep_wj_maintenance
display_name: Jeep WJ Maintenance Log
connector: google_sheets
enabled: true
domain_tags: [vehicle, maintenance]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id_env: DOTENV_SHEET_ID
  worksheet: Maintenance
  header_row: 1
  credentials_ref: google_sheets_readonly
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("DOTENV_SHEET_ID", raising=False)
    monkeypatch.chdir(project_dir)

    configs = load_source_configs(source_dir)

    assert len(configs) == 1
    assert configs[0].connector_config["spreadsheet_id"] == "sheet-from-dotenv"


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("sensitivity", "secret"),
        ("access_mode", "read_write"),
    ],
)
def test_invalid_shared_fields_fail_validation(
    tmp_path: Path,
    field_name: str,
    field_value: str,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    yaml_text = """
source_id: jeep_wj_maintenance
display_name: Jeep WJ Maintenance Log
connector: google_sheets
enabled: true
domain_tags: [vehicle]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: abc123
  worksheet: Maintenance
  header_row: 1
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
"""
    original_value = "low" if field_name == "sensitivity" else "read_only"
    yaml_text = yaml_text.replace(
        f"{field_name}: {original_value}",
        f"{field_name}: {field_value}",
    )
    (source_dir / "invalid.yaml").write_text(yaml_text, encoding="utf-8")

    with pytest.raises(SourceConfigValidationError):
        load_source_configs(source_dir)
