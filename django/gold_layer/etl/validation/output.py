from __future__ import annotations

from collections import Counter, defaultdict

from django.db.models import Count

from gold_layer.models import GoldHrAssessment, GoldHrCandidateEvidence, GoldHrExcludedRecord

from ..types import TransformResult, ValidationResult


def validate_transform(result: TransformResult) -> ValidationResult:
    checks: list[dict] = []
    assessment_keys = [(row.target_employee_id, row.area_group_key) for row in result.assessments]
    candidate_keys = [
        (row.target_employee_id, row.parent_area_id, row.candidate_employee_id)
        for row in result.candidates
    ]
    _check(checks, "assessment_grain_unique", len(assessment_keys) == len(set(assessment_keys)), 0)
    _check(checks, "candidate_grain_unique", len(candidate_keys) == len(set(candidate_keys)), 0)

    grouped = defaultdict(lambda: Counter(CONFIRMED=0, HELD=0))
    for candidate in result.candidates:
        grouped[(candidate.target_employee_id, candidate.parent_area_id)][candidate.candidate_state] += 1
        valid_warning_state = (
            candidate.candidate_state == "CONFIRMED" and not {"DATA_CONFLICT", "PROFILE_MISSING"}.intersection(candidate.warning_codes)
        ) or (
            candidate.candidate_state == "HELD" and bool({"DATA_CONFLICT", "PROFILE_MISSING"}.intersection(candidate.warning_codes))
        )
        if not valid_warning_state:
            _check(checks, "candidate_warning_state", False, candidate.candidate_employee_id)

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
    return ValidationResult(passed=all(check["passed"] for check in checks), checks=tuple(checks))


def _check(checks: list[dict], name: str, passed: bool, observed) -> None:
    checks.append({"name": name, "passed": bool(passed), "observed": observed})
