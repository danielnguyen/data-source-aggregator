from __future__ import annotations

import pytest

from app.errors import ServiceError
from app.services.source_ref import parse_source_ref


def test_parse_source_ref_returns_parts() -> None:
    parsed = parse_source_ref("google_sheets:jeep_wj_maintenance:Maintenance!A44:H44")

    assert parsed.source_type == "google_sheets"
    assert parsed.source_id == "jeep_wj_maintenance"
    assert parsed.native_locator == "Maintenance!A44:H44"


def test_parse_source_ref_rejects_invalid_shape() -> None:
    with pytest.raises(ServiceError, match="invalid"):
        parse_source_ref("bad-ref")
