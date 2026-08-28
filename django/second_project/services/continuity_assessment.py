from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Iterable

from django.db.models import Prefetch
from django.utils import timezone

from ..models import SilverArea, SilverEmployee


class AssessmentStatus(StrEnum):
    REVIEWABLE = "REVIEWABLE"
    PARTIAL = "PARTIAL"
    NO_MATCH = "NO_MATCH"
    ON_HOLD = "ON_HOLD"


STATUS_LABELS = {
    AssessmentStatus.REVIEWABLE: "내부 지속 검토 가능",
    AssessmentStatus.PARTIAL: "일부 영역 내부 지속 검토 가능",
    AssessmentStatus.NO_MATCH: "내부 인력 근거 미확인",
    AssessmentStatus.ON_HOLD: "판단 보류 · 데이터 정정 필요",
}

WARNING_LABELS = {
    "AREA_MAPPING_MISSING": "담당 업무영역의 상위 업무영역 연결을 확인해야 합니다.",
    "AREA_PROFILE_MISSING": "업무영역 이름 또는 상위 업무영역 이름을 확인해야 합니다.",
    "DATA_CONFLICT": "현재 등록정보에 충돌 표시가 있어 데이터 담당자 확인이 필요합니다.",
    "PROFILE_MISSING": "이름·부서·직위 중 비어 있는 현재 등록정보를 확인해야 합니다.",
    "TENURE_UNAVAILABLE": "입사일이 조회 기준일 이후여서 근속기간을 산정할 수 없습니다.",
}


class AssessmentNotAvailable(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AssessmentGuidance:
    continuity_signal: str
    staffing_direction: str
    replacement_status: str
    next_action: str


@dataclass(frozen=True)
class CandidateEvidence:
    employee_id: str
    employee_name: str
    profile_image_url: str | None
    department_name: str
    position_name: str
    department_match: bool
    position_match: bool
    tenure_label: str
    tenure_days: int | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class AreaAssessment:
    parent_area_id: str | None
    parent_area_name: str
    area_names: tuple[str, ...]
    status: AssessmentStatus
    confirmed_candidate_count: int | None
    held_candidate_count: int
    candidates: tuple[CandidateEvidence, ...]
    held_candidates: tuple[CandidateEvidence, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ContinuityAssessment:
    target_employee_id: str
    target_employee_name: str
    target_department_name: str
    target_position_name: str
    target_tenure_label: str
    status: AssessmentStatus
    status_label: str
    areas: tuple[AreaAssessment, ...]
    unique_candidate_count: int | None
    data_warning_count: int
    warnings: tuple[str, ...]
    assessed_at: datetime
    as_of_date: date


@dataclass(frozen=True)
class AreaSummary:
    parent_area_id: str | None
    parent_area_name: str
    area_names: tuple[str, ...]
    status: AssessmentStatus
    status_label: str
    confirmed_candidate_count: int | None
    held_candidate_count: int


@dataclass(frozen=True)
class AssessmentSummary:
    target_employee_id: str
    target_employee_name: str
    status: AssessmentStatus
    status_label: str
    areas: tuple[AreaSummary, ...]
    unique_candidate_count: int | None
    data_warning_count: int
    warnings: tuple[str, ...]
    assessed_at: datetime


def assess_manager_continuity(
    manager_id: str,
    *,
    as_of_date: date | None = None,
    using: str = "default",
) -> ContinuityAssessment:
    effective_date = as_of_date or timezone.localdate()
    target = _load_target(manager_id, using=using)
    if target.is_active:
        raise AssessmentNotAvailable(
            "현재 재직 상태인 관리자는 퇴직 대상 분석을 실행할 수 없습니다.",
            code="TARGET_IS_ACTIVE",
        )

    target_warning_codes = _target_warning_codes(target, effective_date)
    if _is_blocking_candidate_warning(target_warning_codes):
        return _target_level_hold(
            target,
            effective_date,
            warning_codes=target_warning_codes,
        )

    target_areas = list(target.managed_areas.all())
    if not target_areas:
        return _target_level_hold(
            target,
            effective_date,
            warning_codes=("AREA_MAPPING_MISSING",),
        )

    grouped_areas: dict[str | None, list[SilverArea]] = {}
    for area in target_areas:
        grouped_areas.setdefault(area.parent_area_id, []).append(area)

    assessments: list[AreaAssessment] = []
    for parent_area_id, areas in sorted(
        grouped_areas.items(),
        key=lambda item: (item[0] is None, item[0] or ""),
    ):
        assessments.append(
            _assess_area_group(
                target,
                parent_area_id,
                areas,
                effective_date,
                using=using,
            )
        )

    overall_status = _overall_status(assessments)
    unique_candidate_ids = {
        candidate.employee_id
        for area in assessments
        for candidate in area.candidates
    }
    has_held_area = any(
        area.status == AssessmentStatus.ON_HOLD for area in assessments
    )
    warning_count = sum(
        len(area.warnings)
        + area.held_candidate_count
        + sum(len(candidate.warnings) for candidate in area.candidates)
        for area in assessments
    )
    return ContinuityAssessment(
        target_employee_id=target.employee_id,
        target_employee_name=target.employee_name,
        target_department_name=target.department_name,
        target_position_name=target.position_name,
        target_tenure_label=_tenure(target.hire_datetime, effective_date)[0],
        status=overall_status,
        status_label=STATUS_LABELS[overall_status],
        areas=tuple(assessments),
        unique_candidate_count=(
            None if has_held_area and not unique_candidate_ids else len(unique_candidate_ids)
        ),
        data_warning_count=warning_count,
        warnings=(),
        assessed_at=timezone.now(),
        as_of_date=effective_date,
    )


def summarize_assessment(assessment: ContinuityAssessment) -> AssessmentSummary:
    return AssessmentSummary(
        target_employee_id=assessment.target_employee_id,
        target_employee_name=assessment.target_employee_name,
        status=assessment.status,
        status_label=assessment.status_label,
        areas=tuple(
            AreaSummary(
                parent_area_id=area.parent_area_id,
                parent_area_name=area.parent_area_name,
                area_names=area.area_names,
                status=area.status,
                status_label=STATUS_LABELS[area.status],
                confirmed_candidate_count=area.confirmed_candidate_count,
                held_candidate_count=area.held_candidate_count,
            )
            for area in assessment.areas
        ),
        unique_candidate_count=assessment.unique_candidate_count,
        data_warning_count=assessment.data_warning_count,
        warnings=assessment.warnings,
        assessed_at=assessment.assessed_at,
    )


def guidance_for(status: AssessmentStatus) -> AssessmentGuidance:
    return {
        AssessmentStatus.REVIEWABLE: AssessmentGuidance(
            continuity_signal="내부 인력으로 지속할 가능성이 있습니다.",
            staffing_direction="내부대체를 먼저 검토한 뒤 충원 필요성을 판단하세요.",
            replacement_status="대체 완료 여부는 현재 데이터로 확인할 수 없습니다.",
            next_action="후보의 실제 역량·업무량·가용성과 인수인계 가능 여부를 확인하세요.",
        ),
        AssessmentStatus.PARTIAL: AssessmentGuidance(
            continuity_signal="일부 업무는 내부 지속 가능성이 있고 일부는 근거가 부족합니다.",
            staffing_direction="내부대체와 부족 영역의 이동·분담·채용을 함께 검토하세요.",
            replacement_status="업무 전체의 대체 완료 여부는 확인할 수 없습니다.",
            next_action="업무영역별 후보 가용성을 확인하고 미충족 영역을 분리해 검토하세요.",
        ),
        AssessmentStatus.NO_MATCH: AssessmentGuidance(
            continuity_signal="현재 등록정보에서는 내부 지속 가능 근거를 찾지 못했습니다.",
            staffing_direction="전환배치·업무분담·채용을 포함한 충원 방안을 검토하세요.",
            replacement_status="후보 부재가 곧 채용 확정을 의미하지는 않습니다.",
            next_action="업무 누락과 다른 조직의 가용 인력을 확인한 뒤 충원 요청의 타당성을 판단하세요.",
        ),
        AssessmentStatus.ON_HOLD: AssessmentGuidance(
            continuity_signal="인사 데이터 오류로 업무 지속 여부를 확정할 수 없는 상태입니다.",
            staffing_direction="내부 대체와 신규채용 판단을 모두 보류하세요.",
            replacement_status="대체가 진행 중이거나 완료되었다는 의미가 아닙니다.",
            next_action="표시된 누락·충돌 정보를 정정한 뒤 다시 조회하세요.",
        ),
    }[status]


def _load_target(manager_id: str, *, using: str) -> SilverEmployee:
    area_queryset = SilverArea.objects.using(using).select_related("parent_area").order_by(
        "parent_area_id",
        "area_id",
    )
    try:
        return (
            SilverEmployee.objects.using(using)
            .prefetch_related(Prefetch("managed_areas", queryset=area_queryset))
            .get(employee_id=manager_id)
        )
    except SilverEmployee.DoesNotExist as error:
        raise AssessmentNotAvailable(
            "해당 관리자 ID를 현재 RDB에서 찾을 수 없습니다.",
            code="TARGET_NOT_FOUND",
        ) from error


def _assess_area_group(
    target: SilverEmployee,
    parent_area_id: str | None,
    areas: Iterable[SilverArea],
    as_of_date: date,
    *,
    using: str,
) -> AreaAssessment:
    area_list = list(areas)
    area_names = tuple(sorted({area.area_name or "" for area in area_list}))
    if parent_area_id is None:
        return AreaAssessment(
            parent_area_id=None,
            parent_area_name="상위 업무영역 확인 필요",
            area_names=area_names,
            status=AssessmentStatus.ON_HOLD,
            confirmed_candidate_count=None,
            held_candidate_count=0,
            candidates=(),
            held_candidates=(),
            warnings=(WARNING_LABELS["AREA_MAPPING_MISSING"],),
        )

    if not all(_has_text(area.area_name) for area in area_list):
        return AreaAssessment(
            parent_area_id=parent_area_id,
            parent_area_name="업무영역 이름 확인 필요",
            area_names=area_names,
            status=AssessmentStatus.ON_HOLD,
            confirmed_candidate_count=None,
            held_candidate_count=0,
            candidates=(),
            held_candidates=(),
            warnings=(WARNING_LABELS["AREA_PROFILE_MISSING"],),
        )

    parent_area_name = area_list[0].parent_area.parent_area_name
    if not _has_text(parent_area_name):
        return AreaAssessment(
            parent_area_id=parent_area_id,
            parent_area_name="상위 업무영역 이름 확인 필요",
            area_names=area_names,
            status=AssessmentStatus.ON_HOLD,
            confirmed_candidate_count=None,
            held_candidate_count=0,
            candidates=(),
            held_candidates=(),
            warnings=(WARNING_LABELS["AREA_PROFILE_MISSING"],),
        )
    queryset = (
        SilverEmployee.objects.using(using)
        .filter(
            is_active=True,
            managed_areas__parent_area_id=parent_area_id,
        )
        .exclude(employee_id=target.employee_id)
        .distinct()
    )

    confirmed: list[CandidateEvidence] = []
    held: list[CandidateEvidence] = []
    for candidate in queryset:
        warning_codes = _candidate_warning_codes(candidate, as_of_date)
        if _is_blocking_candidate_warning(warning_codes):
            tenure_label, tenure_days = _tenure(candidate.hire_datetime, as_of_date)
            held.append(
                CandidateEvidence(
                    employee_id=candidate.employee_id,
                    employee_name=candidate.employee_name,
                    profile_image_url=candidate.profile_image_url,
                    department_name=candidate.department_name,
                    position_name=candidate.position_name,
                    department_match=candidate.department_name == target.department_name,
                    position_match=candidate.position_name == target.position_name,
                    tenure_label=tenure_label,
                    tenure_days=tenure_days,
                    warnings=_warning_messages(warning_codes),
                )
            )
            continue
        tenure_label, tenure_days = _tenure(candidate.hire_datetime, as_of_date)
        confirmed.append(
            CandidateEvidence(
                employee_id=candidate.employee_id,
                employee_name=candidate.employee_name,
                profile_image_url=candidate.profile_image_url,
                department_name=candidate.department_name,
                position_name=candidate.position_name,
                department_match=candidate.department_name == target.department_name,
                position_match=candidate.position_name == target.position_name,
                tenure_label=tenure_label,
                tenure_days=tenure_days,
                warnings=_warning_messages(warning_codes),
            )
        )

    confirmed.sort(key=_candidate_sort_key)
    if confirmed:
        status = AssessmentStatus.REVIEWABLE
        candidate_count: int | None = len(confirmed)
    elif held:
        status = AssessmentStatus.ON_HOLD
        candidate_count = None
    else:
        status = AssessmentStatus.NO_MATCH
        candidate_count = 0

    return AreaAssessment(
        parent_area_id=parent_area_id,
        parent_area_name=parent_area_name,
        area_names=area_names,
        status=status,
        confirmed_candidate_count=candidate_count,
        held_candidate_count=len(held),
        candidates=tuple(confirmed),
        held_candidates=tuple(held),
        warnings=(),
    )


def _target_warning_codes(target: SilverEmployee, as_of_date: date) -> tuple[str, ...]:
    return _candidate_warning_codes(target, as_of_date)


def _candidate_warning_codes(
    candidate: SilverEmployee,
    as_of_date: date,
) -> tuple[str, ...]:
    warnings: list[str] = []
    correction_codes = {str(code) for code in candidate.correction_codes}
    if "DATE_CONFLICT" in correction_codes:
        warnings.append("DATA_CONFLICT")
    if not all(
        _has_text(value)
        for value in (
            candidate.employee_name,
            candidate.department_name,
            candidate.position_name,
        )
    ):
        warnings.append("PROFILE_MISSING")
    if candidate.hire_datetime.date() > as_of_date:
        warnings.append("TENURE_UNAVAILABLE")
    return tuple(warnings)


def _is_blocking_candidate_warning(warnings: tuple[str, ...]) -> bool:
    return bool({"DATA_CONFLICT", "PROFILE_MISSING"}.intersection(warnings))


def _warning_messages(warning_codes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(WARNING_LABELS[code] for code in warning_codes)


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _candidate_sort_key(candidate: CandidateEvidence) -> tuple[object, ...]:
    tenure_sort = -(candidate.tenure_days or -1)
    return (
        not candidate.department_match,
        not candidate.position_match,
        tenure_sort,
        candidate.employee_name,
        candidate.employee_id,
    )


def _tenure(hire_datetime: datetime, as_of_date: date) -> tuple[str, int | None]:
    hire_date = hire_datetime.date()
    if hire_date > as_of_date:
        return "산정 불가", None
    months = (as_of_date.year - hire_date.year) * 12 + as_of_date.month - hire_date.month
    if as_of_date.day < hire_date.day:
        months -= 1
    years, remaining_months = divmod(max(months, 0), 12)
    return f"{years}년 {remaining_months}개월", (as_of_date - hire_date).days


def _overall_status(areas: list[AreaAssessment]) -> AssessmentStatus:
    statuses = {area.status for area in areas}
    if statuses == {AssessmentStatus.REVIEWABLE}:
        return AssessmentStatus.REVIEWABLE
    if statuses == {AssessmentStatus.NO_MATCH}:
        return AssessmentStatus.NO_MATCH
    if statuses == {AssessmentStatus.ON_HOLD}:
        return AssessmentStatus.ON_HOLD
    return AssessmentStatus.PARTIAL


def _target_level_hold(
    target: SilverEmployee,
    as_of_date: date,
    *,
    warning_codes: tuple[str, ...],
) -> ContinuityAssessment:
    status = AssessmentStatus.ON_HOLD
    return ContinuityAssessment(
        target_employee_id=target.employee_id,
        target_employee_name=target.employee_name,
        target_department_name=target.department_name,
        target_position_name=target.position_name,
        target_tenure_label=_tenure(target.hire_datetime, as_of_date)[0],
        status=status,
        status_label=STATUS_LABELS[status],
        areas=(),
        unique_candidate_count=None,
        data_warning_count=len(warning_codes),
        warnings=_warning_messages(warning_codes),
        assessed_at=timezone.now(),
        as_of_date=as_of_date,
    )
