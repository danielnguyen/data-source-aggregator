# Deployment

`data-source-aggregator` is designed to run with mounted configuration, mounted secrets, and a persistent audit log. The container image does not bake in local secrets, local `.env` files, or local audit data.

## Runtime layout

Create the local runtime files outside the image:

```text
config/
  sources/
    vehicle_maintenance.example.yaml
    calendar.example.yaml
    vehicle_log_primary.yaml        # local, not committed
    calendar_sports.yaml            # local, not committed
  credentials.yaml.example
  credentials.yaml                  # local, not committed

secrets/
  google_sheets_readonly.json       # local, not committed

var/
  audit/
    events.jsonl                    # local, not committed
```

`config/credentials.yaml`, `secrets/`, `.env`, and `var/` are gitignored.
Real local `config/sources/*.yaml` files are also gitignored by default; keep committed examples in `.example.yaml` files and keep operator configs local.

## Source templates stay inactive

Files ending in `.example.yaml` or `.example.yml` are templates only. The runtime loader ignores them.

To enable a source, copy the template to a non-example filename and edit the copy:

```bash
cp config/sources/vehicle_maintenance.example.yaml config/sources/vehicle_log_primary.yaml
cp config/sources/calendar.example.yaml config/sources/calendar_sports.yaml
mkdir -p secrets var/audit
```

Copy `config/credentials.yaml.example` to `config/credentials.yaml` only if you enable a source that uses `connector_config.credentials_ref`, such as Google Sheets. A public ICS-only setup does not need `config/credentials.yaml`.

Use public-safe names when copying examples into real local config files. `source_id` is visible in APIs, source refs, audit events, and traces.

## Docker Compose

The included [docker-compose.yml](../docker-compose.yml) mounts the whole `config/` directory read-only, mounts secrets read-only, and mounts the audit log directory writable:

- `./config:/app/config:ro`
- `./secrets:/app/secrets:ro`
- `./var/audit:/app/var/audit`

The service-level environment is:

- `SOURCE_CONFIG_DIR=/app/config/sources`
- `CREDENTIALS_CONFIG_PATH=/app/config/credentials.yaml`
- `AUDIT_LOG_PATH=/app/var/audit/events.jsonl`

This layout allows `/app/config/credentials.yaml` to be absent cleanly when no enabled source needs credentials. Source examples still remain inactive until copied to non-example filenames inside the mounted `config/sources/` directory.

Bring the service up:

```bash
docker compose up --build
```

The app listens on port `8000` in the container and is published as `8000:8000` by default.

## Health and readiness

The compose healthcheck uses `GET /health` as a simple liveness probe.

For readiness, treat the service as ready when both of these are true:

1. `GET /health` returns `200 OK`.
2. `GET /v1/sources` returns the sources you intended to mount.

If source config or credential refs are invalid, startup should fail rather than silently serving a broken configuration.

## Safety notes

Mounted runtime data should remain outside the image. Do not place any of the following directly in `docker-compose.yml` or commit them into the repository:

- service account JSON
- private keys
- tokens
- spreadsheet IDs
- private ICS URLs

API responses and audit logs must not expose:

- service account JSON
- private keys
- tokens
- credential paths
- credential refs where avoidable
- spreadsheet IDs
- private ICS URLs
- raw connector configs
