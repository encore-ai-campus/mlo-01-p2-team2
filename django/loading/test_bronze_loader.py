"""End-to-end loader tests using an in-memory MongoDB substitute."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from second_project.repository.mongodb_repository import RawWriteResult
from second_project.service.bronze_config import LoaderConfig
from second_project.service.bronze_loader import BronzeLoader
from second_project.service.structured_logging import StructuredLogWriter


DATASET_ID = "9b6bc75f-2a62-4221-b5a3-077cd1938371"
SOURCE_SHA256 = "a" * 64


def make_line(record_id: int) -> str:
    return json.dumps(
        {
            "dataset_id": DATASET_ID,
            "source_filename": "records.csv",
            "source_sha256": SOURCE_SHA256,
            "record_id": record_id,
            "source_row_no": record_id,
            "source_record_sha256": f"{record_id:x}" * 64,
            "scheduled_release_at": "2026-08-27T10:00:00+09:00",
            "payload": {"mgr_nm": " 이름 "},
            "_crawl": {
                "run_id": "crawl-run-1",
                "collected_at": "2026-08-27T10:00:01+09:00",
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


class FakeMongoStore:
    raw: dict[str, dict] = {}
    quarantines: dict[str, dict] = {}
    runs: dict[str, dict] = {}
    manifests: dict[str, dict] = {}

    def __init__(self, config: LoaderConfig) -> None:
        self.config = config
        self.closed = False

    def ping_and_prepare(self) -> None:
        return None

    def start_run(self, document: dict) -> None:
        self.runs[document["_id"]] = document

    def update_run(self, run_id: str, values: dict) -> None:
        self.runs[run_id].update(values)

    def save_manifest(self, document: dict) -> None:
        self.manifests[document["_id"]] = document

    def upsert_raw_batch(self, documents) -> RawWriteResult:
        inserted = 0
        duplicate = 0
        inserted_ids = []
        for document in documents:
            old = self.raw.get(document["_id"])
            if old is None:
                self.raw[document["_id"]] = document
                inserted += 1
                inserted_ids.append(document["_id"])
            elif old["source_record_sha256"] == document["source_record_sha256"]:
                duplicate += 1
            else:
                raise RuntimeError("fake checksum conflict")
        return RawWriteResult(inserted, duplicate, tuple(inserted_ids))

    def verify_inserted_batch(self, documents) -> None:
        for document in documents:
            assert self.raw[document["_id"]]["raw_line_sha256"] == document["raw_line_sha256"]

    def insert_quarantine_batch(self, documents) -> None:
        for document in documents:
            self.quarantines[document["_id"]] = document

    def close(self) -> None:
        self.closed = True


class BronzeLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeMongoStore.raw.clear()
        FakeMongoStore.quarantines.clear()
        FakeMongoStore.runs.clear()
        FakeMongoStore.manifests.clear()

    def _config(self, root: Path, input_path: Path, log_path: Path) -> LoaderConfig:
        return LoaderConfig(
            project_root=root,
            input_path=input_path,
            log_path=log_path,
            batch_size=1,
        )

    def test_loads_raw_records_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "records.jsonl"
            log_path = root / "logs" / "pipeline.jsonl"
            input_path.write_text(make_line(1) + "\n" + make_line(2) + "\n", encoding="utf-8")

            with patch("second_project.service.bronze_loader.MongoRepository", FakeMongoStore):
                first = BronzeLoader(
                    self._config(root, input_path, log_path),
                    run_id="load-run-1",
                    logger=StructuredLogWriter(log_path, "load-run-1", echo=False),
                ).run()
                second = BronzeLoader(
                    self._config(root, input_path, log_path),
                    run_id="load-run-2",
                    logger=StructuredLogWriter(log_path, "load-run-2", echo=False),
                ).run()

        self.assertEqual(first.status, "success")
        self.assertEqual(first.inserted_count, 2)
        self.assertEqual(second.inserted_count, 0)
        self.assertEqual(second.duplicate_count, 2)
        self.assertEqual(len(FakeMongoStore.raw), 2)
        self.assertEqual(FakeMongoStore.manifests["load-run-1"]["status"], "success")

    def test_invalid_line_is_quarantined_without_changing_valid_raw_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "records.jsonl"
            log_path = root / "logs" / "pipeline.jsonl"
            input_path.write_text(make_line(1) + "\nnot-json\n", encoding="utf-8")

            with patch("second_project.service.bronze_loader.MongoRepository", FakeMongoStore):
                result = BronzeLoader(
                    self._config(root, input_path, log_path),
                    run_id="load-run-1",
                    logger=StructuredLogWriter(log_path, "load-run-1", echo=False),
                ).run()

        self.assertEqual(result.status, "partial_failure")
        self.assertEqual(result.input_count, 2)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.quarantine_count, 1)
        self.assertEqual(result.input_count, result.success_count + result.failure_count + result.quarantine_count)
        self.assertEqual(len(FakeMongoStore.raw), 1)
        self.assertEqual(len(FakeMongoStore.quarantines), 1)


if __name__ == "__main__":
    unittest.main()
