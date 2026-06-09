from __future__ import annotations

from pydantic import BaseModel

from app.errors import ServiceError


class ParsedSourceRef(BaseModel):
    source_type: str
    source_id: str
    native_locator: str


def parse_source_ref(source_ref: str) -> ParsedSourceRef:
    parts = source_ref.split(":", 2)
    if len(parts) != 3 or any(not part for part in parts):
        raise ServiceError(
            "invalid_source_ref",
            "The provided source_ref is invalid.",
            status_code=400,
            details={"source_ref": source_ref},
        )

    return ParsedSourceRef(
        source_type=parts[0],
        source_id=parts[1],
        native_locator=parts[2],
    )
