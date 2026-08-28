from datetime import date, datetime

from django.test import TestCase
from django.utils import timezone

from second_project.models import SilverArea, SilverEmployee, SilverParentArea
from second_project.services.continuity_assessment import (
    AssessmentNotAvailable,
    AssessmentStatus,
    assess_manager_continuity,
    guidance_for,
)


class ContinuityAssessmentTests(TestCase):
    databases = {"default"}

    def setUp(self) -> None:
        self.target = self._employee(
            "EMP000001",
            "퇴직대상",
            department="개발부",
            position="팀장",
            hire=date(2010, 1, 15),
            active=False,
        )
        self.parent_a = self._parent("PAREA001", "플랫폼")
        self._area("AREA0001", "플랫폼 운영", self.target, self.parent_a)

    def test_only_active_candidates_in_same_parent_are_included(self) -> None:
        matching = self._employee(
            "EMP000002",
            "확정후보",
            department="개발부",
            position="팀장",
            hire=date(2012, 4, 1),
        )
        inactive = self._employee("EMP000003", "비재직자", active=False)
        other_parent_candidate = self._employee("EMP000004", "다른영역")
        self._area("AREA0002", "플랫폼 개발", matching, self.parent_a)
        self._area("AREA0003", "플랫폼 지원", matching, self.parent_a)
        self._area("AREA0004", "플랫폼 감사", inactive, self.parent_a)
        parent_b = self._parent("PAREA002", "데이터")
        self._area("AREA0005", "데이터 분석", other_parent_candidate, parent_b)

        assessment = assess_manager_continuity(
            self.target.employee_id,
            as_of_date=date(2026, 8, 27),
        )

        self.assertEqual(assessment.status, AssessmentStatus.REVIEWABLE)
        self.assertEqual(assessment.unique_candidate_count, 1)
        self.assertEqual(
            [candidate.employee_id for candidate in assessment.areas[0].candidates],
            [matching.employee_id],
        )

    def test_department_then_position_then_tenure_control_stable_order(self) -> None:
        department_and_position = self._employee(
            "EMP000010",
            "부서직위일치",
            department="개발부",
            position="팀장",
            hire=date(2020, 1, 1),
        )
        position_only = self._employee(
            "EMP000011",
            "직위만일치",
            department="영업부",
            position="팀장",
            hire=date(2000, 1, 1),
        )
        department_only = self._employee(
            "EMP000012",
            "부서만일치",
            department="개발부",
            position="매니저",
            hire=date(2005, 1, 1),
        )
        for index, employee in enumerate(
            (department_and_position, position_only, department_only),
            start=10,
        ):
            self._area(f"AREA00{index}", f"업무 {index}", employee, self.parent_a)

        candidates = assess_manager_continuity(
            self.target.employee_id,
            as_of_date=date(2026, 8, 27),
        ).areas[0].candidates

        self.assertEqual(
            [candidate.employee_id for candidate in candidates],
            [
                department_and_position.employee_id,
                department_only.employee_id,
                position_only.employee_id,
            ],
        )

    def test_candidate_with_date_conflict_is_held_not_counted_as_no_match(self) -> None:
        held = self._employee(
            "EMP000020",
            "확인필요",
            correction_codes=["DATE_CONFLICT"],
        )
        self._area("AREA0020", "충돌 업무", held, self.parent_a)

        assessment = assess_manager_continuity(self.target.employee_id)

        self.assertEqual(assessment.status, AssessmentStatus.ON_HOLD)
        self.assertIsNone(assessment.areas[0].confirmed_candidate_count)
        self.assertEqual(assessment.areas[0].held_candidate_count, 1)
        self.assertEqual(assessment.data_warning_count, 1)
        self.assertEqual(
            assessment.areas[0].held_candidates[0].employee_id,
            held.employee_id,
        )
        self.assertIn("충돌", assessment.areas[0].held_candidates[0].warnings[0])

    def test_candidate_profile_image_url_is_exposed_as_optional_evidence(self) -> None:
        candidate = self._employee(
            "EMP000021",
            "사진후보",
            profile_image_url="https://intranet.example.test/profiles/EMP000021.jpg",
        )
        self._area("AREA0021", "사진 업무", candidate, self.parent_a)

        assessment = assess_manager_continuity(self.target.employee_id)

        self.assertEqual(
            assessment.areas[0].candidates[0].profile_image_url,
            candidate.profile_image_url,
        )

    def test_no_active_candidate_is_no_match(self) -> None:
        assessment = assess_manager_continuity(self.target.employee_id)

        self.assertEqual(assessment.status, AssessmentStatus.NO_MATCH)
        self.assertEqual(assessment.areas[0].confirmed_candidate_count, 0)

    def test_mixed_area_results_are_partial(self) -> None:
        parent_b = self._parent("PAREA002", "데이터")
        self._area("AREA0006", "데이터 운영", self.target, parent_b)
        candidate = self._employee("EMP000030", "플랫폼후보")
        self._area("AREA0007", "플랫폼 개발", candidate, self.parent_a)

        assessment = assess_manager_continuity(self.target.employee_id)

        self.assertEqual(assessment.status, AssessmentStatus.PARTIAL)
        self.assertEqual(
            {area.status for area in assessment.areas},
            {AssessmentStatus.REVIEWABLE, AssessmentStatus.NO_MATCH},
        )

    def test_missing_parent_relation_is_on_hold(self) -> None:
        self._area("AREA0008", "매핑 누락", self.target, None)

        assessment = assess_manager_continuity(self.target.employee_id)

        self.assertEqual(assessment.status, AssessmentStatus.PARTIAL)
        missing = next(area for area in assessment.areas if area.parent_area_id is None)
        self.assertEqual(missing.status, AssessmentStatus.ON_HOLD)
        self.assertIsNone(missing.confirmed_candidate_count)
        self.assertIsNone(assessment.unique_candidate_count)

    def test_active_target_is_rejected(self) -> None:
        self.target.is_active = True
        self.target.save(update_fields=["is_active"])

        with self.assertRaises(AssessmentNotAvailable) as context:
            assess_manager_continuity(self.target.employee_id)

        self.assertEqual(context.exception.code, "TARGET_IS_ACTIVE")

    def test_target_with_data_conflict_is_held_before_candidate_analysis(self) -> None:
        self.target.correction_codes = ["DATE_CONFLICT"]
        self.target.save(update_fields=["correction_codes"])

        assessment = assess_manager_continuity(self.target.employee_id)

        self.assertEqual(assessment.status, AssessmentStatus.ON_HOLD)
        self.assertIsNone(assessment.unique_candidate_count)
        self.assertEqual(assessment.data_warning_count, 1)
        self.assertIn("충돌", assessment.warnings[0])

    def test_target_with_blank_profile_value_is_held(self) -> None:
        self.target.department_name = ""
        self.target.save(update_fields=["department_name"])

        assessment = assess_manager_continuity(self.target.employee_id)

        self.assertEqual(assessment.status, AssessmentStatus.ON_HOLD)
        self.assertIn("비어 있는", assessment.warnings[0])

    def test_guidance_separates_continuity_from_hiring_decision(self) -> None:
        reviewable = guidance_for(AssessmentStatus.REVIEWABLE)
        no_match = guidance_for(AssessmentStatus.NO_MATCH)
        on_hold = guidance_for(AssessmentStatus.ON_HOLD)

        self.assertIn("내부대체를 먼저", reviewable.staffing_direction)
        self.assertIn("현재 데이터로 확인할 수 없습니다", reviewable.replacement_status)
        self.assertIn("충원 방안", no_match.staffing_direction)
        self.assertIn("채용 확정", no_match.replacement_status)
        self.assertIn("인사 데이터 오류", on_hold.continuity_signal)
        self.assertIn("신규채용 판단을 모두 보류", on_hold.staffing_direction)
        self.assertIn("대체가 진행 중이거나 완료", on_hold.replacement_status)

    def _employee(
        self,
        employee_id: str,
        name: str,
        *,
        department: str = "개발부",
        position: str = "매니저",
        hire: date = date(2015, 1, 1),
        active: bool = True,
        correction_codes: list[str] | None = None,
        profile_image_url: str | None = None,
    ) -> SilverEmployee:
        return SilverEmployee.objects.create(
            employee_id=employee_id,
            employee_name=name,
            profile_image_url=profile_image_url,
            department_name=department,
            position_name=position,
            hire_datetime=timezone.make_aware(datetime.combine(hire, datetime.min.time())),
            is_active=active,
            source_record_id=f"source-{employee_id}",
            dataset_id="test-dataset",
            normalization_run_id="test-run",
            correction_codes=correction_codes or [],
            standardization=[],
        )

    def _parent(self, parent_id: str, name: str) -> SilverParentArea:
        return SilverParentArea.objects.create(
            parent_area_id=parent_id,
            parent_area_name=name,
            source_record_id=f"source-{parent_id}",
            dataset_id="test-dataset",
            normalization_run_id="test-run",
            correction_codes=[],
            standardization=[],
        )

    def _area(
        self,
        area_id: str,
        name: str,
        employee: SilverEmployee,
        parent: SilverParentArea | None,
    ) -> SilverArea:
        return SilverArea.objects.create(
            area_id=area_id,
            area_name=name,
            manager_employee=employee,
            parent_area=parent,
            area_registered_at=timezone.make_aware(datetime(2020, 1, 1)),
            source_record_id=f"source-{area_id}",
            dataset_id="test-dataset",
            normalization_run_id="test-run",
            correction_codes=[],
            standardization=[],
        )
