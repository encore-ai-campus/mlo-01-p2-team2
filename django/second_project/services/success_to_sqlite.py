from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connections, transaction
from django.db.utils import NotSupportedError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..repository.models import (
    LegacyOrgRecord,
    SilverArea,
    SilverEmployee,
    SilverParentArea,
    SilverTopAreaDetail,
)


KST = ZoneInfo("Asia/Seoul")
DEFAULT_COLLECTIONS = {
    "silver_employee": "silver_employee",
    "silver_area": "silver_area",
    "silver_parent_area": "silver_parent_area",
    "silver_top_area_detail": "silver_top_area_detail",
}


@dataclass(frozen=True)
class LoadSummary:
    """성공 MongoDB에서 SQLite로 옮긴 실행 결과."""

    source_database: str
    target_alias: str
    counts: dict[str, int]
    dry_run: bool

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_database": self.source_database,
            "target_alias": self.target_alias,
            "counts": dict(self.counts),
            "total": self.total,
            "dry_run": self.dry_run,
        }


@dataclass(frozen=True)
class _CollectionSpec:
    name: str
    model: type[Any]
    builder: Callable[[Mapping[str, Any], str], Any]
    update_fields: tuple[str, ...]


def load_success_to_sqlite(
    config_path: str | Path,
    *,
    target_alias: str = "sqlite3",
    batch_size: int | None = None,
    dry_run: bool = False,
) -> LoadSummary:
    """성공 MongoDB 문서를 Django ORM으로 SQLite에 upsert한다.

    source/sink 설정의 Django MongoDB alias가 반환하는 MongoClient를 사용하며,
    성공 DB(`silver_database` 또는 `success_database`)만 읽는다. Silver 컬렉션이
    있으면 네 정규화 모델로, 없으면 `success_collection`의 legacy_org 행을
    staging 모델로 적재한다. 실패 DB에는 접근하지 않으므로 quarantine 문서는
    SQLite로 흘러가지 않는다.
    """

    if batch_size is not None and batch_size <= 0:
        raise ValueError("batch_size는 1 이상이어야 합니다.")

    app_config = _load_pipeline_config(config_path)
    sink = app_config.sink
    sink_kind = sink.kind.lower().replace("-", "_")
    if sink_kind != "django_mongodb":
        raise ValueError(
            "성공 DB 재적재 설정의 sink.type은 django_mongodb여야 합니다."
        )

    source_database = sink.silver_database or sink.success_database
    if not source_database:
        raise ValueError("성공 MongoDB database 이름이 설정되지 않았습니다.")
    effective_batch_size = batch_size or sink.batch_size
    source_alias = sink.database_alias
    source_connection = connections[source_alias]
    source_connection.ensure_connection()
    client = getattr(source_connection, "connection", None)
    if client is None:
        raise RuntimeError(
            f"Django database alias `{source_alias}`가 MongoClient를 열지 못했습니다."
        )

    source_db = client[source_database]
    collections = {
        **DEFAULT_COLLECTIONS,
        **sink.silver_collections,
    }
    # Silver collection 존재 여부로 모드를 자동 추론하면, 과거 실행의
    # silver_* 컬렉션이 남아 있는 legacy 성공 DB도 잘못 canonical 모드로
    # 읽게 된다. 설정에서 Silver 대상을 명시한 경우에만 canonical 모드로
    # 고정하고, 그 외에는 sink.success_collection을 legacy staging으로 읽는다.
    canonical_mode = bool(sink.silver_collections or sink.silver_database)
    if canonical_mode:
        collection_specs = [
            (collections[spec.name], spec)
            for spec in _collection_specs()
        ]
    else:
        collection_specs = [
            (sink.success_collection, _legacy_collection_spec())
        ]

    target_counts: dict[str, int] = {}
    if dry_run:
        for collection_name, spec in collection_specs:
            target_counts[spec.name] = _count_and_validate(
                source_db[collection_name],
                spec,
                effective_batch_size,
            )
    else:
        with transaction.atomic(using=target_alias):
            for collection_name, spec in collection_specs:
                target_counts[spec.name] = _load_collection(
                    source_db[collection_name],
                    spec,
                    target_alias=target_alias,
                    batch_size=effective_batch_size,
                )

    return LoadSummary(
        source_database=source_database,
        target_alias=target_alias,
        counts=target_counts,
        dry_run=dry_run,
    )


def _load_pipeline_config(config_path: str | Path) -> Any:
    """설정 파일에서 SinkConfig을 읽는다."""

    pipeline_src = Path(settings.BASE_DIR).parent / "validation_pipeline" / "src"
    pipeline_src_string = str(pipeline_src.resolve())
    if pipeline_src.is_dir() and pipeline_src_string not in sys.path:
        sys.path.insert(0, pipeline_src_string)

    try:
        from mongo_pipeline.config import AppConfig
    except ImportError as error:
        raise RuntimeError(
            "mongo_pipeline을 불러올 수 없습니다. validation_pipeline/src 경로를 확인하세요."
        ) from error

    return AppConfig.from_file(config_path)


def _collection_specs() -> tuple[_CollectionSpec, ...]:
    metadata_fields = (
        "source_record_id",
        "dataset_id",
        "normalization_run_id",
        "correction_codes",
        "standardization",
    )
    return (
        _CollectionSpec(
            name="silver_employee",
            model=SilverEmployee,
            builder=_build_employee,
            update_fields=(
                "employee_name",
                "department_name",
                "position_name",
                "hire_datetime",
                "is_active",
                *metadata_fields,
            ),
        ),
        _CollectionSpec(
            name="silver_parent_area",
            model=SilverParentArea,
            builder=_build_parent_area,
            update_fields=("parent_area_name", *metadata_fields),
        ),
        _CollectionSpec(
            name="silver_top_area_detail",
            model=SilverTopAreaDetail,
            builder=_build_top_area_detail,
            update_fields=(
                "top_area_name",
                "top_area_level",
                "top_area_registered_at",
                *metadata_fields,
            ),
        ),
        _CollectionSpec(
            name="silver_area",
            model=SilverArea,
            builder=_build_area,
            update_fields=(
                "area_name",
                "manager_employee",
                "area_registered_at",
                "parent_area",
                *metadata_fields,
            ),
        ),
    )


def _legacy_collection_spec() -> _CollectionSpec:
    """legacy_org 규칙의 행 단위 성공 collection용 staging spec을 반환한다."""

    return _CollectionSpec(
        name="legacy_org_record",
        model=LegacyOrgRecord,
        builder=_build_legacy_record,
        update_fields=(
            "source_document_id",
            "dataset_id",
            "record_id",
            "source_row_no",
            "crawl_run_id",
            "ingested_at_kst",
            "release_slot",
            "scheduled_release_at",
            "source_record_sha256",
            "mgr_no",
            "mgr_nm",
            "mgr_act_yn",
            "mgr_pos_nm",
            "mgr_dept_nm",
            "mgr_hire_dtm",
            "area_no",
            "area_nm",
            "area_reg_dtm",
            "p_area_no",
            "p_area_nm",
            "top_area_no",
            "top_area_nm",
            "top_area_lvl",
            "top_area_reg_dtm",
            "raw_json",
            "standardization",
        ),
    )


def _count_and_validate(collection: Any, spec: _CollectionSpec, batch_size: int) -> int:
    count = 0
    for document in collection.find({}, batch_size=batch_size):
        _build_document(spec, document)
        count += 1
    return count


def _load_collection(
    collection: Any,
    spec: _CollectionSpec,
    *,
    target_alias: str,
    batch_size: int,
) -> int:
    manager = spec.model.objects.using(target_alias)
    objects: list[Any] = []
    count = 0
    for document in collection.find({}, batch_size=batch_size):
        objects.append(_build_document(spec, document))
        count += 1
        if len(objects) >= batch_size:
            _bulk_upsert(
                manager,
                objects,
                spec,
                batch_size=batch_size,
            )
            objects.clear()

    if objects:
        _bulk_upsert(
            manager,
            objects,
            spec,
            batch_size=batch_size,
        )
    return count


def _build_document(spec: _CollectionSpec, document: Any) -> Any:
    if not isinstance(document, Mapping):
        raise ValueError(f"{spec.name} 문서가 object가 아닙니다.")
    try:
        return spec.builder(document, spec.name)
    except (TypeError, ValueError) as error:
        document_id = document.get("_id", "unknown")
        raise ValueError(f"{spec.name} _id={document_id}: {error}") from error


def _bulk_upsert(
    manager: Any,
    objects: Sequence[Any],
    spec: _CollectionSpec,
    *,
    batch_size: int,
) -> None:
    """SQLite의 ON CONFLICT upsert를 사용하고 구버전에는 ORM fallback을 둔다."""

    if not objects:
        return
    try:
        manager.bulk_create(
            list(objects),
            batch_size=batch_size,
            update_conflicts=True,
            update_fields=list(spec.update_fields),
            unique_fields=[spec.model._meta.pk.name],
        )
    except (NotSupportedError, TypeError):
        for instance in objects:
            primary_key = spec.model._meta.pk
            lookup = {primary_key.attname: getattr(instance, primary_key.attname)}
            defaults = {
                field.attname: getattr(instance, field.attname)
                for field in (
                    spec.model._meta.get_field(field_name)
                    for field_name in spec.update_fields
                )
            }
            manager.update_or_create(defaults=defaults, **lookup)


def _metadata(document: Mapping[str, Any], collection: str) -> dict[str, Any]:
    return {
        "source_record_id": _required_text(document, "source_record_id", collection),
        "dataset_id": _required_text(document, "dataset_id", collection),
        "normalization_run_id": _required_text(
            document,
            "normalization_run_id",
            collection,
        ),
        "correction_codes": _json_list(document.get("correction_codes", []), "correction_codes"),
        "standardization": _json_list(
            document.get("_standardization", []),
            "_standardization",
        ),
    }


def _build_legacy_record(document: Mapping[str, Any], collection: str) -> LegacyOrgRecord:
    """legacy_org 성공 문서를 source_record_id 기준으로 staging row로 만든다."""

    source_record_id = _optional_text(document.get("source_record_id"))
    if source_record_id is None:
        # legacy_org.yaml 입력에는 source_record_id 컬럼이 없을 수 있다.
        # 원천 CSV의 record_id를 안정적인 staging PK로 사용한다.
        source_record_id = _optional_text(document.get("record_id"))
    if source_record_id is None:
        source_record_id = _optional_text(document.get("source_document_id"))
    if source_record_id is None:
        raise ValueError(f"{collection} _id에 사용할 source_record_id가 없습니다.")

    return LegacyOrgRecord(
        source_record_id=source_record_id,
        source_document_id=_optional_text(document.get("source_document_id")),
        dataset_id=_required_text(document, "dataset_id", collection),
        record_id=_optional_text(document.get("record_id")),
        source_row_no=_optional_int(document.get("source_row_no"), "source_row_no"),
        crawl_run_id=_optional_text(document.get("crawl_run_id")),
        ingested_at_kst=_optional_datetime(document.get("ingested_at_kst"), "ingested_at_kst"),
        release_slot=_optional_text(document.get("release_slot")),
        scheduled_release_at=_optional_datetime(
            document.get("scheduled_release_at"),
            "scheduled_release_at",
        ),
        source_record_sha256=_optional_text(document.get("source_record_sha256")),
        mgr_no=_optional_text(document.get("mgr_no")),
        mgr_nm=_optional_text(document.get("mgr_nm")),
        mgr_act_yn=_optional_text(document.get("mgr_act_yn")),
        mgr_pos_nm=_optional_text(document.get("mgr_pos_nm")),
        mgr_dept_nm=_optional_text(document.get("mgr_dept_nm")),
        mgr_hire_dtm=_optional_datetime(document.get("mgr_hire_dtm"), "mgr_hire_dtm"),
        area_no=_required_text(document, "area_no", collection),
        area_nm=_required_text(document, "area_nm", collection),
        area_reg_dtm=_optional_datetime(document.get("area_reg_dtm"), "area_reg_dtm"),
        p_area_no=_optional_text(document.get("p_area_no")),
        p_area_nm=_optional_text(document.get("p_area_nm")),
        top_area_no=_required_text(document, "top_area_no", collection),
        top_area_nm=_required_text(document, "top_area_nm", collection),
        top_area_lvl=_required_text(document, "top_area_lvl", collection),
        top_area_reg_dtm=_optional_datetime(
            document.get("top_area_reg_dtm"),
            "top_area_reg_dtm",
        ),
        raw_json=_raw_text(document.get("raw_json")),
        standardization=_json_list(
            document.get("_standardization", []),
            "_standardization",
        ),
    )


def _build_employee(document: Mapping[str, Any], collection: str) -> SilverEmployee:
    return SilverEmployee(
        employee_id=_required_text(document, "employee_id", collection),
        employee_name=_required_text(document, "employee_name", collection),
        department_name=_required_text(document, "department_name", collection),
        position_name=_required_text(document, "position_name", collection),
        hire_datetime=_required_datetime(document, "hire_datetime", collection),
        is_active=_required_bool(document, "is_active", collection),
        **_metadata(document, collection),
    )


def _build_parent_area(document: Mapping[str, Any], collection: str) -> SilverParentArea:
    return SilverParentArea(
        parent_area_id=_required_text(document, "parent_area_id", collection),
        parent_area_name=_required_text(document, "parent_area_name", collection),
        **_metadata(document, collection),
    )


def _build_top_area_detail(
    document: Mapping[str, Any],
    collection: str,
) -> SilverTopAreaDetail:
    return SilverTopAreaDetail(
        top_area_id=_required_text(document, "top_area_id", collection),
        top_area_name=_required_text(document, "top_area_name", collection),
        top_area_level=_required_text(document, "top_area_level", collection),
        top_area_registered_at=_required_datetime(
            document,
            "top_area_registered_at",
            collection,
        ),
        **_metadata(document, collection),
    )


def _build_area(document: Mapping[str, Any], collection: str) -> SilverArea:
    return SilverArea(
        area_id=_required_text(document, "area_id", collection),
        area_name=_required_text(document, "area_name", collection),
        manager_employee_id=_required_text(
            document,
            "manager_employee_id",
            collection,
        ),
        area_registered_at=_required_datetime(
            document,
            "area_registered_at",
            collection,
        ),
        parent_area_id=_optional_text(document.get("parent_area_id")),
        **_metadata(document, collection),
    )


def _required_text(document: Mapping[str, Any], field: str, collection: str) -> str:
    value = document.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"필수 필드 `{field}`가 없습니다.")
    return str(value)


def _optional_int(value: Any, field: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"`{field}`를 정수로 해석할 수 없습니다: {value!r}") from error


def _optional_text(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return str(value)


def _optional_datetime(value: Any, field: str) -> datetime | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = parse_datetime(value)
    else:
        parsed = None
    if parsed is None:
        raise ValueError(f"`{field}` datetime을 해석할 수 없습니다: {value!r}")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, KST)
    return parsed


def _raw_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _required_datetime(document: Mapping[str, Any], field: str, collection: str) -> datetime:
    value = document.get(field)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = parse_datetime(value)
    else:
        parsed = None
    if parsed is None:
        raise ValueError(f"필수 datetime 필드 `{field}`를 해석할 수 없습니다.")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, KST)
    return parsed


def _required_bool(document: Mapping[str, Any], field: str, collection: str) -> bool:
    value = document.get(field)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"필수 boolean 필드 `{field}`가 아닙니다.")


def _json_list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"`{field}`는 array여야 합니다.")
    return list(value)
