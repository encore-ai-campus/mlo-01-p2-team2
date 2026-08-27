from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mongo_pipeline.config import SourceConfig  # noqa: E402
from mongo_pipeline.loggers import create_stage_loggers  # noqa: E402
from mongo_pipeline.pipeline import Pipeline  # noqa: E402
from mongo_pipeline.sinks import JsonlSink  # noqa: E402
from mongo_pipeline.sources import IterableSource, MongoSource  # noqa: E402
from mongo_pipeline.standardizers import CommonStandardizer  # noqa: E402
from mongo_pipeline.validators import build_default_validators  # noqa: E402


class PipelineTest(unittest.TestCase):
    def test_pipeline_standardizes_profiles_and_rejects_invalid_document(self) -> None:
        documents = [
            {
                "_id": 1,
                "created_at": datetime(2026, 8, 26, tzinfo=timezone.utc),
                "amount": Decimal("10.50"),
                "payload": b"abc",
            },
            {"_id": 2, "name": None},
            {"name": "missing id"},
        ]

        with tempfile.TemporaryDirectory() as temp_directory:
            sink = JsonlSink(temp_directory, "test-run")
            pipeline = Pipeline(
                source=IterableSource(documents, name="test"),
                standardizer=CommonStandardizer(),
                validators=build_default_validators(["_id"], {}),
                sink=sink,
                run_id="test-run",
            )

            result = pipeline.run()

            self.assertEqual(result.report["status"], "PARTIAL_SUCCESS")
            self.assertEqual(result.report["counts"]["extracted"], 3)
            self.assertEqual(result.report["counts"]["accepted"], 2)
            self.assertEqual(result.report["counts"]["rejected"], 1)
            self.assertEqual(
                result.report["schema_profile"]["fields"]["_id"]["missing_count"],
                1,
            )

            accepted = _read_jsonl(Path(temp_directory) / "test-run" / "standardized.jsonl")
            self.assertEqual(accepted[0]["created_at"], "2026-08-26T00:00:00Z")
            self.assertEqual(accepted[0]["amount"], "10.50")
            self.assertEqual(accepted[0]["payload"]["$binary"], "YWJj")

            rejected = _read_jsonl(Path(temp_directory) / "test-run" / "rejected.jsonl")
            self.assertEqual(rejected[0]["stage"], "validation")
            self.assertEqual(rejected[0]["reasons"][0]["field"], "_id")

    def test_unsupported_value_is_quarantined(self) -> None:
        class UnknownValue:
            def __str__(self) -> str:
                return "unknown"

        with tempfile.TemporaryDirectory() as temp_directory:
            sink = JsonlSink(temp_directory, "unsupported-run")
            pipeline = Pipeline(
                source=IterableSource([{"_id": 1, "value": UnknownValue()}]),
                standardizer=CommonStandardizer(),
                validators=build_default_validators(["_id"], {}),
                sink=sink,
                run_id="unsupported-run",
            )

            result = pipeline.run()

            self.assertEqual(result.report["counts"]["standardization_failed"], 1)
            rejected = _read_jsonl(
                Path(temp_directory) / "unsupported-run" / "rejected.jsonl"
            )
            self.assertEqual(rejected[0]["stage"], "standardization")

    def test_non_finite_number_is_quarantined_without_breaking_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            sink = JsonlSink(temp_directory, "nan-run")
            pipeline = Pipeline(
                source=IterableSource([{"_id": 1, "value": float("nan")}]),
                standardizer=CommonStandardizer(),
                validators=build_default_validators(["_id"], {}),
                sink=sink,
                run_id="nan-run",
            )

            result = pipeline.run()

            self.assertEqual(result.report["status"], "FAILED")
            rejected = _read_jsonl(Path(temp_directory) / "nan-run" / "rejected.jsonl")
            self.assertEqual(rejected[0]["stage"], "standardization")
            self.assertIn("document_repr", rejected[0])

    def test_empty_source_succeeds_for_incremental_runs(self) -> None:
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as temp_directory:
            pipeline = Pipeline(
                source=IterableSource([]),
                standardizer=CommonStandardizer(),
                validators=build_default_validators(["_id"], {}),
                sink=JsonlSink(temp_directory, "empty-run"),
                run_id="empty-run",
                logger=logger,
            )

            result = pipeline.run()

        self.assertEqual(result.report["status"], "SUCCESS")
        logger.warning.assert_called_once()

    def test_unexpected_standardizer_error_fails_pipeline(self) -> None:
        class BrokenStandardizer:
            def standardize(self, document: dict) -> dict:
                raise RuntimeError("implementation bug")

        with tempfile.TemporaryDirectory() as temp_directory:
            sink = JsonlSink(temp_directory, "failed-run")
            pipeline = Pipeline(
                source=IterableSource([{"_id": 1}]),
                standardizer=BrokenStandardizer(),
                validators=build_default_validators(["_id"], {}),
                sink=sink,
                run_id="failed-run",
            )

            with self.assertRaisesRegex(RuntimeError, "implementation bug"):
                pipeline.run()

            report_path = Path(temp_directory) / "failed-run" / "report.json"
            with report_path.open("r", encoding="utf-8") as file:
                self.assertEqual(json.load(file)["status"], "FAILED")

    def test_field_type_rule_can_be_added_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            sink = JsonlSink(temp_directory, "type-run")
            pipeline = Pipeline(
                source=IterableSource([{"_id": 1, "age": "30"}]),
                standardizer=CommonStandardizer(),
                validators=build_default_validators(["_id"], {"age": "integer"}),
                sink=sink,
                run_id="type-run",
            )

            result = pipeline.run()

            self.assertEqual(result.report["counts"]["validation_failed"], 1)
            self.assertEqual(result.report["quality"]["issue_counts"]["field_types"], 1)

    def test_stage_logs_are_separated_and_summarized(self) -> None:
        documents = [
            {
                "_id": 1,
                "age": 30,
                "created_at": datetime(2026, 8, 26, tzinfo=timezone.utc),
            },
            {"_id": 2, "age": "invalid"},
            {"age": 20},
        ]

        with tempfile.TemporaryDirectory() as temp_directory:
            log_directory = Path(temp_directory) / "logs"
            standardize_logger, validation_logger = create_stage_loggers(
                log_directory,
                logging.INFO,
            )
            pipeline = Pipeline(
                source=IterableSource(documents),
                standardizer=CommonStandardizer(),
                validators=build_default_validators(["_id"], {"age": "integer"}),
                sink=JsonlSink(temp_directory, "log-run"),
                run_id="log-run",
                logger=MagicMock(),
                standardize_logger=standardize_logger,
                validation_logger=validation_logger,
            )

            pipeline.run()
            standardize_log = (log_directory / "standardize.log").read_text(
                encoding="utf-8"
            )
            validation_log = (log_directory / "validation.log").read_text(
                encoding="utf-8"
            )
            _close_logger(standardize_logger)
            _close_logger(validation_logger)

        self.assertRegex(standardize_log, r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]")
        self.assertIn("컬럼명 변환 0건", standardize_log)
        self.assertIn("타입 변환 1건", standardize_log)
        self.assertIn("표준화 완료 3건", standardize_log)
        self.assertIn("변환 실패 0건", standardize_log)
        self.assertIn("검사 3건", validation_log)
        self.assertIn("PASS 1건", validation_log)
        self.assertIn("FAIL 2건", validation_log)
        self.assertIn("NULL 오류 1건", validation_log)
        self.assertIn("형식 오류 1건", validation_log)

    def test_mongo_source_can_switch_to_aggregation_from_config(self) -> None:
        config = SourceConfig(
            uri_env="TEST_MONGODB_URI",
            database="sample",
            collection="documents",
            aggregation=[{"$unwind": "$items"}],
            limit=2,
        )
        client = MagicMock()
        database = MagicMock()
        collection = MagicMock()
        client.__getitem__.return_value = database
        database.__getitem__.return_value = collection
        collection.aggregate.return_value = iter([{"_id": 1, "item": "A"}])
        mongo_client = MagicMock(return_value=client)
        fake_pymongo = SimpleNamespace(MongoClient=mongo_client)

        with (
            patch.dict(sys.modules, {"pymongo": fake_pymongo}),
            patch.dict(os.environ, {"TEST_MONGODB_URI": "mongodb://example"}),
        ):
            source = MongoSource(config)
            documents = list(source.read())
            source.close()

        self.assertEqual(documents, [{"_id": 1, "item": "A"}])
        collection.aggregate.assert_called_once_with(
            [{"$unwind": "$items"}, {"$limit": 2}],
            batchSize=500,
        )
        client.close.assert_called_once()

    def test_mongo_source_rejects_write_aggregation_stages(self) -> None:
        for stage in ({"$out": "result"}, {"$merge": "result"}):
            with self.subTest(stage=stage):
                with self.assertRaisesRegex(ValueError, "사용할 수 없습니다"):
                    SourceConfig(
                        uri_env="TEST_MONGODB_URI",
                        database="sample",
                        collection="documents",
                        aggregation=[stage],
                    )


def _read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def _close_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
