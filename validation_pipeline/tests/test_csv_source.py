from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mongo_pipeline.config import AppConfig  # noqa: E402
from mongo_pipeline.bronze import verify_manifest_file  # noqa: E402
from mongo_pipeline.pipeline import Pipeline  # noqa: E402
from mongo_pipeline.rule_standardizer import YamlRuleStandardizer  # noqa: E402
from mongo_pipeline.sinks import JsonlSink  # noqa: E402
from mongo_pipeline.sources import CsvSource  # noqa: E402
from mongo_pipeline.validators import build_default_validators  # noqa: E402


RULES_PATH = PROJECT_ROOT / "rules" / "silver_canonical.yaml"


class CsvSourceTest(unittest.TestCase):
    def test_dotted_headers_are_nested_and_source_metadata_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "records.csv"
            path.write_text(
                "dataset_id,source.record_id,source.source_row_no,source.payload.mgr_no\n"
                "DATA-001,R-001,18,EMP 002471\n",
                encoding="utf-8-sig",
            )

            documents = list(CsvSource(path).read())

        self.assertEqual(documents[0]["dataset_id"], "DATA-001")
        self.assertEqual(documents[0]["source"]["record_id"], "R-001")
        self.assertEqual(documents[0]["source"]["source_row_no"], "18")
        self.assertEqual(documents[0]["source"]["payload"]["mgr_no"], "EMP 002471")

    def test_missing_source_id_uses_file_and_row_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "records.csv"
            path.write_text("dataset_id,area_no\nDATA-001,biz-00001\n", encoding="utf-8")

            document = next(CsvSource(path).read())

        self.assertEqual(document["source"]["record_id"], "records.csv:row:2")
        self.assertEqual(document["source"]["source_row_no"], 2)

    def test_bad_row_is_quarantined_or_can_fail_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "records.csv"
            path.write_text(
                "dataset_id,area_no\nDATA-001,biz-00001,extra\n",
                encoding="utf-8",
            )

            documents = list(CsvSource(path).read())
            self.assertEqual(documents[0]["_source_error"]["type"], "row_shape_error")
            self.assertEqual(documents[0]["_source_error"]["source_line_no"], 2)

            with self.assertRaisesRegex(ValueError, "CSV 2번째 줄"):
                list(CsvSource(path, continue_on_parse_error=False).read())

    def test_csv_runs_through_canonical_silver_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            path = root / "silver_input.csv"
            path.write_text(
                "dataset_id,source_record_id,area_no,area_nm,p_area_no,p_area_nm,"
                "top_area_no,top_area_nm,top_area_lvl,mgr_no,mgr_nm,mgr_dept_nm,"
                "mgr_pos_nm,mgr_hire_dtm,mgr_act_yn,area_reg_dtm,top_area_reg_dtm\n"
                "DATA-001,R-001,biz-02168,영업부,biz-00241,본부,biz-00241,본부,"
                "TOP_LEVEL,EMP 002471,임 예준,인사팀,사 원,2009/03/19 17:53:58,YES,"
                "2016-05-26 11:06:42,2026-04-09 02:10:31\n",
                encoding="utf-8-sig",
            )

            result = Pipeline(
                source=CsvSource(path),
                standardizer=YamlRuleStandardizer.from_file(RULES_PATH),
                validators=build_default_validators([], {}),
                sink=JsonlSink(root / "output", "csv-run"),
                run_id="csv-run",
            ).run()

            report = json.loads(
                (root / "output" / "csv-run" / "report.json").read_text(
                    encoding="utf-8"
                )
            )
            manifest = json.loads(
                (root / "output" / "csv-run" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(verify_manifest_file(manifest))

        self.assertEqual(result.report["status"], "SUCCESS")
        self.assertEqual(report["source"]["type"], "csv_file")
        self.assertEqual(report["silver"]["model_counts"]["silver_employee"], 1)
        self.assertEqual(result.report["restoration"]["restoration_rate"], 1.0)
        self.assertEqual(manifest["raw_path"], str(path.resolve()))
        self.assertEqual(result.report["bronze"]["integrity_rate"], 1.0)

    def test_config_accepts_csv_format_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "source": {
                            "type": "csv",
                            "path": "records.csv",
                            "encoding": "cp949",
                            "delimiter": ";",
                            "quotechar": "'",
                            "skipinitialspace": True,
                        }
                    }
                ),
                encoding="utf-8",
            )

            config = AppConfig.from_file(config_path)

        self.assertEqual(config.source.kind, "csv")
        self.assertEqual(config.source.path, root / "records.csv")
        self.assertEqual(config.source.encoding, "cp949")
        self.assertEqual(config.source.delimiter, ";")
        self.assertEqual(config.source.quotechar, "'")
        self.assertTrue(config.source.skipinitialspace)


if __name__ == "__main__":
    unittest.main()
