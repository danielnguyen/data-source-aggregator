from __future__ import annotations

import os
from pathlib import Path

from app.models import AuditEvent

DEFAULT_AUDIT_LOG_PATH = Path("var/audit/events.jsonl")


class AuditLogWriter:
    def __init__(self, path: Path | None = None) -> None:
        configured_path = path or Path(os.getenv("AUDIT_LOG_PATH", DEFAULT_AUDIT_LOG_PATH))
        self.path = configured_path

    def write_event(self, audit_event: AuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(audit_event.model_dump_json())
            handle.write("\n")
