from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_source_configs
from app.credentials import load_credential_registry
from app.errors import SourceConfigValidationError


def test_loads_default_credentials_config_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    credentials_path = project_dir / "config" / "credentials.yaml"
    credentials_path.parent.mkdir(parents=True)
    credentials_path.write_text(
        """
credentials:
  google_sheets_readonly:
    type: google_service_account_file
    path: secrets/google_sheets_readonly.json
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(project_dir)

    credential_registry = load_credential_registry()

    assert "google_sheets_readonly" in credential_registry.credentials


def test_loads_credentials_config_from_override_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "custom-credentials.yaml"
    credentials_path.write_text(
        """
credentials:
  github_readonly:
    type: token_file
    path: secrets/github_readonly_token
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))

    credential_registry = load_credential_registry()

    assert "github_readonly" in credential_registry.credentials


def test_validates_allowed_credential_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "credentials.yaml"
    credentials_path.write_text(
        """
credentials:
  bad_credential:
    type: nope
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))

    with pytest.raises(SourceConfigValidationError):
        load_credential_registry()


def test_rejects_invalid_credential_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "credentials.yaml"
    credentials_path.write_text(
        """
credentials:
  Invalid Credential:
    type: google_application_default
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))

    with pytest.raises(SourceConfigValidationError):
        load_credential_registry()


def test_rejects_google_service_account_file_without_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "credentials.yaml"
    credentials_path.write_text(
        """
credentials:
  google_sheets_readonly:
    type: google_service_account_file
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))

    with pytest.raises(SourceConfigValidationError):
        load_credential_registry()


def test_rejects_token_file_without_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "credentials.yaml"
    credentials_path.write_text(
        """
credentials:
  github_readonly:
    type: token_file
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))

    with pytest.raises(SourceConfigValidationError):
        load_credential_registry()


def test_allows_google_application_default_without_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "credentials.yaml"
    credentials_path.write_text(
        """
credentials:
  google_application_default:
    type: google_application_default
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))

    credential_registry = load_credential_registry()

    assert credential_registry.credentials["google_application_default"].path is None


def test_enabled_google_sheets_source_with_known_credentials_ref_passes(
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
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "source.yaml").write_text(
        """
source_id: vehicle_log_primary
connector: google_sheets
enabled: true
public_profile:
  display_name: Vehicle Log
  description: Example vehicle records.
  domain_tags: [vehicle]
private_profile:
  display_name: Example Private Vehicle Log
  description: Private operator notes for a configured vehicle sheet.
  domain_tags: [vehicle_detail]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: sheet-id
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

    source_configs = load_source_configs(source_dir)

    assert len(source_configs) == 1


def test_enabled_google_sheets_source_with_unknown_credentials_ref_fails_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "credentials.yaml"
    credentials_path.write_text("credentials: {}\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "source.yaml").write_text(
        """
source_id: vehicle_log_primary
connector: google_sheets
enabled: true
public_profile:
  display_name: Vehicle Log
  description: Example vehicle records.
  domain_tags: [vehicle]
private_profile:
  display_name: Example Private Vehicle Log
  description: Private operator notes for a configured vehicle sheet.
  domain_tags: [vehicle_detail]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: sheet-id
  worksheet: Maintenance
  header_row: 1
  credentials_ref: missing_credential
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: true
""",
        encoding="utf-8",
    )

    with pytest.raises(SourceConfigValidationError, match="missing_credential"):
        load_source_configs(source_dir)


def test_enabled_source_requiring_credentials_fails_if_credentials_config_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CREDENTIALS_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "source.yaml").write_text(
        """
source_id: vehicle_log_primary
connector: google_sheets
enabled: true
public_profile:
  display_name: Vehicle Log
  description: Example vehicle records.
  domain_tags: [vehicle]
private_profile:
  display_name: Example Private Vehicle Log
  description: Private operator notes for a configured vehicle sheet.
  domain_tags: [vehicle_detail]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: sheet-id
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

    with pytest.raises(SourceConfigValidationError, match="google_sheets_readonly"):
        load_source_configs(source_dir)


def test_disabled_source_with_unknown_credentials_ref_is_ignored_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_path = tmp_path / "credentials.yaml"
    credentials_path.write_text("credentials: {}\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_CONFIG_PATH", str(credentials_path))
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "source.yaml").write_text(
        """
source_id: vehicle_log_primary
connector: google_sheets
enabled: false
public_profile:
  display_name: Vehicle Log
  description: Example vehicle records.
  domain_tags: [vehicle]
private_profile:
  display_name: Example Private Vehicle Log
  description: Private operator notes for a configured vehicle sheet.
  domain_tags: [vehicle_detail]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: sheet-id
  worksheet: Maintenance
  header_row: 1
  credentials_ref: missing_credential
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
        source_configs = load_source_configs(source_dir)

    assert source_configs == []
