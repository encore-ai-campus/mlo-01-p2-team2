from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings

from second_project.gold.etl import run_gold_pipeline
from second_project.gold.etl.repositories.gold_writer import ReleaseIdentityConflict
from second_project.gold.models import GoldHrAssessment, GoldHrCandidateEvidence, GoldReleaseRun
from second_project.models import SilverArea, SilverEmployee, SilverParentArea, SilverTopAreaDetail


class GoldPipelineTests(TestCase):
    databases = {"default", "gold"}

    def setUp(self):
        metadata = {
            "dataset_id": "dataset-1",
            "normalization_run_id": "run-1",
            "correction_codes": [],
            "standardization": [],
        }
        self.target = SilverEmployee.objects.create(
            employee_id="EMP000001",
            employee_name="target person",
            department_name="platform",
            position_name="manager",
            hire_datetime=datetime(2018, 1, 1, tzinfo=UTC),
            is_active=False,
            source_record_id="source-target",
            **metadata,
        )
        self.candidate = SilverEmployee.objects.create(
            employee_id="EMP000002",
            employee_name="candidate person",
            department_name="platform",
            position_name="manager",
            hire_datetime=datetime(2020, 1, 1, tzinfo=UTC),
            is_active=True,
            source_record_id="source-candidate",
            **metadata,
        )
        parent = SilverParentArea.objects.create(
            parent_area_id="PAREA001",
            parent_area_name="service",
            source_record_id="source-parent",
            **metadata,
        )
        SilverTopAreaDetail.objects.create(
            top_area_id="TOP00001",
            top_area_name="platform",
            top_area_level="TOP",
            top_area_registered_at=datetime(2020, 1, 1, tzinfo=UTC),
            source_record_id="source-top",
            **metadata,
        )
        for area_id, manager in (("AREA0001", self.target), ("AREA0002", self.candidate)):
            SilverArea.objects.create(
                area_id=area_id,
                area_name="billing",
                manager_employee=manager,
                area_registered_at=datetime(2020, 1, 1, tzinfo=UTC),
                parent_area=parent,
                source_record_id=f"source-{area_id}",
                **metadata,
            )

    def test_loads_validates_packages_and_reuses_successful_release(self):
        with TemporaryDirectory() as directory, override_settings(GOLD_RELEASE_ROOT=Path(directory)):
            result = run_gold_pipeline(
                release_id="release-1",
                dataset_version="2026.08.28",
                as_of_date=date(2026, 8, 28),
                expected_counts={"assessment_rows": 1, "candidate_rows": 1, "excluded_rows": 0},
            )
            rerun = run_gold_pipeline(
                release_id="release-1",
                dataset_version="2026.08.28",
                as_of_date=date(2026, 8, 28),
            )

            self.assertTrue(result.passed)
            self.assertTrue(result.loaded)
            self.assertTrue(result.load_validation.passed)
            self.assertTrue(rerun.reused)
            self.assertEqual(GoldReleaseRun.objects.using("gold").get().status, "SUCCESS")
            self.assertEqual(GoldHrAssessment.objects.using("gold").count(), 1)
            self.assertEqual(GoldHrCandidateEvidence.objects.using("gold").count(), 1)
            artifact_text = "\n".join(path.read_text(encoding="utf-8-sig") for path in result.release_directory.iterdir())
            self.assertNotIn("target person", artifact_text)
            self.assertNotIn("candidate person", artifact_text)
            self.assertNotIn("EMP000001", artifact_text)
            self.assertNotIn("checksum", artifact_text.casefold())
            with self.assertRaises(ReleaseIdentityConflict):
                run_gold_pipeline(
                    release_id="release-1",
                    dataset_version="different-version",
                    as_of_date=date(2026, 8, 28),
                )
