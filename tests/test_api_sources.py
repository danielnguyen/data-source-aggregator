from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.main import create_app


def _write_credentials_config(tmp_path: Path, monkeypatch) -> None:
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


@pytest.mark.anyio
async def test_health_route(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "data-source-aggregator",
    }


@pytest.mark.anyio
async def test_sources_routes_return_safe_registry_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "source.yaml").write_text(
        """
source_id: jeep_wj_maintenance
display_name: Jeep WJ Maintenance Log
connector: google_sheets
enabled: true
description: Maintenance records for the Jeep WJ.
domain_tags: [vehicle, maintenance, jeep_wj]
sensitivity: low
access_mode: read_only
connector_config:
  spreadsheet_id: sheet-secret-id
  worksheet: Maintenance
  header_row: 1
  credentials_ref: google_sheets_readonly
retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  max_context_rows: 250
  allow_full_fetch: true
""",
        encoding="utf-8",
    )

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        list_response = await client.get("/v1/sources")
        detail_response = await client.get("/v1/sources/jeep_wj_maintenance")

    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["sources"][0]["source_id"] == "jeep_wj_maintenance"
    assert payload["sources"][0]["status"] == "ready"
    assert payload["sources"][0]["capabilities"] == ["profile", "search", "fetch", "context"]
    assert "connector_config" not in payload["sources"][0]
    assert "sheet-secret-id" not in str(payload)

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["source"]["retrieval"]["default_mode"] == "targeted"
    assert (
        detail_payload["source"]["profile"]["summary"]
        == "Google Sheet source using worksheet Maintenance."
    )
    assert "sheet-secret-id" not in str(detail_payload)


@pytest.mark.anyio
async def test_get_source_returns_404_for_unknown_source(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/sources/missing_source")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "source_not_found",
        "message": "Source 'missing_source' is not configured or is disabled.",
        "details": {"source_id": "missing_source"},
    }
