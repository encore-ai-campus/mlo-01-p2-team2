from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mongo_pipeline.config import AppConfig  # noqa: E402
from mongo_pipeline.pipeline import Pipeline  # noqa: E402
from mongo_pipeline.rule_standardizer import YamlRuleStandardizer  # noqa: E402
from mongo_pipeline.sinks import JsonlSink  # noqa: E402
from mongo_pipeline.sources import YamlFileSource  # noqa: E402
from mongo_pipeline.standardizers import StandardizationError  # noqa: E402
from mongo_pipeline.validators import build_default_validators  # noqa: E402
from mongo_pipeline.yaml_support import YamlLoadError  # noqa: E402


RULES_PATH = PROJECT_ROOT / "rules" / "legacy_org.yaml"
COLUMN_CONTRACT_PATH = PROJECT_ROOT / "rules" / "legacy_org_flat.yaml"
INPUT_PATH = PROJECT_ROOT / "examples" / "legacy_input.yaml"


class YamlRuleStandardizerTest(unittest.TestCase):
    def test_column_contract_uses_headers_only_and_preserves_mongo_id(self) -> None:
        document = {
            "_id": "mongo-1",
            "id": "business-1",
            "dataset_id": "dataset-1",
            "record_id": 10,
            "extra_sample_value": "must not be projected",
            "payload": {"mgr_nm": "not a rule"},
        }

        result = YamlRuleStandardizer.from_file(COLUMN_CONTRACT_PATH).standardize(
            document
        )

        self.assertEqual(result["_id"], "mongo-1")
        self.assertEqual(result["id"], "business-1")
        self.assertEqual(result["dataset_id"], "dataset-1")
        self.assertEqual(result["record_id"], 10)
        self.assertIsNone(result["mgr_nm"])
        self.assertNotIn("extra_sample_value", result)
        self.assertNotIn("payload", result)

    def test_legacy_rule_file_normalizes_fields_and_generates_stable_id(self) -> None:
        document = {
            "mgr_nm": "　임예준　",
            "area_nm": "　물류부 48　",
            "top_area_lvl": "TOP LEVEL",
            "area_no": "biz-02168",
            "mgr_pos_nm": "사 원",
            "top_area_nm": "R &D",
            "mgr_no": "EMP 002471",
            "p_area_nm": "　R&D　",
            "area_reg_dtm": "20160526200642",
            "mgr_dept_nm": "R &D팀",
            "top_area_no": "BIZ-00241",
            "mgr_act_yn": "YES",
            "p_area_no": "biz_00241",
            "top_area_reg_dtm": "2026-04-09 02:10:31",
            "mgr_hire_dtm": "2009/03/19 17:53:58",
        }
        standardizer = YamlRuleStandardizer.from_file(RULES_PATH)

        first = standardizer.standardize(document)
        second = standardizer.standardize(document)

        self.assertEqual(first["area_no"], "BIZ_02168")
        self.assertEqual(first["mgr_no"], "EMP002471")
        self.assertEqual(first["mgr_nm"], "임예준")
        self.assertEqual(first["mgr_pos_nm"], "사원")
        self.assertEqual(first["top_area_lvl"], "L1")
        self.assertEqual(first["top_area_nm"], "R&D")
        self.assertEqual(first["mgr_act_yn"], "ACTIVE")
        self.assertEqual(first["area_reg_dtm"], "2016-05-26T11:06:42Z")
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertNotIn("release_at", first)
        self.assertTrue(
            any(event["rule_id"] == "META-001" for event in first["_standardization"])
        )

    def test_optional_invalid_values_become_null_with_audit_warning(self) -> None:
        document = _minimal_document()
        document.update(
            {
                "mgr_nm": "오류값",
                "mgr_no": "UNKNOWN",
                "mgr_hire_dtm": "9999-99-99 99:99:99",
            }
        )

        result = YamlRuleStandardizer.from_file(RULES_PATH).standardize(document)

        self.assertIsNone(result["mgr_nm"])
        self.assertIsNone(result["mgr_no"])
        self.assertIsNone(result["mgr_hire_dtm"])
        nullified = {
            event["field"]
            for event in result["_standardization"]
            if event["action"] == "NULLIFIED"
        }
        self.assertEqual(nullified, {"mgr_nm", "mgr_no", "mgr_hire_dtm"})

    def test_required_invalid_code_is_rejected_with_rule_id(self) -> None:
        document = _minimal_document()
        document["area_no"] = "BROKEN"

        with self.assertRaisesRegex(StandardizationError, "ID-001-area"):
            YamlRuleStandardizer.from_file(RULES_PATH).standardize(document)

    def test_cross_field_mismatch_is_rejected(self) -> None:
        document = _minimal_document()
        document["p_area_no"] = "BIZ_00002"

        with self.assertRaisesRegex(StandardizationError, "XFIELD-001"):
            YamlRuleStandardizer.from_file(RULES_PATH).standardize(document)

    def test_suspicious_area_name_is_kept_with_warning(self) -> None:
        document = _minimal_document()
        document["area_nm"] = "고객서비스서비스 11"

        result = YamlRuleStandardizer.from_file(RULES_PATH).standardize(document)

        self.assertEqual(result["area_nm"], "고객서비스서비스 11")
        self.assertTrue(
            any(
                event["rule_id"] == "TXT-003" and event["action"] == "WARNING"
                for event in result["_standardization"]
            )
        )

    def test_duplicate_yaml_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "duplicate.yaml"
            path.write_text(
                "schema_version: 1\nname: test\nname: duplicate\nfields: {}\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(YamlLoadError, "중복 YAML 키"):
                YamlRuleStandardizer.from_file(path)

    def test_yaml_file_source_and_pipeline_run_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            pipeline = Pipeline(
                source=YamlFileSource(INPUT_PATH),
                standardizer=YamlRuleStandardizer.from_file(RULES_PATH),
                validators=build_default_validators([], {}),
                sink=JsonlSink(temp_directory, "yaml-run"),
                run_id="yaml-run",
            )

            result = pipeline.run()

            self.assertEqual(result.report["status"], "FAILED")
            self.assertEqual(result.report["counts"]["extracted"], 3)
            self.assertEqual(result.report["counts"]["accepted"], 2)
            self.assertEqual(result.report["counts"]["rejected"], 1)
            self.assertEqual(result.report["standardization"]["name"], "legacy-org-v0.1")

    def test_config_resolves_relative_rule_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "source": {
                            "uri_env": "MONGODB_URI",
                            "database": "sample",
                            "collection": "raw",
                        },
                        "standardization": {"rules_file": "rules/legacy.yaml"},
                    }
                ),
                encoding="utf-8",
            )

            config = AppConfig.from_file(config_path)

        self.assertEqual(config.standardization.rules_file, root / "rules" / "legacy.yaml")


def _minimal_document() -> dict:
    return {
        "area_nm": "법무팀",
        "top_area_lvl": "L1",
        "area_no": "BIZ_00001",
        "top_area_nm": "법무",
        "top_area_no": "BIZ_00001",
        "p_area_nm": "법무",
        "p_area_no": "BIZ_00001",
    }


if __name__ == "__main__":
    unittest.main()
