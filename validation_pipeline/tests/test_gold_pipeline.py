from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from gold_pipeline.pipeline import run_gold_pipeline  # noqa: E402


RULES_PATH = PROJECT_ROOT / "rules" / "silver_canonical.yaml"


class GoldPipelineTest(unittest.TestCase):
    def test_sqlite_silver_is_validated_and_packaged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            database_path = root / "silver.sqlite3"
            output_dir = root / "release"
            _create_silver_database(database_path)

            result = run_gold_pipeline(
                database_path,
                output_dir,
                release_version="0.1.0",
                run_id="gold-test-001",
                rules_path=RULES_PATH,
            )

            self.assertTrue(result.release_ready)
            self.assertEqual(result.report["status"], "SUCCESS")
            self.assertEqual(result.report["counts"]["source_total"], 4)
            self.assertEqual(result.report["counts"]["accepted_total"], 4)
            self.assertEqual(result.report["counts"]["rejected_total"], 0)
            self.assertEqual(result.report["counts"]["gold_area_dataset"], 1)
            self.assertEqual(
                result.report["rules"]["name"],
                "silver-canonical-v1.0",
            )

            area_rows = _read_jsonl(output_dir / "data/gold_area_dataset.jsonl")
            self.assertEqual(len(area_rows), 1)
            self.assertEqual(area_rows[0]["area_id"], "BIZ00001")
            self.assertEqual(area_rows[0]["manager_employee_name"], "홍길동")
            self.assertEqual(
                area_rows[0]["area_registered_at"],
                "2024-01-02T03:04:05+09:00",
            )
            self.assertTrue((output_dir / "schema.json").exists())
            self.assertTrue((output_dir / "validation_report.json").exists())
            self.assertTrue((output_dir / "manifest.json").exists())
            self.assertFalse((output_dir / "checksums.sha256").exists())
            schema = json.loads((output_dir / "schema.json").read_text(encoding="utf-8"))
            employee_fields = {
                field["name"]
                for field in schema["datasets"]["silver_employee"]["fields"]
            }
            self.assertIn("source_table", employee_fields)
            self.assertEqual(
                next(
                    field["type"]
                    for field in schema["datasets"]["silver_employee"]["fields"]
                    if field["name"] == "correction_codes"
                ),
                "array",
            )

    def test_same_run_id_rewrites_instead_of_appending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            database_path = root / "silver.sqlite3"
            output_dir = root / "release"
            _create_silver_database(database_path)

            for _ in range(2):
                result = run_gold_pipeline(
                    database_path,
                    output_dir,
                    release_version="0.1.0",
                    run_id="gold-repeatable",
                    rules_path=RULES_PATH,
                )
                self.assertTrue(result.release_ready)

            for filename, expected_count in (
                ("silver_employee.jsonl", 1),
                ("silver_area.jsonl", 1),
                ("silver_parent_area.jsonl", 1),
                ("silver_top_area_detail.jsonl", 1),
                ("gold_area_dataset.jsonl", 1),
                ("rejected_records.jsonl", 0),
            ):
                self.assertEqual(
                    len(_read_jsonl(output_dir / "data" / filename)),
                    expected_count,
                )

    def test_missing_manager_is_rejected_and_release_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            database_path = root / "silver.sqlite3"
            output_dir = root / "release"
            _create_silver_database(
                database_path,
                manager_employee_id="EMP999999",
            )

            result = run_gold_pipeline(
                database_path,
                output_dir,
                release_version="0.1.0",
                run_id="gold-invalid-fk",
                rules_path=RULES_PATH,
            )

            self.assertFalse(result.release_ready)
            self.assertEqual(result.report["status"], "PARTIAL_SUCCESS")
            self.assertEqual(result.report["quality"]["issue_counts"]["FK_ORPHAN"], 1)
            self.assertEqual(
                len(_read_jsonl(output_dir / "data/rejected_records.jsonl")),
                1,
            )
            self.assertEqual(
                len(_read_jsonl(output_dir / "data/gold_area_dataset.jsonl")),
                0,
            )


def _create_silver_database(
    database_path: Path,
    *,
    manager_employee_id: str = "EMP000001",
) -> None:
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE silver_employee (
            source_record_id VARCHAR(255) NOT NULL,
            dataset_id VARCHAR(255) NOT NULL,
            normalization_run_id VARCHAR(255) NOT NULL,
            correction_codes TEXT NOT NULL,
            _standardization TEXT NOT NULL,
            employee_id VARCHAR(9) PRIMARY KEY,
            employee_name VARCHAR(255) NOT NULL,
            department_name VARCHAR(255) NOT NULL,
            position_name VARCHAR(255) NOT NULL,
            hire_datetime DATETIME NOT NULL,
            is_active BOOLEAN NOT NULL
        );
        CREATE TABLE silver_parent_area (
            source_record_id VARCHAR(255) NOT NULL,
            dataset_id VARCHAR(255) NOT NULL,
            normalization_run_id VARCHAR(255) NOT NULL,
            correction_codes TEXT NOT NULL,
            _standardization TEXT NOT NULL,
            parent_area_id VARCHAR(8) PRIMARY KEY,
            parent_area_name VARCHAR(255) NOT NULL
        );
        CREATE TABLE silver_top_area_detail (
            source_record_id VARCHAR(255) NOT NULL,
            dataset_id VARCHAR(255) NOT NULL,
            normalization_run_id VARCHAR(255) NOT NULL,
            correction_codes TEXT NOT NULL,
            _standardization TEXT NOT NULL,
            top_area_id VARCHAR(8) PRIMARY KEY,
            top_area_name VARCHAR(255) NOT NULL,
            top_area_level VARCHAR(32) NOT NULL,
            top_area_registered_at DATETIME NOT NULL
        );
        CREATE TABLE silver_area (
            source_record_id VARCHAR(255) NOT NULL,
            dataset_id VARCHAR(255) NOT NULL,
            normalization_run_id VARCHAR(255) NOT NULL,
            correction_codes TEXT NOT NULL,
            _standardization TEXT NOT NULL,
            area_id VARCHAR(8) PRIMARY KEY,
            area_name VARCHAR(255) NOT NULL,
            area_registered_at DATETIME NOT NULL,
            manager_employee_id VARCHAR(9) NOT NULL,
            parent_area_id VARCHAR(8)
        );
        """
    )
    metadata = (
        "SRC-001",
        "DATA-001",
        "RUN-001",
        json.dumps(["CODE_FORMAT_NORMALIZED"]),
        json.dumps([{"rule_id": "TEST", "action": "NORMALIZED"}]),
    )
    connection.execute(
        "INSERT INTO silver_employee VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*metadata, "EMP000001", "홍길동", "개발팀", "과장", "2020-01-01 09:00:00", 1),
    )
    connection.execute(
        "INSERT INTO silver_parent_area VALUES (?, ?, ?, ?, ?, ?, ?)",
        (*metadata, "BIZ00002", "본부"),
    )
    connection.execute(
        "INSERT INTO silver_top_area_detail VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            *metadata,
            "BIZ00003",
            "최상위",
            "TOP",
            "2024-01-01 00:00:00",
        ),
    )
    connection.execute(
        "INSERT INTO silver_area VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            *metadata,
            "BIZ00001",
            "영업부",
            "2024-01-02 03:04:05",
            manager_employee_id,
            "BIZ00002",
        ),
    )
    connection.commit()
    connection.close()


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
