# Data Source Aggregator

Data Source Aggregator is a read-only FastAPI service for searching and fetching configured sources.

## Run locally

- `pip install -e .[dev]`
- `uvicorn app.main:app --reload`

## Docker

```bash
docker compose up --build
```

See [docs/deployment.md](docs/deployment.md) for the mount layout and [docs/smoke-tests.md](docs/smoke-tests.md) for a short smoke test.

## Config files

- `config/sources/*.yaml`
- `config/credentials.yaml`
- `secrets/`
- `var/audit/`

## Environment

```text
DSA_API_KEY=
```

- `DSA_API_KEY` is optional for local development.
- When `DSA_API_KEY` is set, deployed and internal callers should send `X-API-Key: <DSA_API_KEY>` on all data-bearing API requests.
- `GET /health` stays open without an API key.

### Example config files

- `config/sources/vehicle_maintenance.example.yaml`
- `config/sources/calendar.example.yaml`
- `config/credentials.yaml.example`

## Local source configs

Real source configs live in `config/sources/*.yaml` and are gitignored by default.

Committed files under `config/sources/*.example.yaml` are examples only.

## Local files (gitignored)

- `config/sources/*.yaml`
- `config/credentials.yaml`
- `secrets/`
- `.env`
- `var/`

## Google Sheets

```yaml
source_id: vehicle_log_example
display_name: Vehicle Log
description: Example vehicle maintenance and operating records.
domain_tags:
  - vehicle
  - maintenance
connector: google_sheets
enabled: true
sensitivity: medium
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

### Credentials

```yaml
credentials:
  google_sheets_readonly:
    type: google_service_account_file
    path: secrets/google_sheets_readonly.json
```

## ICS calendar

```yaml
source_id: calendar_sports_example
display_name: Sports Calendar
description: Example sports schedule source.
domain_tags:
  - calendar
  - sports
connector: ics_calendar
enabled: true
sensitivity: low
access_mode: read_only

connector_config:
  url: "https://example.com/sports-calendar.ics"
  timezone: America/Toronto

retrieval:
  default_mode: targeted
  max_results: 20
  max_bytes: 100000
  max_text_chars: 40000
  lookback_days: 30
  lookahead_days: 365
  allow_full_fetch: true

result_text:
  title_from: summary
  include_fields:
    - summary
    - start
    - end
    - location
    - description
```

## API examples

Health:

```bash
curl http://localhost:8000/health
```

List sources:

```bash
curl http://localhost:8000/v1/sources
```

If `DSA_API_KEY` is set, include the header:

```bash
curl http://localhost:8000/v1/sources \
  -H "X-API-Key: $DSA_API_KEY"
```

Search:

```bash
curl -X POST http://localhost:8000/v1/sources/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "battery replacement",
    "source_ids": ["vehicle_log_primary"],
    "retrieval_mode": "targeted",
    "allowed_sensitivity": "low",
    "budget": {
      "max_results": 10,
      "max_bytes": 50000,
      "max_text_chars": 20000
    },
    "include_raw": false
  }'
```

Fetch:

```bash
curl -X POST http://localhost:8000/v1/sources/fetch \
  -H "Content-Type: application/json" \
  -d '{
    "source_ref": "google_sheets:vehicle_log_primary:Maintenance!A44:H44",
    "include_raw": true,
    "budget": {
      "max_bytes": 50000,
      "max_text_chars": 20000
    }
  }'
```

Context:

```bash
curl -X POST http://localhost:8000/v1/sources/context \
  -H "Content-Type: application/json" \
  -d '{
    "source_ref": "ics_calendar:calendar_sports:event:sports-team-home-20261010",
    "context_mode": "upcoming_events",
    "budget": {
      "max_rows": 5,
      "max_bytes": 100000,
      "max_text_chars": 40000
    }
  }'
```

Context pack:

Returns compact evidence for downstream assistants. This endpoint does not generate an answer, and raw payloads are omitted by default.

```bash
curl -X POST http://localhost:8000/v1/context-pack \
  -H "Content-Type: application/json" \
  -d '{
    "query": "what maintenance did I do recently on the Jeep?",
    "source_ids": ["vehicle_log_primary"],
    "retrieval_mode": "targeted",
    "allowed_sensitivity": "medium",
    "budget": {
      "max_results": 5,
      "max_bytes": 50000,
      "max_text_chars": 12000
    }
  }'
```

Example response:

```json
{
  "query_id": "q_...",
  "query": "what maintenance did I do recently on the Jeep?",
  "sources_used": ["vehicle_log_primary"],
  "items": [
    {
      "result_id": "r_...",
      "source_type": "google_sheets",
      "source_id": "vehicle_log_primary",
      "source_name": "Vehicle Log - Primary",
      "source_ref": "google_sheets:vehicle_log_primary:'Form responses 1'!A13:I13",
      "retrieved_at": "2026-06-10T00:00:00Z",
      "title": "09/03/2026",
      "content_type": "spreadsheet_row",
      "text": "Date: 09/03/2026\nKilometers: 83061\nComments/Repair Notes: Engine oil...",
      "confidence": "high",
      "warnings": []
    }
  ],
  "warnings": [],
  "errors": [],
  "budget": {
    "max_results": 5,
    "returned_results": 1,
    "estimated_bytes": 1234,
    "truncated": false
  }
}
```

## Audit log

- Path: `var/audit/events.jsonl`
- Override with `AUDIT_LOG_PATH`
- Request headers, including `X-API-Key`, are not written to audit events.
