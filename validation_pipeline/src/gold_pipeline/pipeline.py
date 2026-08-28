from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .canonical_contract import CanonicalRuleCatalog
from .rdb_source import DEFAULT_SILVER_TABLES, SQLiteGoldSource, TableSpec


GOLD_SCHEMA_VERSION = "gold-1.0"
DEFAULT_RULES_PATH = (
    Path(__file__).resolve().parents[2] / "rules" / "silver_canonical.yaml"
)


@dataclass(frozen=True)
class GoldRunResult:
    """Gold ETL 실행 결과와 생성된 release package 위치."""

    output_dir: Path
    report: dict[str, Any]
    manifest: dict[str, Any]

    @property
    def release_ready(self) -> bool:
        return bool(self.report.get("release_ready"))


class GoldPipeline:
    """SQLite Silver 테이블을 검증하고 재현 가능한 Gold 패키지로 만든다."""

    def __init__(
        self,
        database_path: str | Path,
        output_dir: str | Path,
        *,
        release_version: str = "0.1.0",
        run_id: str | None = None,
        rules_path: str | Path = DEFAULT_RULES_PATH,
        table_specs: tuple[TableSpec, ...] = DEFAULT_SILVER_TABLES,
    ) -> None:
        self.database_path = Path(database_path)
        self.output_dir = Path(output_dir)
        self.release_version = release_version
        self.run_id = run_id or _new_run_id()
        self.rules_path = Path(rules_path)
        self.table_specs = table_specs
        self.canonical = CanonicalRuleCatalog(self.rules_path)

    def run(self) -> GoldRunResult:
        """입력 snapshot을 읽고 같은 output_dir을 안전하게 재생성한다."""

        with SQLiteGoldSource(
            self.database_path,
            table_specs=self.table_specs,
        ) as source:
            source_schema = source.validate_schema()
            source_rows = {
                spec.table_name: source.read_rows(spec)
                for spec in self.table_specs
            }

        (
            accepted_by_entity,
            rejected_records,
            issue_counts,
            source_counts,
            rejected_by_entity,
            correction_counts,
            standardization_event_count,
        ) = self._transform_and_validate(source_rows)

        area_dataset, relation_rejections = self._build_area_dataset(
            accepted_by_entity
        )
        for rejection in relation_rejections:
            rejected_records.append(rejection)
            entity_type = str(rejection["entity_type"])
            rejected_by_entity[entity_type] += 1
            for issue in rejection["issues"]:
                issue_counts[str(issue["error_code"])] += 1

        accepted_counts = {
            spec.entity_type: len(accepted_by_entity[spec.entity_type])
            for spec in self.table_specs
        }
        source_total = sum(source_counts.values())
        rejected_total = len(rejected_records)
        status = _status(source_total, rejected_total)

        report = self._build_report(
            status=status,
            source_schema=source_schema,
            source_counts=source_counts,
            accepted_counts=accepted_counts,
            rejected_by_entity=rejected_by_entity,
            rejected_total=rejected_total,
            issue_counts=issue_counts,
            correction_counts=correction_counts,
            standardization_event_count=standardization_event_count,
            area_dataset_count=len(area_dataset),
        )
        schema = self._build_release_schema()
        data_dictionary = self._build_data_dictionary()
        manifest = self._build_manifest(
            report=report,
            source_schema=source_schema,
            accepted_by_entity=accepted_by_entity,
            area_dataset=area_dataset,
            rejected_records=rejected_records,
        )
        readme = self._build_readme(report)
        release_notes = self._build_release_notes(report)

        self._write_package(
            source_schema=source_schema,
            schema=schema,
            report=report,
            manifest=manifest,
            accepted_by_entity=accepted_by_entity,
            area_dataset=area_dataset,
            rejected_records=rejected_records,
            data_dictionary=data_dictionary,
            readme=readme,
            release_notes=release_notes,
        )

        return GoldRunResult(
            output_dir=self.output_dir,
            report=report,
            manifest=manifest,
        )

    def _transform_and_validate(
        self,
        source_rows: dict[str, list[dict[str, Any]]],
    ) -> tuple[
        dict[str, list[dict[str, Any]]],
        list[dict[str, Any]],
        Counter[str],
        dict[str, int],
        Counter[str],
        Counter[str],
        int,
    ]:
        accepted_by_entity = {
            spec.entity_type: []
            for spec in self.table_specs
        }
        rejected_records: list[dict[str, Any]] = []
        issue_counts: Counter[str] = Counter()
        rejected_by_entity: Counter[str] = Counter()
        source_counts: dict[str, int] = {}
        correction_counts: Counter[str] = Counter()
        standardization_event_count = 0

        for spec in self.table_specs:
            rows = source_rows[spec.table_name]
            source_counts[spec.entity_type] = len(rows)
            seen_keys: set[str] = set()
            for row_index, row in enumerate(rows):
                record, issues, corrections, event_count = self._record_from_row(
                    spec,
                    row,
                    row_index=row_index,
                )
                correction_counts.update(corrections)
                standardization_event_count += event_count
                record_key = str(record["record_key"])
                if record_key in seen_keys:
                    issues.append(
                        {
                            "field": spec.primary_key,
                            "error_code": "DUPLICATE_FINAL_VALUE",
                            "message": (
                                "최종 Gold record key가 같은 테이블 안에서 "
                                "중복됩니다."
                            ),
                        }
                    )
                seen_keys.add(record_key)

                if issues:
                    rejected_records.append(
                        {
                            "stage": "gold_validation",
                            "entity_type": spec.entity_type,
                            "record_key": record_key,
                            "issues": issues,
                            "record": record,
                        }
                    )
                    rejected_by_entity[spec.entity_type] += 1
                    for issue in issues:
                        issue_counts[str(issue["error_code"])] += 1
                    continue
                accepted_by_entity[spec.entity_type].append(record)

        return (
            accepted_by_entity,
            rejected_records,
            issue_counts,
            source_counts,
            rejected_by_entity,
            correction_counts,
            standardization_event_count,
        )

    def _record_from_row(
        self,
        spec: TableSpec,
        row: dict[str, Any],
        *,
        row_index: int,
    ) -> tuple[dict[str, Any], list[dict[str, str]], Counter[str], int]:
        normalized: dict[str, Any] = {}
        issues: list[dict[str, str]] = []
        correction_counts: Counter[str] = Counter()
        standardization_event_count = 0

        for column in spec.all_columns:
            value = row.get(column)
            if column in spec.json_columns:
                value, json_issue = _parse_json_column(column, value)
                if json_issue is not None:
                    issues.append(json_issue)
                if column == "correction_codes" and isinstance(value, list):
                    invalid_codes = [
                        item for item in value if not isinstance(item, str)
                    ]
                    if invalid_codes:
                        issues.append(
                            {
                                "field": column,
                                "error_code": "TYPE_MISMATCH",
                                "message": "correction_codes는 문자열 배열이어야 합니다.",
                            }
                        )
                    correction_counts.update(
                        item for item in value if isinstance(item, str)
                    )
                if column == "_standardization" and isinstance(value, list):
                    standardization_event_count += len(value)
            try:
                normalized[column] = self.canonical.normalize(column, value)
            except ValueError as error:
                normalized[column] = None
                issues.append(
                    {
                        "field": column,
                        "error_code": _normalization_error_code(column),
                        "message": str(error),
                    }
                )

        for column in spec.required_columns:
            if _is_blank(normalized.get(column)):
                issues.append(
                    {
                        "field": column,
                        "error_code": "REQUIRED_VALUE_MISSING",
                        "message": "Silver RDB 필수 컬럼이 null 또는 공백입니다.",
                    }
                )

        canonical_issues = self.canonical.validate(
            normalized,
            fields=spec.all_columns,
        )
        issues.extend(issue.as_dict() for issue in canonical_issues)

        primary_value = normalized.get(spec.primary_key)
        record_id = (
            str(primary_value)
            if not _is_blank(primary_value)
            else f"missing-{row_index}"
        )
        record = {
            "record_key": f"{spec.entity_type}:{record_id}",
            "entity_type": spec.entity_type,
            "record_id": record_id,
            **{
                column: normalized.get(column)
                for column in spec.data_columns
            },
            "source_record_id": normalized.get("source_record_id"),
            "dataset_id": normalized.get("dataset_id"),
            "normalization_run_id": normalized.get("normalization_run_id"),
            "correction_codes": normalized.get("correction_codes", []),
            "standardization": normalized.get("_standardization", []),
            "source_table": spec.table_name,
        }
        return record, _dedupe_issues(issues), correction_counts, standardization_event_count

    def _build_area_dataset(
        self,
        accepted_by_entity: dict[str, list[dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        employee_by_id = {
            str(record["employee_id"]): record
            for record in accepted_by_entity.get("employee", [])
        }
        parent_by_id = {
            str(record["parent_area_id"]): record
            for record in accepted_by_entity.get("parent_area", [])
        }
        valid_areas: list[dict[str, Any]] = []
        relation_rejections: list[dict[str, Any]] = []

        for area in accepted_by_entity.get("area", []):
            issues: list[dict[str, str]] = []
            manager_id = area.get("manager_employee_id")
            manager = employee_by_id.get(str(manager_id))
            if manager is None:
                issues.append(
                    {
                        "field": "manager_employee_id",
                        "error_code": "FK_ORPHAN",
                        "message": "참조하는 employee_id가 Gold 직원 데이터에 없습니다.",
                    }
                )

            parent_id = area.get("parent_area_id")
            parent = None
            if parent_id is not None:
                parent = parent_by_id.get(str(parent_id))
                if parent is None:
                    issues.append(
                        {
                            "field": "parent_area_id",
                            "error_code": "FK_ORPHAN",
                            "message": (
                                "참조하는 parent_area_id가 Gold 상위영역 "
                                "데이터에 없습니다."
                            ),
                        }
                    )

            if issues:
                relation_rejections.append(
                    {
                        "stage": "gold_validation",
                        "entity_type": "area",
                        "record_key": area["record_key"],
                        "issues": issues,
                        "record": area,
                    }
                )
                continue

            valid_areas.append(area)

        accepted_by_entity["area"] = valid_areas
        area_dataset = [
            self._join_area(area, employee_by_id, parent_by_id)
            for area in valid_areas
        ]
        return area_dataset, relation_rejections

    @staticmethod
    def _join_area(
        area: dict[str, Any],
        employee_by_id: dict[str, dict[str, Any]],
        parent_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        manager = employee_by_id[str(area["manager_employee_id"])]
        parent_id = area.get("parent_area_id")
        parent = parent_by_id.get(str(parent_id)) if parent_id is not None else None
        return {
            "record_key": f"area_dataset:{area['area_id']}",
            "entity_type": "area_dataset",
            "area_id": area["area_id"],
            "area_name": area["area_name"],
            "area_registered_at": area["area_registered_at"],
            "manager_employee_id": area["manager_employee_id"],
            "manager_employee_name": manager.get("employee_name"),
            "manager_department_name": manager.get("department_name"),
            "manager_position_name": manager.get("position_name"),
            "manager_hire_datetime": manager.get("hire_datetime"),
            "manager_is_active": manager.get("is_active"),
            "parent_area_id": parent_id,
            "parent_area_name": parent.get("parent_area_name") if parent else None,
            "source_record_id": area["source_record_id"],
            "dataset_id": area["dataset_id"],
            "normalization_run_id": area["normalization_run_id"],
            "correction_codes": area["correction_codes"],
            "standardization": area["standardization"],
        }

    def _build_report(
        self,
        *,
        status: str,
        source_schema: dict[str, Any],
        source_counts: dict[str, int],
        accepted_counts: dict[str, int],
        rejected_by_entity: Counter[str],
        rejected_total: int,
        issue_counts: Counter[str],
        correction_counts: Counter[str],
        standardization_event_count: int,
        area_dataset_count: int,
    ) -> dict[str, Any]:
        checks = {
            "source_schema": "PASS",
            "required_fields": (
                "FAIL"
                if issue_counts["REQUIRED_VALUE_MISSING"]
                else "PASS"
            ),
            "canonical_contract": (
                "FAIL"
                if any(
                    issue_counts[code]
                    for code in (
                        "TYPE_MISMATCH",
                        "DOMAIN_UNKNOWN",
                        "DATETIME_PARSE_FAILED",
                    )
                )
                else "PASS"
            ),
            "final_unique_key": (
                "FAIL"
                if issue_counts["DUPLICATE_FINAL_VALUE"]
                else "PASS"
            ),
            "referential_integrity": (
                "FAIL" if issue_counts["FK_ORPHAN"] else "PASS"
            ),
            "metadata_json": (
                "FAIL"
                if issue_counts["JSON_PARSE_FAILED"]
                else "PASS"
            ),
        }
        return {
            "schema_version": GOLD_SCHEMA_VERSION,
            "status": status,
            "release_ready": status == "SUCCESS",
            "release_version": self.release_version,
            "run_id": self.run_id,
            "generated_at_utc": _now_utc(),
            "source": {
                "type": "sqlite",
                "database": str(self.database_path.resolve()),
                "tables": source_schema,
            },
            "rules": self.canonical.description,
            "counts": {
                "source_total": sum(source_counts.values()),
                "accepted_total": sum(accepted_counts.values()),
                "rejected_total": rejected_total,
                "gold_area_dataset": area_dataset_count,
                "source_by_entity": source_counts,
                "accepted_by_entity": accepted_counts,
                "rejected_by_entity": dict(rejected_by_entity),
            },
            "quality": {
                "checks": checks,
                "issue_counts": dict(issue_counts),
                "correction_code_counts": dict(correction_counts),
                "standardization_event_count": standardization_event_count,
            },
        }

    def _build_release_schema(self) -> dict[str, Any]:
        datasets: dict[str, Any] = {}
        for spec in self.table_specs:
            fields = (
                "record_key",
                "entity_type",
                "record_id",
                *spec.data_columns,
                "source_record_id",
                "dataset_id",
                "normalization_run_id",
                "correction_codes",
                "standardization",
                "source_table",
            )
            datasets[spec.table_name] = {
                "format": "jsonl",
                "fields": self.canonical.schema_for(
                    fields
                ),
                "primary_key": spec.primary_key,
            }

        area_fields = (
            "record_key",
            "entity_type",
            "area_id",
            "area_name",
            "area_registered_at",
            "manager_employee_id",
            "manager_employee_name",
            "manager_department_name",
            "manager_position_name",
            "manager_hire_datetime",
            "manager_is_active",
            "parent_area_id",
            "parent_area_name",
            "source_record_id",
            "dataset_id",
            "normalization_run_id",
            "correction_codes",
            "standardization",
        )
        datasets["gold_area_dataset"] = {
            "format": "jsonl",
            "purpose": "area·manager·parent 관계를 평탄화한 AI Ready 주 데이터셋",
            "fields": self.canonical.schema_for(
                area_fields
            ),
            "primary_key": "area_id",
        }
        datasets["rejected_records"] = {
            "format": "jsonl",
            "purpose": "Gold 최종 검증 실패 행과 오류 사유",
        }
        return {
            "schema_version": GOLD_SCHEMA_VERSION,
            "record_format": "JSON Lines (UTF-8)",
            "canonical_contract": self.canonical.description,
            "datasets": datasets,
        }

    def _build_manifest(
        self,
        *,
        report: dict[str, Any],
        source_schema: dict[str, Any],
        accepted_by_entity: dict[str, list[dict[str, Any]]],
        area_dataset: list[dict[str, Any]],
        rejected_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        outputs: list[dict[str, Any]] = [
            {
                "path": "source_schema.json",
                "kind": "source_schema",
            },
            {
                "path": "schema.json",
                "kind": "release_schema",
            },
            {
                "path": "validation_report.json",
                "kind": "validation_packet",
            },
            {
                "path": "data_dictionary.md",
                "kind": "documentation",
            },
            {
                "path": "README.md",
                "kind": "documentation",
            },
            {
                "path": "release_notes.md",
                "kind": "documentation",
            },
        ]
        for spec in self.table_specs:
            outputs.append(
                {
                    "path": f"data/{spec.table_name}.jsonl",
                    "kind": "entity_snapshot",
                    "row_count": len(accepted_by_entity[spec.entity_type]),
                }
            )
        outputs.extend(
            [
                {
                    "path": "data/gold_area_dataset.jsonl",
                    "kind": "primary_dataset",
                    "row_count": len(area_dataset),
                },
                {
                    "path": "data/rejected_records.jsonl",
                    "kind": "quarantine",
                    "row_count": len(rejected_records),
                },
            ]
        )
        return {
            "package_type": "gold_dataset_release",
            "schema_version": GOLD_SCHEMA_VERSION,
            "release_version": self.release_version,
            "run_id": self.run_id,
            "created_at_utc": _now_utc(),
            "source": {
                "type": "sqlite",
                "database": str(self.database_path.resolve()),
                "tables": list(source_schema),
            },
            "canonical_contract": {
                "name": self.canonical.name,
                "schema_version": self.canonical.schema_version,
                "rules_file": str(self.rules_path.resolve()),
            },
            "validation": {
                "status": report["status"],
                "release_ready": report["release_ready"],
                "report": "validation_report.json",
            },
            "outputs": outputs,
            "notes": [
                "동일한 input snapshot과 run_id로 재실행하면 JSONL 파일을 append하지 않고 재생성합니다.",
                "checksum 파일은 생성하지 않습니다.",
            ],
        }

    def _write_package(
        self,
        *,
        source_schema: dict[str, Any],
        schema: dict[str, Any],
        report: dict[str, Any],
        manifest: dict[str, Any],
        accepted_by_entity: dict[str, list[dict[str, Any]]],
        area_dataset: list[dict[str, Any]],
        rejected_records: list[dict[str, Any]],
        data_dictionary: str,
        readme: str,
        release_notes: str,
    ) -> None:
        data_dir = self.output_dir / "data"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        _write_json(self.output_dir / "source_schema.json", source_schema)
        _write_json(self.output_dir / "schema.json", schema)
        _write_json(self.output_dir / "validation_report.json", report)
        _write_json(self.output_dir / "manifest.json", manifest)
        _write_text(self.output_dir / "data_dictionary.md", data_dictionary)
        _write_text(self.output_dir / "README.md", readme)
        _write_text(self.output_dir / "release_notes.md", release_notes)

        for spec in self.table_specs:
            _write_jsonl(
                data_dir / f"{spec.table_name}.jsonl",
                accepted_by_entity[spec.entity_type],
            )
        _write_jsonl(data_dir / "gold_area_dataset.jsonl", area_dataset)
        _write_jsonl(data_dir / "rejected_records.jsonl", rejected_records)

    def _build_data_dictionary(self) -> str:
        lines = [
            "# Gold Dataset Data Dictionary",
            "",
            f"- Schema version: {GOLD_SCHEMA_VERSION}",
            f"- Canonical contract: {self.canonical.name}",
            "- Datetime output timezone: Asia/Seoul",
            "",
            "## Gold area dataset",
            "",
            "| Field | Type | Nullable | Canonical rule |",
            "|---|---|---:|---|",
        ]
        area_fields = (
            "area_id",
            "area_name",
            "area_registered_at",
            "manager_employee_id",
            "manager_employee_name",
            "manager_department_name",
            "manager_position_name",
            "manager_hire_datetime",
            "manager_is_active",
            "parent_area_id",
            "parent_area_name",
            "source_record_id",
            "dataset_id",
            "normalization_run_id",
            "correction_codes",
            "standardization",
        )
        for field in area_fields:
            rule = self.canonical._rules.get(field)
            if rule is None:
                field_type = "array" if field in {"correction_codes", "standardization"} else "string"
                nullable = "예"
                rule_id = "Gold derived"
            else:
                field_type = {
                    "text": "string",
                    "code": "string",
                    "enum": "string",
                    "boolean": "boolean",
                    "datetime": "string (ISO 8601)",
                }.get(rule.kind, rule.kind)
                nullable = "아니오" if rule.required else "예"
                rule_id = rule.rule_id
            lines.append(f"| {field} | {field_type} | {nullable} | {rule_id} |")

        lines.extend(
            [
                "",
                "## Entity snapshot tables",
                "",
                "| Source table | Gold file | Primary key |",
                "|---|---|---|",
            ]
        )
        for spec in self.table_specs:
            lines.append(
                f"| {spec.table_name} | data/{spec.table_name}.jsonl | "
                f"{spec.primary_key} |"
            )
        return "\n".join(lines) + "\n"

    def _build_readme(self, report: dict[str, Any]) -> str:
        return f"""# Gold Dataset Release

## Release information

- Release version: {self.release_version}
- Run ID: {self.run_id}
- Status: {report["status"]}
- Release ready: {str(report["release_ready"]).lower()}
- Canonical contract: {self.canonical.name}

## Main dataset

data/gold_area_dataset.jsonl은 SQLite Silver의 silver_area를 중심으로
직원과 상위영역 정보를 조인한 AI Ready 평탄화 데이터셋입니다.

## Supporting files

- data/silver_*.jsonl: Silver entity별 검증 통과 snapshot
- data/rejected_records.jsonl: Gold 최종 검증 실패 행과 사유
- schema.json: 배포 데이터 스키마
- validation_report.json: 필수값·canonical 규칙·중복·FK 검증 결과
- source_schema.json: 입력 SQLite 실제 스키마와 row count
- manifest.json: 실행·입출력·버전 메타데이터
- data_dictionary.md: 필드 설명

## Re-run behavior

같은 SQLite snapshot과 같은 run_id, output 경로로 다시 실행하면 기존 JSONL에
행을 추가하지 않고 생성 파일을 재작성합니다. 따라서 재실행 시 중복 누적을
방지할 수 있습니다.

이 패키지는 checksum 파일을 포함하지 않습니다.
"""

    def _build_release_notes(self, report: dict[str, Any]) -> str:
        return f"""# Release Notes

## {self.release_version}

- Source: SQLite Silver tables
- Canonical validation: {self.canonical.name}
- Status: {report["status"]}
- Accepted rows: {report["counts"]["accepted_total"]}
- Rejected rows: {report["counts"]["rejected_total"]}
- Primary dataset rows: {report["counts"]["gold_area_dataset"]}
- Checksum: 미생성
"""


def run_gold_pipeline(
    database_path: str | Path,
    output_dir: str | Path,
    *,
    release_version: str = "0.1.0",
    run_id: str | None = None,
    rules_path: str | Path = DEFAULT_RULES_PATH,
) -> GoldRunResult:
    """SQLite Silver → Gold release package 실행 편의 함수."""

    return GoldPipeline(
        database_path=database_path,
        output_dir=output_dir,
        release_version=release_version,
        run_id=run_id,
        rules_path=rules_path,
    ).run()


def _status(source_total: int, rejected_total: int) -> str:
    if rejected_total == 0:
        return "SUCCESS"
    if source_total > rejected_total:
        return "PARTIAL_SUCCESS"
    return "FAILED"


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"gold-{timestamp}-{uuid4().hex[:8]}"


def _now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_json_column(
    column: str,
    value: Any,
) -> tuple[Any, dict[str, str] | None]:
    if value is None:
        return [], None
    if isinstance(value, (list, dict)):
        return value, None
    if not isinstance(value, str):
        return None, {
            "field": column,
            "error_code": "TYPE_MISMATCH",
            "message": "SQLite JSON TEXT 컬럼이 문자열이 아닙니다.",
        }
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        return None, {
            "field": column,
            "error_code": "JSON_PARSE_FAILED",
            "message": f"JSON metadata를 해석할 수 없습니다: {error.msg}",
        }
    if not isinstance(parsed, (list, dict)):
        return None, {
            "field": column,
            "error_code": "TYPE_MISMATCH",
            "message": "JSON metadata는 array 또는 object여야 합니다.",
        }
    return parsed, None


def _normalization_error_code(column: str) -> str:
    if column.endswith("datetime") or column.endswith("registered_at"):
        return "DATETIME_PARSE_FAILED"
    return "DOMAIN_UNKNOWN"


def _dedupe_issues(issues: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (
            str(issue.get("field", "")),
            str(issue.get("error_code", "")),
            str(issue.get("message", "")),
        )
        if key not in seen:
            result.append(issue)
            seen.add(key)
    return result


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    content = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    _write_text(path, content)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)
