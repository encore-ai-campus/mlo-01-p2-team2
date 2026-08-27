from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mongo_pipeline.config import AppConfig, SourceConfig  # noqa: E402
from mongo_pipeline.rule_standardizer import YamlRuleStandardizer  # noqa: E402
from mongo_pipeline.sinks import DjangoMongoSink, MongoSink  # noqa: E402
from mongo_pipeline.sources import DjangoMongoSource, JsonlSource  # noqa: E402


RULES_PATH = PROJECT_ROOT / "rules" / "legacy_org_jsonl.yaml"


class JsonlSourceTest(unittest.TestCase):
    def test_valid_and_malformed_lines_are_both_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "records.jsonl"
            path.write_text('{"record_id": 1}\nnot-json\n[1, 2]\n', encoding="utf-8")

            documents = list(JsonlSource(path).read())

        self.assertEqual(documents[0], {"record_id": 1})
        self.assertEqual(documents[1]["_source_error"]["type"], "json_decode_error")
        self.assertEqual(documents[2]["_source_error"]["type"], "not_an_object")

    def test_parse_error_can_fail_the_source_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "records.jsonl"
            path.write_text("not-json\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "JSONL 1번째 줄"):
                list(JsonlSource(path, continue_on_parse_error=False).read())


class LegacyJsonlRuleTest(unittest.TestCase):
    def test_nested_payload_is_standardized_and_id_is_stable(self) -> None:
        document = {
            "dataset_id": "dataset-1",
            "record_id": 153751,
            "source_record_sha256": "abc123",
            "payload": {
                "mgr_nm": "　임 예준　",
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
            },
        }
        standardizer = YamlRuleStandardizer.from_file(RULES_PATH)

        first = standardizer.standardize(document)
        second = standardizer.standardize(document)

        self.assertEqual(first["payload"]["area_no"], "BIZ_02168")
        self.assertEqual(first["payload"]["mgr_no"], "EMP002471")
        self.assertEqual(first["payload"]["mgr_nm"], "임예준")
        self.assertEqual(first["payload"]["top_area_lvl"], "L1")
        self.assertEqual(first["payload"]["mgr_act_yn"], "ACTIVE")
        self.assertEqual(first["payload"]["area_reg_dtm"], "2016-05-26T11:06:42Z")
        self.assertEqual(first["_id"], second["_id"])
        self.assertEqual(document["payload"]["area_no"], "biz-02168")


class ConfigTest(unittest.TestCase):
    def test_jsonl_source_and_django_sink_paths_are_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "source": {"type": "jsonl", "path": "records.jsonl"},
                        "standardization": {"rules_file": "rules/legacy.yaml"},
                        "sink": {
                            "type": "django_mongodb",
                            "project_root": "django",
                            "success_database": "success",
                            "success_collection": "records",
                            "failure_database": "failure",
                            "failure_collection": "records",
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = AppConfig.from_file(config_path)

        self.assertEqual(config.source.kind, "jsonl")
        self.assertEqual(config.source.path, root / "records.jsonl")
        self.assertEqual(config.sink.project_root, root / "django")
        self.assertEqual(config.sink.kind, "django_mongodb")

    def test_django_source_reads_from_the_alias_database(self) -> None:
        fake_client = _FakeMongoClient()
        fake_collection = MagicMock()
        fake_collection.find.return_value = iter([{"_id": "raw-1"}])
        fake_client.database("raw").collections["records"] = fake_collection
        fake_connection = SimpleNamespace(
            connection=fake_client,
            settings_dict={"NAME": "configured"},
            ensure_connection=MagicMock(),
        )
        fake_django = ModuleType("django")
        fake_django.setup = MagicMock()
        fake_django_db = ModuleType("django.db")
        fake_django_db.connections = {"mongodb": fake_connection}

        with patch.dict(
            sys.modules,
            {"django": fake_django, "django.db": fake_django_db},
        ):
            source = DjangoMongoSource(
                SourceConfig(
                    kind="django_mongodb",
                    database="raw",
                    collection="records",
                )
            )
            documents = list(source.read())
            source.close()

        self.assertEqual(documents, [{"_id": "raw-1"}])
        fake_connection.ensure_connection.assert_called_once()
        fake_collection.find.assert_called_once_with({}, None, batch_size=500)


class MongoSinkTest(unittest.TestCase):
    def test_missing_id_hash_is_stable_across_pipeline_runs(self) -> None:
        fake_client = _FakeMongoClient()
        fake_pymongo = SimpleNamespace(
            MongoClient=lambda uri, serverSelectionTimeoutMS: fake_client,
            ReplaceOne=_FakeReplaceOne,
        )

        with patch.dict(
            sys.modules,
            {"pymongo": fake_pymongo},
        ), patch.dict(os.environ, {"TEST_MONGODB_URI": "mongodb://example"}):
            first = MongoSink(
                uri_env="TEST_MONGODB_URI",
                success_database="standardized",
                success_collection="records",
                failure_database="failed",
                failure_collection="records",
                run_id="run-1",
            )
            first.write_success({"dataset_id": "dataset-1", "record_id": 1})
            first.close()

            second = MongoSink(
                uri_env="TEST_MONGODB_URI",
                success_database="standardized",
                success_collection="records",
                failure_database="failed",
                failure_collection="records",
                run_id="run-2",
            )
            second.write_success({"dataset_id": "dataset-1", "record_id": 1})
            second.close()

        operations = fake_client.database("standardized").collection("records").operations
        self.assertEqual(len(operations), 2)
        self.assertEqual(
            operations[0].replacement["_id"],
            operations[1].replacement["_id"],
        )

    def test_success_and_failure_are_upserted_to_separate_databases(self) -> None:
        fake_client = _FakeMongoClient()
        fake_pymongo = SimpleNamespace(
            MongoClient=lambda uri, serverSelectionTimeoutMS: fake_client,
            ReplaceOne=_FakeReplaceOne,
        )

        with tempfile.TemporaryDirectory() as temp_directory:
            with (
                patch.dict(sys.modules, {"pymongo": fake_pymongo}),
                patch.dict(os.environ, {"TEST_MONGODB_URI": "mongodb://example"}),
            ):
                sink = MongoSink(
                    uri_env="TEST_MONGODB_URI",
                    success_database="standardized",
                    success_collection="records",
                    failure_database="failed",
                    failure_collection="records",
                    report_database="standardized",
                    report_collection="pipeline_runs",
                    run_id="run-1",
                    batch_size=2,
                    local_report_root=temp_directory,
                )
                sink.write_success({"_id": "ok-1", "value": "standard"})
                sink.write_rejected(
                    document_id="bad-1",
                    stage="validation",
                    reasons=[{"rule": "required_fields", "field": "payload.area_no"}],
                    document={"value": None},
                )
                report_path = sink.write_report({"run_id": "run-1", "status": "PARTIAL_SUCCESS"})
                self.assertTrue(Path(report_path).exists())
                sink.close()

        self.assertEqual(fake_client.admin.pings, 1)
        self.assertEqual(len(fake_client.database("standardized").collection("records").operations), 1)
        self.assertEqual(len(fake_client.database("failed").collection("records").operations), 1)
        self.assertEqual(
            len(fake_client.database("standardized").collection("pipeline_runs").operations),
            1,
        )
        self.assertTrue(fake_client.closed)


class DjangoMongoSinkTest(unittest.TestCase):
    def test_description_exposes_django_alias_without_uri(self) -> None:
        sink = DjangoMongoSink(
            database_alias="mongodb",
            settings_module="config.settings",
            success_database="standardized",
            success_collection="records",
            failure_database="failed",
            failure_collection="records",
            run_id="run-1",
        )

        self.assertEqual(sink.description["type"], "django_mongodb")
        self.assertEqual(sink.description["database_alias"], "mongodb")

    def test_django_sink_reuses_connection_alias_and_separates_databases(self) -> None:
        fake_client = _FakeMongoClient()
        fake_connection = SimpleNamespace(
            connection=fake_client,
            ensure_connection=MagicMock(),
        )
        fake_django = ModuleType("django")
        fake_django.setup = MagicMock()
        fake_django_db = ModuleType("django.db")
        fake_django_db.connections = {"mongodb": fake_connection}
        fake_pymongo = SimpleNamespace(ReplaceOne=_FakeReplaceOne)

        with patch.dict(
            sys.modules,
            {
                "django": fake_django,
                "django.db": fake_django_db,
                "pymongo": fake_pymongo,
            },
        ):
            sink = DjangoMongoSink(
                database_alias="mongodb",
                settings_module="config.settings",
                success_database="standardized",
                success_collection="records",
                failure_database="failed",
                failure_collection="records",
                run_id="run-1",
                batch_size=10,
            )
            sink.write_success({"_id": "ok-1"})
            sink.write_report({"run_id": "run-1", "status": "SUCCESS"})
            sink.close()

        fake_connection.ensure_connection.assert_called_once()
        fake_django.setup.assert_called_once()
        self.assertEqual(
            len(fake_client.database("standardized").collection("records").operations),
            1,
        )
        self.assertEqual(
            fake_client.database("standardized")
            .collection("_pipeline_runs")
            .operations[0]
            .replacement["_id"],
            "run-1",
        )
        self.assertFalse(fake_client.closed)


class _FakeReplaceOne:
    def __init__(self, selector: dict, replacement: dict, *, upsert: bool) -> None:
        self.selector = selector
        self.replacement = replacement
        self.upsert = upsert


class _FakeAdmin:
    def __init__(self) -> None:
        self.pings = 0

    def command(self, name: str) -> None:
        if name == "ping":
            self.pings += 1


class _FakeCollection:
    def __init__(self) -> None:
        self.operations: list[_FakeReplaceOne] = []

    def bulk_write(self, operations: list[_FakeReplaceOne], *, ordered: bool) -> None:
        self.operations.extend(operations)


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def collection(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collection(name)


class _FakeMongoClient:
    def __init__(self) -> None:
        self.admin = _FakeAdmin()
        self.databases: dict[str, _FakeDatabase] = {}
        self.closed = False

    def database(self, name: str) -> _FakeDatabase:
        return self.databases.setdefault(name, _FakeDatabase())

    def __getitem__(self, name: str) -> _FakeDatabase:
        return self.database(name)

    def close(self) -> None:
        self.closed = True


if __name__ == "__main__":
    unittest.main()
