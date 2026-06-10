from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.connectors.base import get_connector
from app.connectors.google_sheets import GoogleSheetsConnector
from app.connectors.ics_calendar import (
    IcsCalendarClient,
    IcsCalendarConnector,
    build_ics_calendar_source_ref,
    parse_ics_calendar_source_ref,
)
from app.errors import ServiceError
from app.main import create_app
from app.models import ContextRequest, FetchRequest, SearchRequest

FIXTURE_ICS_TEXT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//DSA Test//EN
BEGIN:VEVENT
UID:sports-team-home-20261010
DTSTART:20261010T190000Z
DTEND:20261010T220000Z
SUMMARY:Sports Team vs Rivals
LOCATION:Scotiabank Arena
DESCRIPTION:Regular season game
LAST-MODIFIED:20260601T120000Z
END:VEVENT
BEGIN:VEVENT
UID:sports-team-away-20261012
DTSTART:20261012T190000Z
DTEND:20261012T220000Z
SUMMARY:Sports Team vs Away Opponent
LOCATION:TD Garden
DESCRIPTION:Away game
END:VEVENT
BEGIN:VEVENT
UID:sports-team-practice-20260501
DTSTART:20260501T150000Z
DTEND:20260501T170000Z
SUMMARY:Sports Team practice
LOCATION:Ford Performance Centre
DESCRIPTION:Past practice session
END:VEVENT
END:VCALENDAR
"""


class FakeIcsCalendarClient(IcsCalendarClient):
    def __init__(self, payload: str, *, should_fail: bool = False) -> None:
        self.payload = payload
        self.should_fail = should_fail

    def get_text(self, url: str) -> str:
        if self.should_fail:
            raise RuntimeError(f"failed to load {url}")
        return self.payload


@pytest.fixture
def ics_source_config(source_config_factory):
    return source_config_factory(
        source_id="calendar_sports",
        display_name="Sports Calendar",
        description="Sports schedule source.",
        domain_tags=["calendar", "sports"],
        connector="ics_calendar",
        connector_config={
            "url": "https://private.example.test/sports-calendar.ics",
            "timezone": "America/Toronto",
        },
        result_text={
            "title_from": "summary",
            "include_fields": ["summary", "start", "end", "location", "description"],
        },
        retrieval={
            "default_mode": "targeted",
            "max_results": 20,
            "max_bytes": 100000,
            "max_text_chars": 40000,
            "lookback_days": 30,
            "lookahead_days": 365,
            "allow_full_fetch": True,
        },
    )


def test_get_connector_returns_real_ics_calendar_connector() -> None:
    connector = get_connector("ics_calendar")

    assert isinstance(connector, IcsCalendarConnector)


def test_google_sheets_still_returns_real_connector() -> None:
    connector = get_connector("google_sheets")

    assert isinstance(connector, GoogleSheetsConnector)


def test_parse_ics_calendar_source_ref_round_trips_uid() -> None:
    source_ref = build_ics_calendar_source_ref("calendar_sports", "sports/team:20261010")

    parsed = parse_ics_calendar_source_ref(source_ref)

    assert parsed.source_id == "calendar_sports"
    assert parsed.uid == "sports/team:20261010"


@pytest.mark.anyio
async def test_search_returns_matching_events_from_fake_ics_text(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
        now_factory=lambda: datetime(2026, 6, 9, tzinfo=UTC),
    )

    results = await connector.search(
        SearchRequest(query="sports team rivals", include_raw=True),
        ics_source_config,
    )

    assert len(results) == 2
    assert results[0].title == "Sports Team vs Rivals"
    assert results[0].source_ref == "ics_calendar:calendar_sports:event:sports-team-home-20261010"
    assert results[0].content_type == "calendar_event"
    assert results[0].confidence.value == "high"
    assert results[0].source_name == "Sports Calendar"


@pytest.mark.anyio
async def test_search_returns_empty_list_when_no_event_matches(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
    )

    results = await connector.search(
        SearchRequest(query="rangers", include_raw=True),
        ics_source_config,
    )

    assert results == []


@pytest.mark.anyio
async def test_search_honors_max_results_and_ranking(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
        now_factory=lambda: datetime(2026, 6, 9, tzinfo=UTC),
    )

    results = await connector.search(
        SearchRequest(query="away game sports team", max_results=1, include_raw=True),
        ics_source_config,
    )

    assert len(results) == 1
    assert results[0].title == "Sports Team vs Away Opponent"


@pytest.mark.anyio
async def test_search_filters_by_lookback_and_lookahead_window(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
        now_factory=lambda: datetime(2026, 6, 9, tzinfo=UTC),
    )

    results = await connector.search(
        SearchRequest(query="practice", include_raw=True),
        ics_source_config,
    )

    assert results == []


@pytest.mark.anyio
async def test_result_text_renders_configured_include_fields(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
        now_factory=lambda: datetime(2026, 6, 9, tzinfo=UTC),
    )

    results = await connector.search(
        SearchRequest(query="rivals", include_raw=True),
        ics_source_config,
    )

    assert results[0].text == (
        "summary: Sports Team vs Rivals\n"
        "start: 2026-10-10T19:00:00+00:00\n"
        "end: 2026-10-10T22:00:00+00:00\n"
        "location: Scotiabank Arena\n"
        "description: Regular season game"
    )


@pytest.mark.anyio
async def test_raw_is_omitted_when_include_raw_false(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
    )

    results = await connector.search(
        SearchRequest(query="rivals", include_raw=False),
        ics_source_config,
    )

    assert results[0].raw is None


@pytest.mark.anyio
async def test_raw_is_included_when_include_raw_true_without_url(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
    )

    results = await connector.search(
        SearchRequest(query="rivals", include_raw=True),
        ics_source_config,
    )

    assert results[0].raw is not None
    assert results[0].raw["uid"] == "sports-team-home-20261010"
    assert "url" not in results[0].raw
    assert "private.example.test" not in str(results[0].raw)


@pytest.mark.anyio
async def test_fetch_by_valid_event_source_ref_returns_expected_event(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
    )

    results = await connector.fetch(
        FetchRequest(
            source_ref="ics_calendar:calendar_sports:event:sports-team-home-20261010",
            include_raw=True,
            budget={"max_bytes": 100000},
        ),
        ics_source_config,
    )

    assert len(results) == 1
    assert results[0].title == "Sports Team vs Rivals"
    assert results[0].source_name == "Sports Calendar"


@pytest.mark.anyio
async def test_fetch_by_malformed_source_ref_returns_invalid_source_ref(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
    )

    with pytest.raises(ServiceError, match="invalid"):
        await connector.fetch(
            FetchRequest(
                source_ref="ics_calendar:calendar_sports:not-an-event",
                include_raw=True,
                budget={"max_bytes": 100000},
            ),
            ics_source_config,
        )


@pytest.mark.anyio
async def test_fetch_rejects_mismatched_source_id(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
    )

    with pytest.raises(ServiceError) as error_info:
        await connector.fetch(
            FetchRequest(
                source_ref="ics_calendar:other_source:event:sports-team-home-20261010",
                include_raw=True,
                budget={"max_bytes": 100000},
            ),
            ics_source_config,
        )

    assert error_info.value.code == "invalid_source_ref"


@pytest.mark.anyio
async def test_context_upcoming_events_returns_events_in_order(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
        now_factory=lambda: datetime(2026, 6, 9, tzinfo=UTC),
    )

    results = await connector.context(
        ContextRequest(
            source_ref="ics_calendar:calendar_sports:event:sports-team-home-20261010",
            context_mode="upcoming_events",
            budget={"max_rows": 2, "max_bytes": 100000},
        ),
        ics_source_config,
    )

    assert [result.title for result in results] == [
        "Sports Team vs Rivals",
        "Sports Team vs Away Opponent",
    ]
    assert all(result.source_name == "Sports Calendar" for result in results)
    assert all(result.raw is None for result in results)


@pytest.mark.anyio
async def test_context_rejects_unknown_mode(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
    )

    with pytest.raises(ServiceError, match="not supported"):
        await connector.context(
            ContextRequest(
                source_ref="ics_calendar:calendar_sports:event:sports-team-home-20261010",
                context_mode="nearby_events",
                budget={"max_rows": 2, "max_bytes": 100000},
            ),
            ics_source_config,
        )


@pytest.mark.anyio
async def test_context_rejects_mismatched_source_id(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
    )

    with pytest.raises(ServiceError) as error_info:
        await connector.context(
            ContextRequest(
                source_ref="ics_calendar:other_source:event:sports-team-home-20261010",
                context_mode="upcoming_events",
                budget={"max_rows": 2, "max_bytes": 100000},
            ),
            ics_source_config,
        )

    assert error_info.value.code == "invalid_source_ref"


@pytest.mark.anyio
async def test_connector_error_does_not_expose_configured_url(ics_source_config) -> None:
    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT, should_fail=True),
    )

    with pytest.raises(ServiceError) as error_info:
        await connector.search(
            SearchRequest(query="sports", include_raw=True),
            ics_source_config,
        )

    error_payload = {
        "code": error_info.value.code,
        "message": error_info.value.message,
        "details": error_info.value.details,
    }
    assert "private.example.test" not in str(error_payload)
    assert "sports-calendar.ics" not in str(error_payload)


def _write_ics_source_config(source_dir: Path) -> None:
    (source_dir / "calendar_sports.yaml").write_text(
        """
source_id: calendar_sports
display_name: Sports Calendar
description: Sports schedule source.
domain_tags: [calendar, sports]
connector: ics_calendar
enabled: true
sensitivity: low
access_mode: read_only
connector_config:
  url: https://private.example.test/sports-calendar.ics
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
  include_fields: [summary, start, end, location, description]
""",
        encoding="utf-8",
    )


@pytest.mark.anyio
async def test_search_route_and_audit_do_not_expose_ics_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.services import fetch as fetch_service
    from app.services import search as search_service

    audit_path = tmp_path / "audit" / "events.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_ics_source_config(source_dir)

    connector = IcsCalendarConnector(
        client_factory=lambda _: FakeIcsCalendarClient(FIXTURE_ICS_TEXT),
        now_factory=lambda: datetime(2026, 6, 9, tzinfo=UTC),
    )
    monkeypatch.setattr(search_service.connector_base, "get_connector", lambda _: connector)
    monkeypatch.setattr(fetch_service.connector_base, "get_connector", lambda _: connector)

    transport = httpx.ASGITransport(app=create_app(source_config_dir=source_dir))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/sources/search",
            json={
                "query": "rivals",
                "source_ids": ["calendar_sports"],
                "retrieval_mode": "targeted",
                "allowed_sensitivity": "low",
                "budget": {
                    "max_results": 5,
                    "max_bytes": 50000,
                    "max_text_chars": 20000,
                },
                "include_raw": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["title"] == "Sports Team vs Rivals"
    assert payload["results"][0]["source_name"] == "Sports Calendar"
    assert "private.example.test" not in json.dumps(payload)

    audit_event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert audit_event["operation"] == "search"
    assert audit_event["status"] == "success"
    assert "private.example.test" not in json.dumps(audit_event)
