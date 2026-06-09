from __future__ import annotations

from collections.abc import Iterable

from app.models import SourceConfig


def render_row_text(
    source_config: SourceConfig,
    values_by_header: dict[str, str],
) -> tuple[str, str]:
    result_text_config = source_config.result_text or {}
    title_field = result_text_config.get("title_from")
    include_fields = _get_include_fields(result_text_config, values_by_header.keys())

    title = _build_title(title_field, values_by_header)
    lines = []
    for field_name in include_fields:
        field_value = values_by_header.get(field_name, "").strip()
        if not field_value:
            continue
        lines.append(f"{field_name}: {field_value}")

    text = "\n".join(lines)
    if not text and title:
        text = title

    return title, text


def _build_title(title_field: object, values_by_header: dict[str, str]) -> str:
    if isinstance(title_field, str):
        value = values_by_header.get(title_field, "").strip()
        if value:
            return value
    return ""


def _get_include_fields(
    result_text_config: dict[str, object],
    fallback_fields: Iterable[str],
) -> list[str]:
    include_fields = result_text_config.get("include_fields")
    if isinstance(include_fields, list):
        return [str(field_name) for field_name in include_fields]
    return [str(field_name) for field_name in fallback_fields]
