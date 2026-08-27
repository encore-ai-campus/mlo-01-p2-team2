"""Validation and Bronze-envelope construction for crawler JSONL records."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_LINE_BYTES = 15 * 1024 * 1024


class RecordValidationError(ValueError):
    """Raised when one JSONL record violates the Bronze input contract."""

    def __init__(self, message: str, *, error_code: str | None = None, source_record_id: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.source_record_id = source_record_id


@dataclass(frozen=True)
class ParsedRecord:
    """A validated source document and its Bronze representation."""

    bronze_document: dict[str, Any]
    dataset_id: str
    source_filename: str
    source_sha256: str
    source_record_id: str
    source_record_sha256: str
    source_run_id: str | None
    collected_at: str | None
    crawler_version: str | None
    retry_count: int | None


def _require_nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecordValidationError(f"{field_name} 값이 비어 있거나 문자열이 아닙니다.", error_code="REQUIRED_VALUE_MISSING")
    return value


def _require_sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise RecordValidationError(f"{field_name} 값이 SHA-256 형식이 아닙니다.", error_code="CHECKSUM_MISMATCH")
    return value


def _require_integer(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RecordValidationError(f"{field_name} 값이 올바른 정수가 아닙니다.", error_code="REQUIRED_VALUE_MISSING")
    return value


def _parse_timestamp(value: Any, field_name: str) -> tuple[str, datetime]:
    if not isinstance(value, str):
        raise RecordValidationError(f"{field_name} 값이 문자열이 아닙니다.", error_code="DATETIME_PARSE_FAILED")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RecordValidationError(f"{field_name} 값을 ISO 날짜로 해석하지 못했습니다.", error_code="DATETIME_PARSE_FAILED") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecordValidationError(f"{field_name} 값에 시간대가 없습니다.", error_code="DATETIME_PARSE_FAILED")
    return value, parsed


def _validate_mongo_keys(value: Any, *, path: str) -> None:
    """Reject dynamic keys that cannot be safely represented in MongoDB."""

    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or "." in key or key.startswith("$"):
                raise RecordValidationError(f"{path}에 MongoDB에서 사용할 수 없는 키가 있습니다.")
            _validate_mongo_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_mongo_keys(child, path=f"{path}[{index}]")


def parse_record_line(
    raw_line: str,
    *,
    line_no: int,
    load_run_id: str,
    input_file_sha256: str,
    ingested_at: datetime | None = None,
) -> ParsedRecord:
    """Validate one line without modifying the source document or payload."""

    content = raw_line.rstrip("\r\n")
    if not content.strip():
        raise RecordValidationError("빈 JSONL 행입니다.")

    encoded = content.encode("utf-8")
    if len(encoded) > MAX_JSON_LINE_BYTES:
        raise RecordValidationError("JSONL 한 행이 MongoDB 문서 크기 제한에 가깝습니다.")

    try:
        document = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RecordValidationError("JSONL 행을 JSON 객체로 해석하지 못했습니다.") from exc

    if not isinstance(document, dict):
        raise RecordValidationError("JSONL 최상위 값이 객체가 아닙니다.")

    required_fields = {
        "dataset_id",
        "source_filename",
        "source_sha256",
        "record_id",
        "source_row_no",
        "source_record_sha256",
        "scheduled_release_at",
        "payload",
    }
    missing = sorted(required_fields - document.keys())
    if missing:
        raise RecordValidationError(
            f"필수 필드가 없습니다: {', '.join(missing)}",
            error_code="REQUIRED_VALUE_MISSING",
        )

    dataset_id = _require_nonempty_string(document["dataset_id"], "dataset_id")
    source_filename = _require_nonempty_string(document["source_filename"], "source_filename")
    source_sha256 = _require_sha256(document["source_sha256"], "source_sha256")
    source_record_sha256 = _require_sha256(document["source_record_sha256"], "source_record_sha256")

    record_id = document["record_id"]
    if type(record_id) is int:
        source_record_id = str(record_id)
    elif isinstance(record_id, str) and record_id:
        source_record_id = record_id
    else:
        raise RecordValidationError(
            "record_id 값이 올바르지 않습니다.",
            error_code="REQUIRED_VALUE_MISSING",
        )

    source_row_no = _require_integer(document["source_row_no"], "source_row_no", minimum=1)
    scheduled_release_at_raw, scheduled_release_at = _parse_timestamp(
        document["scheduled_release_at"],
        "scheduled_release_at",
    )

    payload = document["payload"]
    if not isinstance(payload, dict):
        raise RecordValidationError(
            "payload가 객체가 아닙니다.",
            error_code="REQUIRED_VALUE_MISSING",
            source_record_id=source_record_id,
        )
    _validate_mongo_keys(payload, path="payload")
    _validate_mongo_keys(document, path="raw_json")

    crawl_metadata = document.get("_crawl")
    if crawl_metadata is not None and not isinstance(crawl_metadata, dict):
        raise RecordValidationError("_crawl이 객체가 아닙니다.")

    source_run_id = None
    collected_at = None
    crawler_version = None
    retry_count = None
    if isinstance(crawl_metadata, dict):
        raw_source_run_id = crawl_metadata.get("run_id")
        if isinstance(raw_source_run_id, str) and raw_source_run_id:
            source_run_id = raw_source_run_id
        raw_collected_at = crawl_metadata.get("collected_at")
        if isinstance(raw_collected_at, str) and raw_collected_at:
            collected_at = raw_collected_at
        raw_crawler_version = crawl_metadata.get("crawler_version")
        if isinstance(raw_crawler_version, str) and raw_crawler_version:
            crawler_version = raw_crawler_version
        raw_retry_count = crawl_metadata.get("retry_count")
        if type(raw_retry_count) is int and raw_retry_count >= 0:
            retry_count = raw_retry_count

    effective_ingested_at = ingested_at or datetime.now(SEOUL)
    raw_line_sha256 = hashlib.sha256(encoded).hexdigest()
    bronze_record_id = f"{dataset_id}:{source_record_id}"

    bronze_document: dict[str, Any] = {
        "_id": bronze_record_id,
        "record_id": bronze_record_id,
        "dataset_id": dataset_id,
        "source_record_id": source_record_id,
        "source_row_no": source_row_no,
        "scheduled_release_at": scheduled_release_at,
        "scheduled_release_at_raw": scheduled_release_at_raw,
        "ingested_at": effective_ingested_at,
        "raw_json": document,
        "raw_json_text": content,
        "raw_line_sha256": raw_line_sha256,
        "source_record_sha256": source_record_sha256,
        "source_sha256": source_sha256,
        "source_filename": source_filename,
        "run_id": source_run_id or load_run_id,
        "load_run_id": load_run_id,
        "source_run_id": source_run_id,
    }

    return ParsedRecord(
        bronze_document=bronze_document,
        dataset_id=dataset_id,
        source_filename=source_filename,
        source_sha256=source_sha256,
        source_record_id=source_record_id,
        source_record_sha256=source_record_sha256,
        source_run_id=source_run_id,
        collected_at=collected_at,
        crawler_version=crawler_version,
        retry_count=retry_count,
    )


def make_quarantine_document(
    raw_line: str,
    *,
    line_no: int,
    load_run_id: str,
    input_file_sha256: str,
    error: RecordValidationError,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Keep an invalid source line for investigation without logging its payload."""

    content = raw_line.rstrip("\r\n")
    return {
        "_id": f"{input_file_sha256}:{line_no}",
        "run_id": load_run_id,
        "input_file_sha256": input_file_sha256,
        "line_no": line_no,
        "raw_line": content,
        "raw_line_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "source_record_id": error.source_record_id,
        "error_type": type(error).__name__,
        "error_code": error.error_code,
        "message": str(error),
        "created_at": created_at or datetime.now(SEOUL),
    }


