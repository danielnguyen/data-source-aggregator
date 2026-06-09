class SourceConfigError(Exception):
    """Base class for source configuration errors."""


class SourceConfigValidationError(SourceConfigError):
    """Raised when a source configuration is invalid."""

