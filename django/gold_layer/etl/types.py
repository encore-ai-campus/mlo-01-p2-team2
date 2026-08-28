from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class EmployeeSnapshot:
    employee_id: str
    employee_name: str
    department_name: str
    position_name: str
    hire_datetime: datetime
    is_active: bool
    source_record_id: str
    dataset_id: str
    normalization_run_id: str
    correction_codes: tuple[str, ...]


@dataclass(frozen=True)
class ParentAreaSnapshot:
    parent_area_id: str
    parent_area_name: str
    source_record_id: str
    dataset_id: str
    normalization_run_id: str


@dataclass(frozen=True)
class AreaSnapshot:
    area_id: str
    area_name: str
    manager_employee_id: str
    parent_area_id: str | None
    source_record_id: str
    dataset_id: str
    normalization_run_id: str


@dataclass(frozen=True)
class TopAreaSnapshot:
    top_area_id: str
    top_area_name: str
    top_area_level: str
    source_record_id: str
    dataset_id: str
    normalization_run_id: str


@dataclass(frozen=True)
class SilverSnapshot:
    employees: tuple[EmployeeSnapshot, ...]
    parents: tuple[ParentAreaSnapshot, ...]
    areas: tuple[AreaSnapshot, ...]
    top_areas: tuple[TopAreaSnapshot, ...]
    dataset_ids: tuple[str, ...]
    normalization_run_ids: tuple[str, ...]


@dataclass(frozen=True)
class QualityIssue:
    code: str
    severity: str
    entity_type: str
    logical_entity_id: str
    message: str


@dataclass
class AssessmentRow:
    target_employee_id: str
    as_of_date: date
    area_group_key: str
    parent_area_id: str | None
    parent_area_name: str
    area_ids: list[str]
    area_names: list[str]
    target_department_name: str
    target_position_name: str
    target_hire_date: date
    target_tenure_days: int | None
    area_status: str
    overall_status: str
    confirmed_candidate_count: int | None
    held_candidate_count: int
    unique_candidate_count: int | None
    warning_codes: list[str]
    source_record_id: str
    normalization_run_id: str


@dataclass(frozen=True)
class CandidateRow:
    target_employee_id: str
    parent_area_id: str
    candidate_employee_id: str
    candidate_state: str
    candidate_department_name: str
    candidate_position_name: str
    candidate_hire_date: date
    candidate_tenure_days: int | None
    department_match: bool
    position_match: bool
    display_order: int
    warning_codes: list[str]
    source_record_id: str
    normalization_run_id: str


@dataclass(frozen=True)
class ExcludedRow:
    exclusion_record_id: str
    entity_type: str
    logical_entity_id: str
    source_record_id: str
    reason_type: str
    reason_codes: list[str]


@dataclass
class TransformResult:
    assessments: list[AssessmentRow] = field(default_factory=list)
    candidates: list[CandidateRow] = field(default_factory=list)
    excluded: list[ExcludedRow] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    checks: tuple[dict[str, Any], ...]

