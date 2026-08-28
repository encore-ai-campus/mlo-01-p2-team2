from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ValidationIssue:
    """검증 실패 규칙과 필드, 사유를 표현한다."""

    rule: str
    category: str
    message: str
    field: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """null 항목을 제외한 저장 가능한 오류 정보로 변환한다."""

        return {key: value for key, value in asdict(self).items() if value is not None}


class Validator(Protocol):
    """새 품질 검증기가 따라야 하는 최소 규약이다."""

    name: str

    def validate(self, document: Mapping[str, Any]) -> list[ValidationIssue]:
        """문서를 검사하고 발견한 문제를 반환한다."""
        ...


class NonEmptyDocumentValidator:
    """빈 문서를 거부한다."""

    name = "non_empty_document"

    def validate(self, document: Mapping[str, Any]) -> list[ValidationIssue]:
        """문서에 하나 이상의 필드가 있는지 검사한다."""

        if document:
            return []
        return [
            ValidationIssue(
                rule=self.name,
                category="null",
                message="빈 문서입니다.",
                error_code="REQUIRED_VALUE_MISSING",
            )
        ]


class RequiredFieldsValidator:
    """설정한 필수 필드의 존재 여부와 null 여부를 검사한다."""

    name = "required_fields"

    def __init__(self, required_fields: Sequence[str]) -> None:
        """점 표기법을 지원하는 필수 필드 목록을 저장한다."""

        self._required_fields = tuple(required_fields)

    def validate(self, document: Mapping[str, Any]) -> list[ValidationIssue]:
        """각 필수 필드가 존재하며 null이 아닌지 검사한다."""

        issues: list[ValidationIssue] = []
        for field in self._required_fields:
            found, value = get_nested_value(document, field)
            if not found or value is None:
                issues.append(
                    ValidationIssue(
                        rule=self.name,
                        category="null",
                        field=field,
                        message="필수 필드가 없거나 null입니다.",
                        error_code="REQUIRED_VALUE_MISSING",
                    )
                )
        return issues


class FieldTypeValidator:
    """설정한 필드가 기대한 기본 타입인지 검사한다."""

    name = "field_types"

    def __init__(self, expected_types: Mapping[str, str]) -> None:
        """필드별 기대 타입을 저장하고 지원 여부를 확인한다."""

        self._expected_types = dict(expected_types)
        unknown = sorted(set(self._expected_types.values()) - set(TYPE_CHECKERS))
        if unknown:
            allowed = ", ".join(sorted(TYPE_CHECKERS))
            raise ValueError(f"지원하지 않는 필드 타입 {unknown}. 사용 가능: {allowed}")

    def validate(self, document: Mapping[str, Any]) -> list[ValidationIssue]:
        """존재하는 각 필드의 실제 타입을 기대 타입과 비교한다."""

        issues: list[ValidationIssue] = []
        for field, expected_type in self._expected_types.items():
            found, value = get_nested_value(document, field)
            if not found or value is None:
                continue
            if not TYPE_CHECKERS[expected_type](value):
                issues.append(
                    ValidationIssue(
                        rule=self.name,
                        category="format",
                        field=field,
                        message=(
                            f"타입이 일치하지 않습니다: "
                            f"expected={expected_type}, actual={type_name(value)}"
                        ),
                        error_code="TYPE_MISMATCH",
                    )
                )
        return issues


def build_default_validators(
    required_fields: Sequence[str],
    field_types: Mapping[str, str],
) -> list[Validator]:
    """공통 검증기와 설정 기반 검증기를 실행 순서대로 구성한다."""

    validators: list[Validator] = [NonEmptyDocumentValidator()]
    if required_fields:
        validators.append(RequiredFieldsValidator(required_fields))
    if field_types:
        validators.append(FieldTypeValidator(field_types))
    return validators


def validate_final_unique_fields(
    documents: Sequence[Mapping[str, Any]],
    unique_fields: Sequence[str],
) -> dict[int, list[ValidationIssue]]:
    """최종 표준화 결과에서 설정한 업무 식별자의 중복을 검사한다.

    이 함수는 표준화기가 반환한 문서의 값을 그대로 비교한다. 따라서
    공백 제거, 코드 포맷 보정, 오류값 처리 등은 이 단계에서 다시 수행하지
    않고 표준화 단계의 결과를 중복 판단의 기준으로 사용한다.
    값이 없거나 공백뿐인 값은 필수값 검증의 책임이므로 중복 검사에서는
    제외한다.
    """

    fields = tuple(
        dict.fromkeys(
            field.strip()
            for field in unique_fields
            if isinstance(field, str) and field.strip()
        )
    )
    issues: dict[int, list[ValidationIssue]] = defaultdict(list)

    for field in fields:
        grouped: dict[str, list[int]] = defaultdict(list)
        for index, document in enumerate(documents):
            found, value = get_nested_value(document, field)
            if not found or value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            grouped[str(value)].append(index)

        for indexes in grouped.values():
            if len(indexes) < 2:
                continue
            for index in indexes:
                issues[index].append(
                    ValidationIssue(
                        rule=f"final_unique.{field}",
                        category="integrity",
                        field=field,
                        message=(
                            f"최종 표준화 후 업무 식별자 `{field}` 값이 "
                            "중복됩니다."
                        ),
                        error_code="DUPLICATE_FINAL_VALUE",
                    )
                )

    return dict(issues)


def get_nested_value(document: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    """점으로 구분한 경로에서 중첩 필드의 존재 여부와 값을 찾는다."""

    current: Any = document
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return False, None
        current = current[key]
    return True, current


def type_name(value: Any) -> str:
    """Python 값을 설정 파일에서 사용하는 타입 이름으로 바꾼다."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


TYPE_CHECKERS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, Mapping),
    "array": lambda value: isinstance(value, list),
}
