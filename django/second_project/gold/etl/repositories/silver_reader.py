from __future__ import annotations

from second_project.models import SilverArea, SilverEmployee, SilverParentArea, SilverTopAreaDetail

from ..types import (
    AreaSnapshot,
    EmployeeSnapshot,
    ParentAreaSnapshot,
    SilverSnapshot,
    TopAreaSnapshot,
)


class SilverReader:
    def __init__(self, *, using: str = "default") -> None:
        self.using = using

    def read(self) -> SilverSnapshot:
        employees = tuple(
            EmployeeSnapshot(
                employee_id=row.employee_id,
                employee_name=row.employee_name,
                department_name=row.department_name,
                position_name=row.position_name,
                hire_datetime=row.hire_datetime,
                is_active=row.is_active,
                source_record_id=row.source_record_id,
                dataset_id=row.dataset_id,
                normalization_run_id=row.normalization_run_id,
                correction_codes=tuple(str(code) for code in row.correction_codes),
            )
            for row in SilverEmployee.objects.using(self.using).all().iterator()
        )
        parents = tuple(
            ParentAreaSnapshot(
                parent_area_id=row.parent_area_id,
                parent_area_name=row.parent_area_name,
                source_record_id=row.source_record_id,
                dataset_id=row.dataset_id,
                normalization_run_id=row.normalization_run_id,
            )
            for row in SilverParentArea.objects.using(self.using).all().iterator()
        )
        areas = tuple(
            AreaSnapshot(
                area_id=row.area_id,
                area_name=row.area_name,
                manager_employee_id=row.manager_employee_id,
                parent_area_id=row.parent_area_id,
                source_record_id=row.source_record_id,
                dataset_id=row.dataset_id,
                normalization_run_id=row.normalization_run_id,
            )
            for row in SilverArea.objects.using(self.using).all().iterator()
        )
        top_areas = tuple(
            TopAreaSnapshot(
                top_area_id=row.top_area_id,
                top_area_name=row.top_area_name,
                top_area_level=row.top_area_level,
                source_record_id=row.source_record_id,
                dataset_id=row.dataset_id,
                normalization_run_id=row.normalization_run_id,
            )
            for row in SilverTopAreaDetail.objects.using(self.using).all().iterator()
        )
        all_rows = (*employees, *parents, *areas, *top_areas)
        return SilverSnapshot(
            employees=employees,
            parents=parents,
            areas=areas,
            top_areas=top_areas,
            dataset_ids=tuple(sorted({row.dataset_id for row in all_rows})),
            normalization_run_ids=tuple(sorted({row.normalization_run_id for row in all_rows})),
        )
