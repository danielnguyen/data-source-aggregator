from __future__ import annotations

from app.models import SourceAccessDiagnostic


class SourceConfigError(Exception):
    """Base class for source configuration errors."""


class SourceConfigValidationError(SourceConfigError):
    """Raised when a source configuration is invalid."""


class ServiceError(Exception):
    """Stable domain error for API responses."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
        diagnostic: SourceAccessDiagnostic | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.diagnostic = diagnostic


def extract_structural_http_status(error: Exception) -> int | None:
    response = getattr(error, "resp", None)
    candidates = (
        getattr(response, "status", None),
        getattr(error, "status_code", None),
    )
    for candidate in candidates:
        if (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and 100 <= candidate <= 599
        ):
            return int(candidate)
    return None


def observe_source_access_failure(error: Exception) -> SourceAccessDiagnostic:
    status_code = extract_structural_http_status(error)
    if status_code is not None:
        return SourceAccessDiagnostic(
            component="data-source-aggregator",
            stage="source_access",
            category="http_status",
            upstream_status_code=status_code,
        )
    if isinstance(error, TimeoutError):
        return SourceAccessDiagnostic(
            component="data-source-aggregator",
            stage="source_access",
            category="timeout",
        )
    return SourceAccessDiagnostic(
        component="data-source-aggregator",
        stage="source_access",
        category="dependency_failure",
    )
