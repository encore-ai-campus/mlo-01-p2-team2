from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Iterable


class AssessmentStatus(StrEnum):
    REVIEWABLE = "REVIEWABLE"
    PARTIAL = "PARTIAL"
    NO_MATCH = "NO_MATCH"
    ON_HOLD = "ON_HOLD"


BLOCKING_EMPLOYEE_WARNINGS = frozenset({"DATA_CONFLICT", "PROFILE_MISSING"})


def has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def employee_warning_codes(
    *,
    employee_name: str,
    department_name: str,
    position_name: str,
    hire_datetime: datetime,
    correction_codes: Iterable[object],
    as_of_date: date,
) -> tuple[str, ...]:
    warnings: list[str] = []
    normalized_codes = {str(code) for code in correction_codes}
    if "DATE_CONFLICT" in normalized_codes:
        warnings.append("DATA_CONFLICT")
    if not all(has_text(value) for value in (employee_name, department_name, position_name)):
        warnings.append("PROFILE_MISSING")
    if hire_datetime.date() > as_of_date:
        warnings.append("TENURE_UNAVAILABLE")
    return tuple(warnings)


def has_blocking_employee_warning(warnings: Iterable[str]) -> bool:
    return bool(BLOCKING_EMPLOYEE_WARNINGS.intersection(warnings))


def tenure(hire_datetime: datetime, as_of_date: date) -> tuple[str, int | None]:
    hire_date = hire_datetime.date()
    if hire_date > as_of_date:
        return "산정 불가", None
    months = (as_of_date.year - hire_date.year) * 12 + as_of_date.month - hire_date.month
    if as_of_date.day < hire_date.day:
        months -= 1
    years, remaining_months = divmod(max(months, 0), 12)
    return f"{years}년 {remaining_months}개월", (as_of_date - hire_date).days


def overall_status(statuses: Iterable[AssessmentStatus]) -> AssessmentStatus:
    unique_statuses = set(statuses)
    if unique_statuses == {AssessmentStatus.REVIEWABLE}:
        return AssessmentStatus.REVIEWABLE
    if unique_statuses == {AssessmentStatus.NO_MATCH}:
        return AssessmentStatus.NO_MATCH
    if unique_statuses == {AssessmentStatus.ON_HOLD}:
        return AssessmentStatus.ON_HOLD
    return AssessmentStatus.PARTIAL


def candidate_display_sort_key(
    *,
    department_match: bool,
    position_match: bool,
    tenure_days: int | None,
    employee_name: str,
    employee_id: str,
) -> tuple[object, ...]:
    return (
        not department_match,
        not position_match,
        -(tenure_days if tenure_days is not None else -1),
        employee_name,
        employee_id,
    )
