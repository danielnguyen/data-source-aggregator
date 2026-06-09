# Data Source Aggregator

Data Source Aggregator is a standalone, read-only service for configured data source access. It lists configured sources and exposes foundational retrieval APIs for search, fetch, and context orchestration.

It is not a memory service. It does not write to external systems, call an LLM, promote memory, or expose connector secrets through API responses or audit logs.

## Local setup

1. Create a virtual environment.
2. Install the project with dev dependencies: `pip install -e .[dev]`
3. Copy a source template to a non-example filename and edit it:
   `cp config/sources/jeep_wj_maintenance.example.yaml config/sources/jeep_wj_maintenance.yaml`
4. Copy `config/credentials.yaml.example` to `config/credentials.yaml` only if an enabled source references a `credentials_ref`.
5. Copy `.env.example` to `.env` only if you want service-level overrides.
6. Start the service with `uvicorn app.main:app --reload`

## Configuration model

Source configs remain the primary configuration mechanism:

- `config/sources/*.yaml` defines configured sources
- `config/credentials.yaml` defines stable credential refs
- `secrets/` or other mounted paths hold private credential material

Files ending in `.example.yaml` or `.example.yml` are templates only and are ignored by the runtime loader.

To activate a source, copy an example file to a non-example filename, then edit it:

- `cp config/sources/jeep_wj_maintenance.example.yaml config/sources/jeep_wj_maintenance.yaml`
- `cp config/credentials.yaml.example config/credentials.yaml`

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

## Google Sheets setup

Google Sheets is the first real connector in the service. It is read-only only.

Example source config:

```yaml
source_id: jeep_wj_maintenance
display_name: Jeep WJ Maintenance Log
connector: google_sheets
enabled: true

domain_tags:
  - vehicle
  - maintenance
  - jeep_wj

sensitivity: low
access_mode: read_only

connector_config:
  spreadsheet_id: "replace-with-google-sheet-id"
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
```

Example credential config:

```yaml
credentials:
  google_sheets_readonly:
    type: google_service_account_file
    path: secrets/google_sheets_readonly.json
```

Supported Google Sheets credential refs in this pass:

- `google_service_account_file`
- `google_application_default`

The connector uses the read-only Sheets scope:

```text
https://www.googleapis.com/auth/spreadsheets.readonly
```

No write operations are supported.

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

Google Sheets search now performs deterministic read-only row matching. Connectors that are not yet implemented may still return stub behavior.

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

Fetch works for configured Google Sheets row and range `source_ref` values. Other connectors may still return `unsupported_operation`.

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

Context supports `nearby_rows` for Google Sheets. Other connectors may still return `unsupported_operation`.

## Included example sources

- `config/sources/jeep_wj_maintenance.example.yaml`
- `config/sources/leafs_calendar.example.yaml`
- `config/credentials.yaml.example`

These examples demonstrate source configs, credential refs, and private credential file mapping without enabling live connector access in this pass.
They are inactive until copied to non-example filenames.

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
- Read-only Google Sheets connector for search, fetch, and nearby row context
- Stub connector dispatch for connectors that are not yet implemented

## Audit log

- Default path: `var/audit/events.jsonl`
- Override env var: `AUDIT_LOG_PATH`
- Audit logs record operation metadata, source IDs, result counts, status, and error codes.
- Audit logs must not contain connector secrets or full raw result payloads.

## Non-goals in this pass

- No ICS fetching yet
- No real ICS, filesystem, GitHub, Gmail, or Google Docs connector implementation yet
- No write operations
- No LLM integration
- No memory promotion
- No universal ontology
