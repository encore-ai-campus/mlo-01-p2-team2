import io
import json
from datetime import datetime

from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from second_project.models import SilverArea, SilverEmployee, SilverParentArea
from second_project.presentation.permissions import HR_GROUP, TEAM_MANAGER_GROUP


class HrGuideManagementCommandTests(TestCase):
    databases = {"default"}

    def test_bootstrap_groups_is_idempotent(self) -> None:
        first_output = io.StringIO()
        second_output = io.StringIO()

        call_command("bootstrap_hr_guide", stdout=first_output)
        call_command("bootstrap_hr_guide", stdout=second_output)

        self.assertIn(HR_GROUP, first_output.getvalue())
        self.assertIn(TEAM_MANAGER_GROUP, first_output.getvalue())
        self.assertIn(HR_GROUP, second_output.getvalue())
        self.assertIn(TEAM_MANAGER_GROUP, second_output.getvalue())
        self.assertEqual(
            Group.objects.filter(name__in=(HR_GROUP, TEAM_MANAGER_GROUP)).count(),
            2,
        )

    def test_fresh_rdb_migration_does_not_create_bronze_table(self) -> None:
        self.assertNotIn("bronze_raw_records", connection.introspection.table_names())

    def test_strict_data_check_fails_when_canonical_data_is_empty(self) -> None:
        with self.assertRaises(CommandError):
            call_command("check_hr_guide_data", strict=True, stdout=io.StringIO())

    def test_data_check_is_ready_when_inactive_target_has_active_peer(self) -> None:
        target = self._employee("EMP000001", active=False)
        candidate = self._employee("EMP000002", active=True)
        parent = SilverParentArea.objects.create(
            parent_area_id="PAREA001",
            parent_area_name="플랫폼",
            **self._metadata("parent"),
        )
        self._area("AREA0001", target, parent)
        self._area("AREA0002", candidate, parent)
        output = io.StringIO()

        call_command("check_hr_guide_data", strict=True, stdout=output)
        payload = json.loads(output.getvalue())

        self.assertEqual(payload["status"], "MINIMUM_READY")
        self.assertEqual(payload["gate_1_status"], "REQUIRES_DATA_OWNER_APPROVAL")
        self.assertEqual(
            payload["counts"]["inactive_targets_with_confirmed_active_peer"],
            1,
        )

    def test_data_check_rejects_relationship_with_conflict_only_candidate(self) -> None:
        target = self._employee("EMP000001", active=False)
        candidate = self._employee("EMP000002", active=True)
        candidate.correction_codes = ["DATE_CONFLICT"]
        candidate.save(update_fields=["correction_codes"])
        parent = SilverParentArea.objects.create(
            parent_area_id="PAREA001",
            parent_area_name="플랫폼",
            **self._metadata("parent"),
        )
        self._area("AREA0001", target, parent)
        self._area("AREA0002", candidate, parent)

        with self.assertRaises(CommandError):
            call_command("check_hr_guide_data", strict=True, stdout=io.StringIO())

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
    def _employee(cls, employee_id: str, *, active: bool) -> SilverEmployee:
        return SilverEmployee.objects.create(
            employee_id=employee_id,
            employee_name=employee_id,
            department_name="개발부",
            position_name="팀장",
            hire_datetime=timezone.make_aware(datetime(2015, 1, 1)),
            is_active=active,
            **cls._metadata(employee_id),
        )

    @classmethod
    def _area(
        cls,
        area_id: str,
        employee: SilverEmployee,
        parent: SilverParentArea,
    ) -> SilverArea:
        return SilverArea.objects.create(
            area_id=area_id,
            area_name=area_id,
            manager_employee=employee,
            parent_area=parent,
            area_registered_at=timezone.make_aware(datetime(2020, 1, 1)),
            **cls._metadata(area_id),
        )
