# Data Source Aggregator

Data Source Aggregator is a standalone, read-only service for listing and inspecting configured data sources. This first pass builds the service skeleton, shared source config validation, and runtime source registry foundation.

It is not a memory service. It does not write to external systems, call an LLM, promote memory, or expose connector secrets through its API responses.

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

## Included example sources

- `config/sources/jeep_wj_maintenance.example.yaml`
- `config/sources/leafs_calendar.example.yaml`

These examples demonstrate the shared source config shape and environment-based connector settings without enabling live connector access in this pass.

## Current scope

- FastAPI application skeleton
- `GET /health`
- Source config models and YAML loader
- Runtime source registry inferred from validated configs
- `GET /v1/sources`
- `GET /v1/sources/{source_id}`
- Unit tests for config loading, validation, registry generation, and source routes

## Non-goals in this pass

- No Google Sheets API access yet
- No ICS fetching yet
- No search, fetch, or context routes yet
- No write operations
- No LLM integration
- No memory promotion
