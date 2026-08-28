"""Structured JSON Lines logging for the Bronze stage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")
VALID_LEVELS = {"INFO", "WARN", "ERROR"}
VALID_STATUSES = {"success", "partial_failure", "failed"}
STANDARD_ERROR_CODES = {
    "REQUIRED_VALUE_MISSING",
    "PK_DUPLICATE",
    "FK_ORPHAN",
    "DOMAIN_UNKNOWN",
    "DATETIME_PARSE_FAILED",
    "CHECKSUM_MISMATCH",
    "DATE_CONFLICT",
    "ROW_COUNT_MISMATCH",
}
OPTIONAL_EVENT_FIELDS = {
    "event_type",
    "page_no",
    "page_status",
    "fetched_count",
    "inserted_count",
    "duplicate_count",
    "retry_count",
    "failed_stage",
    "file_size_bytes",
    "file_sha256",
    "source_run_id_count",
}

API_KEY_PATTERN = re.compile(r"brz_\d{8}_[A-Za-z0-9_-]+")
MONGODB_URI_PATTERN = re.compile(r"mongodb(?:\+srv)?://[^\s]+", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/][^\s]+")
UNIX_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s/]+/)+[^\s]*")


def now_iso() -> str:
    return datetime.now(SEOUL).isoformat(timespec="milliseconds")


def mask_identifier(value: Any) -> str | None:
    """Return a stable non-reversible value for a source identifier."""

    if value is None:
        return None
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"masked-{digest}"


def safe_message(message: str) -> str:
    """Remove credentials, endpoints, and local paths from a log message."""

    result = API_KEY_PATTERN.sub("<credential>", message)
    result = MONGODB_URI_PATTERN.sub("<credential>", result)
    result = URL_PATTERN.sub("<private-endpoint>", result)
    result = WINDOWS_PATH_PATTERN.sub("<internal-path>", result)
    return UNIX_PATH_PATTERN.sub("<internal-path>", result)


def _safe_int(value: Any, default: int = 0) -> int:
    if type(value) is int and value >= 0:
        return value
    return default


def normalize_event(value: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an existing JSONL event without retaining arbitrary payloads."""

    run_id = value.get("run_id")
    stage = value.get("stage")
    timestamp = value.get("timestamp")
    if not isinstance(run_id, str) or not run_id:
        return None
    if not isinstance(stage, str) or not stage:
        return None
    if not isinstance(timestamp, str) or not timestamp:
        return None

    level = value.get("level", "INFO")
    if level == "WARNING":
        level = "WARN"
    if level not in VALID_LEVELS:
        level = "INFO"

    status = value.get("status", "success")
    if status not in VALID_STATUSES:
        status = "failed"

    input_count = _safe_int(value.get("input_count"))
    success_count = _safe_int(value.get("success_count"))
    failure_count = _safe_int(value.get("failure_count"))
    quarantine_count = _safe_int(value.get("quarantine_count"))
    if input_count != success_count + failure_count + quarantine_count:
        return None

    message = value.get("message", "")
    if not isinstance(message, str):
        message = str(message)

    event: dict[str, Any] = {
        "timestamp": timestamp,
        "level": level,
        "run_id": run_id,
        "stage": stage,
        "dataset_id": value.get("dataset_id") or "UNKNOWN",
        "status": status,
        "input_count": input_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "quarantine_count": quarantine_count,
        "duration_ms": _safe_int(value.get("duration_ms")),
        "message": safe_message(message),
    }

    source_record_id = value.get("source_record_id")
    if source_record_id is not None:
        event["source_record_id"] = mask_identifier(source_record_id)
    rule_id = value.get("rule_id")
    if isinstance(rule_id, str) and rule_id:
        event["rule_id"] = rule_id
    error_code = value.get("error_code")
    if error_code in STANDARD_ERROR_CODES:
        event["error_code"] = error_code
    correction_codes = value.get("correction_codes")
    if isinstance(correction_codes, list) and all(isinstance(code, str) for code in correction_codes):
        event["correction_codes"] = correction_codes

    for field_name in OPTIONAL_EVENT_FIELDS:
        field_value = value.get(field_name)
        if field_value is None:
            continue
        if field_name.endswith("_count") or field_name in {"page_no", "retry_count", "file_size_bytes"}:
            if type(field_value) is int and field_value >= 0:
                event[field_name] = field_value
        elif isinstance(field_value, (str, bool, float)):
            event[field_name] = safe_message(field_value) if isinstance(field_value, str) else field_value
    return event


class StructuredLogWriter:
    """Write sanitized events to the local JSONL log only."""

    def __init__(self, path: Path, run_id: str, *, stage: str = "bronze", echo: bool = True) -> None:
        self.path = path
        self.run_id = run_id
        self.stage = stage
        self.echo = echo

    def emit(
        self,
        level: str,
        message: str,
        *,
        dataset_id: str = "UNKNOWN",
        status: str = "success",
        input_count: int = 0,
        success_count: int = 0,
        failure_count: int = 0,
        quarantine_count: int = 0,
        duration_ms: int = 0,
        source_record_id: Any = None,
        rule_id: str | None = None,
        error_code: str | None = None,
        correction_codes: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if level == "WARNING":
            level = "WARN"
        if level not in VALID_LEVELS:
            raise ValueError(f"지원하지 않는 로그 레벨입니다: {level}")
        if status not in VALID_STATUSES:
            raise ValueError(f"지원하지 않는 로그 상태입니다: {status}")
        counts = (input_count, success_count, failure_count, quarantine_count)
        if any(type(count) is not int or count < 0 for count in counts):
            raise ValueError("로그 건수는 0 이상의 정수여야 합니다.")
        if input_count != success_count + failure_count + quarantine_count:
            raise ValueError("로그 건수 합계가 input_count와 일치하지 않습니다.")
        if error_code is not None and error_code not in STANDARD_ERROR_CODES:
            raise ValueError(f"표준 오류 코드가 아닙니다: {error_code}")

        event: dict[str, Any] = {
            "timestamp": now_iso(),
            "level": level,
            "run_id": self.run_id,
            "stage": self.stage,
            "dataset_id": dataset_id or "UNKNOWN",
            "status": status,
            "input_count": input_count,
            "success_count": success_count,
            "failure_count": failure_count,
            "quarantine_count": quarantine_count,
            "duration_ms": max(0, int(duration_ms)),
            "message": safe_message(message),
        }
        if source_record_id is not None:
            event["source_record_id"] = mask_identifier(source_record_id)
        if rule_id:
            event["rule_id"] = rule_id
        if error_code:
            event["error_code"] = error_code
        if correction_codes:
            event["correction_codes"] = list(correction_codes)
        if extra:
            for key, value in extra.items():
                if key in OPTIONAL_EVENT_FIELDS and value is not None:
                    event[key] = value

        self._write_local(event)
        return event

    def info(self, message: str, **kwargs: Any) -> dict[str, Any]:
        return self.emit("INFO", message, **kwargs)

    def warn(self, message: str, **kwargs: Any) -> dict[str, Any]:
        return self.emit("WARN", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> dict[str, Any]:
        return self.emit("ERROR", message, **kwargs)

    def _write_local(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "posix":
                os.chmod(self.path.parent, 0o700)
                os.chmod(self.path, 0o600)
        except OSError as exc:
            raise RuntimeError("Bronze 로그 파일을 저장하지 못했습니다.") from exc
        if self.echo:
            print(serialized, end="", file=sys.stderr)
