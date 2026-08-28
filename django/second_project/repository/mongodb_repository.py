"""MongoDB repository owned by the second_project app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

try:
    from pymongo import UpdateOne, WriteConcern
    from pymongo.errors import BulkWriteError, PyMongoError
except ImportError:  # pragma: no cover - exercised in environments without dependencies
    UpdateOne = None
    WriteConcern = None
    BulkWriteError = Exception
    PyMongoError = Exception

from second_project.service.bronze_config import LoaderConfig


class MongoDependencyError(RuntimeError):
    """Raised when PyMongo is not installed."""


class MongoStoreError(RuntimeError):
    """Raised when MongoDB cannot safely complete a write."""

    error_code: str | None = None


class ChecksumConflictError(MongoStoreError):
    """Raised when one logical source record has different source hashes."""

    error_code = "CHECKSUM_MISMATCH"


@dataclass(frozen=True)
class RawWriteResult:
    inserted_count: int
    duplicate_count: int
    inserted_ids: tuple[str, ...]


class MongoRepository:
    """Own the app's MongoDB collections through Django's database alias.

    The repository uses the MongoDB connection created by Django's configured
    ``mongodb`` alias.  It issues explicit MongoDB queries because the Bronze
    collections contain append-only documents and operational metadata rather
    than ordinary relational rows.  No MongoDB client is created outside
    Django's connection management.
    """

    def __init__(self, config: LoaderConfig) -> None:
        if UpdateOne is None or WriteConcern is None:
            raise MongoDependencyError(
                "PyMongo가 설치되어 있지 않습니다. requirements.txt를 설치하세요."
            )

        try:
            from django.conf import settings
            from django.db import connections
        except ImportError as exc:
            raise MongoDependencyError(
                "Django가 설치되어 있지 않습니다. Django 가상환경을 활성화하세요."
            ) from exc
        database_settings = settings.DATABASES.get(config.mongo_alias)
        if not database_settings:
            raise MongoStoreError(
                f"Django DATABASES에 {config.mongo_alias} 별칭이 없습니다."
            )
        connection_database_name = database_settings.get("NAME")
        if not isinstance(connection_database_name, str) or not connection_database_name:
            raise MongoStoreError("MongoDB 연결 데이터베이스명이 설정되지 않았습니다.")
        database_name = config.database
        if not isinstance(database_name, str) or not database_name:
            raise MongoStoreError("Bronze 대상 데이터베이스명이 설정되지 않았습니다.")

        try:
            self.django_connection = connections[config.mongo_alias]
            self.django_connection.ensure_connection()
            self.client = self.django_connection.connection
            # Django alias의 NAME(db_mount)과 애플리케이션이 사용할 Bronze
            # database(second_project)는 다를 수 있다. 연결의 기본 DB에
            # 의존하면 loader와 validation/dashboard가 서로 다른 컬렉션을
            # 보게 되므로, LoaderConfig의 대상 DB를 명시적으로 사용한다.
            self.database = self.client[database_name]
            write_concern = WriteConcern(w="majority")
            self.raw_records = self.database.get_collection(
                config.raw_collection,
                write_concern=write_concern,
            )
            self.runs = self.database.get_collection(
                config.run_collection,
                write_concern=write_concern,
            )
            self.manifests = self.database.get_collection(
                config.manifest_collection,
                write_concern=write_concern,
            )
            self.quarantine = self.database.get_collection(
                config.quarantine_collection,
                write_concern=write_concern,
            )
        except PyMongoError as exc:
            raise MongoStoreError("MongoDB 클라이언트를 초기화하지 못했습니다.") from exc

    @staticmethod
    def _index_keys(index: Any) -> list[tuple[str, Any]]:
        """Return index keys in their declared order for safe comparison."""

        return list(index.get("key", {}).items())

    def _ensure_index(
        self,
        collection: Any,
        keys: list[tuple[str, Any]],
        *,
        name: str,
        unique: bool = False,
    ) -> str:
        """Create an index only when an equivalent one is not already present.

        MongoDB rejects creating the same key pattern under a different name.
        An existing equivalent index is therefore reused instead of being
        dropped or renamed, which keeps loader startup idempotent and safe.
        """

        target_keys = list(keys)
        for existing in collection.list_indexes():
            if self._index_keys(existing) != target_keys:
                continue

            existing_unique = bool(existing.get("unique", False))
            if existing_unique != unique:
                raise MongoStoreError(
                    "기존 MongoDB 인덱스의 unique 옵션이 설계와 다릅니다: "
                    f"{existing.get('name', '<unknown>')}"
                )
            return str(existing["name"])

        return str(collection.create_index(keys, unique=unique, name=name))

    def ping_and_prepare(self) -> None:
        try:
            self.client.admin.command("ping")
            self._ensure_index(
                self.raw_records,
                [("dataset_id", 1), ("source_record_id", 1)],
                unique=True,
                name="uq_dataset_source_record",
            )
            self._ensure_index(
                self.raw_records,
                [("source_sha256", 1)],
                name="idx_source_sha256",
            )
            self._ensure_index(
                self.raw_records,
                [("source_record_sha256", 1)],
                name="idx_source_record_sha256",
            )
            self._ensure_index(
                self.raw_records,
                [("run_id", 1)],
                name="idx_run_id",
            )
            self._ensure_index(
                self.raw_records,
                [("scheduled_release_at", 1)],
                name="idx_scheduled_release_at",
            )
            self._ensure_index(
                self.runs,
                [("run_id", 1)],
                unique=True,
                name="uq_run_id",
            )
            self._ensure_index(
                self.runs,
                [("status", 1), ("started_at", -1)],
                name="idx_run_status",
            )
            self._ensure_index(
                self.manifests,
                [("run_id", 1)],
                unique=True,
                name="uq_manifest_run_id",
            )
            self._ensure_index(
                self.quarantine,
                [("run_id", 1), ("line_no", 1)],
                name="idx_quarantine_run_line",
            )
        except PyMongoError as exc:
            raise MongoStoreError(
                "MongoDB 연결 또는 Bronze 인덱스 준비에 실패했습니다."
            ) from exc

    def start_run(self, document: dict[str, Any]) -> None:
        try:
            self.runs.insert_one(document)
        except PyMongoError as exc:
            raise MongoStoreError("Bronze 실행 이력을 시작하지 못했습니다.") from exc

    def update_run(self, run_id: str, values: dict[str, Any]) -> None:
        try:
            self.runs.update_one({"_id": run_id}, {"$set": values})
        except PyMongoError as exc:
            raise MongoStoreError("Bronze 실행 이력을 갱신하지 못했습니다.") from exc

    def save_manifest(self, document: dict[str, Any]) -> None:
        try:
            self.manifests.replace_one(
                {"_id": document["_id"]},
                document,
                upsert=True,
            )
        except PyMongoError as exc:
            raise MongoStoreError("Bronze manifest를 저장하지 못했습니다.") from exc

    def upsert_raw_batch(self, documents: Iterable[dict[str, Any]]) -> RawWriteResult:
        """Insert new raw documents and accept exact replays without overwriting."""

        source_documents = list(documents)
        if not source_documents:
            return RawWriteResult(0, 0, ())

        unique_documents: list[dict[str, Any]] = []
        seen_by_id: dict[str, str] = {}
        duplicate_count = 0
        for document in source_documents:
            document_id = str(document["_id"])
            source_hash = str(document["source_record_sha256"])
            previous_hash = seen_by_id.get(document_id)
            if previous_hash is not None:
                if previous_hash != source_hash:
                    raise ChecksumConflictError(
                        "같은 source_record_id에 서로 다른 source_record_sha256가 있습니다."
                    )
                duplicate_count += 1
                continue
            seen_by_id[document_id] = source_hash
            unique_documents.append(document)

        try:
            existing = {
                item["_id"]: item
                for item in self.raw_records.find(
                    {"_id": {"$in": [item["_id"] for item in unique_documents]}},
                    {"source_record_sha256": 1, "source_sha256": 1},
                )
            }
        except PyMongoError as exc:
            raise MongoStoreError("기존 Bronze 레코드를 확인하지 못했습니다.") from exc

        new_documents: list[dict[str, Any]] = []
        for document in unique_documents:
            existing_document = existing.get(document["_id"])
            if existing_document is None:
                new_documents.append(document)
                continue
            if existing_document.get("source_record_sha256") != document["source_record_sha256"]:
                raise ChecksumConflictError(
                    "기존 Bronze 레코드와 source_record_sha256가 다릅니다."
                )
            if existing_document.get("source_sha256") != document["source_sha256"]:
                raise ChecksumConflictError(
                    "기존 Bronze 레코드와 source_sha256가 다릅니다."
                )
            duplicate_count += 1

        if not new_documents:
            return RawWriteResult(0, duplicate_count, ())

        operations = [
            UpdateOne(
                {"_id": document["_id"]},
                {"$setOnInsert": document},
                upsert=True,
            )
            for document in new_documents
        ]
        try:
            result = self.raw_records.bulk_write(operations, ordered=False)
        except BulkWriteError as exc:
            raise MongoStoreError("Bronze raw batch 적재에 실패했습니다.") from exc
        except PyMongoError as exc:
            raise MongoStoreError("Bronze raw batch 적재에 실패했습니다.") from exc

        inserted_ids = tuple(
            str(new_documents[index]["_id"])
            for index in result.upserted_ids
        )
        duplicate_count += len(new_documents) - result.upserted_count
        return RawWriteResult(result.upserted_count, duplicate_count, inserted_ids)

    def verify_inserted_batch(self, documents: Iterable[dict[str, Any]]) -> None:
        source_documents = list(documents)
        if not source_documents:
            return
        ids = [document["_id"] for document in source_documents]
        try:
            stored = {
                item["_id"]: item.get("raw_line_sha256")
                for item in self.raw_records.find(
                    {"_id": {"$in": ids}},
                    {"raw_line_sha256": 1},
                )
            }
        except PyMongoError as exc:
            raise MongoStoreError("Bronze 원본 무결성을 확인하지 못했습니다.") from exc

        for document in source_documents:
            if stored.get(document["_id"]) != document["raw_line_sha256"]:
                raise ChecksumConflictError(
                    "MongoDB에 저장된 Bronze 원본 체크섬이 입력과 다릅니다."
                )

    def insert_quarantine_batch(self, documents: Iterable[dict[str, Any]]) -> None:
        source_documents = list(documents)
        if not source_documents:
            return
        operations = [
            UpdateOne(
                {"_id": document["_id"]},
                {"$setOnInsert": document},
                upsert=True,
            )
            for document in source_documents
        ]
        try:
            self.quarantine.bulk_write(operations, ordered=False)
        except PyMongoError as exc:
            raise MongoStoreError("Bronze quarantine 적재에 실패했습니다.") from exc

    def close(self) -> None:
        # django-mongodb-backend owns the pooled MongoClient.  Its close()
        # method releases request-level state without closing the shared pool.
        self.django_connection.close()


# Compatibility name for callers that used the old loading.mongo_store module.
MongoStore = MongoRepository
