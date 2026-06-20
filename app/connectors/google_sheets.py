from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from http import HTTPStatus
from uuid import uuid4

from app.credentials import (
    CredentialRegistry,
    CredentialType,
    load_credential_registry,
)
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
from app.services.relevance import build_query_relevance_profile, overlap_score, tokenize_text
from app.services.result_text import render_row_text
from app.services.source_ref import parse_source_ref

READ_ONLY_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
SOURCE_REF_PATTERN = re.compile(
    r"^(?P<worksheet>(?:'[^']*(?:''[^']*)*'|[^!]+))!"
    r"(?P<start_col>[A-Z]+)(?P<start_row>\d+)"
    r"(?::(?P<end_col>[A-Z]+)(?P<end_row>\d+))?$"
)


class GoogleSheetsClient:
    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        raise NotImplementedError


class LiveGoogleSheetsClient(GoogleSheetsClient):
    def __init__(self, service) -> None:
        self._service = service

    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        response = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )
        return response.get("values", [])


@dataclass
class SheetRow:
    row_number: int
    values_by_header: dict[str, str]
    range_name: str


@dataclass(frozen=True)
class RowMatch:
    score: int
    matched_tokens: set[str]
    specific_matched_tokens: set[str]

    @property
    def has_specific_subject_match(self) -> bool:
        return bool(self.specific_matched_tokens)


class GoogleSheetsConnector:
    connector_name = "google_sheets"

    def __init__(
        self,
        client_factory: Callable[[SourceConfig], GoogleSheetsClient] | None = None,
        credential_registry_loader: Callable[[], CredentialRegistry] = load_credential_registry,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._client_factory = client_factory or self._build_client
        self._credential_registry_loader = credential_registry_loader
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    async def search(
        self,
        request: SearchRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        sheet_rows = self._load_sheet_rows(source_config)
        query_profile = build_query_relevance_profile(request.query)
        source_tokens = _source_identity_tokens(source_config)
        scored_rows: list[tuple[RowMatch, date | None, int, ResultEnvelope]] = []

        for index, sheet_row in enumerate(sheet_rows):
            result_envelope = self._build_row_result(
                source_config,
                sheet_row,
                include_raw=request.include_raw,
            )
            row_match = _score_row(
                query_profile.tokens,
                source_tokens,
                sheet_row,
                result_envelope,
            )
            if row_match.score <= 0:
                continue
            scored_rows.append((row_match, result_envelope.record_date, index, result_envelope))

        if query_profile.wants_latest:
            scored_rows.sort(
                key=lambda item: (
                    -(1 if item[0].has_specific_subject_match else 0),
                    -(1 if item[1] is not None else 0),
                    -(item[1].toordinal() if item[1] is not None else 0),
                    -item[0].score,
                    item[2],
                )
            )
        else:
            scored_rows.sort(key=lambda item: (-item[0].score, item[2]))
        if request.max_results is not None:
            scored_rows = scored_rows[: request.max_results]
        return [result_envelope for _, _, _, result_envelope in scored_rows]

    async def check_health(self, source_config: SourceConfig) -> SourceHealth:
        checked_at = self._now_factory()
        try:
            client = self._client_factory(source_config)
            client.get_values(
                self._spreadsheet_id(source_config),
                self._health_check_range(source_config),
            )
        except ServiceError as exc:
            return SourceHealth(
                status=SourceStatus.UNAVAILABLE,
                last_checked_at=checked_at,
                last_error=_map_google_health_service_error(exc),
            )
        except Exception as exc:
            return SourceHealth(
                status=SourceStatus.UNAVAILABLE,
                last_checked_at=checked_at,
                last_error=_map_google_health_exception(exc),
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
        parsed = parse_google_sheets_source_ref(request.source_ref)
        if parsed.source_id != source_config.source_id:
            raise ServiceError(
                "invalid_source_ref",
                "The provided source_ref does not match the configured source.",
                status_code=400,
                details={"source_ref": request.source_ref},
            )
        expected_worksheet = source_config.connector_config["worksheet"]
        if parsed.worksheet != expected_worksheet:
            raise ServiceError(
                "invalid_source_ref",
                "The provided source_ref does not match the configured worksheet.",
                status_code=400,
                details={"source_ref": request.source_ref},
            )

        sheet_rows = self._load_sheet_rows(source_config)
        matching_rows = [
            sheet_row
            for sheet_row in sheet_rows
            if parsed.start_row <= sheet_row.row_number <= parsed.end_row
        ]
        if not matching_rows:
            return []

        if parsed.start_row == parsed.end_row:
            return [
                self._build_row_result(
                    source_config,
                    matching_rows[0],
                    include_raw=request.include_raw,
                )
            ]

        return [
            self._build_range_result(
                source_config,
                matching_rows,
                parsed,
                include_raw=request.include_raw,
            )
        ]

    async def context(
        self,
        request: ContextRequest,
        source_config: SourceConfig,
    ) -> list[ResultEnvelope]:
        if request.context_mode != "nearby_rows":
            raise ServiceError(
                "unsupported_operation",
                f"Context mode '{request.context_mode}' is not supported for google_sheets.",
                status_code=501,
                details={"context_mode": request.context_mode, "operation": "context"},
            )

        parsed = parse_google_sheets_source_ref(request.source_ref)
        if parsed.source_id != source_config.source_id:
            raise ServiceError(
                "invalid_source_ref",
                "The provided source_ref does not match the configured source.",
                status_code=400,
                details={"source_ref": request.source_ref},
            )
        expected_worksheet = source_config.connector_config["worksheet"]
        if parsed.worksheet != expected_worksheet:
            raise ServiceError(
                "invalid_source_ref",
                "The provided source_ref does not match the configured worksheet.",
                status_code=400,
                details={"source_ref": request.source_ref},
            )

        max_context_rows = int(source_config.retrieval.model_extra.get("max_context_rows", 20))
        requested_rows = (
            request.budget.max_rows
            if request.budget and request.budget.max_rows
            else None
        )
        row_limit = min(requested_rows, max_context_rows) if requested_rows else max_context_rows

        before_count = row_limit // 2
        after_count = row_limit - before_count - 1
        start_row = max(self._header_row(source_config) + 1, parsed.start_row - before_count)
        end_row = parsed.start_row + after_count

        sheet_rows = self._load_sheet_rows(source_config)
        selected_rows = [
            sheet_row
            for sheet_row in sheet_rows
            if start_row <= sheet_row.row_number <= end_row
        ]

        return [
            self._build_row_result(
                source_config,
                sheet_row,
                include_raw=False,
                available_context=False,
            )
            for sheet_row in selected_rows
        ]

    def _load_sheet_rows(self, source_config: SourceConfig) -> list[SheetRow]:
        client = self._client_factory(source_config)
        worksheet = self._worksheet_name(source_config)
        values = client.get_values(
            self._spreadsheet_id(source_config),
            quote_worksheet_name(worksheet),
        )
        header_row = self._header_row(source_config)
        if len(values) < header_row:
            return []

        headers = [str(value).strip() for value in values[header_row - 1]]
        last_col = column_index_to_letter(max(len(headers), 1))
        sheet_rows: list[SheetRow] = []
        for index, row_values in enumerate(values[header_row:], start=header_row + 1):
            values_by_header = {
                header: str(row_values[position]).strip()
                for position, header in enumerate(headers)
                if header and position < len(row_values)
            }
            if not any(value for value in values_by_header.values()):
                continue
            sheet_rows.append(
                SheetRow(
                    row_number=index,
                    values_by_header=values_by_header,
                    range_name=f"{quote_worksheet_name(worksheet)}!A{index}:{last_col}{index}",
                )
            )
        return sheet_rows

    def _build_row_result(
        self,
        source_config: SourceConfig,
        sheet_row: SheetRow,
        *,
        include_raw: bool,
        available_context: bool = True,
    ) -> ResultEnvelope:
        title, text = render_row_text(source_config, sheet_row.values_by_header)
        if not title:
            title = f"Row {sheet_row.row_number}"
        if not text:
            text = title

        context_items = (
            [AvailableContext(context_mode="nearby_rows", description="Fetch nearby rows.")]
            if available_context
            else []
        )

        return ResultEnvelope(
            result_id=f"r_{uuid4().hex}",
            source_type="google_sheets",
            source_id=source_config.source_id,
            source_name=source_config.display_name,
            source_ref=f"google_sheets:{source_config.source_id}:{sheet_row.range_name}",
            retrieved_at=self._now_factory(),
            cache_status=CacheStatus.LIVE,
            title=title,
            content_type="spreadsheet_row",
            text=text,
            confidence=Confidence.HIGH,
            raw=(
                {
                    "sheet_name": self._worksheet_name(source_config),
                    "range": sheet_row.range_name.split("!", 1)[1],
                    "row_number": sheet_row.row_number,
                    "values_by_header": dict(sheet_row.values_by_header),
                }
                if include_raw
                else None
            ),
            available_context=context_items,
            record_date=_extract_sheet_row_date(sheet_row.values_by_header),
        )

    def _build_range_result(
        self,
        source_config: SourceConfig,
        sheet_rows: list[SheetRow],
        parsed: "ParsedGoogleSheetsSourceRef",
        *,
        include_raw: bool,
    ) -> ResultEnvelope:
        text = "\n\n".join(
            render_row_text(source_config, sheet_row.values_by_header)[1]
            or f"Row {sheet_row.row_number}"
            for sheet_row in sheet_rows
        )
        title = f"{parsed.worksheet} rows {parsed.start_row}-{parsed.end_row}"
        headers = list(sheet_rows[0].values_by_header.keys()) if sheet_rows else []
        raw_rows = [
            [sheet_row.values_by_header.get(header, "") for header in headers]
            for sheet_row in sheet_rows
        ]
        return ResultEnvelope(
            result_id=f"r_{uuid4().hex}",
            source_type="google_sheets",
            source_id=source_config.source_id,
            source_name=source_config.display_name,
            source_ref=f"google_sheets:{source_config.source_id}:{parsed.original_locator}",
            retrieved_at=self._now_factory(),
            cache_status=CacheStatus.LIVE,
            title=title,
            content_type="spreadsheet_range",
            text=text,
            confidence=Confidence.HIGH,
            raw=(
                {
                    "sheet_name": parsed.worksheet,
                    "range": parsed.original_locator.split("!", 1)[1],
                    "headers": headers,
                    "rows": raw_rows,
                }
                if include_raw
                else None
            ),
        )

    def _build_client(self, source_config: SourceConfig) -> GoogleSheetsClient:
        credentials_ref = source_config.connector_config.get("credentials_ref")
        if not isinstance(credentials_ref, str) or not credentials_ref:
            raise ServiceError(
                "credentials_missing",
                "A credentials_ref is required for the google_sheets connector.",
                status_code=500,
                details={"source_id": source_config.source_id},
            )

        credential_registry = self._credential_registry_loader()
        credential_config = credential_registry.credentials.get(credentials_ref)
        if credential_config is None:
            raise ServiceError(
                "credentials_missing",
                "The configured credential is not available for google_sheets.",
                status_code=500,
                details={
                    "source_id": source_config.source_id,
                    "connector": self.connector_name,
                },
            )

        try:
            from google.auth import default as google_auth_default
            from google.oauth2.service_account import Credentials as ServiceAccountCredentials
            from googleapiclient.discovery import build
        except ModuleNotFoundError as exc:
            raise ServiceError(
                "connector_error",
                "Google Sheets dependencies are not installed.",
                status_code=500,
                details={"connector": self.connector_name},
            ) from exc

        try:
            if credential_config.type == CredentialType.GOOGLE_SERVICE_ACCOUNT_FILE:
                credentials = ServiceAccountCredentials.from_service_account_file(
                    credential_config.path,
                    scopes=[READ_ONLY_SHEETS_SCOPE],
                )
            elif credential_config.type == CredentialType.GOOGLE_APPLICATION_DEFAULT:
                credentials, _ = google_auth_default(scopes=[READ_ONLY_SHEETS_SCOPE])
            else:
                raise ServiceError(
                    "credentials_missing",
                    "The configured credential is not supported for google_sheets.",
                    status_code=500,
                    details={
                        "source_id": source_config.source_id,
                        "connector": self.connector_name,
                    },
                )

            service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
            return LiveGoogleSheetsClient(service)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "credentials_missing",
                "The google_sheets connector could not initialize its read-only credentials.",
                status_code=500,
                details={
                    "source_id": source_config.source_id,
                    "connector": self.connector_name,
                },
            ) from exc

    def _spreadsheet_id(self, source_config: SourceConfig) -> str:
        spreadsheet_id = source_config.connector_config.get("spreadsheet_id")
        if not isinstance(spreadsheet_id, str) or not spreadsheet_id:
            raise ServiceError(
                "invalid_request",
                "The configured google_sheets source is missing spreadsheet_id.",
                status_code=500,
                details={"source_id": source_config.source_id},
            )
        return spreadsheet_id

    def _worksheet_name(self, source_config: SourceConfig) -> str:
        worksheet = source_config.connector_config.get("worksheet")
        if not isinstance(worksheet, str) or not worksheet:
            raise ServiceError(
                "invalid_request",
                "The configured google_sheets source is missing worksheet.",
                status_code=500,
                details={"source_id": source_config.source_id},
            )
        return worksheet

    def _header_row(self, source_config: SourceConfig) -> int:
        header_row = source_config.connector_config.get("header_row")
        if not isinstance(header_row, int) or header_row < 1:
            raise ServiceError(
                "invalid_request",
                "The configured google_sheets source has an invalid header_row.",
                status_code=500,
                details={"source_id": source_config.source_id},
            )
        return header_row

    def _health_check_range(self, source_config: SourceConfig) -> str:
        worksheet = quote_worksheet_name(self._worksheet_name(source_config))
        header_row = self._header_row(source_config)
        return f"{worksheet}!A{header_row}:A{header_row}"


@dataclass
class ParsedGoogleSheetsSourceRef:
    source_id: str
    worksheet: str
    start_col: str
    start_row: int
    end_col: str
    end_row: int
    original_locator: str


def parse_google_sheets_source_ref(source_ref: str) -> ParsedGoogleSheetsSourceRef:
    parsed_source_ref = parse_source_ref(source_ref)
    if parsed_source_ref.source_type != "google_sheets":
        raise ServiceError(
            "invalid_source_ref",
            "The provided source_ref is not a google_sheets reference.",
            status_code=400,
            details={"source_ref": source_ref},
        )

    match = SOURCE_REF_PATTERN.fullmatch(parsed_source_ref.native_locator)
    if match is None:
        raise ServiceError(
            "invalid_source_ref",
            "The provided source_ref has an invalid worksheet range.",
            status_code=400,
            details={"source_ref": source_ref},
        )

    worksheet = unquote_worksheet_name(match.group("worksheet"))
    start_row = int(match.group("start_row"))
    end_col = match.group("end_col") or match.group("start_col")
    end_row = int(match.group("end_row") or match.group("start_row"))
    if end_row < start_row:
        raise ServiceError(
            "invalid_source_ref",
            "The provided source_ref has an invalid row range.",
            status_code=400,
            details={"source_ref": source_ref},
        )

    return ParsedGoogleSheetsSourceRef(
        source_id=parsed_source_ref.source_id,
        worksheet=worksheet,
        start_col=match.group("start_col"),
        start_row=start_row,
        end_col=end_col,
        end_row=end_row,
        original_locator=parsed_source_ref.native_locator,
    )


def quote_worksheet_name(worksheet: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_]+", worksheet):
        return worksheet
    escaped = worksheet.replace("'", "''")
    return f"'{escaped}'"


def unquote_worksheet_name(worksheet: str) -> str:
    if worksheet.startswith("'") and worksheet.endswith("'"):
        return worksheet[1:-1].replace("''", "'")
    return worksheet


def column_index_to_letter(index: int) -> str:
    result = []
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        result.append(chr(65 + remainder))
    return "".join(reversed(result))


def _score_row(
    query_tokens: set[str],
    source_tokens: set[str],
    sheet_row: SheetRow,
    result_envelope: ResultEnvelope,
) -> RowMatch:
    if not query_tokens:
        return RowMatch(score=0, matched_tokens=set(), specific_matched_tokens=set())

    row_tokens = tokenize_text(
        " ".join(
            [
                result_envelope.title,
                result_envelope.text,
                *sheet_row.values_by_header.values(),
            ]
        )
    )
    score, matched_tokens = overlap_score(query_tokens, row_tokens, weight=10)
    if matched_tokens:
        score += len(matched_tokens)
    specific_matched_tokens = matched_tokens - source_tokens
    return RowMatch(
        score=score,
        matched_tokens=matched_tokens,
        specific_matched_tokens=specific_matched_tokens,
    )


def _source_identity_tokens(source_config: SourceConfig) -> set[str]:
    return tokenize_text(
        " ".join(
            [
                source_config.source_id,
                source_config.display_name,
                source_config.connector,
            ]
        )
    )


def _extract_sheet_row_date(values_by_header: dict[str, str]) -> date | None:
    for header, value in values_by_header.items():
        if "date" not in header.lower():
            continue
        parsed = _parse_sheet_date(value)
        if parsed is not None:
            return parsed
    return None


def _parse_sheet_date(value: str) -> date | None:
    normalized = value.strip()
    if not normalized:
        return None

    for parser in (
        _parse_iso_date,
        _parse_slash_date,
    ):
        parsed = parser(normalized)
        if parsed is not None:
            return parsed
    return None


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_slash_date(value: str) -> date | None:
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if match is None:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _map_google_health_service_error(error: ServiceError) -> str:
    if error.code == "credentials_missing":
        return "credentials_missing"
    if error.code == "connector_error":
        return "connector_not_configured"
    if error.code == "invalid_request":
        return "connector_not_configured"
    return "source_unavailable"


def _map_google_health_exception(error: Exception) -> str:
    status_code = _extract_http_status_code(error)
    if status_code == HTTPStatus.UNAUTHORIZED:
        return "credentials_expired"
    if status_code == HTTPStatus.FORBIDDEN:
        return "permission_denied"
    if status_code is not None:
        return "source_unavailable"

    message = str(error).lower()
    if "expired" in message or "refresh" in message:
        return "credentials_expired"
    if "permission" in message or "forbidden" in message or "access denied" in message:
        return "permission_denied"
    if "credential" in message and ("missing" in message or "not found" in message):
        return "credentials_missing"
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
