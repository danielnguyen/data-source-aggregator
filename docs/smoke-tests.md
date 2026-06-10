# Smoke Tests

These checks assume you have copied any needed `.example.yaml` template to a non-example filename and created the matching local secret files.

## Build and start

```bash
docker compose up --build
```

## Health

```bash
curl http://localhost:8000/health
```

Expected shape:

```json
{"status":"ok","service":"data-source-aggregator"}
```

## List sources

```bash
curl http://localhost:8000/v1/sources
```

If no non-example source config files are mounted yet, an empty list is expected.

## Search Google Sheets source

Run this only if `vehicle_log_primary.yaml` is present and valid:

```bash
curl -X POST http://localhost:8000/v1/sources/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "battery",
    "source_ids": ["vehicle_log_primary"],
    "retrieval_mode": "targeted",
    "allowed_sensitivity": "low",
    "budget": {
      "max_results": 5,
      "max_bytes": 50000,
      "max_text_chars": 20000
    },
    "include_raw": false
  }'
```

## Search ICS source

Run this only if `calendar_sports.yaml` is present and valid:

```bash
curl -X POST http://localhost:8000/v1/sources/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "leafs",
    "source_ids": ["calendar_sports"],
    "retrieval_mode": "targeted",
    "allowed_sensitivity": "low",
    "budget": {
      "max_results": 5,
      "max_bytes": 50000,
      "max_text_chars": 20000
    },
    "include_raw": false
  }'
```

## Verify audit log

```bash
tail -n 5 var/audit/events.jsonl
```

Audit entries should show request metadata and status, but must not contain secrets, credential paths, spreadsheet IDs, private ICS URLs, or raw connector config.
