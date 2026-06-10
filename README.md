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

## Audit log

- Path: `var/audit/events.jsonl`
- Override with `AUDIT_LOG_PATH`
