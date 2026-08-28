from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import DataLakeCollectionConfig, DataLakeConfig, SinkConfig
from .time_utils import iso_utc


class DjangoMongoDataLakeBackup:
    """원천 collection을 파일과 별도 DATA-LAKE MongoDB에 시간별 snapshot으로 보존한다."""

    def __init__(
        self,
        config: DataLakeConfig,
        *,
        sink_config: SinkConfig,
    ) -> None:
        """DATA-LAKE 설정과 정상·실패·리포트 대상의 기본값을 저장한다."""

        self._config = config
        self._sink_config = sink_config
        self._client: Any | None = None

    def run(self, *, now: datetime | None = None) -> dict[str, Any]:
        """대상 collection을 내보내고 manifest를 원자적으로 기록한다."""

        if not self._config.enabled:
            raise RuntimeError("DATA-LAKE backup이 비활성화되어 있습니다.")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current_utc = current.astimezone(timezone.utc)
        current_kst = current_utc.astimezone(ZoneInfo("Asia/Seoul"))
        backup_id = current_utc.strftime("%Y%m%dT%H%M%SZ")
        backup_directory = (
            self._config.root
            / current_kst.strftime("%Y")
            / current_kst.strftime("%m")
            / current_kst.strftime("%d")
            / current_kst.strftime("%H")
            / backup_id
        )
        backup_directory.mkdir(parents=True, exist_ok=True)

        targets = self._targets()
        if self._config.database in {target.database for target in targets}:
            raise ValueError(
                "DATA-LAKE database는 백업 원천 database와 달라야 합니다: "
                f"{self._config.database}"
            )
        client = self._ensure_client()
        objects: list[dict[str, Any]] = []
        try:
            for target in targets:
                objects.append(
                    self._backup_collection(
                        client,
                        target,
                        backup_directory,
                        backup_id=backup_id,
                        backed_up_at=iso_utc(current_utc),
                    )
                )
        except Exception:
            # 부분 파일은 다음 동일 slot 실행에서 재생성할 수 있도록 제거한다.
            for path in backup_directory.glob("*.jsonl"):
                path.unlink(missing_ok=True)
            raise

        manifest = {
            "backup_id": backup_id,
            "created_at": iso_utc(current_utc),
            "slot_timezone": "Asia/Seoul",
            "slot": current_kst.strftime("%Y-%m-%dT%H:00:00+09:00"),
            "format": "jsonl",
            "source": {
                "type": "django_mongodb",
                "database_alias": self._config.database_alias,
                "settings_module": self._config.settings_module,
            },
            "destination": {
                "database": self._config.database,
                "manifest_collection": self._config.manifest_collection,
            },
            "objects": objects,
            "status": "SUCCESS",
        }
        self._write_mongodb_manifest(client, manifest)
        manifest_path = backup_directory / "manifest.json"
        _atomic_json_write(manifest_path, manifest)
        return {
            **manifest,
            "manifest_path": str(manifest_path.resolve()),
        }

    def close(self) -> None:
        """Django connection pool은 Django 수명주기에 맡긴다."""

        self._client = None

    def _targets(self) -> tuple[DataLakeCollectionConfig, ...]:
        if self._config.collections:
            return self._config.collections
        defaults = [
            DataLakeCollectionConfig(
                database=self._sink_config.bronze_database,
                collection=self._sink_config.bronze_collection,
                name="bronze_raw_records",
            ),
            DataLakeCollectionConfig(
                database=self._sink_config.success_database,
                collection=self._sink_config.success_collection,
                name="success_records",
            ),
            DataLakeCollectionConfig(
                database=self._sink_config.failure_database,
                collection=self._sink_config.failure_collection,
                name="failure_records",
            ),
            DataLakeCollectionConfig(
                database=self._sink_config.report_database or self._sink_config.success_database,
                collection=self._sink_config.report_collection,
                name="pipeline_runs",
            ),
        ]
        unique: dict[tuple[str, str], DataLakeCollectionConfig] = {}
        for target in defaults:
            unique[(target.database, target.collection)] = target
        return tuple(unique.values())

    def _write_mongodb_manifest(
        self,
        client: Any,
        manifest: Mapping[str, Any],
    ) -> None:
        """별도 DATA-LAKE DB에 실행별 manifest를 upsert한다."""

        client[self._config.database][self._config.manifest_collection].update_one(
            {"_id": manifest["backup_id"]},
            {"$set": dict(manifest)},
            upsert=True,
        )

    def _backup_collection(
        self,
        client: Any,
        target: DataLakeCollectionConfig,
        directory: Path,
        *,
        backup_id: str,
        backed_up_at: str,
    ) -> dict[str, Any]:
        collection = client[target.database][target.collection]
        label = _safe_name(target.name or f"{target.database}__{target.collection}")
        final_path = directory / f"{label}.jsonl"
        temp_path = directory / f".{label}.jsonl.tmp"
        digest = hashlib.sha256()
        row_count = 0
        snapshot_collection = client[self._config.database][
            _safe_name(target.name or f"{target.database}__{target.collection}")
        ]
        snapshot_operations: list[Any] = []
        try:
            from pymongo import ReplaceOne
        except ImportError as error:
            raise RuntimeError(
                "DATA-LAKE MongoDB backup에는 pymongo가 필요합니다."
            ) from error
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                for document in collection.find({}, batch_size=self._config.batch_size):
                    safe_document = _json_safe(document)
                    line = json.dumps(
                        safe_document,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    )
                    encoded = (line + "\n").encode("utf-8")
                    file.write(line)
                    file.write("\n")
                    digest.update(encoded)
                    row_count += 1
                    snapshot_id = _snapshot_id(
                        backup_id=backup_id,
                        database=target.database,
                        collection=target.collection,
                        document=safe_document,
                    )
                    snapshot_operations.append(
                        ReplaceOne(
                            {"_id": snapshot_id},
                            {
                                "_id": snapshot_id,
                                "backup_id": backup_id,
                                "backed_up_at": backed_up_at,
                                "source_database": target.database,
                                "source_collection": target.collection,
                                "source_id": safe_document.get("_id"),
                                "document": safe_document,
                            },
                            upsert=True,
                        )
                    )
                    if len(snapshot_operations) >= self._config.batch_size:
                        snapshot_collection.bulk_write(
                            snapshot_operations,
                            ordered=False,
                        )
                        snapshot_operations.clear()
                if snapshot_operations:
                    snapshot_collection.bulk_write(
                        snapshot_operations,
                        ordered=False,
                    )
            temp_path.replace(final_path)
        finally:
            temp_path.unlink(missing_ok=True)

        return {
            "name": target.name or f"{target.database}__{target.collection}",
            "database": target.database,
            "collection": target.collection,
            "path": str(final_path.relative_to(self._config.root)),
            "row_count": row_count,
            "sha256": digest.hexdigest(),
        }

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client

        if self._config.project_root is not None:
            project_root = str(self._config.project_root.resolve())
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", self._config.settings_module)

        try:
            import django
            from django.db import connections
        except ImportError as error:
            raise RuntimeError(
                "DATA-LAKE backup에는 Django와 django-mongodb-backend가 필요합니다."
            ) from error

        try:
            django.setup()
            connection = connections[self._config.database_alias]
            connection.ensure_connection()
        except Exception as error:
            raise RuntimeError(
                f"DATA-LAKE용 Django database alias `{self._config.database_alias}`에 연결할 수 없습니다."
            ) from error

        client = getattr(connection, "connection", None)
        if client is None:
            raise RuntimeError(
                f"Django database alias `{self._config.database_alias}`가 MongoClient를 열지 못했습니다."
            )
        client.admin.command("ping")
        self._client = client
        return client


def _json_safe(value: Any) -> Any:
    """Mongo/BSON 값을 JSONL에서 재현 가능한 값으로 변환한다."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("DATA-LAKE backup에는 NaN/Infinity를 저장할 수 없습니다.")
        return value
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "$binary": base64.b64encode(bytes(value)).decode("ascii"),
            "subtype": "00",
        }
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)


def _atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return name.strip("._") or "collection"


def _snapshot_id(
    *,
    backup_id: str,
    database: str,
    collection: str,
    document: Mapping[str, Any],
) -> str:
    """백업 실행과 원천 문서를 함께 식별하는 결정적 MongoDB `_id`를 만든다."""

    source_value = document.get("_id")
    source_key = json.dumps(
        source_value if source_value is not None else document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(
        f"{backup_id}\x00{database}\x00{collection}\x00{source_key}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"{backup_id}:{digest}"
