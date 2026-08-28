from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mongo_pipeline.loggers import create_stage_loggers  # noqa: E402
from mongo_pipeline.pipeline import Pipeline  # noqa: E402
from mongo_pipeline.rule_standardizer import (  # noqa: E402
    YamlRuleStandardizer,
)
from mongo_pipeline.silver import (  # noqa: E402
    split_silver_models,
    validate_silver_models,
)
from mongo_pipeline.sinks import JsonlSink  # noqa: E402
from mongo_pipeline.sources import IterableSource  # noqa: E402
from mongo_pipeline.validators import build_default_validators  # noqa: E402


RULES_PATH = PROJECT_ROOT / "rules" / "silver_canonical.yaml"


class SilverContractTest(unittest.TestCase):
    def test_canonical_rule_maps_and_splits_four_models(self) -> None:
        standardizer = YamlRuleStandardizer.from_file(RULES_PATH)
        document = standardizer.standardize(_raw_document("R-001"))

        self.assertEqual(document["employee_id"], "EMP002471")
        self.assertEqual(document["area_id"], "BIZ02168")
        self.assertIs(document["is_active"], True)
        self.assertEqual(document["top_area_level"], "TOP")
        self.assertEqual(document["hire_datetime"], "2009-03-19T17:53:58+09:00")
        self.assertEqual(document["source_record_id"], "R-001")
        self.assertEqual(document["dataset_id"], "DATA-001")
        self.assertTrue(
            set(document["correction_codes"]).issubset(
                {
                    "ACTIVE_STATUS_NORMALIZED",
                    "CODE_FORMAT_NORMALIZED",
                    "DATETIME_FORMAT_NORMALIZED",
                    "WHITESPACE_NORMALIZED",
                    "UNICODE_NORMALIZED",
                    "TAB_CHARACTER_ERROR",
                    "TOP_LEVEL_NORMALIZED",
                    "DATE_CONFLICT",
                }
            )
        )

        models = split_silver_models(document)
        self.assertEqual(
            set(models),
            {
                "silver_employee",
                "silver_area",
                "silver_parent_area",
                "silver_top_area_detail",
            },
        )
        self.assertEqual(validate_silver_models([document]), {})

    def test_legacy_status_and_top_level_aliases_are_normalized(self) -> None:
        standardizer = YamlRuleStandardizer.from_file(RULES_PATH)
        for field, value, expected in (
            ("mgr_act_yn", "1", True),
            ("top_area_lvl", "L1", "TOP"),
        ):
            with self.subTest(field=field):
                document = _raw_document("R-002")
                document[field] = value
                standardized = standardizer.standardize(document)
                output_field = {
                    "mgr_act_yn": "is_active",
                    "top_area_lvl": "top_area_level",
                }[field]
                self.assertEqual(standardized[output_field], expected)

    def test_compact_fourteen_digit_datetime_is_normalized(self) -> None:
        standardizer = YamlRuleStandardizer.from_file(RULES_PATH)
        document = _raw_document("R-003")
        document["mgr_hire_dtm"] = "20090319175358"

        standardized = standardizer.standardize(document)

        self.assertEqual(
            standardized["hire_datetime"],
            "2009-03-19T17:53:58+09:00",
        )

    def test_pk_and_fk_conflicts_are_returned_by_source_row(self) -> None:
        standardizer = YamlRuleStandardizer.from_file(RULES_PATH)
        first = standardizer.standardize(_raw_document("R-003"))
        second_raw = _raw_document("R-004")
        second_raw["employee_name"] = "다른 이름"
        second_raw["manager_employee_id"] = "EMP999999"
        second = standardizer.standardize(second_raw)

        issues = validate_silver_models([first, second])
        self.assertIn(0, issues)
        self.assertIn(1, issues)
        codes = {issue.error_code for row in issues.values() for issue in row}
        self.assertIn("PK_DUPLICATE", codes)
        self.assertIn("FK_ORPHAN", codes)

    def test_pipeline_writes_model_files_and_jsonl_quality_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            log_directory = root / "logs"
            standardize_logger, validation_logger = create_stage_loggers(
                log_directory,
                logging.INFO,
            )
            try:
                result = Pipeline(
                    source=IterableSource([_raw_document("R-005")]),
                    standardizer=YamlRuleStandardizer.from_file(RULES_PATH),
                    validators=build_default_validators([], {}),
                    sink=JsonlSink(root / "output", "silver-run"),
                    run_id="silver-run",
                    standardize_logger=standardize_logger,
                    validation_logger=validation_logger,
                ).run()
            finally:
                _close_logger(standardize_logger)
                _close_logger(validation_logger)

            self.assertEqual(result.report["status"], "SUCCESS")
            self.assertEqual(result.report["restoration"]["restoration_rate"], 1.0)
            run_directory = root / "output" / "silver-run"
            for model_name in (
                "silver_employee",
                "silver_area",
                "silver_parent_area",
                "silver_top_area_detail",
            ):
                self.assertTrue((run_directory / f"{model_name}.jsonl").exists())

            pipeline_log = _read_jsonl(log_directory / "pipeline.jsonl")
            quality_log = _read_jsonl(log_directory / "quality.jsonl")
            restoration_log = _read_jsonl(log_directory / "restoration.jsonl")
            self.assertTrue(pipeline_log)
            self.assertTrue(quality_log)
            self.assertEqual(restoration_log[-1]["status"], "success")
            for event in [*pipeline_log, *quality_log, *restoration_log]:
                self.assertEqual(event["run_id"], "silver-run")
                self.assertIn(event["level"], {"INFO", "WARN", "ERROR"})
                self.assertIn(
                    event["status"], {"success", "partial_failure", "failed"}
                )
                self.assertEqual(
                    event["input_count"],
                    event["success_count"]
                    + event["failure_count"]
                    + event["quarantine_count"],
                )


def _raw_document(source_record_id: str) -> dict:
    return {
        "dataset_id": "DATA-001",
        "source": {"record_id": source_record_id},
        "mgr_no": "EMP 002471",
        "mgr_nm": "임 예준",
        "mgr_dept_nm": "인사팀",
        "mgr_pos_nm": "사 원",
        "mgr_hire_dtm": "2009/03/19 17:53:58",
        "mgr_act_yn": "YES",
        "area_no": "biz-02168",
        "area_nm": "영업부",
        "area_reg_dtm": "2016-05-26 11:06:42",
        "p_area_no": "biz-00241",
        "p_area_nm": "본부",
        "top_area_no": "biz-00241",
        "top_area_nm": "본부",
        "top_area_lvl": "TOP_LEVEL",
        "top_area_reg_dtm": "2026-04-09 02:10:31",
        "_runtime": {"normalization_run_id": "RUN-001"},
    }


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _close_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
