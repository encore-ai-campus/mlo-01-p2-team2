from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mongo_pipeline.rule_standardizer import FieldRule, load_rule_definition


KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class CanonicalValidationIssue:
    """canonical 규칙으로 확인한 한 개의 Gold 검증 오류."""

    field: str
    error_code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "error_code": self.error_code,
            "message": self.message,
        }


class CanonicalRuleCatalog:
    """silver_canonical.yaml을 RDB Silver 컬럼 검증 계약으로 노출한다."""

    def __init__(self, rules_path: str | Path) -> None:
        self.rules_path = Path(rules_path)
        definition = load_rule_definition(self.rules_path)
        self.name = definition.name
        self.schema_version = definition.schema_version
        self._rules: dict[str, FieldRule] = {
            rule.field: rule for rule in definition.field_rules
        }

    @property
    def description(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "rules_file": str(self.rules_path.resolve()),
            "validated_fields": sorted(self._rules),
        }

    def normalize(self, field: str, value: Any) -> Any:
        """Silver에 저장된 값을 Gold JSON 표현으로만 정규화한다.

        텍스트·코드·enum은 Silver의 최종 값을 보존하고, SQLite에서 문자열로
        읽히는 datetime만 canonical output_timezone(Asia/Seoul) ISO 형식으로
        맞춘다. 원천값을 다시 추정하거나 다른 업무값으로 보정하지 않는다.
        """

        rule = self._rules.get(field)
        if rule is None or value is None:
            return value
        if rule.kind == "datetime":
            return _canonical_datetime(value, rule)
        if rule.kind == "boolean":
            return _canonical_boolean(value)
        if rule.kind in {"text", "code", "enum"} and not isinstance(value, str):
            return str(value)
        return value

    def validate(
        self,
        values: dict[str, Any],
        *,
        fields: tuple[str, ...] | None = None,
    ) -> list[CanonicalValidationIssue]:
        """주어진 RDB 컬럼 subset을 silver_canonical 규칙으로 검증한다."""

        selected_fields = fields or tuple(values)
        issues: list[CanonicalValidationIssue] = []
        for field in selected_fields:
            if field not in self._rules:
                continue
            rule = self._rules[field]
            value = values.get(field)
            if value is None:
                if rule.required:
                    issues.append(
                        CanonicalValidationIssue(
                            field=field,
                            error_code="REQUIRED_VALUE_MISSING",
                            message="canonical 필수 필드가 null입니다.",
                        )
                    )
                continue

            if rule.kind in {"text", "code", "enum"} and not isinstance(value, str):
                issues.append(
                    CanonicalValidationIssue(
                        field=field,
                        error_code="TYPE_MISMATCH",
                        message=f"문자열이 필요합니다: actual={type(value).__name__}",
                    )
                )
                continue
            if rule.kind == "boolean" and not isinstance(value, bool):
                issues.append(
                    CanonicalValidationIssue(
                        field=field,
                        error_code="TYPE_MISMATCH",
                        message=f"boolean이 필요합니다: actual={type(value).__name__}",
                    )
                )
                continue
            if rule.kind == "code" and not _matches_code(value, rule):
                issues.append(
                    CanonicalValidationIssue(
                        field=field,
                        error_code="DOMAIN_UNKNOWN",
                        message="canonical 식별자 형식과 일치하지 않습니다.",
                    )
                )
                continue
            if rule.kind == "enum" and value not in rule.allowed_values:
                issues.append(
                    CanonicalValidationIssue(
                        field=field,
                        error_code="DOMAIN_UNKNOWN",
                        message=(
                            "허용된 enum 값이 아닙니다: "
                            f"allowed={list(rule.allowed_values)}"
                        ),
                    )
                )
                continue
            if rule.kind == "datetime" and not _is_canonical_datetime(value):
                issues.append(
                    CanonicalValidationIssue(
                        field=field,
                        error_code="DATETIME_PARSE_FAILED",
                        message="canonical timezone 포함 ISO datetime이 아닙니다.",
                    )
                )

        return issues

    def schema_for(self, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        """패키지 schema.json에 넣을 canonical 필드 설명을 만든다."""

        derived_types = {
            "record_key": "string",
            "entity_type": "string",
            "record_id": "string",
            "source_table": "string",
            "correction_codes": "array",
            "standardization": "array",
        }
        derived_required = {
            "record_key",
            "entity_type",
            "record_id",
            "source_table",
            "correction_codes",
            "standardization",
        }
        result: list[dict[str, Any]] = []
        for field in fields:
            rule = self._rules.get(field)
            if rule is None:
                result.append(
                    {
                        "name": field,
                        "type": derived_types.get(field, "string"),
                        "nullable": field not in derived_required,
                        "source": "gold_derived",
                    }
                )
                continue
            type_name = {
                "text": "string",
                "code": "string",
                "enum": "string",
                "boolean": "boolean",
                "datetime": "string",
            }.get(rule.kind, rule.kind)
            result.append(
                {
                    "name": field,
                    "type": type_name,
                    "nullable": not rule.required,
                    "canonical_rule_id": rule.rule_id,
                    "source": "silver_canonical.yaml",
                }
            )
        return result


def _canonical_datetime(value: Any, rule: FieldRule) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        candidate = value.strip().replace("Z", "+00:00")
        parsed = _parse_datetime(candidate, rule)
    else:
        raise ValueError(
            f"datetime 값이 아닙니다: actual={type(value).__name__}"
        )

    input_zone = _zone(rule.input_timezone)
    output_zone = _zone(rule.output_timezone)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=input_zone)
    return parsed.astimezone(output_zone).isoformat(timespec="seconds")


def _parse_datetime(candidate: str, rule: FieldRule) -> datetime:
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass
    for input_format in rule.input_formats:
        try:
            return datetime.strptime(candidate, input_format)
        except ValueError:
            continue
    raise ValueError(f"datetime을 해석할 수 없습니다: {candidate!r}")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return KST


def _canonical_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in {"true", "1", "yes", "y", "active"}:
            return True
        if token in {"false", "0", "no", "n", "inactive"}:
            return False
    raise ValueError(f"boolean을 해석할 수 없습니다: {value!r}")


def _matches_code(value: str, rule: FieldRule) -> bool:
    prefix = re.escape(rule.prefix)
    separator = re.escape(rule.separator)
    pattern = rf"^{prefix}{separator}[0-9]{{{rule.digits}}}$"
    return re.fullmatch(pattern, value) is not None


def _is_canonical_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    expected_offset = datetime(2000, 1, 1, tzinfo=KST).utcoffset()
    return parsed.tzinfo is not None and parsed.utcoffset() == expected_offset
