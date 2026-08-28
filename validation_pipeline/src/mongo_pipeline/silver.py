from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .validators import ValidationIssue


SILVER_MODEL_NAMES = (
    "silver_employee",
    "silver_area",
    "silver_parent_area",
    "silver_top_area_detail",
)

SILVER_MODEL_FIELDS: dict[str, tuple[str, ...]] = {
    "silver_employee": (
        "employee_id",
        "employee_name",
        "department_name",
        "position_name",
        "hire_datetime",
        "is_active",
    ),
    "silver_area": (
        "area_id",
        "area_name",
        "manager_employee_id",
        "area_registered_at",
        "parent_area_id",
    ),
    "silver_parent_area": (
        "parent_area_id",
        "parent_area_name",
    ),
    "silver_top_area_detail": (
        "top_area_id",
        "top_area_name",
        "top_area_level",
        "top_area_registered_at",
    ),
}

SILVER_MODEL_PRIMARY_KEYS = {
    "silver_employee": "employee_id",
    "silver_area": "area_id",
    "silver_parent_area": "parent_area_id",
    "silver_top_area_detail": "top_area_id",
}

SILVER_MODEL_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "silver_employee": SILVER_MODEL_FIELDS["silver_employee"],
    "silver_area": (
        "area_id",
        "area_name",
        "manager_employee_id",
        "area_registered_at",
    ),
    "silver_parent_area": SILVER_MODEL_FIELDS["silver_parent_area"],
    "silver_top_area_detail": SILVER_MODEL_FIELDS["silver_top_area_detail"],
}

SILVER_METADATA_FIELDS = (
    "source_record_id",
    "dataset_id",
    "normalization_run_id",
    "correction_codes",
    "_standardization",
)

APPROVED_CORRECTION_CODES = frozenset(
    {
        "ACTIVE_STATUS_NORMALIZED",
        "CODE_FORMAT_NORMALIZED",
        "DATETIME_FORMAT_NORMALIZED",
        "WHITESPACE_NORMALIZED",
        "UNICODE_NORMALIZED",
        "TAB_CHARACTER_ERROR",
        "TOP_LEVEL_NORMALIZED",
        "DATE_CONFLICT",
    }
)

_EMPLOYEE_ID = re.compile(r"^EMP[0-9]{6}$")
_AREA_ID = re.compile(r"^BIZ[0-9]{5}$")
_KST = timedelta(hours=9)


@dataclass(frozen=True)
class RestorationResult:
    """Bronze 고유 원천 ID 대비 Silver 연결 복구 결과다."""

    evaluated: bool
    source_distinct_count: int
    silver_recovered_source_count: int
    restoration_rate: float | None
    target_rate: float
    gate_status: str
    bronze_integrity_rate: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated": self.evaluated,
            "bronze_distinct_source_count": self.source_distinct_count,
            "source_distinct_count": self.source_distinct_count,
            "silver_recovered_source_count": self.silver_recovered_source_count,
            "restoration_rate": self.restoration_rate,
            "target_rate": self.target_rate,
            "gate_status": self.gate_status,
            "bronze_integrity_rate": self.bronze_integrity_rate,
        }


def is_silver_document(document: Mapping[str, Any]) -> bool:
    """canonical 규칙이 만든 통합 Silver 후보인지 판별한다."""

    return {
        "employee_id",
        "area_id",
        "top_area_id",
        "normalization_run_id",
        "correction_codes",
    }.issubset(document)


def split_silver_models(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """통합 표준 문서를 네 개의 논리 Silver 모델로 분리한다."""

    if not is_silver_document(document):
        return {}

    metadata = {
        field: document[field]
        for field in SILVER_METADATA_FIELDS
        if field in document
    }
    models: dict[str, dict[str, Any]] = {}
    for model_name, fields in SILVER_MODEL_FIELDS.items():
        if model_name == "silver_parent_area":
            if document.get("parent_area_id") is None and document.get("parent_area_name") is None:
                continue
        model = {
            field: document.get(field)
            for field in fields
        }
        model.update(metadata)
        primary_key = SILVER_MODEL_PRIMARY_KEYS[model_name]
        if model.get(primary_key) is not None:
            model["_id"] = model[primary_key]
        models[model_name] = model
    return models


def model_fingerprint(model_name: str, document: Mapping[str, Any]) -> str:
    """PK 중복 비교에서 실행 메타데이터를 제외한 업무값 fingerprint를 만든다."""

    values = {
        field: document.get(field)
        for field in SILVER_MODEL_FIELDS[model_name]
    }
    return json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)


def validate_silver_models(
    documents: Sequence[Mapping[str, Any]],
) -> dict[int, list[ValidationIssue]]:
    """문서 묶음의 네 Silver 모델 필수값·PK·FK를 검증한다.

    한 원천 행에서 어느 모델이라도 실패하면 호출자가 해당 원천 행 전체를
    quarantine할 수 있도록 원천 문서 index별 오류 목록을 반환한다.
    """

    bundles: dict[int, dict[str, dict[str, Any]]] = {
        index: split_silver_models(document)
        for index, document in enumerate(documents)
        if is_silver_document(document)
    }
    if not bundles:
        return {}

    issues: dict[int, list[ValidationIssue]] = defaultdict(list)
    for index, models in bundles.items():
        for model_name, model in models.items():
            _validate_model(index, model_name, model, issues)
        for field in ("source_record_id", "dataset_id", "normalization_run_id"):
            if _missing(documents[index].get(field)):
                _add_issue(
                    issues,
                    index,
                    ValidationIssue(
                        rule="silver.metadata.required",
                        category="null",
                        field=field,
                        message="Silver lineage/실행 메타데이터가 없거나 null입니다.",
                        error_code="REQUIRED_VALUE_MISSING",
                    ),
                )
        correction_codes = documents[index].get("correction_codes")
        if not isinstance(correction_codes, list) or any(
            not isinstance(code, str) or code not in APPROVED_CORRECTION_CODES
            for code in correction_codes
        ):
            _add_issue(
                issues,
                index,
                ValidationIssue(
                    rule="silver.metadata.correction_codes",
                    category="domain",
                    field="correction_codes",
                    message="승인된 correction_codes array가 아닙니다.",
                    error_code="DOMAIN_UNKNOWN",
                ),
            )
        source_document = documents[index]
        parent_id = source_document.get("parent_area_id")
        parent_name = source_document.get("parent_area_name")
        if (parent_id is None) != (parent_name is None):
            _add_issue(
                issues,
                index,
                ValidationIssue(
                    rule="silver_area.parent_area_pair",
                    category="null",
                    field="parent_area_id",
                    message="parent_area_id와 parent_area_name은 함께 있거나 함께 null이어야 합니다.",
                    error_code="REQUIRED_VALUE_MISSING",
                ),
            )

    _validate_primary_key_conflicts(bundles, issues)

    employee_ids = {
        model.get("employee_id")
        for models in bundles.values()
        for model in [models.get("silver_employee", {})]
        if isinstance(model.get("employee_id"), str)
    }
    parent_area_ids = {
        model.get("parent_area_id")
        for models in bundles.values()
        for model in [models.get("silver_parent_area", {})]
        if isinstance(model.get("parent_area_id"), str)
    }
    for index, models in bundles.items():
        area = models.get("silver_area")
        if area is None:
            continue
        manager_id = area.get("manager_employee_id")
        if isinstance(manager_id, str) and manager_id not in employee_ids:
            _add_issue(
                issues,
                index,
                ValidationIssue(
                    rule="silver_area.manager_employee_id_fk",
                    category="reference",
                    field="manager_employee_id",
                    message="참조하는 employee_id가 Silver 직원 모델에 없습니다.",
                    error_code="FK_ORPHAN",
                ),
            )
        parent_id = area.get("parent_area_id")
        if parent_id is not None and parent_id not in parent_area_ids:
            _add_issue(
                issues,
                index,
                ValidationIssue(
                    rule="silver_area.parent_area_id_fk",
                    category="reference",
                    field="parent_area_id",
                    message="참조하는 parent_area_id가 Silver 상위영역 모델에 없습니다.",
                    error_code="FK_ORPHAN",
                ),
            )

    return dict(issues)


def calculate_restoration_rate(
    bronze_documents: Sequence[Mapping[str, Any]],
    accepted_documents: Sequence[Mapping[str, Any]],
    *,
    target_rate: float = 0.95,
) -> RestorationResult:
    """Bronze 고유 source_record_id 중 Silver까지 연결된 비율을 계산한다."""

    input_ids = _source_ids(bronze_documents)
    accepted_ids = _source_ids(accepted_documents)
    if not input_ids:
        return RestorationResult(
            evaluated=False,
            source_distinct_count=0,
            silver_recovered_source_count=0,
            restoration_rate=None,
            target_rate=target_rate,
            gate_status="not_evaluable",
        )

    recovered = input_ids & accepted_ids
    rate = len(recovered) / len(input_ids)
    return RestorationResult(
        evaluated=True,
        source_distinct_count=len(input_ids),
        silver_recovered_source_count=len(recovered),
        restoration_rate=round(rate, 6),
        target_rate=target_rate,
        gate_status="passed" if rate >= target_rate else "failed",
    )


def _validate_model(
    index: int,
    model_name: str,
    model: Mapping[str, Any],
    issues: dict[int, list[ValidationIssue]],
) -> None:
    for field in SILVER_MODEL_REQUIRED_FIELDS[model_name]:
        if _missing(model.get(field)):
            _add_issue(
                issues,
                index,
                ValidationIssue(
                    rule=f"{model_name}.required",
                    category="null",
                    field=field,
                    message="Silver 모델 필수 필드가 없거나 null입니다.",
                    error_code="REQUIRED_VALUE_MISSING",
                ),
            )

    for field in SILVER_MODEL_FIELDS[model_name]:
        value = model.get(field)
        if value is None:
            continue
        if field == "is_active" and not isinstance(value, bool):
            _add_type_issue(index, model_name, field, "boolean", value, issues)
        elif field in {
            "employee_id",
            "employee_name",
            "department_name",
            "position_name",
            "area_id",
            "area_name",
            "manager_employee_id",
            "parent_area_id",
            "parent_area_name",
            "top_area_id",
            "top_area_name",
            "top_area_level",
        } and not isinstance(value, str):
            _add_type_issue(index, model_name, field, "string", value, issues)
        elif field.endswith("datetime") or field.endswith("registered_at"):
            if not isinstance(value, str) or not _is_kst_datetime(value):
                _add_issue(
                    issues,
                    index,
                    ValidationIssue(
                        rule=f"{model_name}.{field}_format",
                        category="format",
                        field=field,
                        message="KST 포함 ISO 8601 datetime 형식이 아닙니다.",
                        error_code="DATETIME_PARSE_FAILED",
                    ),
                )

    for field in {
        "manager_employee_id",
        "parent_area_id",
    } & set(SILVER_MODEL_FIELDS[model_name]):
        value = model.get(field)
        if value is None:
            continue
        pattern = _EMPLOYEE_ID if field == "manager_employee_id" else _AREA_ID
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            _add_issue(
                issues,
                index,
                ValidationIssue(
                    rule=f"{model_name}.{field}_format",
                    category="format",
                    field=field,
                    message="표준 참조 식별자 형식이 아닙니다.",
                    error_code="DOMAIN_UNKNOWN",
                ),
            )

    primary_key = SILVER_MODEL_PRIMARY_KEYS[model_name]
    primary_value = model.get(primary_key)
    if primary_value is not None:
        pattern = _EMPLOYEE_ID if primary_key == "employee_id" else _AREA_ID
        if not isinstance(primary_value, str) or pattern.fullmatch(primary_value) is None:
            _add_issue(
                issues,
                index,
                ValidationIssue(
                    rule=f"{model_name}.{primary_key}_format",
                    category="format",
                    field=primary_key,
                    message="표준 식별자 형식이 아닙니다.",
                    error_code="DOMAIN_UNKNOWN",
                ),
            )

    if model_name == "silver_top_area_detail" and model.get("top_area_level") != "TOP":
        _add_issue(
            issues,
            index,
            ValidationIssue(
                rule="silver_top_area_detail.top_area_level_domain",
                category="domain",
                field="top_area_level",
                message="top_area_level은 TOP이어야 합니다.",
                error_code="DOMAIN_UNKNOWN",
            ),
        )



def _validate_primary_key_conflicts(
    bundles: Mapping[int, Mapping[str, Mapping[str, Any]]],
    issues: dict[int, list[ValidationIssue]],
) -> None:
    grouped: dict[tuple[str, Any], list[tuple[int, str]]] = defaultdict(list)
    for index, models in bundles.items():
        for model_name, model in models.items():
            primary_key = SILVER_MODEL_PRIMARY_KEYS[model_name]
            value = model.get(primary_key)
            if value is not None:
                grouped[(model_name, str(value))].append(
                    (index, model_fingerprint(model_name, model))
                )

    for (model_name, primary_key), values in grouped.items():
        if len({fingerprint for _, fingerprint in values}) <= 1:
            continue
        for index, _ in values:
            _add_issue(
                issues,
                index,
                ValidationIssue(
                    rule=f"{model_name}.{SILVER_MODEL_PRIMARY_KEYS[model_name]}_unique",
                    category="integrity",
                    field=SILVER_MODEL_PRIMARY_KEYS[model_name],
                    message=f"Silver 모델 PK `{primary_key}` 값이 서로 다른 업무값으로 중복됩니다.",
                    error_code="PK_DUPLICATE",
                ),
            )


def _add_type_issue(
    index: int,
    model_name: str,
    field: str,
    expected: str,
    value: Any,
    issues: dict[int, list[ValidationIssue]],
) -> None:
    _add_issue(
        issues,
        index,
        ValidationIssue(
            rule=f"{model_name}.type",
            category="format",
            field=field,
            message=f"타입이 일치하지 않습니다: expected={expected}, actual={type(value).__name__}",
            error_code="TYPE_MISMATCH",
        ),
    )


def _add_issue(
    issues: dict[int, list[ValidationIssue]],
    index: int,
    issue: ValidationIssue,
) -> None:
    if issue not in issues[index]:
        issues[index].append(issue)


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _is_kst_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == _KST


def _source_ids(documents: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        str(document["source_record_id"])
        for document in documents
        if document.get("source_record_id") not in (None, "")
    }
