from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .validators import type_name


@dataclass
class FieldStats:
    """한 필드의 출현, null, 타입별 건수를 누적한다."""

    present_count: int = 0
    null_count: int = 0
    type_counts: Counter[str] = field(default_factory=Counter)


class SchemaProfiler:
    """문서를 읽는 동안 최상위 필드 구조를 제한된 크기로 요약한다."""

    def __init__(self, max_fields: int = 1_000) -> None:
        """추적할 최대 필드 수와 빈 통계 상태를 준비한다."""

        self._max_fields = max_fields
        self._fields: dict[str, FieldStats] = {}
        self.document_count = 0
        self.untracked_field_count = 0

    def observe(self, document: Mapping[str, Any]) -> None:
        """문서 한 건의 필드 존재 여부, null, 타입을 통계에 반영한다."""

        self.document_count += 1
        for field_name, value in document.items():
            if field_name not in self._fields and len(self._fields) >= self._max_fields:
                self.untracked_field_count += 1
                continue
            stats = self._fields.setdefault(field_name, FieldStats())
            stats.present_count += 1
            if value is None:
                stats.null_count += 1
            stats.type_counts[type_name(value)] += 1

    def report(self) -> dict[str, Any]:
        """필드별 누락률과 타입 분포를 리포트 형태로 반환한다."""

        fields: dict[str, Any] = {}
        total = self.document_count
        for field_name in sorted(self._fields):
            stats = self._fields[field_name]
            missing = total - stats.present_count
            fields[field_name] = {
                "present_count": stats.present_count,
                "missing_count": missing,
                "missing_ratio": round(missing / total, 4) if total else 0.0,
                "null_count": stats.null_count,
                "types": dict(sorted(stats.type_counts.items())),
            }
        return {
            "profiled_documents": total,
            "fields": fields,
            "untracked_field_occurrences": self.untracked_field_count,
        }
