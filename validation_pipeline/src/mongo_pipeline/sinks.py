from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, TextIO

from .silver import SILVER_MODEL_NAMES, model_fingerprint, split_silver_models


class DocumentSink(Protocol):
    """파이프라인이 저장 방식과 무관하게 결과를 기록하게 하는 규약이다."""

    @property
    def description(self) -> dict[str, Any]:
        """리포트에 포함할 저장 위치 정보를 반환한다."""
        ...

    def write_success(self, document: dict[str, Any]) -> None:
        """검증을 통과한 문서를 저장한다."""
        ...

    def write_bronze(self, record: dict[str, Any], *, persist: bool = True) -> None:
        """표준화 전 원본 보존 레코드를 저장한다."""
        ...

    def write_manifest(self, manifest: dict[str, Any]) -> str:
        """Bronze 실행 Manifest를 저장하고 위치를 반환한다."""
        ...

    def flush(self) -> None:
        """현재 파일·DB 버퍼를 반영한다."""
        ...

    def write_rejected(
        self,
        *,
        document_id: str | None,
        stage: str,
        reasons: list[dict[str, Any]],
        document: Any | None = None,
    ) -> None:
        """제외된 문서와 처리 단계, 실패 사유를 저장한다."""
        ...

    def write_report(self, report: dict[str, Any]) -> str:
        """실행 리포트를 저장하고 위치를 반환한다."""
        ...

    def close(self) -> None:
        """저장소가 사용한 파일이나 연결을 정리한다."""
        ...


class JsonlSink:
    """정상·제외 문서를 JSONL로, 실행 요약을 JSON으로 저장한다."""

    def __init__(self, output_root: str | Path, run_id: str) -> None:
        """실행별 디렉터리와 결과 파일을 새로 연다."""

        self.run_directory = Path(output_root) / run_id
        self.run_directory.mkdir(parents=True, exist_ok=False)

        self.standardized_path = self.run_directory / "standardized.jsonl"
        self.rejected_path = self.run_directory / "rejected.jsonl"
        self.report_path = self.run_directory / "report.json"
        self.bronze_path = self.run_directory / "bronze_raw_records.jsonl"
        self.manifest_path = self.run_directory / "manifest.json"
        self.silver_paths = {
            model_name: self.run_directory / f"{model_name}.jsonl"
            for model_name in SILVER_MODEL_NAMES
        }

        self._standardized_file = self.standardized_path.open("w", encoding="utf-8")
        self._rejected_file = self.rejected_path.open("w", encoding="utf-8")
        self._bronze_file = self.bronze_path.open("w", encoding="utf-8")
        self._silver_files = {
            model_name: path.open("w", encoding="utf-8")
            for model_name, path in self.silver_paths.items()
        }
        self._silver_seen: dict[str, set[tuple[Any, str]]] = {
            model_name: set() for model_name in SILVER_MODEL_NAMES
        }
        self._closed = False

    @property
    def description(self) -> dict[str, Any]:
        """생성한 디렉터리와 파일의 절대 경로를 반환한다."""

        return {
            "run_directory": str(self.run_directory.resolve()),
            "standardized": str(self.standardized_path.resolve()),
            "rejected": str(self.rejected_path.resolve()),
            "quarantine": str(self.rejected_path.resolve()),
            "report": str(self.report_path.resolve()),
            "bronze": str(self.bronze_path.resolve()),
            "manifest": str(self.manifest_path.resolve()),
            "silver_models": {
                model_name: str(path.resolve())
                for model_name, path in self.silver_paths.items()
            },
        }

    def write_success(self, document: dict[str, Any]) -> None:
        """검증 통과 문서를 정상 데이터 파일에 기록한다."""

        self._write_json_line(self._standardized_file, document)
        for model_name, model in split_silver_models(document).items():
            primary_key = model.get(
                {
                    "silver_employee": "employee_id",
                    "silver_area": "area_id",
                    "silver_parent_area": "parent_area_id",
                    "silver_top_area_detail": "top_area_id",
                }[model_name]
            )
            identity = (primary_key, model_fingerprint(model_name, model))
            if identity in self._silver_seen[model_name]:
                continue
            self._silver_seen[model_name].add(identity)
            self._write_json_line(self._silver_files[model_name], model)

    def write_bronze(self, record: dict[str, Any], *, persist: bool = True) -> None:
        """표준화 전 원본 보존 레코드를 JSONL에 기록한다."""

        self._write_json_line(self._bronze_file, record)

    def write_manifest(self, manifest: dict[str, Any]) -> str:
        """실행별 Bronze Manifest를 JSON 파일에 기록한다."""

        self._bronze_file.flush()
        with self.manifest_path.open("w", encoding="utf-8") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
        return str(self.manifest_path.resolve())

    def write_rejected(
        self,
        *,
        document_id: str | None,
        stage: str,
        reasons: list[dict[str, Any]],
        document: Any | None = None,
    ) -> None:
        """실패 문서와 원인을 제외 데이터 파일에 기록한다."""

        record = _quarantine_record(
            run_id=self.run_directory.name,
            document_id=document_id,
            stage=stage,
            reasons=reasons,
            document=document,
        )
        if document is not None:
            record["document"] = document
        try:
            self._write_json_line(self._rejected_file, record, use_fallback=True)
        except (TypeError, ValueError):
            record.pop("document", None)
            record["document_repr"] = repr(document)
            self._write_json_line(self._rejected_file, record, use_fallback=True)

    def write_report(self, report: dict[str, Any]) -> str:
        """실행 리포트를 JSON 파일로 기록하고 절대 경로를 반환한다."""

        with self.report_path.open("w", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
        return str(self.report_path.resolve())

    def flush(self) -> None:
        """열린 JSONL 파일 버퍼를 디스크에 반영한다."""

        self._standardized_file.flush()
        self._rejected_file.flush()
        self._bronze_file.flush()
        for file in self._silver_files.values():
            file.flush()

    def close(self) -> None:
        """열려 있는 정상·제외 데이터 파일을 닫는다."""

        if self._closed:
            return
        self._standardized_file.close()
        self._rejected_file.close()
        self._bronze_file.close()
        for file in self._silver_files.values():
            file.close()
        self._closed = True

    @staticmethod
    def _write_json_line(
        file: TextIO,
        record: Any,
        *,
        use_fallback: bool = False,
    ) -> None:
        """하나의 값을 JSON 한 줄로 직렬화해 파일에 추가한다."""

        fallback = str if use_fallback else None
        line = json.dumps(
            record,
            ensure_ascii=False,
            allow_nan=False,
            default=fallback,
        )
        file.write(f"{line}\n")


class MongoSink:
    """정상 문서와 제외 문서를 서로 다른 MongoDB 데이터베이스에 저장한다.

    한 번의 실행에서 같은 `_id`를 다시 쓰면 upsert하므로 재시도 시 정상 문서가
    중복 생성되지 않는다. 실패 문서는 원본/표준화 문서와 사유를 함께 보존한다.
    """

    def __init__(
        self,
        *,
        uri_env: str,
        success_database: str,
        success_collection: str,
        failure_database: str,
        failure_collection: str,
        run_id: str,
        report_database: str | None = None,
        report_collection: str = "_pipeline_runs",
        bronze_database: str | None = None,
        bronze_collection: str = "bronze_raw_records",
        manifest_collection: str = "bronze_manifest",
        silver_database: str | None = None,
        silver_collections: Mapping[str, str] | None = None,
        batch_size: int = 500,
        rule_version: str | None = None,
        local_report_root: str | Path | None = None,
    ) -> None:
        """MongoDB 연결, 정상/실패 대상, 실행별 버퍼를 준비한다."""

        if not uri_env:
            raise ValueError("MongoSink의 uri_env는 비어 있을 수 없습니다.")
        if not all(
            [
                success_database,
                success_collection,
                failure_database,
                failure_collection,
                bronze_collection,
                manifest_collection,
                report_collection,
                run_id,
            ]
        ):
            raise ValueError("MongoSink의 DB, 컬렉션, run_id는 비어 있을 수 없습니다.")
        if batch_size <= 0:
            raise ValueError("MongoSink의 batch_size는 1 이상이어야 합니다.")

        self._uri_env = uri_env
        self._success_database = success_database
        self._success_collection_name = success_collection
        self._failure_database = failure_database
        self._failure_collection_name = failure_collection
        self._report_database = report_database or success_database
        self._report_collection_name = report_collection
        self._bronze_database = bronze_database or "second_project"
        self._bronze_collection_name = bronze_collection
        self._manifest_collection_name = manifest_collection
        self._silver_database = silver_database or success_database
        configured_silver_collections = dict(silver_collections or {})
        self._silver_collection_names = {
            model_name: configured_silver_collections.get(model_name, model_name)
            for model_name in SILVER_MODEL_NAMES
        }
        self._run_id = run_id
        self._batch_size = batch_size
        self._rule_version = rule_version
        self._client: Any | None = None
        self._success_collection: Any | None = None
        self._failure_collection: Any | None = None
        self._report_collection: Any | None = None
        self._bronze_collection: Any | None = None
        self._manifest_collection: Any | None = None
        self._silver_collections: dict[str, Any] = {}
        self._success_buffer: list[dict[str, Any]] = []
        self._failure_buffer: list[dict[str, Any]] = []
        self._bronze_buffer: list[dict[str, Any]] = []
        self._silver_buffers: dict[str, list[dict[str, Any]]] = {
            model_name: [] for model_name in SILVER_MODEL_NAMES
        }
        self._closed = False
        self._ingested_at = _now_utc()

        self._local_report_path: Path | None = None
        self._local_bronze_path: Path | None = None
        self._local_manifest_path: Path | None = None
        self._local_bronze_file: TextIO | None = None
        if local_report_root is not None:
            run_directory = Path(local_report_root) / run_id
            run_directory.mkdir(parents=True, exist_ok=True)
            self._local_report_path = run_directory / "report.json"
            self._local_bronze_path = run_directory / "bronze_raw_records.jsonl"
            self._local_manifest_path = run_directory / "manifest.json"
            self._local_bronze_file = self._local_bronze_path.open(
                "w", encoding="utf-8"
            )

    @property
    def description(self) -> dict[str, Any]:
        """URI 비밀번호 없이 정상/실패/리포트 저장 위치를 반환한다."""

        description: dict[str, Any] = {
            "type": "mongodb",
            "success": {
                "database": self._success_database,
                "collection": self._success_collection_name,
            },
            "failure": {
                "database": self._failure_database,
                "collection": self._failure_collection_name,
            },
            "report": {
                "database": self._report_database,
                "collection": self._report_collection_name,
            },
            "bronze": {
                "database": self._bronze_database,
                "collection": self._bronze_collection_name,
            },
            "manifest": {
                "database": self._bronze_database,
                "collection": self._manifest_collection_name,
            },
            "silver_models": {
                model_name: {
                    "database": self._silver_database,
                    "collection": collection_name,
                }
                for model_name, collection_name in self._silver_collection_names.items()
            },
        }
        if self._local_report_path is not None:
            description["local_report"] = str(self._local_report_path.resolve())
        if self._local_bronze_path is not None:
            description["local_bronze"] = str(self._local_bronze_path.resolve())
        if self._local_manifest_path is not None:
            description["local_manifest"] = str(self._local_manifest_path.resolve())
        return description

    def write_success(self, document: dict[str, Any]) -> None:
        """검증 통과 문서를 정상 DB 버퍼에 추가한다."""

        self._ensure_open()
        models = split_silver_models(document)
        if not models:
            prepared = self._prepare_success_document(document)
            self._success_buffer.append(prepared)
            if len(self._success_buffer) >= self._batch_size:
                self._flush_success()
            return

        for model_name, model in models.items():
            prepared = self._prepare_success_document(model)
            self._silver_buffers[model_name].append(prepared)
            if len(self._silver_buffers[model_name]) >= self._batch_size:
                self._flush_silver(model_name)

    def write_bronze(self, record: dict[str, Any], *, persist: bool = True) -> None:
        """표준화 전 원본 보존 레코드를 Bronze DB와 선택적 파일에 추가한다."""

        self._ensure_open()
        prepared = _safe_json_value(record)
        if not isinstance(prepared, dict):
            raise RuntimeError("Bronze 레코드를 object로 저장할 수 없습니다.")
        if self._local_bronze_file is not None:
            self._local_bronze_file.write(
                json.dumps(prepared, ensure_ascii=False, allow_nan=False, default=str)
                + "\n"
            )
        if persist:
            self._bronze_buffer.append(prepared)
            if len(self._bronze_buffer) >= self._batch_size:
                self._flush_bronze()

    def write_manifest(self, manifest: dict[str, Any]) -> str:
        """Bronze 실행 Manifest를 DB와 선택적 로컬 파일에 저장한다."""

        self._ensure_open()
        self._flush_bronze()
        self._ensure_client()
        safe_manifest = _safe_json_value(manifest)
        if not isinstance(safe_manifest, dict):
            raise RuntimeError("Manifest를 object로 저장할 수 없습니다.")
        manifest_document = dict(safe_manifest)
        manifest_document["_id"] = self._run_id
        assert self._manifest_collection is not None
        self._upsert_documents(self._manifest_collection, [manifest_document])

        if self._local_manifest_path is not None:
            with self._local_manifest_path.open("w", encoding="utf-8") as file:
                json.dump(
                    safe_manifest,
                    file,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                file.write("\n")
            return str(self._local_manifest_path.resolve())
        return (
            f"mongodb://{self._bronze_database}/"
            f"{self._manifest_collection_name}/{self._run_id}"
        )

    def write_rejected(
        self,
        *,
        document_id: str | None,
        stage: str,
        reasons: list[dict[str, Any]],
        document: Any | None = None,
    ) -> None:
        """실패 문서와 원인, 실행 메타데이터를 실패 DB 버퍼에 추가한다."""

        self._ensure_open()
        safe_document = _safe_json_value(document) if document is not None else None
        record = _quarantine_record(
            run_id=self._run_id,
            document_id=document_id,
            stage=stage,
            reasons=reasons,
            document=safe_document,
        )
        record.update(
            {
                "document": safe_document,
                "reprocess_status": "pending",
                "attempt_count": 0,
                "reprocess_history": [],
                "_pipeline": {
                    "run_id": self._run_id,
                    "batch_id": self._run_id,
                    "ingested_at": self._ingested_at,
                },
            }
        )
        if self._rule_version:
            record["_pipeline"]["rule_version"] = self._rule_version
        identity = document_id or _stable_hash(document)
        record["_id"] = f"failure:{stage}:{identity}"
        self._failure_buffer.append(record)
        if len(self._failure_buffer) >= self._batch_size:
            self._flush_failure()

    def write_report(self, report: dict[str, Any]) -> str:
        """정상/실패 버퍼를 비우고 리포트를 DB와 선택적 로컬 파일에 저장한다."""

        self._ensure_open()
        self._flush_bronze()
        self._flush_success()
        self._flush_all_silver()
        self._flush_failure()

        safe_report = _safe_json_value(report)
        if not isinstance(safe_report, dict):
            raise RuntimeError("실행 리포트를 object로 저장할 수 없습니다.")
        report_document = dict(safe_report)
        report_document["_id"] = self._run_id
        report_document["stored_at"] = _now_utc()
        assert self._report_collection is not None
        self._upsert_documents(self._report_collection, [report_document])

        if self._local_report_path is not None:
            with self._local_report_path.open("w", encoding="utf-8") as file:
                json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)
                file.write("\n")
            return str(self._local_report_path.resolve())
        return (
            f"mongodb://{self._report_database}/"
            f"{self._report_collection_name}/{self._run_id}"
        )

    def flush(self) -> None:
        """현재 버퍼를 MongoDB에 반영한다."""

        self._ensure_open()
        if self._local_bronze_file is not None:
            self._local_bronze_file.flush()
        self._flush_bronze()
        self._flush_success()
        self._flush_all_silver()
        self._flush_failure()

    def mark_reprocess_resolved(
        self,
        *,
        failure_id: Any,
        reprocess_run_id: str,
        attempt_count: int,
        success_document_id: Any,
    ) -> None:
        """재처리 성공 원본의 실패 큐 상태를 resolved로 갱신한다."""

        self._ensure_open()
        self._ensure_client()
        assert self._failure_collection is not None
        now = _now_utc()
        result = self._failure_collection.update_one(
            {"_id": failure_id},
            {
                "$set": {
                    "reprocess_status": "resolved",
                    "attempt_count": attempt_count,
                    "last_reprocess_run_id": reprocess_run_id,
                    "resolved_at": now,
                    "last_attempt_at": now,
                    "resolved_document_id": success_document_id,
                },
                "$push": {
                    "reprocess_history": {
                        "run_id": reprocess_run_id,
                        "attempt": attempt_count,
                        "attempted_at": now,
                        "status": "resolved",
                        "document_id": success_document_id,
                    }
                },
            },
            upsert=False,
        )
        _require_update_match(result, failure_id)

    def record_reprocess_failure(
        self,
        *,
        failure_id: Any,
        reprocess_run_id: str,
        attempt_count: int,
        max_attempts: int,
        stage: str,
        reasons: list[dict[str, Any]],
        document: Any | None,
    ) -> str:
        """재처리 실패 원인과 시도 이력을 기존 실패 문서에 누적한다."""

        self._ensure_open()
        self._ensure_client()
        assert self._failure_collection is not None
        status = "exhausted" if attempt_count >= max_attempts else "retry"
        now = _now_utc()
        safe_document = _safe_json_value(document) if document is not None else None
        safe_reasons = _safe_json_value(reasons)
        result = self._failure_collection.update_one(
            {"_id": failure_id},
            {
                "$set": {
                    "stage": stage,
                    "reasons": safe_reasons,
                    "document": safe_document,
                    "reprocess_status": status,
                    "attempt_count": attempt_count,
                    "last_reprocess_run_id": reprocess_run_id,
                    "last_attempt_at": now,
                },
                "$push": {
                    "reprocess_history": {
                        "run_id": reprocess_run_id,
                        "attempt": attempt_count,
                        "attempted_at": now,
                        "status": status,
                        "stage": stage,
                        "reasons": safe_reasons,
                    }
                },
            },
            upsert=False,
        )
        _require_update_match(result, failure_id)
        return status

    def close(self) -> None:
        """버퍼를 비우고 연결을 닫는다."""

        if self._closed:
            return
        try:
            self._flush_bronze()
            self._flush_success()
            self._flush_all_silver()
            self._flush_failure()
        finally:
            if self._client is not None:
                self._close_client()
                self._client = None
            if self._local_bronze_file is not None:
                self._local_bronze_file.close()
                self._local_bronze_file = None
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("이미 닫힌 MongoSink에 쓸 수 없습니다.")

    def _create_client(self) -> Any:
        try:
            from pymongo import MongoClient
        except ImportError as error:
            raise RuntimeError(
                "pymongo가 필요합니다. `pip install -e .`로 설치해 주세요."
            ) from error

        uri = os.getenv(self._uri_env)
        if not uri:
            raise RuntimeError(
                f"MongoDB URI 환경 변수 `{self._uri_env}`가 설정되지 않았습니다."
            )
        return MongoClient(uri, serverSelectionTimeoutMS=10_000)

    def _close_client(self) -> None:
        """하위 클래스가 Django가 관리하는 client를 보존할 수 있는 확장 지점."""

        if self._client is not None:
            self._client.close()

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        self._client = self._create_client()
        self._client.admin.command("ping")
        self._success_collection = self._client[self._success_database][
            self._success_collection_name
        ]
        self._failure_collection = self._client[self._failure_database][
            self._failure_collection_name
        ]
        self._report_collection = self._client[self._report_database][
            self._report_collection_name
        ]
        self._bronze_collection = self._client[self._bronze_database][
            self._bronze_collection_name
        ]
        self._manifest_collection = self._client[self._bronze_database][
            self._manifest_collection_name
        ]
        self._silver_collections = {
            model_name: self._client[self._silver_database][collection_name]
            for model_name, collection_name in self._silver_collection_names.items()
        }

    def _flush_success(self) -> None:
        if not self._success_buffer:
            return
        self._ensure_client()
        assert self._success_collection is not None
        self._upsert_documents(self._success_collection, self._success_buffer)
        self._success_buffer.clear()

    def _flush_bronze(self) -> None:
        if not self._bronze_buffer:
            return
        self._ensure_client()
        assert self._bronze_collection is not None
        update_one = _get_update_one()
        if update_one is None or not hasattr(self._bronze_collection, "bulk_write"):
            self._upsert_documents(self._bronze_collection, self._bronze_buffer)
            self._bronze_buffer.clear()
            return

        keyed: dict[tuple[str, str], dict[str, Any]] = {}
        unkeyed: list[dict[str, Any]] = []
        for document in self._bronze_buffer:
            dataset_id = document.get("dataset_id")
            source_record_id = document.get("source_record_id")
            if dataset_id in (None, "") or source_record_id in (None, ""):
                unkeyed.append(document)
                continue
            keyed[(str(dataset_id), str(source_record_id))] = document

        if keyed:
            operations = [
                update_one(
                    {
                        "dataset_id": dataset_id,
                        "source_record_id": source_record_id,
                    },
                    {"$setOnInsert": document},
                    upsert=True,
                )
                for (dataset_id, source_record_id), document in keyed.items()
            ]
            self._bronze_collection.bulk_write(operations, ordered=False)
        if unkeyed:
            self._upsert_documents(self._bronze_collection, unkeyed)
        self._bronze_buffer.clear()

    def _flush_failure(self) -> None:
        if not self._failure_buffer:
            return
        self._ensure_client()
        assert self._failure_collection is not None
        self._upsert_documents(self._failure_collection, self._failure_buffer)
        self._failure_buffer.clear()

    def _flush_silver(self, model_name: str) -> None:
        buffer = self._silver_buffers[model_name]
        if not buffer:
            return
        self._ensure_client()
        collection = self._silver_collections[model_name]
        self._upsert_documents(collection, buffer)
        buffer.clear()

    def _flush_all_silver(self) -> None:
        for model_name in SILVER_MODEL_NAMES:
            self._flush_silver(model_name)

    def _prepare_success_document(self, document: Mapping[str, Any]) -> dict[str, Any]:
        prepared = dict(document)
        prepared["_pipeline"] = _merge_pipeline_metadata(
            prepared.get("_pipeline"),
            run_id=self._run_id,
            ingested_at=self._ingested_at,
            rule_version=self._rule_version,
        )
        _ensure_document_id(prepared)
        return prepared

    @staticmethod
    def _upsert_documents(collection: Any, documents: list[dict[str, Any]]) -> None:
        """동일 키는 마지막 값으로 deduplicate한 뒤 bulk upsert한다."""

        latest: dict[str, dict[str, Any]] = {}
        for document in documents:
            key = document["_id"]
            token = json.dumps(key, ensure_ascii=False, sort_keys=True, default=str)
            latest[token] = document
        unique_documents = list(latest.values())

        replace_one = _get_replace_one()
        if replace_one is not None and hasattr(collection, "bulk_write"):
            operations = [
                replace_one(
                    {"_id": document["_id"]},
                    document,
                    upsert=True,
                )
                for document in unique_documents
            ]
            collection.bulk_write(operations, ordered=False)
            return

        for document in unique_documents:
            collection.replace_one(
                {"_id": document["_id"]},
                document,
                upsert=True,
            )


class DjangoMongoSink(MongoSink):
    """Django의 `django_mongodb_backend` 연결을 재사용하는 Mongo sink."""

    def __init__(
        self,
        *,
        database_alias: str = "mongodb",
        settings_module: str = "config.settings",
        project_root: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        """Django 설정 모듈과 database alias를 저장한다."""

        self._database_alias = database_alias
        self._settings_module = settings_module
        self._project_root = Path(project_root) if project_root is not None else None
        self._django_connection: Any | None = None
        super().__init__(uri_env="django-managed", **kwargs)

    @property
    def description(self) -> dict[str, Any]:
        """저장소 종류와 Django database alias를 리포트에 포함한다."""

        description = super().description
        description["type"] = "django_mongodb"
        description["database_alias"] = self._database_alias
        description["settings_module"] = self._settings_module
        return description

    def _create_client(self) -> Any:
        """Django를 초기화하고 backend가 관리하는 MongoClient를 반환한다."""

        if self._project_root is not None:
            project_root = str(self._project_root.resolve())
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", self._settings_module)

        try:
            import django
            from django.db import connections
        except ImportError as error:
            raise RuntimeError(
                "Django MongoDB sink에는 Django와 django-mongodb-backend가 필요합니다."
            ) from error

        try:
            django.setup()
            connection = connections[self._database_alias]
            connection.ensure_connection()
        except Exception as error:
            raise RuntimeError(
                f"Django database alias `{self._database_alias}`에 연결할 수 없습니다."
            ) from error

        client = getattr(connection, "connection", None)
        if client is None:
            raise RuntimeError(
                f"Django database alias `{self._database_alias}`가 MongoClient를 열지 못했습니다."
            )
        self._django_connection = connection
        return client

    def _close_client(self) -> None:
        """Django가 수명 주기를 관리하므로 연결 풀을 닫지 않는다."""

        self._client = None


def _get_replace_one() -> Any | None:
    """PyMongo ReplaceOne을 지연 로드해 테스트와 선택적 설치를 지원한다."""

    try:
        from pymongo import ReplaceOne
    except ImportError:
        return None
    return ReplaceOne


def _get_update_one() -> Any | None:
    """PyMongo UpdateOne을 지연 로드해 Bronze business key upsert를 지원한다."""

    try:
        from pymongo import UpdateOne
    except ImportError:
        return None
    return UpdateOne


def _quarantine_record(
    *,
    run_id: str,
    document_id: str | None,
    stage: str,
    reasons: list[dict[str, Any]],
    document: Any | None,
) -> dict[str, Any]:
    """문서 원문과 분리해 재처리 가능한 격리 메타데이터를 만든다."""

    identity = document_id or _stable_hash(document)
    quarantine_id = f"{run_id}:{stage}:{identity}"
    first_reason = reasons[0] if reasons and isinstance(reasons[0], Mapping) else {}
    source_record_id = _document_value(
        document,
        "source.record_id",
        "source_record_id",
        "record_id",
        "_id",
    )
    if source_record_id is None:
        source_record_id = document_id or f"unknown:{_stable_hash(document)}"
    raw_reference = _document_value(
        document,
        "raw_reference",
        "bronze_record_id",
        "source.record_id",
        "source_record_id",
        "record_id",
        "_id",
    )
    if raw_reference is None:
        raw_reference = source_record_id

    return {
        "quarantine_id": quarantine_id,
        "run_id": run_id,
        "source_record_id": str(source_record_id),
        "document_id": document_id,
        "stage": stage,
        "rule_id": _reason_value(first_reason, "rule_id", "rule") or "UNKNOWN_RULE",
        "error_code": _reason_value(first_reason, "error_code") or "PIPELINE_ERROR",
        "quarantined_at": _now_utc(),
        "raw_reference": str(raw_reference),
        "reasons": _safe_json_value(reasons),
    }


def _reason_value(reason: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = reason.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _document_value(document: Any, *paths: str) -> Any | None:
    """문서에서 로그·격리 메타데이터로 사용할 식별자만 찾는다."""

    current_document = document
    for path in paths:
        current: Any = current_document
        for key in path.split("."):
            if not isinstance(current, Mapping) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None and current != "":
            return current
    return None


def _ensure_document_id(document: dict[str, Any]) -> Any:
    """문서에 안정적인 `_id`가 없으면 내용 해시로 보완한다."""

    current = document.get("_id")
    if current is None or isinstance(current, (Mapping, list, tuple)):
        # 실행 시각·run_id가 들어가는 `_pipeline`은 재실행마다 달라지므로
        # 내용 기반 identity에서 제외해야 scheduled upsert가 중복을 만들지 않는다.
        identity_document = {
            key: value for key, value in document.items() if key != "_pipeline"
        }
        current = f"sha256:{_stable_hash(identity_document)}"
        document["_id"] = current
    return current


def _merge_pipeline_metadata(
    existing: Any,
    *,
    run_id: str,
    ingested_at: str,
    rule_version: str | None,
) -> dict[str, Any]:
    """기존 `_pipeline` 메타데이터를 보존하면서 실행 정보를 추가한다."""

    metadata = dict(existing) if isinstance(existing, Mapping) else {}
    metadata.setdefault("run_id", run_id)
    metadata.setdefault("batch_id", run_id)
    metadata.setdefault("ingested_at", ingested_at)
    if rule_version:
        metadata.setdefault("rule_version", rule_version)
    return metadata


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_hash(value: Any) -> str:
    """JSON으로 표현 가능한 형태의 SHA-256 해시를 만든다."""

    safe_value = _safe_json_value(value)
    encoded = json.dumps(
        safe_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_json_value(value: Any) -> Any:
    """Mongo 실패 문서에 넣을 값을 JSON-safe 값으로 변환한다."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError):
        return repr(value)


def _require_update_match(result: Any, failure_id: Any) -> None:
    """실패 큐 원본이 사라진 경우 조용히 성공 처리하지 않도록 확인한다."""

    matched = getattr(result, "matched_count", None)
    if matched is not None and matched == 0:
        raise RuntimeError(f"재처리 실패 문서를 찾을 수 없습니다: {failure_id!r}")
