from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SourceSchemaError(RuntimeError):
    """Gold 입력으로 사용할 SQLite 스키마가 계약과 다를 때 발생한다."""


@dataclass(frozen=True)
class TableSpec:
    """Silver RDB 테이블과 Gold 변환에 필요한 컬럼 계약."""

    table_name: str
    entity_type: str
    primary_key: str
    metadata_columns: tuple[str, ...]
    data_columns: tuple[str, ...]
    required_columns: tuple[str, ...]
    json_columns: tuple[str, ...] = ()
    datetime_columns: tuple[str, ...] = ()
    boolean_columns: tuple[str, ...] = ()

    @property
    def all_columns(self) -> tuple[str, ...]:
        return self.metadata_columns + self.data_columns


_METADATA_COLUMNS = (
    "source_record_id",
    "dataset_id",
    "normalization_run_id",
    "correction_codes",
    "_standardization",
)


DEFAULT_SILVER_TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        table_name="silver_employee",
        entity_type="employee",
        primary_key="employee_id",
        metadata_columns=_METADATA_COLUMNS,
        data_columns=(
            "employee_id",
            "employee_name",
            "department_name",
            "position_name",
            "hire_datetime",
            "is_active",
        ),
        required_columns=(
            *_METADATA_COLUMNS[:3],
            "employee_id",
            "employee_name",
            "department_name",
            "position_name",
            "hire_datetime",
            "is_active",
        ),
        json_columns=("correction_codes", "_standardization"),
        datetime_columns=("hire_datetime",),
        boolean_columns=("is_active",),
    ),
    TableSpec(
        table_name="silver_parent_area",
        entity_type="parent_area",
        primary_key="parent_area_id",
        metadata_columns=_METADATA_COLUMNS,
        data_columns=("parent_area_id", "parent_area_name"),
        required_columns=(
            *_METADATA_COLUMNS[:3],
            "parent_area_id",
            "parent_area_name",
        ),
        json_columns=("correction_codes", "_standardization"),
    ),
    TableSpec(
        table_name="silver_top_area_detail",
        entity_type="top_area",
        primary_key="top_area_id",
        metadata_columns=_METADATA_COLUMNS,
        data_columns=(
            "top_area_id",
            "top_area_name",
            "top_area_level",
            "top_area_registered_at",
        ),
        required_columns=(
            *_METADATA_COLUMNS[:3],
            "top_area_id",
            "top_area_name",
            "top_area_level",
            "top_area_registered_at",
        ),
        json_columns=("correction_codes", "_standardization"),
        datetime_columns=("top_area_registered_at",),
    ),
    TableSpec(
        table_name="silver_area",
        entity_type="area",
        primary_key="area_id",
        metadata_columns=_METADATA_COLUMNS,
        data_columns=(
            "area_id",
            "area_name",
            "area_registered_at",
            "manager_employee_id",
            "parent_area_id",
        ),
        required_columns=(
            *_METADATA_COLUMNS[:3],
            "area_id",
            "area_name",
            "area_registered_at",
            "manager_employee_id",
        ),
        json_columns=("correction_codes", "_standardization"),
        datetime_columns=("area_registered_at",),
    ),
)


def _quote_identifier(identifier: str) -> str:
    """SQLite identifier를 안전하게 quote한다."""

    return '"' + identifier.replace('"', '""') + '"'


class SQLiteGoldSource:
    """Django ORM에 의존하지 않고 Silver SQLite 테이블을 읽는 입력 어댑터."""

    def __init__(
        self,
        database_path: str | Path,
        table_specs: tuple[TableSpec, ...] = DEFAULT_SILVER_TABLES,
    ) -> None:
        self.database_path = Path(database_path)
        self.table_specs = table_specs
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> SQLiteGoldSource:
        if not self.database_path.is_file():
            raise FileNotFoundError(
                f"SQLite 데이터베이스 파일이 없습니다: {self.database_path}"
            )
        self.connection = sqlite3.connect(str(self.database_path))
        self.connection.row_factory = sqlite3.Row
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("SQLiteGoldSource는 with 블록 안에서 사용해야 합니다.")
        return self.connection

    def validate_schema(self) -> dict[str, Any]:
        """필수 테이블·컬럼을 확인하고 실제 SQLite 스키마를 반환한다."""

        connection = self._require_connection()
        snapshot: dict[str, Any] = {}

        for spec in self.table_specs:
            table_info = connection.execute(
                f"PRAGMA table_info({_quote_identifier(spec.table_name)})"
            ).fetchall()
            if not table_info:
                raise SourceSchemaError(
                    f"필수 Silver 테이블이 없습니다: {spec.table_name}"
                )

            actual_columns = {str(row["name"]): row for row in table_info}
            missing_columns = [
                column
                for column in spec.all_columns
                if column not in actual_columns
            ]
            if missing_columns:
                raise SourceSchemaError(
                    f"{spec.table_name}에 필수 컬럼이 없습니다: "
                    + ", ".join(missing_columns)
                )
            if not actual_columns[spec.primary_key]["pk"]:
                raise SourceSchemaError(
                    f"{spec.table_name}.{spec.primary_key}가 PRIMARY KEY로 선언되지 않았습니다."
                )

            row_count = connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(spec.table_name)}"
            ).fetchone()[0]
            snapshot[spec.table_name] = {
                "entity_type": spec.entity_type,
                "primary_key": spec.primary_key,
                "expected_columns": list(spec.all_columns),
                "columns": [
                    {
                        "name": str(row["name"]),
                        "declared_type": str(row["type"] or ""),
                        "not_null": bool(row["notnull"]),
                        "primary_key": bool(row["pk"]),
                    }
                    for row in table_info
                ],
                "row_count": int(row_count),
            }

        return snapshot

    def read_rows(self, spec: TableSpec) -> list[dict[str, Any]]:
        """테이블을 PK 순서로 읽어 실행 결과가 결정적이 되도록 한다."""

        connection = self._require_connection()
        columns = ", ".join(_quote_identifier(column) for column in spec.all_columns)
        table_name = _quote_identifier(spec.table_name)
        primary_key = _quote_identifier(spec.primary_key)
        rows = connection.execute(
            f"SELECT {columns} FROM {table_name} ORDER BY {primary_key}"
        ).fetchall()
        return [dict(row) for row in rows]
