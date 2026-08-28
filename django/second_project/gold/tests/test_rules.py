from datetime import UTC, date, datetime

from django.test import SimpleTestCase

from second_project.gold.etl.rules import build_gold_rows
from second_project.gold.etl.types import (
    AreaSnapshot,
    EmployeeSnapshot,
    ParentAreaSnapshot,
    SilverSnapshot,
    TopAreaSnapshot,
)
from second_project.gold.etl.validation import validate_transform


class GoldRuleTests(SimpleTestCase):
    def test_deduplicates_candidates_and_keeps_future_hire_with_warning(self):
        snapshot = _snapshot(
            employees=(
                _employee("EMP000001", active=False),
                _employee("EMP000002", active=True, hire_year=2030),
                _employee("EMP000003", active=True, corrections=("DATE_CONFLICT",)),
            ),
            areas=(
                _area("AREA0001", "EMP000001"),
                _area("AREA0002", "EMP000002"),
                _area("AREA0003", "EMP000002"),
                _area("AREA0004", "EMP000003"),
            ),
        )

        result = build_gold_rows(snapshot, as_of_date=date(2026, 8, 28))

        self.assertEqual(len(result.assessments), 1)
        self.assertEqual(result.assessments[0].area_status, "REVIEWABLE")
        self.assertEqual(result.assessments[0].confirmed_candidate_count, 1)
        self.assertEqual(result.assessments[0].held_candidate_count, 1)
        self.assertEqual(len(result.candidates), 2)
        future = next(row for row in result.candidates if row.candidate_employee_id == "EMP000002")
        self.assertEqual(future.candidate_state, "CONFIRMED")
        self.assertIsNone(future.candidate_tenure_days)
        self.assertEqual(future.warning_codes, ["TENURE_UNAVAILABLE"])
        self.assertTrue(validate_transform(result).passed)

    def test_no_candidate_is_zero_but_blocking_target_is_null(self):
        no_match = build_gold_rows(
            _snapshot(
                employees=(_employee("EMP000001", active=False),),
                areas=(_area("AREA0001", "EMP000001"),),
            ),
            as_of_date=date(2026, 8, 28),
        )
        self.assertEqual(no_match.assessments[0].area_status, "NO_MATCH")
        self.assertEqual(no_match.assessments[0].confirmed_candidate_count, 0)

        held = build_gold_rows(
            _snapshot(
                employees=(_employee("EMP000001", active=False, corrections=("DATE_CONFLICT",)),),
                areas=(_area("AREA0001", "EMP000001"),),
            ),
            as_of_date=date(2026, 8, 28),
        )
        self.assertEqual(held.assessments[0].area_status, "ON_HOLD")
        self.assertIsNone(held.assessments[0].confirmed_candidate_count)
        self.assertEqual(held.assessments[0].warning_codes, ["DATA_CONFLICT"])


def _employee(employee_id: str, *, active: bool, hire_year: int = 2020, corrections=()):
    return EmployeeSnapshot(
        employee_id=employee_id,
        employee_name=f"name-{employee_id}",
        department_name="department",
        position_name="manager",
        hire_datetime=datetime(hire_year, 1, 1, tzinfo=UTC),
        is_active=active,
        source_record_id=f"source-{employee_id}",
        dataset_id="dataset-1",
        normalization_run_id="run-1",
        correction_codes=tuple(corrections),
    )


def _area(area_id: str, employee_id: str):
    return AreaSnapshot(
        area_id=area_id,
        area_name=f"area-{area_id}",
        manager_employee_id=employee_id,
        parent_area_id="PAREA001",
        source_record_id=f"source-{area_id}",
        dataset_id="dataset-1",
        normalization_run_id="run-1",
    )


def _snapshot(*, employees, areas):
    return SilverSnapshot(
        employees=tuple(employees),
        parents=(ParentAreaSnapshot("PAREA001", "parent", "source-parent", "dataset-1", "run-1"),),
        areas=tuple(areas),
        top_areas=(TopAreaSnapshot("TOP00001", "top", "TOP", "source-top", "dataset-1", "run-1"),),
        dataset_ids=("dataset-1",),
        normalization_run_ids=("run-1",),
    )
