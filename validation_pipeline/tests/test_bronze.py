from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mongo_pipeline.bronze import (  # noqa: E402
    build_bronze_record,
    build_manifest,
    bronze_integrity,
    validate_bronze_record,
    validate_manifest,
    verify_manifest_file,
)
from mongo_pipeline.pipeline import Pipeline  # noqa: E402
from mongo_pipeline.sinks import JsonlSink  # noqa: E402
from mongo_pipeline.sources import IterableSource  # noqa: E402
from mongo_pipeline.standardizers import CommonStandardizer  # noqa: E402
from mongo_pipeline.validators import build_default_validators  # noqa: E402


class BronzeContractTest(unittest.TestCase):
    def test_bronze_record_preserves_canonical_raw_and_hash(self) -> None:
        record = build_bronze_record(
            {
                "_id": "raw-1",
                "dataset_id": "dataset-1",
                "payload": {"name": "원본"},
            },
            run_id="run-1",
            row_number=1,
            ingested_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )

        self.assertEqual(
            record["source_record_sha256"],
            hashlib.sha256(record["raw_json"].encode("utf-8")).hexdigest(),
        )
        self.assertEqual(record["source_record_id"], "raw-1")
        self.assertEqual(record["source"]["record_id"], "raw-1")
        self.assertEqual(validate_bronze_record(record), [])

    def test_manifest_schema_and_source_file_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            raw_path = Path(temp_directory) / "records.csv"
            raw_path.write_bytes(b"record_id\nraw-1\n")
            manifest = build_manifest(
                run_id="run-1",
                source_description={
                    "type": "csv_file",
                    "path": str(raw_path),
                },
                started_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
                row_count=1,
                status="SUCCESS",
            )

            validate_manifest(manifest)
            self.assertTrue(verify_manifest_file(manifest))
            self.assertEqual(
                manifest["checksum_sha256"],
                hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            )

    def test_pipeline_connects_bronze_manifest_and_restoration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            output_root = Path(temp_directory) / "output"
            result = Pipeline(
                source=IterableSource(
                    [{"_id": "raw-1"}, {"_id": "raw-2"}, {"name": "missing-id"}],
                    name="fixture",
                ),
                standardizer=CommonStandardizer(),
                validators=build_default_validators(["_id"], {}),
                sink=JsonlSink(output_root, "run-1"),
                run_id="run-1",
            ).run()

            run_directory = output_root / "run-1"
            bronze_records = [
                json.loads(line)
                for line in (run_directory / "bronze_raw_records.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line
            ]
            manifest = json.loads(
                (run_directory / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(verify_manifest_file(manifest))

        self.assertEqual(len(bronze_records), 3)
        self.assertEqual(manifest["row_count"], 3)
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(
            manifest["raw_path"],
            str((run_directory / "bronze_raw_records.jsonl").resolve()),
        )
        self.assertEqual(result.report["bronze"]["integrity_rate"], 1.0)
        self.assertEqual(result.report["restoration"]["bronze_distinct_source_count"], 3)
        self.assertEqual(result.report["restoration"]["silver_recovered_source_count"], 2)
        self.assertEqual(result.report["restoration"]["gate_status"], "failed")


if __name__ == "__main__":
    unittest.main()
