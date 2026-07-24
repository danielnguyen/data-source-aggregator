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

Each source may declare:

```yaml
authority_role: authoritative
```

Allowed values are `authoritative`, `supplemental`, and `unknown`. The field is
operator-configured and is never inferred from source names, tags, connectors,
health, or retrieval results. Omitted values default to `unknown`.

Each source may also declare optional operator-configured material scope
references:

```yaml
scope_refs:
  time: fy2026
  version: release-152
  domain: credential-management
  project: firefox
```

Any non-empty subset of `time`, `version`, `domain`, and `project` is valid.
Values are case-preserving identifiers from 1 through 120 characters matching
`^[A-Za-z0-9][A-Za-z0-9._:-]*$`. They are copied only from validated source
configuration and are never inferred from source names, tags, descriptions,
connectors, content, health, user text, or provider output. Legacy source
configurations without `scope_refs` remain valid.

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
authority_role: unknown
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
authority_role: unknown
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

The response reports the bounded configured-source inventory:

```json
{
  "inventory_scope": "configured_sources",
  "inventory_status": "complete",
  "sources": []
}
```

`complete` means every non-example source config in the resolved source-config
directory was represented. It does not mean every potentially relevant
real-world source was configured. Valid disabled and currently unavailable
sources remain represented and do not reduce inventory completeness. An invalid
disabled config that is deliberately omitted produces `partial`; a missing
source-config directory produces `unknown`. Source and credential configuration,
paths, environment references, URLs, secrets, and raw YAML are not exposed by
the source inventory API.

The list and detail inventory responses expose an exact configured `scope_refs`
object when present and omit the key when it is absent. This metadata does not
change retrieval, source selection, authority, health, audit, credentials, or
connector behavior. Connector configuration, URLs, secrets, and raw source
content remain excluded from the inventory projection.

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

Google Sheets sources with `retrieval.allow_full_fetch: true` also declare the
`configured_worksheet` context mode:

```json
{
  "context_mode": "configured_worksheet",
  "description": "Fetch every non-empty record from the configured worksheet."
}
```

This operation is bounded to the one spreadsheet and worksheet named by the
validated source configuration. It reads every non-empty record after the
configured header row, preserves worksheet order, and returns one raw-free
`spreadsheet_range`. It does not cover other worksheets, spreadsheets, source
configurations, or potentially relevant real-world sources.

Both the configured `max_context_rows` ceiling (default `20` when omitted) and
the request `budget.max_rows` ceiling apply. The existing configured and request
byte and text limits also apply. If the complete record set exceeds any
effective limit, the request fails with `result_too_large`; the connector does
not return a prefix, summary, nearby-row substitute, or other partial success.
An empty configured worksheet returns an empty context response without
fabricating absence evidence.

```bash
curl -X POST http://localhost:8000/v1/sources/context \
  -H "Content-Type: application/json" \
  -d '{
    "source_ref": "google_sheets:vehicle_log_example:Maintenance!A12:H12",
    "context_mode": "configured_worksheet",
    "budget": {
      "max_rows": 20,
      "max_bytes": 50000,
      "max_text_chars": 12000
    }
  }'
```

The mode is an acquisition capability, not an exhaustive conclusion by itself.
Sources with `allow_full_fetch: false` do not advertise or execute it.

Context pack:

Returns compact evidence for downstream assistants. This endpoint does not generate an answer, and raw payloads are omitted by default.

Each item includes an explicit `available_context` collection. These bounded descriptors are declared by the connector for that specific source reference and tell a downstream caller which context-expansion modes are available. They are capability discovery only: `/v1/context-pack` does not execute expansion, infer modes from connector or content type, or add expansion arguments. An empty list means the connector declared no expansion option for that item.

Descriptors contain only a bounded identifier-safe `context_mode` and bounded human-readable `description`. They do not include URLs, credentials, source or connector configuration, raw content, or executable arguments. Descriptor bytes are included in the existing serialized-item `max_bytes` accounting; descriptions are bounded metadata and do not consume the separate evidence-text `max_text_chars` budget.

When `source_ids` are omitted, `/v1/context-pack` uses deterministic metadata relevance over configured source metadata such as source IDs, display names, descriptions, domain tags, connector names, and source profile fields. Weak or ambiguous matches fall back to the full eligible source set instead of returning no evidence. Explicit `source_ids` always override relevance, and explicit `domain_tags` always constrain the eligible source set first.

When multiple sources are searched, the service keeps a bounded per-source candidate set and then round-robins candidates in source-relevance order so one high-volume source cannot consume the entire result budget just because it is configured first. The final `budget` still enforces `max_results`, `max_bytes`, and `max_text_chars`. The optional `diagnostics` field reports bounded selection and ranking details without exposing secrets or raw private payloads.

For clear latest-record queries such as `last`, `latest`, `newest`, or `most recent`, result ordering stays relevance-first and then prefers source-native record dates within the relevant result set when those dates are available.

```bash
curl -X POST http://localhost:8000/v1/context-pack \
  -H "Content-Type: application/json" \
  -d '{
    "query": "when did I last change the oil in my vehicle?",
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
  "query": "when did I last change the oil in my vehicle?",
  "sources_used": ["vehicle_log_example"],
  "items": [
    {
      "result_id": "r_...",
      "source_type": "google_sheets",
      "source_id": "vehicle_log_example",
      "source_name": "Vehicle Log",
      "source_ref": "google_sheets:vehicle_log_example:Maintenance!A13:I13",
      "retrieved_at": "2026-06-10T00:00:00Z",
      "title": "09/03/2026",
      "content_type": "spreadsheet_row",
      "text": "Date: 09/03/2026\nKilometers: 83061\nComments/Repair Notes: Engine oil...",
      "confidence": "high",
      "available_context": [
        {
          "context_mode": "nearby_rows",
          "description": "Fetch nearby rows."
        },
        {
          "context_mode": "configured_worksheet",
          "description": "Fetch every non-empty record from the configured worksheet."
        }
      ],
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
  },
  "diagnostics": {
    "selection_mode": "query_relevance",
    "considered_source_ids": ["vehicle_log_example", "calendar_sports_example"],
    "selected_source_ids": ["vehicle_log_example"],
    "source_diagnostics": [
      {
        "source_id": "vehicle_log_example",
        "score": 22,
        "score_band": "high",
        "reasons": ["display_name_match", "domain_tag_match", "description_match"]
      }
    ],
    "ranking_mode": "single_source",
    "candidate_counts_by_source": {
      "vehicle_log_example": 1
    },
    "budget_truncated_candidates": false
  }
}
```

## Audit log

- Path: `var/audit/events.jsonl`
- Override with `AUDIT_LOG_PATH`
- Request headers, including `X-API-Key`, are not written to audit events.
