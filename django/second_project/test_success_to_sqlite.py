from datetime import datetime

from django.test import SimpleTestCase
from django.utils import timezone

from second_project.services.success_to_sqlite import _build_employee


class SuccessToSqliteEmployeeTests(SimpleTestCase):
    def test_profile_image_url_is_optional(self) -> None:
        employee = _build_employee(self._document(), "silver_employee")

        self.assertIsNone(employee.profile_image_url)

    def test_profile_image_url_is_preserved_when_supplied(self) -> None:
        document = self._document()
        document["profile_image_url"] = (
            "https://intranet.example.test/profiles/EMP000001.jpg"
        )

        employee = _build_employee(document, "silver_employee")

        self.assertEqual(employee.profile_image_url, document["profile_image_url"])

    @staticmethod
    def _document() -> dict[str, object]:
        return {
            "employee_id": "EMP000001",
            "employee_name": "테스트 관리자",
            "department_name": "플랫폼개발부",
            "position_name": "팀장",
            "hire_datetime": timezone.make_aware(datetime(2020, 1, 1)),
            "is_active": True,
            "source_record_id": "source-employee-1",
            "dataset_id": "test-dataset",
            "normalization_run_id": "test-run",
            "correction_codes": [],
            "_standardization": [],
        }
