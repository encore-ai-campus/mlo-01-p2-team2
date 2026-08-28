from __future__ import annotations

from collections import Counter, defaultdict

from django.db.models import Count

from gold_layer.models import GoldHrAssessment, GoldHrCandidateEvidence, GoldHrExcludedRecord

from second_project.domain.continuity_policy import AssessmentStatus, overall_status

from ..types import SilverSnapshot, TransformResult, ValidationResult


def validate_transform(result: TransformResult, snapshot: SilverSnapshot | None = None) -> ValidationResult:
    checks: list[dict] = []
    assessment_keys = [(row.target_employee_id, row.area_group_key) for row in result.assessments]
    candidate_keys = [
        (row.target_employee_id, row.parent_area_id, row.candidate_employee_id)
        for row in result.candidates
    ]
    _check(checks, "assessment_grain_unique", len(assessment_keys) == len(set(assessment_keys)), 0)
    _check(checks, "candidate_grain_unique", len(candidate_keys) == len(set(candidate_keys)), 0)

    grouped = defaultdict(lambda: Counter(CONFIRMED=0, HELD=0))
    order_groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    active_ids = {row.employee_id for row in snapshot.employees if row.is_active} if snapshot else set()
    employee_parent_keys = {
        (row.manager_employee_id, row.parent_area_id) for row in snapshot.areas
    } if snapshot else set()
    for candidate in result.candidates:
        grouped[(candidate.target_employee_id, candidate.parent_area_id)][candidate.candidate_state] += 1
        order_groups[(candidate.target_employee_id, candidate.parent_area_id, candidate.candidate_state)].append(candidate.display_order)
        valid_warning_state = (
            candidate.candidate_state == "CONFIRMED" and not {"DATA_CONFLICT", "PROFILE_MISSING"}.intersection(candidate.warning_codes)
        ) or (
            candidate.candidate_state == "HELD" and bool({"DATA_CONFLICT", "PROFILE_MISSING"}.intersection(candidate.warning_codes))
        )
        if not valid_warning_state:
            _check(checks, "candidate_warning_state", False, 1)
        _check(checks, "candidate_is_not_target", candidate.candidate_employee_id != candidate.target_employee_id, 0 if candidate.candidate_employee_id != candidate.target_employee_id else 1)
        if snapshot:
            _check(checks, "candidate_is_active", candidate.candidate_employee_id in active_ids, 0 if candidate.candidate_employee_id in active_ids else 1)
            has_parent_evidence = (candidate.candidate_employee_id, candidate.parent_area_id) in employee_parent_keys
            _check(checks, "candidate_parent_evidence", has_parent_evidence, 0 if has_parent_evidence else 1)

    for orders in order_groups.values():
        _check(checks, "candidate_display_order_contiguous", sorted(orders) == list(range(1, len(orders) + 1)), len(orders))

    for assessment in result.assessments:
        if assessment.parent_area_id is None:
            continue
        counts = grouped[(assessment.target_employee_id, assessment.parent_area_id)]
        expected_confirmed = counts["CONFIRMED"] if assessment.area_status != "ON_HOLD" or counts["CONFIRMED"] else None
        valid_counts = (
            assessment.confirmed_candidate_count == expected_confirmed
            and assessment.held_candidate_count == counts["HELD"]
        )
        _check(checks, "assessment_candidate_counts", valid_counts, assessment.area_group_key)
        if assessment.area_status == "NO_MATCH":
            _check(checks, "no_match_is_zero", assessment.confirmed_candidate_count == 0, assessment.area_group_key)
        if assessment.area_status == "ON_HOLD":
            _check(checks, "on_hold_is_null", assessment.confirmed_candidate_count is None, assessment.area_group_key)
    assessments_by_target: dict[str, list] = defaultdict(list)
    confirmed_by_target: dict[str, set[str]] = defaultdict(set)
    for candidate in result.candidates:
        if candidate.candidate_state == "CONFIRMED":
            confirmed_by_target[candidate.target_employee_id].add(candidate.candidate_employee_id)
    for assessment in result.assessments:
        assessments_by_target[assessment.target_employee_id].append(assessment)
    for target_rows in assessments_by_target.values():
        recalculated = overall_status(AssessmentStatus(row.area_status) for row in target_rows)
        _check(checks, "overall_status_recalculated", all(row.overall_status == recalculated for row in target_rows), str(recalculated))
        target_id = target_rows[0].target_employee_id
        expected_unique = len(confirmed_by_target[target_id])
        if any(row.area_status == "ON_HOLD" for row in target_rows) and expected_unique == 0:
            expected_unique = None
        _check(checks, "unique_candidate_count", all(row.unique_candidate_count == expected_unique for row in target_rows), expected_unique)
    if not checks:
        _check(checks, "transform_structure", True, 0)
    return ValidationResult(passed=all(check["passed"] for check in checks), checks=tuple(checks))


def validate_loaded_release(*, release_id: str, using: str, expected_counts: dict[str, int]) -> ValidationResult:
    assessments = GoldHrAssessment.objects.using(using).filter(release_id=release_id)
    candidates = GoldHrCandidateEvidence.objects.using(using).filter(release_id=release_id)
    excluded = GoldHrExcludedRecord.objects.using(using).filter(release_id=release_id)
    actual = {
        "assessment_rows": assessments.count(),
        "candidate_rows": candidates.count(),
        "excluded_rows": excluded.count(),
    }
    checks: list[dict] = []
    for name, value in actual.items():
        _check(checks, f"loaded_{name}", value == expected_counts[name], value)

    candidate_groups = set(candidates.values_list("target_employee_id", "parent_area_id"))
    assessment_groups = set(
        assessments.exclude(parent_area_id__isnull=True).values_list("target_employee_id", "parent_area_id")
    )
    _check(checks, "candidate_has_assessment", candidate_groups.issubset(assessment_groups), len(candidate_groups - assessment_groups))
    duplicate_count = (
        candidates.values("target_employee_id", "parent_area_id", "candidate_employee_id")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .count()
    )
    _check(checks, "loaded_candidate_duplicates", duplicate_count == 0, duplicate_count)
    invalid_state_count = 0
    grouped = defaultdict(lambda: Counter(CONFIRMED=0, HELD=0))
    confirmed_by_target: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates.iterator():
        blocking = bool({"DATA_CONFLICT", "PROFILE_MISSING"}.intersection(candidate.warning_codes))
        if (candidate.candidate_state == "CONFIRMED" and blocking) or (candidate.candidate_state == "HELD" and not blocking):
            invalid_state_count += 1
        grouped[(candidate.target_employee_id, candidate.parent_area_id)][candidate.candidate_state] += 1
        if candidate.candidate_state == "CONFIRMED":
            confirmed_by_target[candidate.target_employee_id].add(candidate.candidate_employee_id)
    _check(checks, "loaded_candidate_warning_state", invalid_state_count == 0, invalid_state_count)

    invalid_count_rows = 0
    invalid_status_rows = 0
    rows_by_target: dict[str, list] = defaultdict(list)
    for assessment in assessments.iterator():
        rows_by_target[assessment.target_employee_id].append(assessment)
        if assessment.parent_area_id is None:
            continue
        counts = grouped[(assessment.target_employee_id, assessment.parent_area_id)]
        expected_confirmed = counts["CONFIRMED"] if assessment.area_status != "ON_HOLD" or counts["CONFIRMED"] else None
        if assessment.confirmed_candidate_count != expected_confirmed or assessment.held_candidate_count != counts["HELD"]:
            invalid_count_rows += 1
        expected_area_status = "REVIEWABLE" if counts["CONFIRMED"] else ("ON_HOLD" if counts["HELD"] else "NO_MATCH")
        if assessment.area_status != expected_area_status:
            invalid_status_rows += 1
    _check(checks, "loaded_assessment_candidate_counts", invalid_count_rows == 0, invalid_count_rows)
    _check(checks, "loaded_area_status", invalid_status_rows == 0, invalid_status_rows)
    invalid_overall_rows = 0
    invalid_unique_rows = 0
    for target_rows in rows_by_target.values():
        recalculated = overall_status(AssessmentStatus(row.area_status) for row in target_rows)
        invalid_overall_rows += sum(row.overall_status != recalculated for row in target_rows)
        target_id = target_rows[0].target_employee_id
        expected_unique = len(confirmed_by_target[target_id])
        if any(row.area_status == "ON_HOLD" for row in target_rows) and expected_unique == 0:
            expected_unique = None
        invalid_unique_rows += sum(row.unique_candidate_count != expected_unique for row in target_rows)
    _check(checks, "loaded_overall_status", invalid_overall_rows == 0, invalid_overall_rows)
    _check(checks, "loaded_unique_candidate_count", invalid_unique_rows == 0, invalid_unique_rows)
    return ValidationResult(passed=all(check["passed"] for check in checks), checks=tuple(checks))


def _check(checks: list[dict], name: str, passed: bool, observed) -> None:
    checks.append({"name": name, "passed": bool(passed), "observed": observed})
