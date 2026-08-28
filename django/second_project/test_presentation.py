from datetime import date, datetime

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from second_project.models import SilverArea, SilverEmployee, SilverParentArea
from second_project.presentation.permissions import HR_GROUP, TEAM_MANAGER_GROUP


class ReviewPresentationTests(TestCase):
    databases = {"default"}

    @classmethod
    def setUpTestData(cls) -> None:
        cls.hr_group = Group.objects.create(name=HR_GROUP)
        cls.team_group = Group.objects.create(name=TEAM_MANAGER_GROUP)
        cls.hr_user = User.objects.create_user("hr-user", password="test-password")
        cls.hr_user.groups.add(cls.hr_group)
        cls.team_user = User.objects.create_user("team-user", password="test-password")
        cls.team_user.groups.add(cls.team_group)
        cls.other_user = User.objects.create_user("other-user", password="test-password")

        cls.target = cls._employee(
            "EMP000001",
            "퇴직대상",
            department="개발부",
            position="팀장",
            active=False,
        )
        cls.candidate = cls._employee(
            "EMP000002",
            "후보개인정보",
            department="개발부",
            position="팀장",
        )
        cls.parent = SilverParentArea.objects.create(
            parent_area_id="PAREA001",
            parent_area_name="플랫폼",
            **cls._metadata("parent"),
        )
        cls._area("AREA0001", "플랫폼 운영", cls.target, cls.parent)
        cls._area("AREA0002", "플랫폼 개발", cls.candidate, cls.parent)

    def test_unauthenticated_user_is_redirected_to_login(self) -> None:
        response = self.client.get(reverse("second_project:review"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('second_project:review')}",
        )

    def test_user_without_review_group_is_forbidden(self) -> None:
        self.client.force_login(self.other_user)
        response = self.client.get(reverse("second_project:review"))
        self.assertEqual(response.status_code, 403)

    def test_team_manager_response_contains_counts_but_no_candidate_pii(self) -> None:
        self.candidate.profile_image_url = (
            "https://intranet.example.test/profiles/EMP000002.jpg"
        )
        self.candidate.save(update_fields=["profile_image_url"])
        self.client.force_login(self.team_user)
        response = self.client.post(
            reverse("second_project:review"),
            {"manager_id": self.target.employee_id},
        )
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "현재 재직 검토 대상")
        self.assertNotIn(self.candidate.employee_name, body)
        self.assertNotIn(self.candidate.employee_id, body)
        self.assertNotIn(self.candidate.department_name, body)
        self.assertNotIn(self.candidate.position_name, body)
        self.assertNotIn(self.candidate.profile_image_url, body)

    def test_hr_response_contains_allowed_candidate_details_without_rank(self) -> None:
        self.client.force_login(self.hr_user)
        response = self.client.post(
            reverse("second_project:review"),
            {"manager_id": self.target.employee_id},
        )
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.candidate.employee_name)
        self.assertContains(response, self.candidate.department_name)
        self.assertContains(response, self.candidate.position_name)
        self.assertContains(response, "HR 판단 가이드")
        self.assertContains(response, "내부대체를 먼저 검토")
        self.assertContains(response, "대체 완료 여부")
        self.assertContains(response, "person-avatar-empty")
        self.assertContains(response, "second_project/images/default-profile.png")
        self.assertContains(response, "사진")
        self.assertContains(response, "인사정보")
        self.assertContains(response, "검토 근거")
        self.assertContains(response, "부서 일치도")
        self.assertContains(response, "직위 일치도")
        self.assertNotContains(response, "<th>데이터 경고</th>", html=True)
        self.assertNotIn("순위", body)
        self.assertNotIn("승인하기", body)
        self.assertNotIn("반려하기", body)

    def test_hr_candidate_profile_image_url_is_rendered_when_present(self) -> None:
        image_url = "https://intranet.example.test/profiles/EMP000002.jpg"
        self.candidate.profile_image_url = image_url
        self.candidate.save(update_fields=["profile_image_url"])
        self.client.force_login(self.hr_user)

        response = self.client.post(
            reverse("second_project:review"),
            {"manager_id": self.target.employee_id},
        )

        self.assertContains(response, image_url)
        self.assertContains(response, 'class="person-avatar"')

    def test_review_form_renders_result_without_putting_manager_id_in_url(self) -> None:
        self.client.force_login(self.hr_user)
        response = self.client.post(
            reverse("second_project:review"),
            {"manager_id": f"  {self.target.employee_id}  "},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], reverse("second_project:review"))
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_local_preview_null_origin_can_submit_in_debug_mode(self) -> None:
        self.client.force_login(self.hr_user)
        response = self.client.post(
            reverse("second_project:review"),
            {"manager_id": self.target.employee_id},
            HTTP_HOST="127.0.0.1:8000",
            HTTP_ORIGIN="null",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.target.employee_name)

    def test_active_manager_request_returns_bad_request(self) -> None:
        self.client.force_login(self.hr_user)
        response = self.client.post(
            reverse("second_project:review"),
            {"manager_id": self.candidate.employee_id},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "현재 재직 상태", status_code=400)

    def test_hr_can_see_held_candidate_reason_but_team_manager_cannot(self) -> None:
        held = self._employee(
            "EMP000003",
            "보류후보",
            department="인사부",
            position="매니저",
        )
        held.correction_codes = ["DATE_CONFLICT"]
        held.save(update_fields=["correction_codes"])
        self._area("AREA0003", "플랫폼 지원", held, self.parent)

        self.client.force_login(self.hr_user)
        hr_response = self.client.post(
            reverse("second_project:review"),
            {"manager_id": self.target.employee_id},
        )
        self.assertContains(hr_response, held.employee_name)
        self.assertContains(hr_response, "충돌 표시")

        self.client.force_login(self.team_user)
        team_response = self.client.post(
            reverse("second_project:review"),
            {"manager_id": self.target.employee_id},
        )
        team_body = team_response.content.decode()
        self.assertNotIn(held.employee_name, team_body)
        self.assertNotIn(held.employee_id, team_body)
        self.assertNotIn("충돌 표시", team_body)

    @staticmethod
    def _metadata(identity: str) -> dict[str, object]:
        return {
            "source_record_id": f"source-{identity}",
            "dataset_id": "test-dataset",
            "normalization_run_id": "test-run",
            "correction_codes": [],
            "standardization": [],
        }

    @classmethod
    def _employee(
        cls,
        employee_id: str,
        name: str,
        *,
        department: str,
        position: str,
        active: bool = True,
        profile_image_url: str | None = None,
    ) -> SilverEmployee:
        return SilverEmployee.objects.create(
            employee_id=employee_id,
            employee_name=name,
            profile_image_url=profile_image_url,
            department_name=department,
            position_name=position,
            hire_datetime=timezone.make_aware(datetime(2015, 1, 1)),
            is_active=active,
            **cls._metadata(employee_id),
        )

    @classmethod
    def _area(
        cls,
        area_id: str,
        name: str,
        employee: SilverEmployee,
        parent: SilverParentArea,
    ) -> SilverArea:
        return SilverArea.objects.create(
            area_id=area_id,
            area_name=name,
            manager_employee=employee,
            parent_area=parent,
            area_registered_at=timezone.make_aware(datetime(2020, 1, 1)),
            **cls._metadata(area_id),
        )
