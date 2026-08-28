from __future__ import annotations

from datetime import datetime, timezone


def iso_utc(value: datetime) -> str:
    """날짜를 UTC ISO 8601 문자열로 변환한다."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
