"""Backward-compatible imports for app-owned structured logging."""

from second_project.service.structured_logging import (
    STANDARD_ERROR_CODES,
    StructuredLogWriter,
    mask_identifier,
    normalize_event,
    now_iso,
    safe_message,
)

__all__ = [
    "STANDARD_ERROR_CODES",
    "StructuredLogWriter",
    "mask_identifier",
    "normalize_event",
    "now_iso",
    "safe_message",
]
