from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from second_project.models import (
    LegacyOrgRecord,
    SilverArea,
    SilverEmployee,
    SilverParentArea,
    SilverTopAreaDetail,
)


class Command(BaseCommand):
    help = "현재 RDB가 인사 요청 검토 화면에 사용할 준비가 됐는지 확인합니다."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--strict",
            action="store_true",
            help="준비 조건을 충족하지 못하면 실패 코드로 종료합니다.",
        )

    def handle(self, *args, **options) -> None:
        employee_rows = {
            employee_id: {
                "active": is_active,
                "name": employee_name,
                "department": department_name,
                "position": position_name,
                "correction_codes": correction_codes or [],
            }
            for (
                employee_id,
                is_active,
                employee_name,
                department_name,
                position_name,
                correction_codes,
            ) in SilverEmployee.objects.values_list(
                "employee_id",
                "is_active",
                "employee_name",
                "department_name",
                "position_name",
                "correction_codes",
            )
        }
        blocked_employee_ids = {
            employee_id
            for employee_id, row in employee_rows.items()
            if "DATE_CONFLICT" in {str(code) for code in row["correction_codes"]}
            or not all(
                isinstance(row[field], str) and row[field].strip()
                for field in ("name", "department", "position")
            )
        }
        valid_parent_ids = set(
            SilverParentArea.objects.exclude(parent_area_name="").values_list(
                "parent_area_id",
                flat=True,
            )
        )
        active_by_parent: dict[str, set[str]] = {}
        inactive_by_parent: dict[str, set[str]] = {}
        for employee_id, parent_area_id, area_name in SilverArea.objects.values_list(
            "manager_employee_id",
            "parent_area_id",
            "area_name",
        ):
            if (
                employee_id in blocked_employee_ids
                or parent_area_id not in valid_parent_ids
                or not isinstance(area_name, str)
                or not area_name.strip()
            ):
                continue
            target = (
                active_by_parent
                if employee_rows[employee_id]["active"]
                else inactive_by_parent
            )
            target.setdefault(parent_area_id, set()).add(employee_id)

        reviewable_target_ids = {
            employee_id
            for parent_area_id, employee_ids in inactive_by_parent.items()
            if active_by_parent.get(parent_area_id)
            for employee_id in employee_ids
        }
        counts = {
            "legacy_org_record": LegacyOrgRecord.objects.count(),
            "silver_employee": SilverEmployee.objects.count(),
            "silver_employee_active": SilverEmployee.objects.filter(is_active=True).count(),
            "silver_employee_inactive": SilverEmployee.objects.filter(is_active=False).count(),
            "silver_area": SilverArea.objects.count(),
            "silver_area_without_parent": SilverArea.objects.filter(parent_area__isnull=True).count(),
            "silver_parent_area": SilverParentArea.objects.count(),
            "silver_top_area_detail": SilverTopAreaDetail.objects.count(),
            "employee_profile_or_conflict_holds": len(blocked_employee_ids),
            "parent_area_name_missing": SilverParentArea.objects.filter(
                parent_area_name=""
            ).count(),
            "area_name_missing": SilverArea.objects.filter(area_name="").count(),
            "inactive_targets_with_confirmed_active_peer": len(reviewable_target_ids),
        }
        checks = {
            "has_inactive_target": counts["silver_employee_inactive"] > 0,
            "has_active_candidate": counts["silver_employee_active"] > 0,
            "has_areas": counts["silver_area"] > 0,
            "has_parent_areas": counts["silver_parent_area"] > 0,
            "has_reviewable_relationship": (
                counts["inactive_targets_with_confirmed_active_peer"] > 0
            ),
            "canonical_not_demo_sized": (
                counts["silver_employee"] > 1
                and counts["silver_area"] > 1
                and counts["silver_parent_area"] > 0
            ),
        }
        ready = all(checks.values())
        payload = {
            "status": "MINIMUM_READY" if ready else "NOT_READY",
            "gate_1_status": "REQUIRES_DATA_OWNER_APPROVAL",
            "counts": counts,
            "checks": checks,
            "notes": [
                "상위영역이 없는 AREA는 화면에서 데이터 확인 필요로 처리됩니다.",
                "TOP_AREA_DETAIL은 현재 판정 조회에 사용하지 않으므로 준비 여부를 차단하지 않습니다.",
                "MINIMUM_READY는 기술적 최소조건일 뿐이며 Gate 1 또는 HR 업무 승인을 의미하지 않습니다.",
                "Gate 1은 PK/FK·전체 품질·기준 SQL 대사와 데이터오너 승인을 별도로 통과해야 합니다.",
            ],
        }
        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        if options["strict"] and not ready:
            raise CommandError("인사 요청 검토용 canonical RDB가 아직 준비되지 않았습니다.")
