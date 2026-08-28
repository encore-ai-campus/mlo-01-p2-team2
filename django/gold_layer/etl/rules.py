from __future__ import annotations

from collections import defaultdict
from datetime import date

from second_project.domain.continuity_policy import (
    AssessmentStatus,
    candidate_display_sort_key,
    employee_warning_codes,
    has_blocking_employee_warning,
    has_text,
    overall_status,
    tenure,
)

from .types import AssessmentRow, CandidateRow, ExcludedRow, SilverSnapshot, TransformResult


def build_gold_rows(snapshot: SilverSnapshot, *, as_of_date: date) -> TransformResult:
    result = TransformResult()
    parents = {row.parent_area_id: row for row in snapshot.parents}
    areas_by_manager: dict[str, list] = defaultdict(list)
    employees = {row.employee_id: row for row in snapshot.employees}
    for area in snapshot.areas:
        areas_by_manager[area.manager_employee_id].append(area)

    for target in sorted(snapshot.employees, key=lambda row: row.employee_id):
        if target.is_active:
            continue
        target_warnings = _employee_warnings(target, as_of_date)
        target_areas = areas_by_manager.get(target.employee_id, [])
        if has_blocking_employee_warning(target_warnings):
            result.assessments.append(_target_hold(target, as_of_date, list(target_warnings)))
            result.excluded.append(_excluded("TARGET", target.employee_id, target.source_record_id, list(target_warnings)))
            continue
        if not target_areas:
            result.assessments.append(_target_hold(target, as_of_date, ["AREA_MAPPING_MISSING"]))
            result.excluded.append(_excluded("TARGET", target.employee_id, target.source_record_id, ["AREA_MAPPING_MISSING"]))
            continue

        grouped: dict[str | None, list] = defaultdict(list)
        for area in target_areas:
            grouped[area.parent_area_id].append(area)

        target_assessments: list[AssessmentRow] = []
        target_candidates: list[CandidateRow] = []
        confirmed_ids: set[str] = set()
        for parent_id, areas in sorted(grouped.items(), key=lambda item: (item[0] is None, item[0] or "")):
            area_ids = sorted(area.area_id for area in areas)
            area_names = sorted({area.area_name or "" for area in areas})
            warning_codes: list[str] = []
            if parent_id is None:
                warning_codes.append("AREA_MAPPING_MISSING")
            elif not all(has_text(area.area_name) for area in areas):
                warning_codes.append("AREA_PROFILE_MISSING")
            elif parent_id not in parents or not has_text(parents[parent_id].parent_area_name):
                warning_codes.append("AREA_PROFILE_MISSING")

            if warning_codes:
                assessment = _assessment(
                    target,
                    as_of_date,
                    area_group_key=f"PARENT:{parent_id}" if parent_id else "UNMAPPED",
                    parent_area_id=parent_id,
                    parent_area_name=(parents[parent_id].parent_area_name if parent_id in parents else ""),
                    area_ids=area_ids,
                    area_names=area_names,
                    area_status=AssessmentStatus.ON_HOLD,
                    confirmed_count=None,
                    held_count=0,
                    warning_codes=warning_codes,
                )
                target_assessments.append(assessment)
                for area in areas:
                    result.excluded.append(_excluded("AREA", area.area_id, area.source_record_id, warning_codes))
                continue

            candidate_ids = {
                area.manager_employee_id
                for area in snapshot.areas
                if area.parent_area_id == parent_id
                and area.manager_employee_id != target.employee_id
                and area.manager_employee_id in employees
                and employees[area.manager_employee_id].is_active
            }
            confirmed: list[tuple] = []
            held: list[tuple] = []
            for candidate_id in candidate_ids:
                candidate = employees[candidate_id]
                warnings = _employee_warnings(candidate, as_of_date)
                tenure_days = tenure(candidate.hire_datetime, as_of_date)[1]
                item = (candidate, warnings, tenure_days)
                if has_blocking_employee_warning(warnings):
                    held.append(item)
                    result.excluded.append(_excluded("CANDIDATE", candidate.employee_id, candidate.source_record_id, list(warnings)))
                else:
                    confirmed.append(item)

            sort_key = lambda item: candidate_display_sort_key(
                department_match=item[0].department_name == target.department_name,
                position_match=item[0].position_name == target.position_name,
                tenure_days=item[2],
                employee_name=item[0].employee_name,
                employee_id=item[0].employee_id,
            )
            confirmed.sort(key=sort_key)
            held.sort(key=sort_key)
            if confirmed:
                area_status = AssessmentStatus.REVIEWABLE
                confirmed_count: int | None = len(confirmed)
            elif held:
                area_status = AssessmentStatus.ON_HOLD
                confirmed_count = None
            else:
                area_status = AssessmentStatus.NO_MATCH
                confirmed_count = 0

            target_assessments.append(
                _assessment(
                    target,
                    as_of_date,
                    area_group_key=f"PARENT:{parent_id}",
                    parent_area_id=parent_id,
                    parent_area_name=parents[parent_id].parent_area_name,
                    area_ids=area_ids,
                    area_names=area_names,
                    area_status=area_status,
                    confirmed_count=confirmed_count,
                    held_count=len(held),
                    warning_codes=[],
                )
            )
            for state, items in (("CONFIRMED", confirmed), ("HELD", held)):
                for display_order, (candidate, warnings, tenure_days) in enumerate(items, start=1):
                    target_candidates.append(
                        CandidateRow(
                            target_employee_id=target.employee_id,
                            parent_area_id=parent_id,
                            candidate_employee_id=candidate.employee_id,
                            candidate_state=state,
                            candidate_department_name=candidate.department_name,
                            candidate_position_name=candidate.position_name,
                            candidate_hire_date=candidate.hire_datetime.date(),
                            candidate_tenure_days=tenure_days,
                            department_match=candidate.department_name == target.department_name,
                            position_match=candidate.position_name == target.position_name,
                            display_order=display_order,
                            warning_codes=list(warnings),
                            source_record_id=candidate.source_record_id,
                            normalization_run_id=candidate.normalization_run_id,
                        )
                    )
                    if state == "CONFIRMED":
                        confirmed_ids.add(candidate.employee_id)

        status = overall_status(AssessmentStatus(row.area_status) for row in target_assessments)
        has_held_area = any(row.area_status == AssessmentStatus.ON_HOLD for row in target_assessments)
        unique_count = None if has_held_area and not confirmed_ids else len(confirmed_ids)
        for row in target_assessments:
            row.overall_status = status
            row.unique_candidate_count = unique_count
        result.assessments.extend(target_assessments)
        result.candidates.extend(target_candidates)
    return result


def _employee_warnings(employee, as_of_date: date) -> tuple[str, ...]:
    return employee_warning_codes(
        employee_name=employee.employee_name,
        department_name=employee.department_name,
        position_name=employee.position_name,
        hire_datetime=employee.hire_datetime,
        correction_codes=employee.correction_codes,
        as_of_date=as_of_date,
    )


def _assessment(target, as_of_date: date, **values) -> AssessmentRow:
    values["confirmed_candidate_count"] = values.pop("confirmed_count")
    values["held_candidate_count"] = values.pop("held_count")
    return AssessmentRow(
        target_employee_id=target.employee_id,
        as_of_date=as_of_date,
        target_department_name=target.department_name,
        target_position_name=target.position_name,
        target_hire_date=target.hire_datetime.date(),
        target_tenure_days=tenure(target.hire_datetime, as_of_date)[1],
        overall_status=AssessmentStatus.ON_HOLD,
        unique_candidate_count=None,
        source_record_id=target.source_record_id,
        normalization_run_id=target.normalization_run_id,
        **values,
    )


def _target_hold(target, as_of_date: date, warnings: list[str]) -> AssessmentRow:
    return _assessment(
        target,
        as_of_date,
        area_group_key="TARGET_HOLD",
        parent_area_id=None,
        parent_area_name="",
        area_ids=[],
        area_names=[],
        area_status=AssessmentStatus.ON_HOLD,
        confirmed_count=None,
        held_count=0,
        warning_codes=warnings,
    )


def _excluded(entity_type: str, entity_id: str, source_record_id: str, reasons: list[str]) -> ExcludedRow:
    reason_key = "+".join(sorted(reasons))
    return ExcludedRow(
        exclusion_record_id=f"{entity_type}:{entity_id}:{reason_key}",
        entity_type=entity_type,
        logical_entity_id=entity_id,
        source_record_id=source_record_id,
        reason_type="DATA_QUALITY_HOLD",
        reason_codes=sorted(reasons),
    )
