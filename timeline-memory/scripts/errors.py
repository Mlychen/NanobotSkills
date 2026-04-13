from __future__ import annotations

from typing import Any


class TimelineStructuredError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        category: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.details = details or {}


class TimelineInvalidArgumentError(TimelineStructuredError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="TM_INVALID_ARGUMENT",
            category="invalid_argument",
            details=details,
        )


class TimelineReadFailedError(TimelineStructuredError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="TM_READ_FAILED",
            category="read_failed",
            details=details,
        )


class TimelineTurnConflictError(TimelineStructuredError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="TM_TURN_CONFLICT",
            category="conflict",
            details=details,
        )


class TimelineMetadataConflictError(TimelineStructuredError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="TM_METADATA_CONFLICT",
            category="conflict",
            details=details,
        )


class TimelinePartialWriteError(TimelineStructuredError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="TM_PARTIAL_WRITE",
            category="recovery_failed",
            details=details,
        )
