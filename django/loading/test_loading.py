"""Unit tests for Bronze input validation and structured logging."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from second_project.service.log_rotation import RotatingJsonlWriter

from .fingerprint import fingerprint_file
from .record_reader import RecordValidationError, parse_record_line
from .structured_logging import StructuredLogWriter


DATASET_ID = "9b6bc75f-2a62-4221-b5a3-077cd1938371"
SOURCE_SHA256 = "a" * 64
RECORD_SHA256 = "b" * 64


def make_line(*, manager_name: str = " 홍길동 ") -> str:
    return json.dumps(
        {
            "dataset_id": DATASET_ID,
            "source_filename": "records.csv",
            "source_sha256": SOURCE_SHA256,
            "record_id": 1,
            "source_row_no": 1,
            "source_record_sha256": RECORD_SHA256,
            "release_slot": 0,
            "scheduled_release_at": "2026-08-27T10:00:00+09:00",
            "payload": {"mgr_nm": manager_name},
            "_crawl": {"collected_at": "2026-08-27T10:00:01+09:00"},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


class RecordReaderTests(unittest.TestCase):
    def test_raw_payload_and_text_are_preserved(self) -> None:
        line = make_line()
        with tempfile.TemporaryDirectory() as directory:
            parsed = parse_record_line(
                line,
                line_no=1,
                load_run_id="load-run",
                input_file_sha256="c" * 64,
            )

        self.assertEqual(parsed.bronze_document["raw_json"]["payload"]["mgr_nm"], " 홍길동 ")
        self.assertEqual(parsed.bronze_document["raw_json_text"], line)
        self.assertEqual(parsed.bronze_document["source_record_id"], "1")
        self.assertEqual(parsed.bronze_document["run_id"], "load-run")
        self.assertEqual(parsed.bronze_document["scheduled_release_at"].tzinfo is not None, True)

    def test_missing_required_field_is_quarantinable(self) -> None:
        document = json.loads(make_line())
        del document["payload"]

        with self.assertRaises(RecordValidationError) as context:
            parse_record_line(
                json.dumps(document),
                line_no=1,
                load_run_id="load-run",
                input_file_sha256="c" * 64,
            )

        self.assertEqual(context.exception.error_code, "REQUIRED_VALUE_MISSING")

    def test_invalid_datetime_uses_standard_error_code(self) -> None:
        document = json.loads(make_line())
        document["scheduled_release_at"] = "not-a-date"

        with self.assertRaises(RecordValidationError) as context:
            parse_record_line(
                json.dumps(document),
                line_no=1,
                load_run_id="load-run",
                input_file_sha256="c" * 64,
            )

        self.assertEqual(context.exception.error_code, "DATETIME_PARSE_FAILED")


class LoggingTests(unittest.TestCase):
    def test_jsonl_writer_rotates_when_size_limit_is_reached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw_data_loading_log.jsonl"
            writer = RotatingJsonlWriter(
                path,
                max_bytes=100,
                backup_count=2,
                interval_seconds=6 * 60 * 60,
            )

            writer.write("a" * 80)
            writer.write("b" * 80)

            self.assertTrue(path.exists())
            self.assertTrue(Path(f"{path}.1").exists())

    def test_log_contains_common_fields_and_masks_sensitive_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw_data_loading_log.jsonl"
            writer = StructuredLogWriter(path, "load-run", echo=False)
            writer.warn(
                r"failed at https://private.example/path C:\private\file.txt",
                dataset_id=DATASET_ID,
                status="partial_failure",
                input_count=10,
                success_count=9,
                failure_count=0,
                quarantine_count=1,
                source_record_id="EMP123",
                error_code="DATETIME_PARSE_FAILED",
            )
            event = json.loads(path.read_text(encoding="utf-8").strip())

        self.assertEqual(event["stage"], "bronze")
        self.assertEqual(event["level"], "WARN")
        self.assertEqual(event["input_count"], 10)
        self.assertEqual(
            event["input_count"],
            event["success_count"] + event["failure_count"] + event["quarantine_count"],
        )
        self.assertNotIn("private.example", event["message"])
        self.assertNotIn(r"C:\private\file.txt", event["message"])
        self.assertTrue(event["source_record_id"].startswith("masked-"))

    def test_fingerprint_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_text(make_line() + "\n", encoding="utf-8")
            first = fingerprint_file(path)
            second = fingerprint_file(path)

        self.assertEqual(first, second)
        self.assertEqual(first.size_bytes, path.stat().st_size if path.exists() else first.size_bytes)


if __name__ == "__main__":
    unittest.main()
