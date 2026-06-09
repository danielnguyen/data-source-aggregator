# Data Source Aggregator

Data Source Aggregator is a standalone, read-only service for configured data source access. It lists configured sources and exposes foundational retrieval APIs for search, fetch, and context orchestration.

It is not a memory service. It does not write to external systems, call an LLM, promote memory, or expose connector secrets through API responses or audit logs.

## Local setup

1. Create a virtual environment.
2. Install the project with dev dependencies: `pip install -e .[dev]`
3. Copy `config/credentials.yaml.example` to `config/credentials.yaml` and set any credential refs you plan to use.
4. Copy `.env.example` to `.env` only if you want service-level overrides.
5. Start the service with `uvicorn app.main:app --reload`

## Configuration model

Source configs remain the primary configuration mechanism:

- `config/sources/*.yaml` defines configured sources
- `config/credentials.yaml` defines stable credential refs
- `secrets/` or other mounted paths hold private credential material

Credential refs are referenced from source configs through `connector_config.credentials_ref`. The service validates the reference, but it does not expose credential paths, token values, or private key contents through its APIs or audit logs.

`.env` is optional and should only be used for service-level overrides such as:

- `SOURCE_CONFIG_DIR`
- `AUDIT_LOG_PATH`
- `CREDENTIALS_CONFIG_PATH`

Do not use `.env` as the primary place for source IDs, URLs, tokens, or private keys.

## Private local files

- `config/credentials.yaml`
- `secrets/`
- `.env`
- `var/`

These are intentionally gitignored.

By default the service loads source configs from `config/sources` and credentials from `config/credentials.yaml`. You can override those with `SOURCE_CONFIG_DIR` and `CREDENTIALS_CONFIG_PATH`. The service also loads a local `.env` file on startup for service-level overrides.

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
- `config/credentials.yaml.example`

These examples demonstrate source configs, credential refs, and private credential file mapping without enabling live connector access in this pass.

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
