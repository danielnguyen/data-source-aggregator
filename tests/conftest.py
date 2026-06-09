from __future__ import annotations

import pytest

from app.models import SourceConfig


@pytest.fixture
def source_config_factory():
    def build_source_config(**overrides):
        payload = {
            "source_id": "jeep_wj_maintenance",
            "display_name": "Jeep WJ Maintenance Log",
            "connector": "google_sheets",
            "enabled": True,
            "domain_tags": ["vehicle", "maintenance"],
            "sensitivity": "low",
            "access_mode": "read_only",
            "connector_config": {
                "spreadsheet_id": "sheet-id",
                "worksheet": "Maintenance",
                "header_row": 1,
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
