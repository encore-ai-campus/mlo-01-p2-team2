from __future__ import annotations

from second_project.domain.continuity_policy import has_text

from ..types import QualityIssue, SilverSnapshot


def validate_source(
    snapshot: SilverSnapshot,
    *,
    require_inactive_target: bool,
    strict_top_area_integrity: bool,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    employee_ids = {row.employee_id for row in snapshot.employees}
    parent_ids = {row.parent_area_id for row in snapshot.parents}

    for area in snapshot.areas:
        if area.manager_employee_id not in employee_ids:
            issues.append(_issue("AREA_EMPLOYEE_ORPHAN", "ERROR", "AREA", area.area_id))
        if area.parent_area_id is not None and area.parent_area_id not in parent_ids:
            issues.append(_issue("AREA_PARENT_ORPHAN", "ERROR", "AREA", area.area_id))
    for parent in snapshot.parents:
        if not has_text(parent.parent_area_name):
            issues.append(_issue("PARENT_PROFILE_MISSING", "WARNING", "PARENT_AREA", parent.parent_area_id))
    for top in snapshot.top_areas:
        if not all(has_text(value) for value in (top.top_area_name, top.top_area_level)):
            issues.append(_issue("TOP_AREA_PROFILE_MISSING", "ERROR", "TOP_AREA", top.top_area_id))

    if strict_top_area_integrity and snapshot.parents and not snapshot.top_areas:
        issues.append(_issue("TOP_AREA_MISSING", "ERROR", "DATASET", "silver"))
    if require_inactive_target and not any(not employee.is_active for employee in snapshot.employees):
        issues.append(_issue("INACTIVE_TARGET_MISSING", "ERROR", "DATASET", "silver"))
    if not snapshot.employees:
        issues.append(_issue("SILVER_EMPLOYEE_EMPTY", "ERROR", "DATASET", "silver"))
    return issues


def _issue(code: str, severity: str, entity_type: str, entity_id: str) -> QualityIssue:
    return QualityIssue(
        code=code,
        severity=severity,
        entity_type=entity_type,
        logical_entity_id=entity_id,
        message=code.replace("_", " ").title(),
    )
