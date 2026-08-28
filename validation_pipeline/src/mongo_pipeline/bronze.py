from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BRONZE_REQUIRED_FIELDS = (
    "record_id",
    "dataset_id",
    "source_record_id",
    "source_row_no",
    "ingested_at",
    "raw_json",
    "source_record_sha256",
    "run_id",
)

MANIFEST_REQUIRED_FIELDS = (
    "run_id",
    "source_name",
    "source_uri",
    "collected_at",
    "ingest_date",
    "raw_path",
    "content_type",
    "file_size_bytes",
    "row_count",
    "checksum_sha256",
    "retry_count",
    "crawler_version",
    "status",
)

MANIFEST_OPTIONAL_FIELDS = ("http_status", "error_summary")
MANIFEST_ALLOWED_FIELDS = frozenset((*MANIFEST_REQUIRED_FIELDS, *MANIFEST_OPTIONAL_FIELDS))
SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class BronzeIntegrity:
    """Bronze 레코드와 원천 artifact의 무결성 결과."""

    rate: float
    valid_record_count: int
    record_count: int
    source_file_verified: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rate": self.rate,
            "valid_record_count": self.valid_record_count,
            "record_count": self.record_count,
            "source_file_verified": self.source_file_verified,
        }


def is_bronze_record(document: Any) -> bool:
    """이미 Bronze wrapper로 저장된 문서인지 판별한다.

    기존 Django loader가 만든 문서는 `raw_json`을 object로 저장하고,
    validation pipeline이 만든 문서는 결정적 JSON 문자열로 저장한다. 두
    형식을 모두 인식해 Mongo source를 다시 읽을 때 Bronze를 중복 생성하지
    않도록 한다.
    """

    if not isinstance(document, Mapping):
        return False
    raw_json = document.get("raw_json")
    if not isinstance(raw_json, (str, Mapping)):
        return False
    return all(
        document.get(field) not in (None, "")
        for field in ("dataset_id", "source_record_id", "source_row_no", "ingested_at")
    )


def unwrap_bronze_record(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bronze wrapper 안의 원천 문서를 표준화 입력으로 복원한다."""

    if not is_bronze_record(document):
        return document
    raw_json = document.get("raw_json")
    if isinstance(raw_json, Mapping):
        return raw_json
    if isinstance(raw_json, str):
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return document
        if isinstance(parsed, Mapping):
            return parsed
    return document


def build_bronze_record(
    document: Mapping[str, Any],
    *,
    run_id: str,
    row_number: int,
    ingested_at: datetime | None = None,
) -> dict[str, Any]:
    """원본 문서를 변경하지 않고 Bronze 보존 wrapper를 만든다.

    `raw_json`은 입력 문서의 결정적 JSON 표현이고, 레코드 해시는 그 UTF-8
    바이트에 대해 계산한다. 표준화 runtime 정보는 호출자가 이 함수 이후에
    추가하므로 원본 보존 값에는 포함되지 않는다.
    """

    if not isinstance(document, Mapping):
        raise TypeError("Bronze 입력 문서는 object여야 합니다.")
    if not run_id:
        raise ValueError("Bronze run_id는 비어 있을 수 없습니다.")
    if row_number <= 0:
        raise ValueError("Bronze row_number는 1 이상이어야 합니다.")

    # validation pipeline이 이미 만든 canonical wrapper는 재실행 시
    # run_id/ingested_at을 바꾸지 않고 그대로 사용한다. 구형 Django loader
    # wrapper는 raw_json 원문만 꺼내 현재 Bronze 계약으로 계산한다.
    if is_bronze_record(document):
        if (
            isinstance(document.get("raw_json"), str)
            and isinstance(document.get("source"), Mapping)
        ):
            return dict(document)
        unwrapped = unwrap_bronze_record(document)
        if unwrapped is not document:
            document = unwrapped

    raw_json = canonical_json(document)
    source_record_sha256 = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    source_record_id = _first_value(
        document,
        "source.record_id",
        "source_record_id",
        "record_id",
        "_id",
    )
    if source_record_id is None:
        source_error = document.get("_source_error")
        source_line_no = _first_value(source_error, "source_line_no")
        if source_line_no is not None:
            source_record_id = f"source-line:{source_line_no}"
        else:
            source_record_id = f"{run_id}:row:{row_number}"
    source_record_id = str(source_record_id)

    dataset_id = _first_value(document, "dataset_id", "source.dataset_id")
    dataset_id = str(dataset_id) if dataset_id is not None else "unknown"
    original_record_id = _first_value(document, "record_id")
    record_id = (
        str(original_record_id)
        if original_record_id is not None
        else f"{run_id}:row:{row_number}"
    )
    source_row_no = _positive_integer(
        _first_value(document, "source.source_row_no", "source_row_no")
    ) or row_number
    ingested_at_kst = _iso_kst(ingested_at or datetime.now(timezone.utc))

    original_source = document.get("source")
    source = dict(original_source) if isinstance(original_source, Mapping) else {}
    source["record_id"] = source_record_id
    source["source_row_no"] = source_row_no
    source["source_record_sha256"] = source_record_sha256

    return {
        "_id": f"{run_id}:bronze:{row_number}",
        "record_id": record_id,
        "dataset_id": dataset_id,
        "source_record_id": source_record_id,
        "source_row_no": source_row_no,
        "ingested_at": ingested_at_kst,
        "ingested_at_kst": ingested_at_kst,
        "raw_json": raw_json,
        "source_record_sha256": source_record_sha256,
        "run_id": run_id,
        "source": source,
    }


def canonical_json(value: Any) -> str:
    """값을 정렬된 JSON 문자열로 직렬화한다.

    Python/BSON 원천의 NaN·Infinity도 Bronze에서 보존할 수 있도록 Mongo
    Extended JSON 표기(`$numberDouble`)로 표현한다. 이 값은 Silver 단계에서
    별도로 품질 오류로 격리된다.
    """

    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_manifest(
    *,
    run_id: str,
    source_description: Mapping[str, Any],
    started_at: datetime,
    row_count: int,
    status: str,
    fallback_raw_path: str | Path | None = None,
    error_summary: str | None = None,
) -> dict[str, Any]:
    """Manifest schema에 맞는 실행별 Bronze 수집 증적을 만든다."""

    if row_count < 0:
        raise ValueError("Manifest row_count는 0 이상이어야 합니다.")

    source_path = _description_path(source_description)
    fallback_path = Path(fallback_raw_path) if fallback_raw_path else None
    artifact_path = _existing_path(source_path) or _existing_path(fallback_path)
    if artifact_path is not None:
        raw_path = str(artifact_path.resolve())
        source_uri = artifact_path.resolve().as_uri()
        file_size_bytes, checksum_sha256 = file_sha256(artifact_path)
    elif source_path is not None:
        raw_path = str(source_path.resolve())
        source_uri = source_path.resolve().as_uri()
        file_size_bytes = 0
        checksum_sha256 = hashlib.sha256(b"").hexdigest()
    else:
        source_uri = _logical_source_uri(source_description)
        raw_path = source_uri
        file_size_bytes = 0
        checksum_sha256 = hashlib.sha256(b"").hexdigest()

    source_name = (
        artifact_path.name
        if artifact_path is not None
        else str(source_description.get("type", "unknown"))
    )
    source_type = str(source_description.get("type", "unknown"))
    manifest = {
        "run_id": run_id,
        "source_name": source_name,
        "source_uri": source_uri,
        "collected_at": _iso_kst(started_at),
        "ingest_date": _iso_kst(started_at)[:10],
        "raw_path": raw_path,
        "content_type": _content_type(source_type),
        "file_size_bytes": file_size_bytes,
        "row_count": row_count,
        "checksum_sha256": checksum_sha256,
        "http_status": None,
        "retry_count": 0,
        "crawler_version": "mongo-pipeline/0.1.0",
        "status": _manifest_status(status),
        "error_summary": error_summary,
    }
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    """`config/manifest-schema.json`의 핵심 제약을 실행 시점에 확인한다."""

    missing = [field for field in MANIFEST_REQUIRED_FIELDS if field not in manifest]
    if missing:
        raise ValueError(f"Manifest 필수 필드가 없습니다: {missing}")
    unknown = sorted(set(manifest) - MANIFEST_ALLOWED_FIELDS)
    if unknown:
        raise ValueError(f"Manifest에 지원하지 않는 필드가 있습니다: {unknown}")

    for field in ("run_id", "source_name", "source_uri", "raw_path", "crawler_version"):
        if not isinstance(manifest[field], str) or not manifest[field]:
            raise ValueError(f"Manifest `{field}`는 비어 있지 않은 문자열이어야 합니다.")
    for field in ("file_size_bytes", "row_count", "retry_count"):
        value = manifest[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Manifest `{field}`는 0 이상 정수여야 합니다.")
    if not SHA256_PATTERN.fullmatch(str(manifest["checksum_sha256"])):
        raise ValueError("Manifest checksum_sha256가 SHA-256 형식이 아닙니다.")
    if manifest.get("http_status") is not None and (
        isinstance(manifest["http_status"], bool)
        or not isinstance(manifest["http_status"], int)
        or not 100 <= manifest["http_status"] <= 599
    ):
        raise ValueError("Manifest http_status가 올바르지 않습니다.")
    if manifest["status"] not in {"success", "partial_failure", "failed"}:
        raise ValueError("Manifest status는 success, partial_failure, failed 중 하나여야 합니다.")
    try:
        collected_at = datetime.fromisoformat(
            str(manifest["collected_at"]).replace("Z", "+00:00")
        )
        date.fromisoformat(str(manifest["ingest_date"]))
    except ValueError as error:
        raise ValueError("Manifest 날짜 필드 형식이 올바르지 않습니다.") from error
    if collected_at.tzinfo is None:
        raise ValueError("Manifest collected_at에는 timezone이 필요합니다.")
    if manifest.get("error_summary") is not None and not isinstance(
        manifest["error_summary"], str
    ):
        raise ValueError("Manifest error_summary는 문자열 또는 null이어야 합니다.")


def validate_bronze_record(record: Mapping[str, Any]) -> list[str]:
    """Bronze 필수값·중첩 계보·레코드 해시를 검증한다."""

    errors: list[str] = []
    for field in BRONZE_REQUIRED_FIELDS:
        value = record.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"{field}:missing")
    source = record.get("source")
    if not isinstance(source, Mapping):
        errors.append("source:missing")
    else:
        if source.get("record_id") != record.get("source_record_id"):
            errors.append("source.record_id:mismatch")
        if source.get("source_row_no") != record.get("source_row_no"):
            errors.append("source.source_row_no:mismatch")
        if source.get("source_record_sha256") != record.get("source_record_sha256"):
            errors.append("source.source_record_sha256:mismatch")

    row_number = record.get("source_row_no")
    if isinstance(row_number, bool) or not isinstance(row_number, int) or row_number <= 0:
        errors.append("source_row_no:invalid")
    raw_json = record.get("raw_json")
    if not isinstance(raw_json, str) or not raw_json:
        errors.append("raw_json:invalid")
    else:
        digest = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        if digest != record.get("source_record_sha256"):
            errors.append("source_record_sha256:mismatch")
    if not SHA256_PATTERN.fullmatch(str(record.get("source_record_sha256", ""))):
        errors.append("source_record_sha256:invalid")
    return errors


def bronze_integrity(
    records: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> BronzeIntegrity:
    """Bronze 레코드 해시와 원천 파일/논리 artifact의 무결성을 계산한다."""

    valid_record_count = sum(not validate_bronze_record(record) for record in records)
    record_count = len(records)
    record_rate = valid_record_count / record_count if record_count else 1.0
    source_file_verified = verify_manifest_file(manifest)
    if source_file_verified is False:
        rate = 0.0
    else:
        rate = record_rate
    return BronzeIntegrity(
        rate=round(rate, 6),
        valid_record_count=valid_record_count,
        record_count=record_count,
        source_file_verified=source_file_verified,
    )


def verify_manifest_file(manifest: Mapping[str, Any]) -> bool | None:
    """Manifest가 가리키는 파일이 있으면 크기·SHA-256을 검증한다."""

    raw_path = manifest.get("raw_path")
    if not isinstance(raw_path, str) or not raw_path:
        return False
    if "://" in raw_path and not raw_path.lower().startswith("file:///"):
        return None
    path = Path(raw_path)
    if not path.is_file():
        return False
    size, digest = file_sha256(path)
    return size == manifest.get("file_size_bytes") and digest == manifest.get(
        "checksum_sha256"
    )


def file_sha256(path: str | Path) -> tuple[int, str]:
    """파일 크기와 SHA-256을 스트리밍으로 계산한다."""

    file_path = Path(path)
    digest = hashlib.sha256()
    size = 0
    with file_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _first_value(document: Any, *paths: str) -> Any | None:
    if not isinstance(document, Mapping):
        return None
    for path in paths:
        current: Any = document
        for key in path.split("."):
            if not isinstance(current, Mapping) or key not in current:
                current = None
                break
            current = current[key]
        if current is None or (isinstance(current, str) and not current.strip()):
            continue
        if isinstance(current, (Mapping, list, tuple)):
            continue
        return current
    return None


def _positive_integer(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _description_path(description: Mapping[str, Any]) -> Path | None:
    value = description.get("path")
    if not isinstance(value, str) or not value:
        return None
    return Path(value)


def _existing_path(path: Path | None) -> Path | None:
    return path if path is not None and path.is_file() else None


def _logical_source_uri(description: Mapping[str, Any]) -> str:
    source_type = str(description.get("type", "unknown"))
    database = description.get("database") or description.get("database_alias")
    collection = description.get("collection")
    if database and collection:
        return f"{source_type}://{database}/{collection}"
    return f"{source_type}://memory"


def _content_type(source_type: str) -> str:
    return {
        "csv_file": "text/csv",
        "jsonl_file": "application/x-ndjson",
        "yaml_file": "application/yaml",
        "mongodb": "application/bson",
        "django_mongodb": "application/bson",
    }.get(source_type, "application/json")


def _manifest_status(status: str) -> str:
    return {
        "SUCCESS": "success",
        "PARTIAL_SUCCESS": "partial_failure",
        "FAILED": "failed",
        "success": "success",
        "partial_failure": "partial_failure",
        "failed": "failed",
    }.get(status, "failed")


def _iso_kst(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(KST).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value:
            return {"$numberDouble": "NaN"}
        if value == float("inf"):
            return {"$numberDouble": "Infinity"}
        if value == float("-inf"):
            return {"$numberDouble": "-Infinity"}
        return value
    if isinstance(value, datetime):
        return _iso_kst(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "$binary": base64.b64encode(bytes(value)).decode("ascii"),
            "subtype": "00",
        }
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)
