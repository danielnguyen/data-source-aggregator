SHELL := /usr/bin/env bash

COMPOSE_FILE := docker-compose.yml
APP_PORT ?= 8000

.PHONY: dev-up dev-down dev-reset dev-logs dev-test dev-lint dev-check dev-install dev-start dev-start-reload dev-smoke dev-prepare dev-compose-config

dev-prepare:
	@mkdir -p config/sources secrets var/audit

dev-up: dev-prepare
	@docker compose -f $(COMPOSE_FILE) up -d

dev-down:
	@docker compose -f $(COMPOSE_FILE) down

dev-reset:
	@docker compose -f $(COMPOSE_FILE) down -v --remove-orphans
	@docker compose -f $(COMPOSE_FILE) up -d --build

dev-logs:
	@docker compose -f $(COMPOSE_FILE) logs -f --tail=200

dev-compose-config:
	@docker compose -f $(COMPOSE_FILE) config

dev-test:
	@pytest -q

dev-lint:
	@ruff check .

dev-check: dev-lint dev-test dev-compose-config

dev-install:
	@python -m pip install -e ".[dev]"

dev-start:
	@uvicorn app.main:app --host 0.0.0.0 --port "$(APP_PORT)"

dev-start-reload:
	@uvicorn app.main:app --host 0.0.0.0 --port "$(APP_PORT)" --reload

dev-smoke:
	@curl -fsS http://localhost:$(APP_PORT)/health
	@curl -fsS http://localhost:$(APP_PORT)/v1/sources