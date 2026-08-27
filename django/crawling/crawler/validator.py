"""Pure validation functions for API responses."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ValidationError(RuntimeError):
    """Raised when an API response violates the expected contract."""


def _require_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(fields - value.keys())
    if missing:
        raise ValidationError(f"{label}에 필수 필드가 없습니다: {', '.join(missing)}")


def _require_int(value: Any, field_name: str) -> int:
    if type(value) is not int:
        raise ValidationError(f"{field_name} 값이 정수가 아닙니다.")
    return value


def _require_timestamp(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} 값이 문자열이 아닙니다.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{field_name} 값이 ISO 날짜 형식이 아닙니다.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{field_name} 값에 시간대가 없습니다.")
    return value


def validate_meta(meta: dict[str, Any]) -> tuple[str, ...]:
    """Validate metadata and return the published payload columns."""

    required = {
        "dataset_id",
        "name",
        "source_filename",
        "source_sha256",
        "columns",
        "total_rows",
        "released_rows",
        "serving_start_at",
        "serving_end_at",
        "refresh_minutes",
        "next_refresh_at",
        "server_time",
        "timezone",
    }
    _require_fields(meta, required, "meta 응답")

    if not isinstance(meta["dataset_id"], str) or not meta["dataset_id"]:
        raise ValidationError("meta.dataset_id가 비어 있습니다.")
    if not isinstance(meta["source_sha256"], str) or not SHA256_PATTERN.fullmatch(
        meta["source_sha256"]
    ):
        raise ValidationError("meta.source_sha256이 SHA-256 형식이 아닙니다.")

    columns = meta["columns"]
    if not isinstance(columns, list) or not columns:
        raise ValidationError("meta.columns가 비어 있거나 목록이 아닙니다.")
    if any(not isinstance(column, str) or not column for column in columns):
        raise ValidationError("meta.columns에 올바르지 않은 컬럼명이 있습니다.")
    if len(columns) != len(set(columns)):
        raise ValidationError("meta.columns에 중복된 컬럼명이 있습니다.")

    total_rows = _require_int(meta["total_rows"], "meta.total_rows")
    released_rows = _require_int(meta["released_rows"], "meta.released_rows")
    refresh_minutes = _require_int(meta["refresh_minutes"], "meta.refresh_minutes")
    if total_rows < 0 or released_rows < 0 or released_rows > total_rows:
        raise ValidationError("meta의 전체/공개 건수가 올바르지 않습니다.")
    if refresh_minutes <= 0:
        raise ValidationError("meta.refresh_minutes는 1 이상이어야 합니다.")

    for field_name in (
        "serving_start_at",
        "serving_end_at",
        "next_refresh_at",
        "server_time",
    ):
        _require_timestamp(meta[field_name], f"meta.{field_name}")

    return tuple(columns)


def validate_records_page(
    page: dict[str, Any],
    *,
    expected_dataset_id: str,
    expected_payload_columns: tuple[str, ...],
) -> None:
    """Validate one cursor page without changing its raw values."""

    required = {
        "dataset_id",
        "items",
        "count",
        "has_more",
        "next_cursor",
        "checkpoint",
        "released_rows",
        "total_rows",
        "next_refresh_at",
        "server_time",
    }
    _require_fields(page, required, "records 응답")

    if page["dataset_id"] != expected_dataset_id:
        raise ValidationError("페이지의 dataset_id가 meta.dataset_id와 다릅니다.")
    items = page["items"]
    if not isinstance(items, list):
        raise ValidationError("records.items가 목록이 아닙니다.")
    count = _require_int(page["count"], "records.count")
    if count != len(items):
        raise ValidationError(
            f"records.count와 실제 items 개수가 다릅니다. count={count} items={len(items)}"
        )
    if type(page["has_more"]) is not bool:
        raise ValidationError("records.has_more가 boolean이 아닙니다.")
    if page["has_more"] and (
        not isinstance(page["next_cursor"], str) or not page["next_cursor"]
    ):
        raise ValidationError("has_more=true이지만 next_cursor가 없습니다.")
    if not isinstance(page["checkpoint"], str) or not page["checkpoint"]:
        raise ValidationError("records.checkpoint가 비어 있습니다.")

    released_rows = _require_int(page["released_rows"], "records.released_rows")
    total_rows = _require_int(page["total_rows"], "records.total_rows")
    if released_rows < 0 or total_rows < 0 or released_rows > total_rows:
        raise ValidationError("records의 전체/공개 건수가 올바르지 않습니다.")
    _require_timestamp(page["next_refresh_at"], "records.next_refresh_at")
    _require_timestamp(page["server_time"], "records.server_time")

    expected_columns = set(expected_payload_columns)
    page_record_ids: set[int] = set()
    item_fields = {
        "record_id",
        "source_row_no",
        "source_record_sha256",
        "release_slot",
        "scheduled_release_at",
        "payload",
    }
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(f"items[{index}]가 객체가 아닙니다.")
        _require_fields(item, item_fields, f"items[{index}]")
        record_id = _require_int(item["record_id"], f"items[{index}].record_id")
        _require_int(item["source_row_no"], f"items[{index}].source_row_no")
        _require_int(item["release_slot"], f"items[{index}].release_slot")
        if record_id in page_record_ids:
            raise ValidationError(f"한 페이지 안에서 record_id가 중복됐습니다: {record_id}")
        page_record_ids.add(record_id)
        source_hash = item["source_record_sha256"]
        if not isinstance(source_hash, str) or not SHA256_PATTERN.fullmatch(source_hash):
            raise ValidationError(f"items[{index}].source_record_sha256 형식이 올바르지 않습니다.")
        _require_timestamp(item["scheduled_release_at"], f"items[{index}].scheduled_release_at")
        payload = item["payload"]
        if not isinstance(payload, dict):
            raise ValidationError(f"items[{index}].payload가 객체가 아닙니다.")
        payload_columns = set(payload)
        if payload_columns != expected_columns:
            missing = sorted(expected_columns - payload_columns)
            extra = sorted(payload_columns - expected_columns)
            raise ValidationError(
                f"items[{index}].payload 컬럼이 meta.columns와 다릅니다. "
                f"missing={missing} extra={extra}"
            )
