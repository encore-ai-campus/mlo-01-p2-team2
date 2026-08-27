from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .standardizers import CommonStandardizer, StandardizationError
from .yaml_support import load_yaml_file


_MISSING = object()
_RULE_FILE_MAX_BYTES = 1024 * 1024
_VALID_KINDS = {"text", "code", "enum", "datetime", "integer"}
_VALID_ERROR_ACTIONS = {"reject", "null"}
_VALID_TEXT_WHITESPACE = {"preserve", "collapse", "remove", "remove_hangul"}
_VALID_ENUM_MATCHES = {
    "exact",
    "casefold",
    "casefold_remove_whitespace",
    "casefold_compact",
}


class RuleConfigurationError(ValueError):
    """YAML 규칙 파일의 구조나 값이 잘못됐을 때 발생한다."""


class _RuleViolation(ValueError):
    """한 필드 값이 선언된 표준화 규칙을 만족하지 못했음을 나타낸다."""


@dataclass(frozen=True)
class FieldRule:
    field: str
    source_paths: tuple[str, ...]
    rule_id: str
    kind: str
    required: bool
    on_error: str
    null_values: frozenset[str]
    invalid_values: frozenset[str]
    whitespace: str = "preserve"
    warning_contains: tuple[str, ...] = ()
    prefix: str = ""
    digits: int = 0
    separator: str = ""
    aliases: tuple[tuple[str, str], ...] = ()
    allowed_values: tuple[str, ...] = ()
    enum_match: str = "exact"
    input_formats: tuple[str, ...] = ()
    input_timezone: str = "UTC"
    output_timezone: str = "UTC"
    correction_code: str | None = None


@dataclass(frozen=True)
class EqualityRule:
    rule_id: str
    fields: tuple[str, ...]
    ignore_null: bool
    on_error: str


@dataclass(frozen=True)
class RecordIdRule:
    enabled: bool
    field: str
    source_system: str
    source_fields: tuple[str, ...]
    namespace: UUID
    overwrite: bool
    rule_id: str = "META-001"


@dataclass(frozen=True)
class AuditRule:
    enabled: bool
    field: str
    correction_codes_field: str | None = None


@dataclass(frozen=True)
class OutputRule:
    """표준화 결과에서 유지할 업무 컬럼과 기술 필드를 정의한다."""

    fields: tuple[str, ...] = ()
    preserve_fields: tuple[str, ...] = ("_id",)
    include_missing_as_null: bool = False


@dataclass(frozen=True)
class YamlRuleDefinition:
    schema_version: int
    name: str
    source_path: Path
    unicode_normalization: str
    field_rules: tuple[FieldRule, ...]
    equality_rules: tuple[EqualityRule, ...]
    record_id: RecordIdRule
    audit: AuditRule
    output: OutputRule


class YamlRuleStandardizer:
    """허용목록 방식의 YAML 규칙을 적용한 뒤 공통 JSON 표준화를 수행한다."""

    def __init__(
        self,
        definition: YamlRuleDefinition,
        *,
        common_standardizer: CommonStandardizer | None = None,
    ) -> None:
        self._definition = definition
        self._common = common_standardizer or CommonStandardizer()
        self._column_renamed = 0
        self.metrics: Counter[str] = Counter(
            column_renamed=0,
            type_converted=0,
            rule_checked=0,
            rule_applied=0,
            rule_nullified=0,
            rule_warning=0,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "YamlRuleStandardizer":
        """YAML 파일을 검증하고 실행 가능한 표준화기를 만든다."""

        return cls(load_rule_definition(path))

    @property
    def description(self) -> dict[str, Any]:
        """실행 리포트에 남길 규칙 파일 정보를 반환한다."""

        return {
            "type": "yaml_rules",
            "name": self._definition.name,
            "schema_version": self._definition.schema_version,
            "rules_file": str(self._definition.source_path.resolve()),
            "output_fields": list(self._definition.output.fields),
        }

    @property
    def requires_runtime_context(self) -> bool:
        """실행 ID·정규화 시각을 YAML 입력에 주입해야 하는지 반환한다."""

        return any(
            path.startswith("_runtime.")
            for rule in self._definition.field_rules
            for path in rule.source_paths
        )

    def standardize(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """문서 복사본에 필드 규칙과 교차 검사를 적용한다."""

        if not isinstance(document, Mapping):
            raise StandardizationError("문서가 object 형태가 아닙니다.")

        standardized, renamed = _clone_document(document)
        self._column_renamed += renamed
        events: list[dict[str, str]] = []

        for rule in self._definition.field_rules:
            self._apply_field_rule(standardized, rule, events)

        self._apply_record_id(standardized, events)
        self._apply_equality_rules(standardized, events)
        self._attach_audit(standardized, events)

        result = self._common.standardize(standardized)
        result = _project_output(result, self._definition.output)
        self.metrics["column_renamed"] = (
            self._column_renamed + self._common.metrics["column_renamed"]
        )
        self.metrics["type_converted"] = self._common.metrics["type_converted"]
        return result

    def _apply_field_rule(
        self,
        document: dict[str, Any],
        rule: FieldRule,
        events: list[dict[str, str]],
    ) -> None:
        raw_value = _MISSING
        for candidate in rule.source_paths:
            candidate_value = _get_path(document, candidate)
            if candidate_value is not _MISSING:
                raw_value = candidate_value
                break
        if raw_value is _MISSING:
            if rule.required:
                raise _standardization_error(rule, "필수 필드가 없습니다.")
            return

        self.metrics["rule_checked"] += 1
        was_null_sentinel = (
            isinstance(raw_value, str)
            and _comparison_token(raw_value, self._definition.unicode_normalization)
            in rule.null_values
        )
        try:
            value = self._normalize_null_and_invalid(raw_value, rule)
            if value is not None:
                value = self._standardize_value(value, rule)
                self._add_content_warnings(value, rule, events)
            if value is None and rule.required:
                raise _RuleViolation("필수 필드가 null 또는 무효값입니다.")
        except _RuleViolation as error:
            if rule.required or rule.on_error == "reject":
                raise _standardization_error(rule, str(error)) from error
            value = None
            self.metrics["rule_nullified"] += 1
            events.append(
                {
                    "rule_id": rule.rule_id,
                    "field": rule.field,
                    "action": "NULLIFIED",
                    "severity": "WARNING",
                    "message": str(error),
                }
            )

        if was_null_sentinel and value is None:
            self.metrics["rule_nullified"] += 1
            events.append(
                {
                    "rule_id": rule.rule_id,
                    "field": rule.field,
                    "action": "NULLIFIED",
                    "severity": "INFO",
                    "message": "null 센티널을 표준 null로 변환했습니다.",
                }
            )

        value_changed = not _values_equal(raw_value, value)
        target_value = _get_path(document, rule.field)
        if value_changed or target_value is _MISSING:
            _set_path(document, rule.field, value)
            self.metrics["rule_applied"] += 1
            if value_changed:
                if not any(
                    event["rule_id"] == rule.rule_id
                    and event["field"] == rule.field
                    and event["action"] == "NULLIFIED"
                    for event in events
                ):
                    events.append(
                        {
                            "rule_id": rule.rule_id,
                            "field": rule.field,
                            "action": "NORMALIZED",
                            "severity": "INFO",
                            **(
                                {"correction_code": rule.correction_code}
                                if rule.correction_code
                                else {}
                            ),
                        }
                    )

                for correction_code in _change_correction_codes(
                    raw_value,
                    value,
                    rule,
                    unicode_normalization=self._definition.unicode_normalization,
                ):
                    if not any(
                        event.get("correction_code") == correction_code
                        and event.get("field") == rule.field
                        for event in events
                    ):
                        events.append(
                            {
                                "rule_id": rule.rule_id,
                                "field": rule.field,
                                "action": "NORMALIZED",
                                "severity": "INFO",
                                "correction_code": correction_code,
                            }
                        )

        if isinstance(raw_value, str) and "\t" in raw_value:
            if not any(
                event.get("correction_code") == "TAB_CHARACTER_ERROR"
                and event.get("field") == rule.field
                for event in events
            ):
                events.append(
                    {
                        "rule_id": rule.rule_id,
                        "field": rule.field,
                        "action": "WARNING",
                        "severity": "WARNING",
                        "correction_code": "TAB_CHARACTER_ERROR",
                        "message": "탭 문자를 공백으로 정규화했습니다.",
                    }
                )

    def _add_content_warnings(
        self,
        value: Any,
        rule: FieldRule,
        events: list[dict[str, str]],
    ) -> None:
        if not isinstance(value, str):
            return
        normalized = unicodedata.normalize(self._definition.unicode_normalization, value)
        matched = [fragment for fragment in rule.warning_contains if fragment in normalized]
        if not matched:
            return
        self.metrics["rule_warning"] += 1
        events.append(
            {
                "rule_id": rule.rule_id,
                "field": rule.field,
                "action": "WARNING",
                "severity": "WARNING",
                "message": f"검토가 필요한 문자열 조각이 있습니다: {matched}",
            }
        )

    def _normalize_null_and_invalid(self, value: Any, rule: FieldRule) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            return value

        token = _comparison_token(value, self._definition.unicode_normalization)
        if token in rule.null_values:
            return None
        if token in rule.invalid_values:
            raise _RuleViolation(f"무효 센티널 값입니다: {value!r}")
        return value

    def _standardize_value(self, value: Any, rule: FieldRule) -> Any:
        if rule.kind == "text":
            return _standardize_text(
                value,
                form=self._definition.unicode_normalization,
                whitespace=rule.whitespace,
            )
        if rule.kind == "code":
            return _standardize_code(
                value,
                form=self._definition.unicode_normalization,
                prefix=rule.prefix,
                digits=rule.digits,
                separator=rule.separator,
            )
        if rule.kind == "enum":
            return _standardize_enum(
                value,
                form=self._definition.unicode_normalization,
                aliases=rule.aliases,
                allowed_values=rule.allowed_values,
                match=rule.enum_match,
            )
        if rule.kind == "datetime":
            return _standardize_datetime(
                value,
                form=self._definition.unicode_normalization,
                input_formats=rule.input_formats,
                input_timezone=rule.input_timezone,
                output_timezone=rule.output_timezone,
            )
        if rule.kind == "integer":
            return _standardize_integer(value)
        raise RuntimeError(f"검증되지 않은 규칙 종류입니다: {rule.kind}")

    def _apply_record_id(
        self,
        document: dict[str, Any],
        events: list[dict[str, str]],
    ) -> None:
        rule = self._definition.record_id
        if not rule.enabled:
            return

        current = _get_path(document, rule.field)
        if current is not _MISSING and current not in (None, "") and not rule.overwrite:
            return

        source_values: list[str] = []
        for field in rule.source_fields:
            value = _get_path(document, field)
            if value is _MISSING or value is None or value == "":
                raise StandardizationError(
                    f"[{rule.rule_id}] {rule.field}: ID 생성 원천 필드가 없습니다: {field}"
                )
            source_values.append(str(value))

        unique_name = "|".join([rule.source_system, *source_values])
        generated = str(uuid5(rule.namespace, unique_name))
        _set_path(document, rule.field, generated)
        self.metrics["rule_applied"] += 1
        events.append(
            {
                "rule_id": rule.rule_id,
                "field": rule.field,
                "action": "GENERATED",
                "severity": "INFO",
            }
        )

    def _apply_equality_rules(
        self,
        document: dict[str, Any],
        events: list[dict[str, str]],
    ) -> None:
        for rule in self._definition.equality_rules:
            values = [_get_path(document, field) for field in rule.fields]
            if _MISSING in values:
                if rule.ignore_null:
                    continue
                message = "비교할 필드 중 존재하지 않는 필드가 있습니다."
            elif rule.ignore_null and any(value is None for value in values):
                continue
            elif all(_values_equal(values[0], value) for value in values[1:]):
                continue
            else:
                message = "필드 값이 서로 일치하지 않습니다."

            field_label = ",".join(rule.fields)
            if rule.on_error == "reject":
                raise StandardizationError(
                    f"[{rule.rule_id}] {field_label}: {message}"
                )
            self.metrics["rule_warning"] += 1
            events.append(
                {
                    "rule_id": rule.rule_id,
                    "field": field_label,
                    "action": "WARNING",
                    "severity": "WARNING",
                    "message": message,
                }
            )

    def _attach_audit(
        self,
        document: dict[str, Any],
        events: list[dict[str, str]],
    ) -> None:
        audit = self._definition.audit
        all_events: list[dict[str, str]] = []
        if audit.enabled:
            existing = _get_path(document, audit.field)
            if existing is _MISSING:
                all_events = list(events)
            elif not isinstance(existing, list):
                raise StandardizationError(
                    f"감사 필드 `{audit.field}`가 이미 존재하며 array 형태가 아닙니다."
                )
            else:
                all_events = [*existing, *events]
            if events or existing is not _MISSING:
                _set_path(document, audit.field, all_events)

        if audit.correction_codes_field:
            _set_path(
                document,
                audit.correction_codes_field,
                _unique_correction_codes(all_events or events),
            )


def load_rule_definition(path: str | Path) -> YamlRuleDefinition:
    """YAML 원본을 강하게 검증해 불변 규칙 정의로 변환한다."""

    rule_path = Path(path)
    raw = load_yaml_file(rule_path, max_bytes=_RULE_FILE_MAX_BYTES)
    root = _as_mapping(raw, "YAML 최상위")
    _reject_unknown_keys(
        root,
        {
            "schema_version",
            "name",
            "defaults",
            "audit",
            "record_id",
            "fields",
            "checks",
            "output",
        },
        "YAML 최상위",
    )

    schema_version = root.get("schema_version")
    if schema_version != 1:
        raise RuleConfigurationError("schema_version은 정수 1이어야 합니다.")
    name = _required_string(root.get("name"), "name")

    defaults = _as_mapping(root.get("defaults", {}), "defaults")
    _reject_unknown_keys(
        defaults,
        {"unicode_normalization", "null_values", "on_error"},
        "defaults",
    )
    unicode_normalization = str(defaults.get("unicode_normalization", "NFKC")).upper()
    if unicode_normalization not in {"NFC", "NFKC"}:
        raise RuleConfigurationError("defaults.unicode_normalization은 NFC 또는 NFKC여야 합니다.")
    default_null_values = _normalized_tokens(
        defaults.get("null_values", [""]),
        form=unicode_normalization,
        label="defaults.null_values",
    )
    default_on_error = _error_action(defaults.get("on_error", "reject"), "defaults.on_error")

    field_rules: list[FieldRule] = []
    fields = _as_mapping(root.get("fields", {}), "fields")
    for raw_field, raw_rule in fields.items():
        field = _field_path(raw_field, "fields의 필드명")
        field_rules.append(
            _parse_field_rule(
                field,
                raw_rule,
                default_null_values=default_null_values,
                default_on_error=default_on_error,
                unicode_normalization=unicode_normalization,
            )
        )

    equality_rules = _parse_checks(root.get("checks", []))
    record_id = _parse_record_id(root.get("record_id", {}))
    audit = _parse_audit(root.get("audit", {}))
    output = _parse_output(root.get("output", {}))

    rule_ids = [rule.rule_id for rule in field_rules]
    rule_ids.extend(rule.rule_id for rule in equality_rules)
    if record_id.enabled:
        rule_ids.append(record_id.rule_id)
    duplicates = sorted({rule_id for rule_id in rule_ids if rule_ids.count(rule_id) > 1})
    if duplicates:
        raise RuleConfigurationError(f"rule_id가 중복되었습니다: {duplicates}")

    return YamlRuleDefinition(
        schema_version=1,
        name=name,
        source_path=rule_path,
        unicode_normalization=unicode_normalization,
        field_rules=tuple(field_rules),
        equality_rules=tuple(equality_rules),
        record_id=record_id,
        audit=audit,
        output=output,
    )


def _parse_field_rule(
    field: str,
    raw_rule: Any,
    *,
    default_null_values: frozenset[str],
    default_on_error: str,
    unicode_normalization: str,
) -> FieldRule:
    rule = _as_mapping(raw_rule, f"fields.{field}")
    kind = str(rule.get("kind", "text")).lower()
    if kind not in _VALID_KINDS:
        raise RuleConfigurationError(
            f"fields.{field}.kind는 {sorted(_VALID_KINDS)} 중 하나여야 합니다."
        )

    common_keys = {
        "rule_id",
        "kind",
        "source",
        "required",
        "on_error",
        "null_values",
        "invalid_values",
        "correction_code",
    }
    kind_keys = {
        "text": {"whitespace", "warning_contains"},
        "code": {"prefix", "digits", "separator"},
        "enum": {"aliases", "allowed_values", "match"},
        "datetime": {"input_formats", "input_timezone", "output_timezone"},
        "integer": set(),
    }[kind]
    _reject_unknown_keys(rule, common_keys | kind_keys, f"fields.{field}")

    source_paths = _source_paths(rule.get("source", field), field)
    rule_id = _required_string(rule.get("rule_id", f"FIELD:{field}"), f"fields.{field}.rule_id")
    required = _boolean(rule.get("required", False), f"fields.{field}.required")
    on_error = _error_action(
        rule.get("on_error", default_on_error),
        f"fields.{field}.on_error",
    )
    correction_code = rule.get("correction_code")
    if correction_code is not None:
        correction_code = _required_string(
            correction_code,
            f"fields.{field}.correction_code",
        )

    field_null_values = _normalized_tokens(
        rule.get("null_values", []),
        form=unicode_normalization,
        label=f"fields.{field}.null_values",
    )
    invalid_values = _normalized_tokens(
        rule.get("invalid_values", []),
        form=unicode_normalization,
        label=f"fields.{field}.invalid_values",
    )
    null_values = default_null_values | field_null_values
    overlap = null_values & invalid_values
    if overlap:
        raise RuleConfigurationError(
            f"fields.{field}의 null_values와 invalid_values가 겹칩니다: {sorted(overlap)}"
        )

    kwargs: dict[str, Any] = {}
    if kind == "text":
        whitespace = str(rule.get("whitespace", "preserve")).lower()
        if whitespace not in _VALID_TEXT_WHITESPACE:
            raise RuleConfigurationError(
                f"fields.{field}.whitespace는 {sorted(_VALID_TEXT_WHITESPACE)} 중 하나여야 합니다."
            )
        kwargs["whitespace"] = whitespace
        kwargs["warning_contains"] = tuple(
            _string_sequence(
                rule.get("warning_contains", []),
                f"fields.{field}.warning_contains",
            )
        )
    elif kind == "code":
        prefix = _required_string(rule.get("prefix"), f"fields.{field}.prefix").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]*", prefix):
            raise RuleConfigurationError(
                f"fields.{field}.prefix는 영문 대문자로 시작하는 영숫자여야 합니다."
            )
        digits = rule.get("digits")
        if isinstance(digits, bool) or not isinstance(digits, int) or not 1 <= digits <= 64:
            raise RuleConfigurationError(f"fields.{field}.digits는 1~64 정수여야 합니다.")
        separator = str(rule.get("separator", ""))
        if len(separator) > 3 or any(character.isspace() for character in separator):
            raise RuleConfigurationError(
                f"fields.{field}.separator는 공백 없는 3자 이하 문자열이어야 합니다."
            )
        kwargs.update(prefix=prefix, digits=digits, separator=separator)
    elif kind == "enum":
        enum_match = str(rule.get("match", "exact")).lower()
        if enum_match not in _VALID_ENUM_MATCHES:
            raise RuleConfigurationError(
                f"fields.{field}.match는 {sorted(_VALID_ENUM_MATCHES)} 중 하나여야 합니다."
            )
        aliases_raw = _as_mapping(rule.get("aliases", {}), f"fields.{field}.aliases")
        aliases = tuple(
            (
                _required_scalar_string(key, f"fields.{field}.aliases key"),
                _required_scalar_string(value, f"fields.{field}.aliases[{key!r}]"),
            )
            for key, value in aliases_raw.items()
        )
        allowed_values = tuple(
            _string_sequence(rule.get("allowed_values", []), f"fields.{field}.allowed_values")
        )
        if not aliases and not allowed_values:
            raise RuleConfigurationError(
                f"fields.{field} enum에는 aliases 또는 allowed_values가 필요합니다."
            )
        _validate_enum_collisions(
            aliases,
            allowed_values,
            match=enum_match,
            form=unicode_normalization,
            label=f"fields.{field}",
        )
        kwargs.update(
            aliases=aliases,
            allowed_values=allowed_values,
            enum_match=enum_match,
        )
    elif kind == "datetime":
        input_formats = tuple(
            _string_sequence(rule.get("input_formats", []), f"fields.{field}.input_formats")
        )
        if not input_formats:
            raise RuleConfigurationError(f"fields.{field}.input_formats가 비어 있습니다.")
        input_timezone = _required_string(
            rule.get("input_timezone", "UTC"),
            f"fields.{field}.input_timezone",
        )
        output_timezone = _required_string(
            rule.get("output_timezone", "UTC"),
            f"fields.{field}.output_timezone",
        )
        _validate_timezone(input_timezone, f"fields.{field}.input_timezone")
        _validate_timezone(output_timezone, f"fields.{field}.output_timezone")
        kwargs.update(
            input_formats=input_formats,
            input_timezone=input_timezone,
            output_timezone=output_timezone,
        )

    return FieldRule(
        field=field,
        source_paths=source_paths,
        rule_id=rule_id,
        kind=kind,
        required=required,
        on_error=on_error,
        null_values=null_values,
        invalid_values=invalid_values,
        correction_code=correction_code,
        **kwargs,
    )


def _parse_checks(raw_checks: Any) -> list[EqualityRule]:
    if raw_checks is None:
        return []
    if isinstance(raw_checks, (str, bytes)) or not isinstance(raw_checks, Sequence):
        raise RuleConfigurationError("checks는 array여야 합니다.")

    checks: list[EqualityRule] = []
    for index, raw_check in enumerate(raw_checks):
        label = f"checks[{index}]"
        check = _as_mapping(raw_check, label)
        _reject_unknown_keys(
            check,
            {"rule_id", "kind", "fields", "ignore_null", "on_error"},
            label,
        )
        kind = str(check.get("kind", "")).lower()
        if kind != "fields_equal":
            raise RuleConfigurationError(f"{label}.kind는 fields_equal만 지원합니다.")
        fields = tuple(_field_path(value, f"{label}.fields") for value in _string_sequence(check.get("fields", []), f"{label}.fields"))
        if len(fields) < 2:
            raise RuleConfigurationError(f"{label}.fields에는 2개 이상의 필드가 필요합니다.")
        on_error = str(check.get("on_error", "reject")).lower()
        if on_error not in {"reject", "warn"}:
            raise RuleConfigurationError(f"{label}.on_error는 reject 또는 warn이어야 합니다.")
        checks.append(
            EqualityRule(
                rule_id=_required_string(check.get("rule_id"), f"{label}.rule_id"),
                fields=fields,
                ignore_null=_boolean(check.get("ignore_null", True), f"{label}.ignore_null"),
                on_error=on_error,
            )
        )
    return checks


def _parse_record_id(raw_record_id: Any) -> RecordIdRule:
    record_id = _as_mapping(raw_record_id, "record_id")
    _reject_unknown_keys(
        record_id,
        {"enabled", "field", "source_system", "source_fields", "namespace_uuid", "overwrite", "rule_id"},
        "record_id",
    )
    enabled = _boolean(record_id.get("enabled", False), "record_id.enabled")
    field = _field_path(record_id.get("field", "record_id"), "record_id.field")
    source_system = str(record_id.get("source_system", "")).strip()
    source_fields = tuple(
        _field_path(value, "record_id.source_fields")
        for value in _string_sequence(record_id.get("source_fields", []), "record_id.source_fields")
    )
    namespace_raw = str(record_id.get("namespace_uuid", NAMESPACE_URL))
    try:
        namespace = UUID(namespace_raw)
    except ValueError as error:
        raise RuleConfigurationError("record_id.namespace_uuid가 UUID 형식이 아닙니다.") from error
    overwrite = _boolean(record_id.get("overwrite", False), "record_id.overwrite")
    rule_id = _required_string(record_id.get("rule_id", "META-001"), "record_id.rule_id")

    if enabled and not source_system:
        raise RuleConfigurationError("record_id.source_system은 비어 있을 수 없습니다.")
    if enabled and not source_fields:
        raise RuleConfigurationError("record_id.source_fields에는 하나 이상의 필드가 필요합니다.")
    return RecordIdRule(
        enabled=enabled,
        field=field,
        source_system=source_system,
        source_fields=source_fields,
        namespace=namespace,
        overwrite=overwrite,
        rule_id=rule_id,
    )


def _parse_audit(raw_audit: Any) -> AuditRule:
    audit = _as_mapping(raw_audit, "audit")
    _reject_unknown_keys(
        audit,
        {"enabled", "field", "correction_codes_field"},
        "audit",
    )
    correction_codes_field = audit.get("correction_codes_field")
    if correction_codes_field is not None:
        correction_codes_field = _field_path(
            correction_codes_field,
            "audit.correction_codes_field",
        )
    return AuditRule(
        enabled=_boolean(audit.get("enabled", False), "audit.enabled"),
        field=_field_path(audit.get("field", "_standardization"), "audit.field"),
        correction_codes_field=correction_codes_field,
    )


def _parse_output(raw_output: Any) -> OutputRule:
    """선택적 결과 projection 설정을 검증한다."""

    output = _as_mapping(raw_output, "output")
    _reject_unknown_keys(
        output,
        {"fields", "preserve_fields", "include_missing_as_null"},
        "output",
    )
    fields = tuple(_field_path(value, "output.fields") for value in _string_sequence(
        output.get("fields", []),
        "output.fields",
    ))
    preserve_fields = tuple(
        _field_path(value, "output.preserve_fields")
        for value in _string_sequence(
            output.get("preserve_fields", ["_id"]),
            "output.preserve_fields",
        )
    )
    return OutputRule(
        fields=fields,
        preserve_fields=preserve_fields,
        include_missing_as_null=_boolean(
            output.get("include_missing_as_null", False),
            "output.include_missing_as_null",
        ),
    )


def _standardize_text(value: Any, *, form: str, whitespace: str) -> str:
    if not isinstance(value, str):
        raise _RuleViolation(f"문자열이 필요합니다: actual={type(value).__name__}")
    text = unicodedata.normalize(form, value).strip()
    if whitespace == "collapse":
        return re.sub(r"\s+", " ", text)
    if whitespace == "remove":
        return re.sub(r"\s+", "", text)
    if whitespace == "remove_hangul":
        return re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", text)
    return text


def _standardize_integer(value: Any) -> int:
    """Mongo/JSON 입력의 정수 식별자를 표준 정수로 변환한다."""

    if isinstance(value, bool):
        raise _RuleViolation("정수 값이 필요합니다: actual=bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value.strip())
    raise _RuleViolation(f"정수 형식이 잘못됐습니다: actual={value!r}")


def _standardize_code(
    value: Any,
    *,
    form: str,
    prefix: str,
    digits: int,
    separator: str,
) -> str:
    if not isinstance(value, str):
        raise _RuleViolation(f"코드 문자열이 필요합니다: actual={type(value).__name__}")
    text = unicodedata.normalize(form, value).strip().upper()
    compact = re.sub(r"[\s_-]+", "", text)
    match = re.fullmatch(rf"{re.escape(prefix)}(\d{{{digits}}})", compact)
    if not match:
        raise _RuleViolation(
            f"코드 형식이 잘못됐습니다: expected={prefix}{separator}{'#' * digits}, actual={value!r}"
        )
    return f"{prefix}{separator}{match.group(1)}"


def _standardize_enum(
    value: Any,
    *,
    form: str,
    aliases: tuple[tuple[str, str], ...],
    allowed_values: tuple[str, ...],
    match: str,
) -> str:
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes)):
        raise _RuleViolation(f"enum 스칼라 값이 필요합니다: actual={type(value).__name__}")
    text = unicodedata.normalize(form, str(value)).strip()
    key = _enum_key(text, form=form, match=match)
    for raw_alias, canonical in aliases:
        if key == _enum_key(raw_alias, form=form, match=match):
            return canonical
    for canonical in allowed_values:
        if key == _enum_key(canonical, form=form, match=match):
            return canonical
    raise _RuleViolation(f"허용 목록에 없는 값입니다: {value!r}")


def _standardize_datetime(
    value: Any,
    *,
    form: str,
    input_formats: tuple[str, ...],
    input_timezone: str,
    output_timezone: str,
) -> str:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        text_value = unicodedata.normalize(form, value).strip()
        iso_candidate = text_value[:-1] + "+00:00" if text_value.endswith("Z") else text_value
        try:
            parsed = datetime.fromisoformat(iso_candidate)
        except ValueError:
            for input_format in input_formats:
                try:
                    parsed = datetime.strptime(text_value, input_format)
                    break
                except ValueError:
                    continue
    else:
        raise _RuleViolation(f"날짜 문자열 또는 datetime이 필요합니다: actual={type(value).__name__}")

    if parsed is None:
        raise _RuleViolation(f"허용된 날짜 형식으로 파싱할 수 없습니다: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(input_timezone))
    normalized = parsed.astimezone(ZoneInfo(output_timezone))
    output = normalized.isoformat(timespec="seconds")
    return output.replace("+00:00", "Z")


def _clone_document(document: Mapping[Any, Any]) -> tuple[dict[str, Any], int]:
    renamed = [0]

    def clone(value: Any, *, path: str, depth: int) -> Any:
        if depth > 100:
            raise StandardizationError(f"{path}: 중첩 깊이가 100을 초과했습니다.")
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for raw_key, raw_value in value.items():
                key = str(raw_key)
                if key in result:
                    raise StandardizationError(
                        f"{path}: 문자열 변환 후 필드명이 중복됩니다: {key}"
                    )
                if raw_key != key:
                    renamed[0] += 1
                result[key] = clone(raw_value, path=f"{path}.{key}", depth=depth + 1)
            return result
        if isinstance(value, list):
            return [
                clone(item, path=f"{path}[{index}]", depth=depth + 1)
                for index, item in enumerate(value)
            ]
        if isinstance(value, tuple):
            return tuple(
                clone(item, path=f"{path}[{index}]", depth=depth + 1)
                for index, item in enumerate(value)
            )
        return value

    return clone(document, path="$", depth=0), renamed[0]


def _get_path(document: Mapping[str, Any], path: str) -> Any:
    current: Any = document
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    current = document
    for key in keys[:-1]:
        child = current.get(key, _MISSING)
        if child is _MISSING:
            child = {}
            current[key] = child
        if not isinstance(child, dict):
            raise StandardizationError(
                f"필드 경로 `{path}`를 설정할 수 없습니다: `{key}`가 object가 아닙니다."
            )
        current = child
    current[keys[-1]] = value


def _comparison_token(value: str, form: str) -> str:
    return unicodedata.normalize(form, value).strip().casefold()


def _enum_key(value: str, *, form: str, match: str) -> str:
    text = unicodedata.normalize(form, value).strip()
    if match == "exact":
        return text
    text = text.casefold()
    if match == "casefold_remove_whitespace":
        return re.sub(r"\s+", "", text)
    if match == "casefold_compact":
        return re.sub(r"[\s_-]+", "", text)
    return text


def _values_equal(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _standardization_error(rule: FieldRule, message: str) -> StandardizationError:
    return StandardizationError(f"[{rule.rule_id}] {rule.field}: {message}")


def _as_mapping(value: Any, label: str) -> Mapping[Any, Any]:
    if not isinstance(value, Mapping):
        raise RuleConfigurationError(f"{label}은 object여야 합니다.")
    return value


def _reject_unknown_keys(
    mapping: Mapping[Any, Any],
    allowed: set[str],
    label: str,
) -> None:
    unknown = sorted(str(key) for key in mapping if str(key) not in allowed)
    if unknown:
        raise RuleConfigurationError(f"{label}에 지원하지 않는 키가 있습니다: {unknown}")


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuleConfigurationError(f"{label}은 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def _required_scalar_string(value: Any, label: str) -> str:
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes)):
        raise RuleConfigurationError(f"{label}은 스칼라 값이어야 합니다.")
    if value is None:
        raise RuleConfigurationError(f"{label}은 null일 수 없습니다.")
    text = str(value)
    if not text:
        raise RuleConfigurationError(f"{label}은 빈 값일 수 없습니다.")
    return text


def _string_sequence(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise RuleConfigurationError(f"{label}은 문자열 array여야 합니다.")
    return [_required_scalar_string(item, f"{label}[{index}]") for index, item in enumerate(value)]


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise RuleConfigurationError(f"{label}은 true 또는 false여야 합니다.")
    return value


def _error_action(value: Any, label: str) -> str:
    # YAML에서 따옴표 없는 `null`은 Python None으로 읽히므로 의도한 action으로 받는다.
    action = "null" if value is None else str(value).lower()
    if action not in _VALID_ERROR_ACTIONS:
        raise RuleConfigurationError(f"{label}은 reject 또는 null이어야 합니다.")
    return action


def _field_path(value: Any, label: str) -> str:
    path = _required_string(value, label)
    if path.startswith(".") or path.endswith(".") or ".." in path:
        raise RuleConfigurationError(f"{label}의 점 표기법이 올바르지 않습니다: {path!r}")
    return path


def _source_paths(value: Any, target_field: str) -> tuple[str, ...]:
    """필드의 원천 경로를 하나 이상의 fallback 경로로 변환한다."""

    if isinstance(value, str):
        return (_field_path(value, f"fields.{target_field}.source"),)
    if isinstance(value, (bytes,)) or not isinstance(value, Sequence):
        raise RuleConfigurationError(
            f"fields.{target_field}.source는 문자열 또는 문자열 array여야 합니다."
        )
    paths = tuple(
        _field_path(item, f"fields.{target_field}.source")
        for item in value
    )
    if not paths:
        raise RuleConfigurationError(
            f"fields.{target_field}.source에는 하나 이상의 경로가 필요합니다."
        )
    if len(set(paths)) != len(paths):
        raise RuleConfigurationError(
            f"fields.{target_field}.source에 중복 경로가 있습니다."
        )
    return paths


def _normalized_tokens(value: Any, *, form: str, label: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise RuleConfigurationError(f"{label}은 문자열 array여야 합니다.")
    tokens: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise RuleConfigurationError(f"{label}[{index}]은 문자열이어야 합니다.")
        tokens.add(_comparison_token(item, form))
    return frozenset(tokens)


def _change_correction_codes(
    raw_value: Any,
    normalized_value: Any,
    rule: FieldRule,
    *,
    unicode_normalization: str,
) -> list[str]:
    """값 변경의 성격을 YAML 계약에서 선언한 보정 코드로 변환한다."""

    if _values_equal(raw_value, normalized_value):
        return []

    codes: list[str] = []
    if isinstance(raw_value, str):
        unicode_value = unicodedata.normalize(unicode_normalization, raw_value)
        if unicode_value != raw_value:
            codes.append("UNICODE_NORMALIZED")
        if "\t" in raw_value:
            codes.append("TAB_CHARACTER_ERROR")
        if (
            rule.kind in {"text", "enum"}
            and isinstance(normalized_value, str)
            and unicode_value != normalized_value
        ):
            codes.append("WHITESPACE_NORMALIZED")

    if rule.correction_code:
        codes.append(rule.correction_code)
    return list(dict.fromkeys(codes))


def _unique_correction_codes(events: Sequence[Mapping[str, Any]]) -> list[str]:
    """감사 이벤트에서 중복 없는 보정 코드 순서를 만든다."""

    codes: list[str] = []
    for event in events:
        code = event.get("correction_code")
        if isinstance(code, str) and code and code not in codes:
            codes.append(code)
    return codes


def _project_output(document: dict[str, Any], output: OutputRule) -> dict[str, Any]:
    """선언된 표준 컬럼과 기술 필드만 결과에 남긴다."""

    if not output.fields:
        return document

    projected: dict[str, Any] = {}
    for field in output.fields:
        value = _get_path(document, field)
        if value is _MISSING:
            if output.include_missing_as_null:
                _set_path(projected, field, None)
            continue
        _set_path(projected, field, value)

    for field in output.preserve_fields:
        value = _get_path(document, field)
        if value is not _MISSING:
            _set_path(projected, field, value)
    return projected


def _validate_enum_collisions(
    aliases: tuple[tuple[str, str], ...],
    allowed_values: tuple[str, ...],
    *,
    match: str,
    form: str,
    label: str,
) -> None:
    seen: dict[str, str] = {}
    for raw_value, canonical in [*aliases, *((value, value) for value in allowed_values)]:
        key = _enum_key(raw_value, form=form, match=match)
        previous = seen.get(key)
        if previous is not None and previous != canonical:
            raise RuleConfigurationError(
                f"{label}에서 같은 입력이 서로 다른 표준값에 매핑됩니다: {raw_value!r}"
            )
        seen[key] = canonical


def _validate_timezone(value: str, label: str) -> None:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise RuleConfigurationError(f"{label}의 시간대를 찾을 수 없습니다: {value}") from error
