from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mongo_pipeline.backup import DjangoMongoDataLakeBackup  # noqa: E402
from mongo_pipeline.config import (  # noqa: E402
    AppConfig,
    DataLakeConfig,
    ReprocessConfig,
    ScheduleConfig,
    SinkConfig,
    SourceConfig,
)
from mongo_pipeline.reprocessing import (  # noqa: E402
    DjangoMongoReprocessSource,
    ReprocessSink,
)
from mongo_pipeline.scheduler import (  # noqa: E402
    PipelineScheduler,
    _StateStore,
    _FileLock,
    _incremental_source_config,
)
from mongo_pipeline.sinks import DjangoMongoSink  # noqa: E402


class OperationsTest(unittest.TestCase):
    def test_incremental_query_contains_one_minute_delay_and_watermark(self) -> None:
        source = SourceConfig(
            kind="django_mongodb",
            database_alias="mongodb",
            database="raw",
            collection="records",
        )
        cutoff = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
        watermark = datetime(2026, 8, 27, 2, 55, tzinfo=timezone.utc)

        updated = _incremental_source_config(
            source,
            watermark_field="ingested_at",
            cutoff=cutoff,
            watermark=watermark,
        )

        self.assertEqual(updated.query["ingested_at"]["$lte"], cutoff)
        self.assertEqual(updated.query["ingested_at"]["$gt"], watermark)

    def test_reprocess_source_unwraps_failure_document_and_builds_pending_query(self) -> None:
        fake_client = _FakeMongoClient()
        failure_collection = fake_client.database("failed").collection("records")
        failure_collection.rows = [
            {
                "_id": "failure-1",
                "document_id": "raw-1",
                "document": {"record_id": 1},
                "reprocess_status": "pending",
                "attempt_count": 0,
            }
        ]
        connection = SimpleNamespace(
            connection=fake_client,
            ensure_connection=MagicMock(),
            settings_dict={"NAME": "configured"},
        )

        with _django_modules(connection):
            source = DjangoMongoReprocessSource(
                ReprocessConfig(
                    enabled=True,
                    database="failed",
                    collection="records",
                    max_attempts=3,
                )
            )
            documents = list(source.read())
            source.close()

        self.assertEqual(documents[0]["record_id"], 1)
        self.assertEqual(documents[0]["_reprocess_context"]["failure_id"], "failure-1")
        self.assertEqual(documents[0]["_reprocess_context"]["attempt_count"], 1)
        self.assertIn("$or", failure_collection.last_find_query)
        self.assertIn("$and", failure_collection.last_find_query)

    def test_reprocess_success_marks_original_failure_resolved(self) -> None:
        fake_client = _FakeMongoClient()
        connection = SimpleNamespace(
            connection=fake_client,
            ensure_connection=MagicMock(),
        )
        fake_pymongo = SimpleNamespace(ReplaceOne=_FakeReplaceOne)

        with (
            _django_modules(connection),
            patch.dict(sys.modules, {"pymongo": fake_pymongo}),
        ):
            sink = DjangoMongoSink(
                database_alias="mongodb",
                settings_module="config.settings",
                success_database="standardized",
                success_collection="records",
                failure_database="failed",
                failure_collection="records",
                report_database="standardized",
                report_collection="pipeline_runs",
                run_id="reprocess-run",
            )
            reprocess = ReprocessSink(sink, max_attempts=3)
            reprocess.write_success(
                {
                    "_id": "success-1",
                    "record_id": 1,
                    "_reprocess_context": {
                        "failure_id": "failure-1",
                        "attempt_count": 1,
                        "document_id": "raw-1",
                    },
                }
            )
            report_path = reprocess.write_report(
                {"run_id": "reprocess-run", "status": "SUCCESS"}
            )
            reprocess.close()

        self.assertEqual(report_path, "mongodb://standardized/pipeline_runs/reprocess-run")
        updates = fake_client.database("failed").collection("records").updates
        self.assertEqual(updates[-1]["update"]["$set"]["reprocess_status"], "resolved")
        self.assertEqual(updates[-1]["update"]["$set"]["attempt_count"], 1)
        report = fake_client.database("standardized").collection("pipeline_runs")
        self.assertEqual(report.operations[0].replacement["_id"], "reprocess-run")
        self.assertEqual(report.operations[0].replacement["reprocess"]["resolved"], 1)

    def test_data_lake_backup_writes_jsonl_and_manifest_checksum(self) -> None:
        fake_client = _FakeMongoClient()
        fake_client.database("standardized").collection("records").rows = [
            {"_id": "ok-1", "record_id": 1}
        ]
        fake_client.database("failed").collection("records").rows = [
            {"_id": "bad-1", "reprocess_status": "pending"}
        ]
        fake_client.database("standardized").collection("pipeline_runs").rows = []
        connection = SimpleNamespace(
            connection=fake_client,
            ensure_connection=MagicMock(),
        )

        with tempfile.TemporaryDirectory() as temp_directory:
            config = DataLakeConfig(
                enabled=True,
                root=Path(temp_directory),
                database_alias="mongodb",
                settings_module="config.settings",
            )
            sink_config = SinkConfig(
                kind="django_mongodb",
                success_database="standardized",
                success_collection="records",
                failure_database="failed",
                failure_collection="records",
                report_database="standardized",
                report_collection="pipeline_runs",
            )
            with _django_modules(connection):
                report = DjangoMongoDataLakeBackup(
                    config,
                    sink_config=sink_config,
                ).run(now=datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc))

            manifest = json.loads(Path(report["manifest_path"]).read_text(encoding="utf-8"))
            record_object = next(item for item in manifest["objects"] if item["name"] == "success_records")
            record_path = Path(temp_directory) / record_object["path"]
            payload = record_path.read_bytes()

        self.assertEqual(record_object["row_count"], 1)
        self.assertEqual(record_object["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(manifest["status"], "SUCCESS")

    def test_state_store_recovers_from_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "state.json"
            state = _StateStore(path)
            state.set("watermark", "2026-08-27T03:00:00Z")
            state.save()
            loaded = _StateStore(path)

        self.assertEqual(loaded.get("watermark"), "2026-08-27T03:00:00Z")

    def test_scheduler_skips_a_tick_when_lock_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            lock_path = root / "state" / "pipeline.lock"
            app_config = AppConfig(
                source=SourceConfig(
                    kind="mongodb",
                    uri_env="MONGODB_URI",
                    database="raw",
                    collection="records",
                ),
                schedule=ScheduleConfig(
                    enabled=True,
                    lock_path=lock_path,
                    watermark_path=root / "state" / "watermark.json",
                ),
            )
            existing_lock = _FileLock(lock_path)
            self.assertTrue(existing_lock.acquire())
            try:
                tick = PipelineScheduler(app_config).run_tick_locked()
            finally:
                existing_lock.release()

        self.assertEqual(tick["status"], "SKIPPED")
        self.assertEqual(tick["reason"], "lock_exists")


class _FakeReplaceOne:
    def __init__(self, selector: dict, replacement: dict, *, upsert: bool) -> None:
        self.selector = selector
        self.replacement = replacement
        self.upsert = upsert


class _FakeAdmin:
    def command(self, name: str) -> None:
        return None


class _FakeUpdateResult:
    matched_count = 1


class _FakeCollection:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.operations: list[_FakeReplaceOne] = []
        self.updates: list[dict] = []
        self.last_find_query: dict = {}

    def find(self, query=None, projection=None, *, batch_size=500):
        self.last_find_query = query or {}
        return iter(self.rows)

    def bulk_write(self, operations: list[_FakeReplaceOne], *, ordered: bool) -> None:
        self.operations.extend(operations)

    def update_one(self, selector: dict, update: dict, *, upsert: bool) -> _FakeUpdateResult:
        self.updates.append({"selector": selector, "update": update, "upsert": upsert})
        return _FakeUpdateResult()


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())

    def collection(self, name: str) -> _FakeCollection:
        return self[name]


class _FakeMongoClient:
    def __init__(self) -> None:
        self.admin = _FakeAdmin()
        self.databases: dict[str, _FakeDatabase] = {}

    def __getitem__(self, name: str) -> _FakeDatabase:
        return self.database(name)

    def database(self, name: str) -> _FakeDatabase:
        return self.databases.setdefault(name, _FakeDatabase())


def _django_modules(connection: Any):
    fake_django = ModuleType("django")
    fake_django.setup = MagicMock()
    fake_db = ModuleType("django.db")
    fake_db.connections = {"mongodb": connection}
    return patch.dict(
        sys.modules,
        {"django": fake_django, "django.db": fake_db},
    )


if __name__ == "__main__":
    unittest.main()
