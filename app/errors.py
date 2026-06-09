from __future__ import annotations


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
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

