from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.main import create_app


def _write_source_config(source_dir: Path) -> None:
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
async def test_search_route_validates_request_shape(tmp_path: Path, monkeypatch) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/search",
            json={"source_ids": ["jeep_wj_maintenance"]},
        )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"] == "Request validation failed."


@pytest.mark.anyio
async def test_search_route_returns_empty_stub_results_and_writes_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/search",
            json={
                "query": "battery replacement",
                "source_ids": ["jeep_wj_maintenance"],
                "domain_tags": ["vehicle"],
                "retrieval_mode": "targeted",
                "max_results": 10,
                "allowed_sensitivity": "medium",
                "budget": {
                    "max_results": 10,
                    "max_bytes": 50000,
                    "max_text_chars": 20000,
                },
                "include_raw": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "battery replacement"
    assert payload["results"] == []
    assert payload["budget"]["returned_results"] == 0
    assert "sheet-secret-id" not in json.dumps(payload)

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["operation"] == "search"
    assert audit_event["status"] == "success"
    assert audit_event["result_count"] == 0
    assert "sheet-secret-id" not in json.dumps(audit_event)


@pytest.mark.anyio
async def test_search_route_returns_stable_unknown_source_error_and_writes_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/search",
            json={"query": "battery", "source_ids": ["unknown_source"]},
        )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == {
        "code": "source_not_found",
        "message": "Source 'unknown_source' is not configured or is disabled.",
        "details": {"source_id": "unknown_source"},
    }

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["status"] == "error"
    assert audit_event["error_code"] == "source_not_found"


@pytest.mark.anyio
async def test_fetch_route_returns_unsupported_operation_and_writes_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/fetch",
            json={
                "source_ref": "google_sheets:jeep_wj_maintenance:Maintenance!A44:H44",
                "include_raw": True,
                "budget": {"max_bytes": 50000, "max_text_chars": 20000},
            },
        )

    assert response.status_code == 501
    payload = response.json()
    assert payload["error"]["code"] == "unsupported_operation"
    assert payload["error"]["details"]["operation"] == "fetch"

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["operation"] == "fetch"
    assert audit_event["status"] == "error"
    assert audit_event["error_code"] == "unsupported_operation"


@pytest.mark.anyio
async def test_fetch_route_rejects_invalid_source_ref(tmp_path: Path, monkeypatch) -> None:
    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    _write_credentials_config(tmp_path, monkeypatch)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_source_config(source_dir)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/fetch",
            json={"source_ref": "bad-ref", "budget": {"max_bytes": 50000}},
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_source_ref"
    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["operation"] == "fetch"
    assert audit_event["status"] == "error"
    assert audit_event["error_code"] == "invalid_source_ref"
