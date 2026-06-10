from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from http import HTTPStatus
from urllib.parse import quote, unquote
from urllib.request import urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.errors import ServiceError
from app.models import (
    AvailableContext,
    CacheStatus,
    Confidence,
    ContextRequest,
    FetchRequest,
    ResultEnvelope,
    SearchRequest,
    SourceConfig,
    SourceHealth,
    SourceStatus,
)
from app.services.source_ref import parse_source_ref


class IcsCalendarClient:
    def get_text(self, url: str) -> str:
        raise NotImplementedError


class HttpIcsCalendarClient(IcsCalendarClient):
    def get_text(self, url: str) -> str:
        with urlopen(url, timeout=15) as response:  # noqa: S310
            return response.read().decode("utf-8")


@dataclass
class CalendarEvent:
    uid: str
    summary: str
    start: datetime
    end: datetime | None
    location: str | None
    description: str | None
    status: str | None
    last_modified: datetime | None
    warnings: list[str]


@dataclass
class ParsedIcsCalendarSourceRef:
    source_id: str
    uid: str


class IcsCalendarConnector:
    connector_name = "ics_calendar"

    def __init__(
        self,
        client_factory: Callable[[SourceConfig], IcsCalendarClient] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._client_factory = client_factory or (lambda _: HttpIcsCalendarClient())
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    async def search(
        self,
        request: SearchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        now = self._now_factory()
        events = self._load_events(source_config)
        windowed_events = [
            event for event in events if self._event_in_search_window(source_config, event, now)
        ]

        query = request.query.strip().lower()
        query_terms = [term for term in query.split() if term]
        scored_events: list[tuple[int, CalendarEvent, ResultEnvelope]] = []

        for event in windowed_events:
            result_envelope = self._build_event_result(
                source_config,
                event,
                include_raw=request.include_raw,
                include_context=True,
            )
            haystack = self._search_haystack(event)
            score = _score_event(query, query_terms, haystack)
            if score <= 0:
                continue
            scored_events.append((score, event, result_envelope))

        scored_events.sort(
            key=lambda item: (
                -item[0],
                0 if item[1].start >= now else 1,
                item[1].start,
                item[1].uid,
            )
        )
        if request.max_results is not None:
            scored_events = scored_events[: request.max_results]
        return [result_envelope for _, _, result_envelope in scored_events]

    async def check_health(self, source_config: SourceConfig) -> SourceHealth:
        checked_at = self._now_factory()
        try:
            payload = self._client_factory(source_config).get_text(
                self._calendar_url(source_config)
            )
            parse_ics_calendar_text(
                payload,
                timezone_name=self._timezone_name(source_config),
            )
        except ServiceError as exc:
            return SourceHealth(
                status=SourceStatus.UNAVAILABLE,
                last_checked_at=checked_at,
                last_error=_map_ics_health_service_error(exc),
            )
        except Exception as exc:
            return SourceHealth(
                status=SourceStatus.UNAVAILABLE,
                last_checked_at=checked_at,
                last_error=_map_ics_health_exception(exc),
            )

        return SourceHealth(
            status=SourceStatus.READY,
            last_checked_at=checked_at,
            last_error=None,
        )

    async def fetch(
        self,
        request: FetchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        parsed = parse_ics_calendar_source_ref(request.source_ref)
        self._validate_source_ref_source(parsed, request.source_ref, source_config)

        event = self._find_event_by_uid(source_config, parsed.uid)
        if event is None:
            return []

        return [
            self._build_event_result(
                source_config,
                event,
                include_raw=request.include_raw,
                include_context=True,
            )
        ]

    async def context(
        self,
        request: ContextRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        if request.context_mode != "upcoming_events":
            raise ServiceError(
                "unsupported_operation",
                f"Context mode '{request.context_mode}' is not supported for ics_calendar.",
                status_code=501,
                details={"context_mode": request.context_mode, "operation": "context"},
            )

        parsed = parse_ics_calendar_source_ref(request.source_ref)
        self._validate_source_ref_source(parsed, request.source_ref, source_config)

        now = self._now_factory()
        limit = self._context_limit(source_config, request)
        events = sorted(
            self._load_events(source_config),
            key=lambda event: (event.start, event.uid),
        )
        upcoming_events = [
            event
            for event in events
            if event.start >= now and self._event_within_lookahead(source_config, event, now)
        ]

        return [
            self._build_event_result(
                source_config,
                event,
                include_raw=False,
                include_context=False,
            )
            for event in upcoming_events[:limit]
        ]

    def _load_events(self, source_config: SourceConfig) -> list[CalendarEvent]:
        client = self._client_factory(source_config)
        try:
            payload = client.get_text(self._calendar_url(source_config))
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "source_unavailable",
                "The ics_calendar source is currently unavailable.",
                status_code=502,
                details={"source_id": source_config.source_id, "connector": self.connector_name},
            ) from exc

        try:
            return parse_ics_calendar_text(
                payload,
                timezone_name=self._timezone_name(source_config),
            )
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "connector_error",
                "The ics_calendar source returned data that could not be parsed.",
                status_code=502,
                details={"source_id": source_config.source_id, "connector": self.connector_name},
            ) from exc

    def _find_event_by_uid(
        self,
        source_config: SourceConfig,
        uid: str,
    ) -> CalendarEvent | None:
        for event in self._load_events(source_config):
            if event.uid == uid:
                return event
        return None

    def _build_event_result(
        self,
        source_config: SourceConfig,
        event: CalendarEvent,
        *,
        include_raw: bool,
        include_context: bool,
    ) -> ResultEnvelope:
        title, text = render_event_text(source_config, event)
        if not title:
            title = event.summary or f"Event {event.uid}"
        if not text:
            text = title

        return ResultEnvelope(
            result_id=f"r_{uuid4().hex}",
            source_type="ics_calendar",
            source_id=source_config.source_id,
            source_name=source_config.display_name,
            source_ref=build_ics_calendar_source_ref(source_config.source_id, event.uid),
            retrieved_at=self._now_factory(),
            source_modified_at=event.last_modified,
            cache_status=CacheStatus.LIVE,
            title=title,
            content_type="calendar_event",
            text=text,
            confidence=Confidence.HIGH,
            raw=_build_raw_payload(event) if include_raw else None,
            available_context=(
                [
                    AvailableContext(
                        context_mode="upcoming_events",
                        description="Fetch upcoming events from this calendar.",
                    )
                ]
                if include_context
                else []
            ),
            warnings=list(event.warnings),
        )

    def _calendar_url(self, source_config: SourceConfig) -> str:
        url = source_config.connector_config.get("url")
        if not isinstance(url, str) or not url:
            url_env = source_config.connector_config.get("url_env")
            if isinstance(url_env, str) and url_env:
                env_value = os.getenv(url_env)
                if isinstance(env_value, str) and env_value:
                    return env_value

            raise ServiceError(
                "invalid_request",
                "The configured ics_calendar source is missing url.",
                status_code=500,
                details={"source_id": source_config.source_id},
            )
        return url

    def _timezone_name(self, source_config: SourceConfig) -> str:
        timezone_name = source_config.connector_config.get("timezone")
        if not isinstance(timezone_name, str) or not timezone_name:
            raise ServiceError(
                "invalid_request",
                "The configured ics_calendar source is missing timezone.",
                status_code=500,
                details={"source_id": source_config.source_id},
            )
        return timezone_name

    def _event_in_search_window(
        self,
        source_config: SourceConfig,
        event: CalendarEvent,
        now: datetime,
    ) -> bool:
        lookback_days = int(source_config.retrieval.model_extra.get("lookback_days", 30))
        lookahead_days = int(source_config.retrieval.model_extra.get("lookahead_days", 365))
        start = now - timedelta(days=lookback_days)
        end = now + timedelta(days=lookahead_days)
        return start <= event.start <= end

    def _event_within_lookahead(
        self,
        source_config: SourceConfig,
        event: CalendarEvent,
        now: datetime,
    ) -> bool:
        lookahead_days = int(source_config.retrieval.model_extra.get("lookahead_days", 365))
        return event.start <= now + timedelta(days=lookahead_days)

    def _context_limit(self, source_config: SourceConfig, request: ContextRequest) -> int:
        default_limit = int(source_config.retrieval.max_results)
        if request.budget and request.budget.max_rows is not None:
            return min(default_limit, request.budget.max_rows)
        return default_limit

    def _validate_source_ref_source(
        self,
        parsed: ParsedIcsCalendarSourceRef,
        source_ref: str,
        source_config: SourceConfig,
    ) -> None:
        if parsed.source_id != source_config.source_id:
            raise ServiceError(
                "invalid_source_ref",
                "The provided source_ref does not match the configured source.",
                status_code=400,
                details={"source_ref": source_ref},
            )

    def _search_haystack(self, event: CalendarEvent) -> str:
        return "\n".join(
            filter(
                None,
                [
                    event.summary,
                    event.location,
                    event.description,
                    event.status,
                ],
            )
        ).lower()


def build_ics_calendar_source_ref(source_id: str, uid: str) -> str:
    return f"ics_calendar:{source_id}:event:{quote(uid, safe='')}"


def parse_ics_calendar_source_ref(source_ref: str) -> ParsedIcsCalendarSourceRef:
    parsed_source_ref = parse_source_ref(source_ref)
    if parsed_source_ref.source_type != "ics_calendar":
        raise ServiceError(
            "invalid_source_ref",
            "The provided source_ref is not an ics_calendar reference.",
            status_code=400,
            details={"source_ref": source_ref},
        )

    prefix = "event:"
    if not parsed_source_ref.native_locator.startswith(prefix):
        raise ServiceError(
            "invalid_source_ref",
            "The provided source_ref has an invalid event locator.",
            status_code=400,
            details={"source_ref": source_ref},
        )

    encoded_uid = parsed_source_ref.native_locator[len(prefix) :]
    if not encoded_uid:
        raise ServiceError(
            "invalid_source_ref",
            "The provided source_ref has an invalid event locator.",
            status_code=400,
            details={"source_ref": source_ref},
        )

    uid = unquote(encoded_uid)
    if not uid:
        raise ServiceError(
            "invalid_source_ref",
            "The provided source_ref has an invalid event locator.",
            status_code=400,
            details={"source_ref": source_ref},
        )

    return ParsedIcsCalendarSourceRef(source_id=parsed_source_ref.source_id, uid=uid)


def parse_ics_calendar_text(payload: str, *, timezone_name: str) -> list[CalendarEvent]:
    timezone = ZoneInfo(timezone_name)
    unfolded_lines = _unfold_ics_lines(payload)
    if "BEGIN:VCALENDAR" not in unfolded_lines:
        raise ValueError("ICS payload is missing VCALENDAR.")
    events: list[CalendarEvent] = []
    in_event = False
    event_lines: list[str] = []

    for line in unfolded_lines:
        if line == "BEGIN:VEVENT":
            in_event = True
            event_lines = []
            continue
        if line == "END:VEVENT":
            in_event = False
            event = _parse_event_block(event_lines, timezone)
            if event is not None:
                events.append(event)
            event_lines = []
            continue
        if in_event:
            event_lines.append(line)

    return events


def render_event_text(source_config: SourceConfig, event: CalendarEvent) -> tuple[str, str]:
    result_text_config = source_config.result_text or {}
    title_field = result_text_config.get("title_from")
    event_fields = _event_field_values(event)
    include_fields = _get_include_fields(result_text_config, event_fields.keys())

    title = ""
    if isinstance(title_field, str):
        title = event_fields.get(title_field, "").strip()

    lines = []
    for field_name in include_fields:
        field_value = event_fields.get(str(field_name), "").strip()
        if not field_value:
            continue
        lines.append(f"{field_name}: {field_value}")

    return title, "\n".join(lines)


def _build_raw_payload(event: CalendarEvent) -> dict[str, object]:
    return {
        "uid": event.uid,
        "summary": event.summary,
        "start": event.start.isoformat(),
        "end": event.end.isoformat() if event.end is not None else None,
        "location": event.location,
        "description": event.description,
        "status": event.status,
        "last_modified": event.last_modified.isoformat() if event.last_modified else None,
    }


def _event_field_values(event: CalendarEvent) -> dict[str, str]:
    return {
        "summary": event.summary,
        "start": event.start.isoformat(),
        "end": event.end.isoformat() if event.end is not None else "",
        "location": event.location or "",
        "description": event.description or "",
        "status": event.status or "",
    }


def _get_include_fields(
    result_text_config: dict[str, object],
    fallback_fields: object,
) -> list[str]:
    include_fields = result_text_config.get("include_fields")
    if isinstance(include_fields, list):
        return [str(field_name) for field_name in include_fields]
    return [str(field_name) for field_name in fallback_fields]


def _unfold_ics_lines(payload: str) -> list[str]:
    unfolded_lines: list[str] = []
    for line in payload.splitlines():
        if not line:
            continue
        if line.startswith((" ", "\t")) and unfolded_lines:
            unfolded_lines[-1] += line[1:]
            continue
        unfolded_lines.append(line.strip())
    return unfolded_lines


def _parse_event_block(lines: list[str], timezone: ZoneInfo) -> CalendarEvent | None:
    properties: dict[str, tuple[dict[str, str], str]] = {}
    warnings: list[str] = []

    for line in lines:
        if ":" not in line:
            continue
        name_and_params, raw_value = line.split(":", 1)
        parts = name_and_params.split(";")
        name = parts[0].upper()
        params: dict[str, str] = {}
        for part in parts[1:]:
            if "=" not in part:
                continue
            param_name, param_value = part.split("=", 1)
            params[param_name.upper()] = param_value

        if (
            name in {"RRULE", "RDATE", "EXDATE", "EXRULE"}
            and "recurrence_not_expanded" not in warnings
        ):
            warnings.append("recurrence_not_expanded")
        properties[name] = (params, _unescape_ics_text(raw_value))

    uid = properties.get("UID", ({}, ""))[1].strip()
    start_value = properties.get("DTSTART")
    if not uid or start_value is None:
        return None

    start = _parse_ics_datetime(start_value[1], start_value[0], timezone)
    end_prop = properties.get("DTEND")
    last_modified_prop = properties.get("LAST-MODIFIED")

    return CalendarEvent(
        uid=uid,
        summary=properties.get("SUMMARY", ({}, ""))[1].strip(),
        start=start,
        end=(
            _parse_ics_datetime(end_prop[1], end_prop[0], timezone)
            if end_prop is not None and end_prop[1].strip()
            else None
        ),
        location=_normalize_optional_text(properties.get("LOCATION", ({}, ""))[1]),
        description=_normalize_optional_text(properties.get("DESCRIPTION", ({}, ""))[1]),
        status=_normalize_optional_text(properties.get("STATUS", ({}, ""))[1]),
        last_modified=(
            _parse_ics_datetime(last_modified_prop[1], last_modified_prop[0], timezone)
            if last_modified_prop is not None and last_modified_prop[1].strip()
            else None
        ),
        warnings=warnings,
    )


def _parse_ics_datetime(raw_value: str, params: dict[str, str], timezone: ZoneInfo) -> datetime:
    value = raw_value.strip()
    if not value:
        raise ValueError("ICS datetime value is empty.")

    if params.get("VALUE") == "DATE" or len(value) == 8 and value.isdigit():
        parsed_date = datetime.strptime(value, "%Y%m%d").date()
        return datetime.combine(parsed_date, time.min, tzinfo=timezone)

    target_timezone = timezone
    if "TZID" in params:
        target_timezone = ZoneInfo(params["TZID"])

    if value.endswith("Z"):
        return _parse_datetime_value(value[:-1]).replace(tzinfo=UTC)

    parsed_datetime = _parse_datetime_value(value)
    return parsed_datetime.replace(tzinfo=target_timezone)


def _parse_datetime_value(value: str) -> datetime:
    formats = ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported ICS datetime format: {value}")


def _normalize_optional_text(value: str) -> str | None:
    normalized = value.strip()
    return normalized or None


def _unescape_ics_text(value: str) -> str:
    return (
        value.replace("\\N", "\n")
        .replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _score_event(query: str, query_terms: list[str], haystack: str) -> int:
    if not haystack:
        return 0

    score = 0
    if query and query in haystack:
        score += 100

    matched_terms = 0
    for term in query_terms:
        if term in haystack:
            matched_terms += 1
            score += 10

    if matched_terms == len(query_terms) and matched_terms > 0:
        score += 25

    return score


def _map_ics_health_service_error(error: ServiceError) -> str:
    if error.code == "invalid_request":
        return "connector_not_configured"
    if error.code == "permission_denied":
        return "permission_denied"
    if error.code == "invalid_source_ref":
        return "invalid_source_ref"
    return "source_unavailable"


def _map_ics_health_exception(error: Exception) -> str:
    status_code = _extract_http_status_code(error)
    if status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        return "permission_denied"
    if status_code is not None:
        return "source_unavailable"

    message = str(error).lower()
    if "permission" in message or "forbidden" in message or "access denied" in message:
        return "permission_denied"
    return "source_unavailable"


def _extract_http_status_code(error: Exception) -> int | None:
    response = getattr(error, "resp", None)
    status = getattr(response, "status", None)
    if isinstance(status, int):
        return status

    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    return None
