from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_source_config_inventory, load_source_configs
from app.errors import SourceConfigValidationError
from app.models import InventoryStatus, SourceAuthorityRole


def _write_ics_source(
    source_dir: Path,
    filename: str,
    *,
    source_id: str,
    enabled: bool = True,
    authority_role: str | None = None,
    display_name: str = "Configured Calendar",
    description: str = "Configured calendar source.",
    domain_tags: str = "[calendar]",
    connector: str = "ics_calendar",
    scope_refs: str | None = None,
) -> None:
    authority_line = (
        f"authority_role: {authority_role}\n"
        if authority_role is not None
        else ""
    )
    scope_refs_block = (
        f"scope_refs:\n{scope_refs}\n"
        if scope_refs is not None
        else ""
    )
    (source_dir / filename).write_text(
        f"""
source_id: {source_id}
display_name: {display_name}
description: {description}
domain_tags: {domain_tags}
connector: {connector}
enabled: {str(enabled).lower()}
{authority_line}{scope_refs_block}sensitivity: low
access_mode: read_only
connector_config:
  url: https://private.example.test/{source_id}.ics
  timezone: UTC
retrieval:
  default_mode: targeted
  max_results: 10
  max_bytes: 100000
  max_text_chars: 40000
  allow_full_fetch: false
""",
        encoding="utf-8",
    )


def test_scope_refs_load_exactly_through_source_inventory(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "calendar.yaml",
        source_id="calendar_records",
        scope_refs=(
            "  time: fy2026\n"
            "  version: release-152\n"
            "  domain: scheduling\n"
            "  project: calendar-service"
        ),
    )

    result = load_source_config_inventory(source_dir)

    assert result.inventory_status is InventoryStatus.COMPLETE
    assert result.source_configs[0].scope_refs is not None
    assert result.source_configs[0].scope_refs.model_dump(mode="json") == {
        "time": "fy2026",
        "version": "release-152",
        "domain": "scheduling",
        "project": "calendar-service",
    }


def test_malformed_enabled_scope_refs_fail_closed(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "invalid-enabled.yaml",
        source_id="calendar_records",
        scope_refs="  time: fy2026\n  project: unsafe project",
    )

    with pytest.raises(
        SourceConfigValidationError,
        match="Invalid enabled source config 'invalid-enabled.yaml'",
    ):
        load_source_config_inventory(source_dir)


def test_malformed_disabled_scope_refs_are_omitted_as_partial(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "valid.yaml",
        source_id="valid_calendar",
    )
    _write_ics_source(
        source_dir,
        "invalid-disabled.yaml",
        source_id="invalid_calendar",
        enabled=False,
        scope_refs="  time: null",
    )

    with pytest.warns(UserWarning, match="invalid-disabled.yaml"):
        result = load_source_config_inventory(source_dir)

    assert [config.source_id for config in result.source_configs] == [
        "valid_calendar"
    ]
    assert result.inventory_status is InventoryStatus.PARTIAL


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
source_id: vehicle_log_primary
display_name: Vehicle Log - Primary
description: Personal vehicle operating records.
domain_tags: [vehicle, maintenance]
connector: google_sheets
enabled: true
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


def test_example_yaml_source_configs_are_ignored(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "vehicle_maintenance.example.yaml").write_text(
        """
source_id: vehicle_log_example
display_name: Vehicle Log
description: Example vehicle records.
domain_tags: [vehicle]
connector: google_sheets
enabled: true
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

    configs = load_source_configs(source_dir)

    assert configs == []


def test_example_yml_source_configs_are_ignored(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "vehicle_maintenance.example.yml").write_text(
        """
source_id: vehicle_log_example
display_name: Vehicle Log
description: Example vehicle records.
domain_tags: [vehicle]
connector: google_sheets
enabled: true
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

    configs = load_source_configs(source_dir)

    assert configs == []


def test_non_example_yaml_source_config_is_loaded(
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
    (source_dir / "vehicle_log_primary.yaml").write_text(
        """
source_id: vehicle_log_primary
display_name: Vehicle Log
description: Example vehicle records.
domain_tags: [vehicle]
connector: google_sheets
enabled: true
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

    configs = load_source_configs(source_dir)

    assert len(configs) == 1
    assert configs[0].source_id == "vehicle_log_primary"


def test_source_config_with_top_level_metadata_loads(
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
    (source_dir / "vehicle_log_primary.yaml").write_text(
        """
source_id: vehicle_log_primary
display_name: Vehicle Log - Primary
description: Personal vehicle operating records.
domain_tags: [vehicle, maintenance]
connector: google_sheets
enabled: true
sensitivity: medium
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

    configs = load_source_configs(source_dir)

    assert len(configs) == 1
    assert configs[0].display_name == "Vehicle Log - Primary"
    assert configs[0].domain_tags == ["vehicle", "maintenance"]


def test_config_missing_display_name_fails_loudly(
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
    (source_dir / "invalid.yaml").write_text(
        """
source_id: vehicle_log_primary
connector: google_sheets
enabled: true
description: Example vehicle records.
domain_tags: [vehicle]
sensitivity: medium
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

    with pytest.raises(SourceConfigValidationError):
        load_source_configs(source_dir)


def test_active_copy_is_loaded_while_example_template_stays_ignored(
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
    source_payload = """
source_id: vehicle_log_example
display_name: Vehicle Log
description: Example vehicle records.
domain_tags: [vehicle]
connector: google_sheets
enabled: true
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
"""
    (source_dir / "vehicle_maintenance.example.yaml").write_text(
        source_payload,
        encoding="utf-8",
    )
    (source_dir / "vehicle_log_example.yaml").write_text(
        source_payload,
        encoding="utf-8",
    )

    configs = load_source_configs(source_dir)

    assert len(configs) == 1
    assert configs[0].source_id == "vehicle_log_example"


def test_invalid_enabled_source_config_fails_loudly(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "invalid.yaml").write_text(
        """
source_id: Invalid Source Id
display_name: Invalid
description: Invalid test config.
domain_tags: [vehicle]
connector: google_sheets
enabled: true
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
description: Invalid test config.
domain_tags: [vehicle]
connector: google_sheets
enabled: false
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
source_id: vehicle_log_primary
display_name: Vehicle Log
description: Example vehicle records.
domain_tags: [vehicle]
connector: google_sheets
enabled: true
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
source_id: calendar_sports
display_name: Sports Calendar
description: Example sports schedule source.
domain_tags: [calendar, sports]
connector: ics_calendar
enabled: false
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
source_id: vehicle_log_primary
display_name: Vehicle Log - Primary
description: Personal vehicle operating records.
domain_tags: [vehicle, maintenance]
connector: google_sheets
enabled: true
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
source_id: vehicle_log_primary
display_name: Vehicle Log
description: Example vehicle records.
domain_tags: [vehicle]
connector: google_sheets
enabled: true
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


def test_legacy_authority_defaults_to_unknown(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "calendar.yaml",
        source_id="authoritative_records",
        display_name="Official Authoritative Records",
        description="The complete canonical source.",
        domain_tags="[official, authoritative]",
    )

    result = load_source_config_inventory(source_dir)

    assert result.inventory_status is InventoryStatus.COMPLETE
    assert len(result.source_configs) == 1
    assert result.source_configs[0].authority_role is SourceAuthorityRole.UNKNOWN


@pytest.mark.parametrize(
    "authority_role",
    ["authoritative", "supplemental", "unknown"],
)
def test_explicit_authority_roles_load_unchanged(
    tmp_path: Path,
    authority_role: str,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "calendar.yaml",
        source_id="calendar_records",
        authority_role=authority_role,
    )

    result = load_source_config_inventory(source_dir)

    assert result.inventory_status is InventoryStatus.COMPLETE
    assert result.source_configs[0].authority_role.value == authority_role


def test_existing_empty_and_example_only_directories_are_complete(
    tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    example_dir = tmp_path / "examples"
    example_dir.mkdir()
    _write_ics_source(
        example_dir,
        "calendar.example.yaml",
        source_id="ignored_example",
        authority_role="unknown",
    )

    empty_result = load_source_config_inventory(empty_dir)
    example_result = load_source_config_inventory(example_dir)

    assert empty_result.source_configs == []
    assert empty_result.inventory_status is InventoryStatus.COMPLETE
    assert example_result.source_configs == []
    assert example_result.inventory_status is InventoryStatus.COMPLETE


def test_missing_directory_is_unknown(tmp_path: Path) -> None:
    result = load_source_config_inventory(tmp_path / "missing")

    assert result.source_configs == []
    assert result.inventory_status is InventoryStatus.UNKNOWN


def test_invalid_disabled_omission_makes_inventory_partial(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "valid.yaml",
        source_id="valid_calendar",
        authority_role="supplemental",
    )
    _write_ics_source(
        source_dir,
        "invalid-disabled.yaml",
        source_id="invalid_calendar",
        enabled=False,
        authority_role="owner_declared",
    )

    with pytest.warns(UserWarning, match="invalid-disabled.yaml"):
        result = load_source_config_inventory(source_dir)

    assert [config.source_id for config in result.source_configs] == [
        "valid_calendar"
    ]
    assert result.inventory_status is InventoryStatus.PARTIAL


def test_partial_inventory_with_no_valid_sources_remains_partial_and_bounded(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "invalid-disabled.yaml",
        source_id="invalid_calendar",
        enabled=False,
        authority_role="owner_declared",
        description="PRIVATE REJECTED SOURCE CONTENT",
    )

    with pytest.warns(UserWarning, match="invalid-disabled.yaml"):
        result = load_source_config_inventory(source_dir)

    assert result.source_configs == []
    assert result.inventory_status is InventoryStatus.PARTIAL
    assert "PRIVATE REJECTED SOURCE CONTENT" not in repr(result)


def test_invalid_enabled_authority_fails_loudly(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "invalid.yaml",
        source_id="invalid_calendar",
        authority_role="owner_declared",
    )

    with pytest.raises(SourceConfigValidationError):
        load_source_config_inventory(source_dir)


def test_duplicate_source_ids_fail_before_registry_construction(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source(
        source_dir,
        "first.yaml",
        source_id="duplicate_calendar",
    )
    _write_ics_source(
        source_dir,
        "second.yaml",
        source_id="duplicate_calendar",
    )

    with pytest.raises(SourceConfigValidationError, match="duplicate source IDs"):
        load_source_config_inventory(source_dir)


def test_inventory_over_32_sources_fails_without_truncation(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    for index in range(33):
        _write_ics_source(
            source_dir,
            f"source-{index:02d}.yaml",
            source_id=f"calendar_{index:02d}",
        )

    with pytest.raises(SourceConfigValidationError, match="exceeds 32 sources"):
        load_source_config_inventory(source_dir)


def test_source_config_path_must_be_a_directory(tmp_path: Path) -> None:
    source_path = tmp_path / "sources"
    source_path.write_text("PRIVATE CONFIG CONTENT", encoding="utf-8")

    with pytest.raises(
        SourceConfigValidationError,
        match="must be a directory",
    ):
        load_source_config_inventory(source_path)
