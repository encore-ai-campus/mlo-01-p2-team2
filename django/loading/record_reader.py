"""Backward-compatible imports for app-owned Bronze input validation."""

from second_project.service.record_reader import (
    MAX_JSON_LINE_BYTES,
    ParsedRecord,
    RecordValidationError,
    make_quarantine_document,
    parse_record_line,
)

__all__ = [
    "MAX_JSON_LINE_BYTES",
    "ParsedRecord",
    "RecordValidationError",
    "make_quarantine_document",
    "parse_record_line",
]
