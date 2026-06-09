# Deployment

`data-source-aggregator` is designed to run with mounted configuration, mounted secrets, and a persistent audit log. The container image does not bake in local secrets, local `.env` files, or local audit data.

## Runtime layout

Create the local runtime files outside the image:

```text
config/
  sources/
    jeep_wj_maintenance.example.yaml
    leafs_calendar.example.yaml
    jeep_wj_maintenance.yaml        # local, not committed
    leafs_calendar.yaml             # local, not committed
  credentials.yaml.example
  credentials.yaml                  # local, not committed

secrets/
  google_sheets_readonly.json       # local, not committed

var/
  audit/
    events.jsonl                    # local, not committed
```

`config/credentials.yaml`, `secrets/`, `.env`, and `var/` are gitignored.

## Source templates stay inactive

Files ending in `.example.yaml` or `.example.yml` are templates only. The runtime loader ignores them.

To enable a source, copy the template to a non-example filename and edit the copy:

```bash
cp config/sources/jeep_wj_maintenance.example.yaml config/sources/jeep_wj_maintenance.yaml
cp config/sources/leafs_calendar.example.yaml config/sources/leafs_calendar.yaml
cp config/credentials.yaml.example config/credentials.yaml
mkdir -p secrets var/audit
```

## Docker Compose

The included [docker-compose.yml](/home/danielnguyen/projects/danielnguyen/data-source-aggregator/docker-compose.yml) mounts configs and secrets read-only, and mounts the audit log directory writable:

- `./config/sources:/app/config/sources:ro`
- `./config/credentials.yaml:/app/config/credentials.yaml:ro`
- `./secrets:/app/secrets:ro`
- `./var/audit:/app/var/audit`

The service-level environment is:

- `SOURCE_CONFIG_DIR=/app/config/sources`
- `CREDENTIALS_CONFIG_PATH=/app/config/credentials.yaml`
- `AUDIT_LOG_PATH=/app/var/audit/events.jsonl`

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
