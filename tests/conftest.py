from __future__ import annotations

import pytest

from app.models import SourceConfig


@pytest.fixture
def source_config_factory():
    def build_source_config(**overrides):
        payload = {
            "source_id": "vehicle_log_primary",
            "connector": "google_sheets",
            "enabled": True,
            "public_profile": {
                "display_name": "Vehicle Log - Primary",
                "description": "Personal vehicle operating records.",
                "domain_tags": ["vehicle", "maintenance"],
            },
            "private_profile": {
                "display_name": "Example Private Vehicle Log",
                "description": "Private operator notes for a configured vehicle sheet.",
                "domain_tags": ["vehicle_detail", "operator_only"],
            },
            "sensitivity": "low",
            "access_mode": "read_only",
            "connector_config": {
                "spreadsheet_id": "sheet-id",
                "worksheet": "Maintenance",
                "header_row": 1,
                "credentials_ref": "google_sheets_readonly",
            },
            "retrieval": {
                "default_mode": "targeted",
                "max_results": 20,
                "max_bytes": 100000,
                "max_text_chars": 40000,
                "allow_full_fetch": True,
            },
        }
        payload.update(overrides)
        return SourceConfig.model_validate(payload)

    return build_source_config
