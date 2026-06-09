# Data Source Aggregator

Data Source Aggregator is a standalone, read-only service for configured data source access. It lists configured sources and exposes foundational retrieval APIs for search, fetch, and context orchestration.

It is not a memory service. It does not write to external systems, call an LLM, promote memory, or expose connector secrets through API responses or audit logs.

## Local setup

1. Create a virtual environment.
2. Install the project with dev dependencies: `pip install -e .[dev]`
3. Copy `.env.example` to `.env` and set any required values.
4. Start the service with `uvicorn app.main:app --reload`

By default the service loads source configs from `config/sources`. You can override that with `SOURCE_CONFIG_DIR`.
The service also loads a local `.env` file on startup so connector env refs work during local development without a separate export step.

## API examples

Health:

```bash
curl http://localhost:8000/health
```

List configured sources:

```bash
curl http://localhost:8000/v1/sources
```

Inspect one configured source:

```bash
curl http://localhost:8000/v1/sources/jeep_wj_maintenance
```

Search a configured source:

```bash
curl -X POST http://localhost:8000/v1/sources/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "battery replacement",
    "source_ids": ["jeep_wj_maintenance"],
    "retrieval_mode": "targeted",
    "allowed_sensitivity": "medium",
    "budget": {
      "max_results": 10,
      "max_bytes": 50000,
      "max_text_chars": 20000
    },
    "include_raw": true
  }'
```

Search currently returns empty stub results until a real connector is implemented.

Fetch a source reference:

```bash
curl -X POST http://localhost:8000/v1/sources/fetch \
  -H "Content-Type: application/json" \
  -d '{
    "source_ref": "google_sheets:jeep_wj_maintenance:Maintenance!A44:H44",
    "include_raw": true,
    "budget": {
      "max_bytes": 50000,
      "max_text_chars": 20000
    }
  }'
```

Fetch currently returns `unsupported_operation` for stub connectors until PR3 implements Google Sheets.

Request broader context:

```bash
curl -X POST http://localhost:8000/v1/sources/context \
  -H "Content-Type: application/json" \
  -d '{
    "source_ref": "google_sheets:jeep_wj_maintenance:Maintenance!A44:H44",
    "context_mode": "nearby_rows",
    "budget": {
      "max_rows": 20,
      "max_bytes": 100000,
      "max_text_chars": 40000
    }
  }'
```

Context currently returns `unsupported_operation` for stub connectors.

## Included example sources

- `config/sources/jeep_wj_maintenance.example.yaml`
- `config/sources/leafs_calendar.example.yaml`

These examples demonstrate the shared source config shape and environment-based connector settings without enabling live connector access in this pass.

## Current scope

- FastAPI application skeleton
- `GET /health`
- `GET /v1/sources`
- `GET /v1/sources/{source_id}`
- `POST /v1/sources/search`
- `POST /v1/sources/fetch`
- `POST /v1/sources/context`
- Source config models and YAML loader
- Runtime source registry inferred from validated configs
- Result envelope model
- Source reference parser
- Retrieval budget model/enforcement
- Stable error response shape
- JSONL audit logging
- Stub connector dispatch only

## Audit log

- Default path: `var/audit/events.jsonl`
- Override env var: `AUDIT_LOG_PATH`
- Audit logs record operation metadata, source IDs, result counts, status, and error codes.
- Audit logs must not contain connector secrets or full raw result payloads.

## Non-goals in this pass

- No Google Sheets API access yet
- No ICS fetching yet
- No real source search/fetch implementation yet
- No filesystem/GitHub/Gmail/Google Docs connectors yet
- No write operations
- No LLM integration
- No memory promotion
- No universal ontology
