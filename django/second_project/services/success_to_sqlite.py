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
    SqliteSyncRun,
)


KST = ZoneInfo("Asia/Seoul")
SUCCESS_DATABASE = "encore_success_experiment"
SUCCESS_COLLECTION = "records"
FAILURE_DATABASE = "encore_failure_experiment"
FAILURE_COLLECTION = "records"
NORMALIZATION_RUN_FIELD = "normalization_run_id"
_CANONICAL_REQUIRED_FIELDS = frozenset(
    {
        "dataset_id",
        "source_record_id",
        "normalization_run_id",
        "correction_codes",
        "employee_id",
        "employee_name",
        "department_name",
        "position_name",
        "hire_datetime",
        "is_active",
        "area_id",
        "area_name",
        "manager_employee_id",
        "area_registered_at",
        "top_area_id",
        "top_area_name",
        "top_area_level",
        "top_area_registered_at",
    }
)
_METADATA_FIELDS = frozenset(
    {
        "source_record_id",
        "dataset_id",
        "normalization_run_id",
        "correction_codes",
        "standardization",
    }
)


@dataclass(frozen=True)
class LoadSummary:
    """성공 MongoDB에서 SQLite로 옮긴 실행 결과."""

    source_database: str
    source_collection: str
    normalization_run_id: str | None
    target_alias: str
    source_count: int
    counts: dict[str, int]
    dry_run: bool
    skipped: bool = False
    no_pending: bool = False

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_database": self.source_database,
            "source_collection": self.source_collection,
            "normalization_run_id": self.normalization_run_id,
            "target_alias": self.target_alias,
            "source_count": self.source_count,
            "counts": dict(self.counts),
            "total": self.total,
            "dry_run": self.dry_run,
            "skipped": self.skipped,
            "no_pending": self.no_pending,
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
    normalization_run_id: str | None = None,
    target_alias: str = "sqlite3",
    batch_size: int | None = None,
    dry_run: bool = False,
) -> LoadSummary:
    """미처리 표준화 실행 하나의 성공 문서만 SQLite에 upsert한다.

    성공 원천은 ``encore_success_experiment.records`` 하나로 고정한다.
    ``normalization_run_id``를 생략하면 성공 collection의 인덱스와 SQLite
    적재 이력을 이용해 가장 오래된 미처리 실행 하나를 자동 선택한다. 이후
    선택된 실행 ID에 equality 조건을 적용하며, 실패 DB나 별도 ``silver_*``
    MongoDB collection은 읽지 않는다. 성공 collection의 통합 표준 문서는
    네 개의 기존 SQLite Silver 모델로 분리되어 적재된다.
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

    source_database = sink.success_database or SUCCESS_DATABASE
    source_collection = sink.success_collection or SUCCESS_COLLECTION
    if (
        source_database != SUCCESS_DATABASE
        or source_collection != SUCCESS_COLLECTION
    ):
        raise ValueError(
            "SQLite 적재 원천은 "
            f"{SUCCESS_DATABASE}.{SUCCESS_COLLECTION}이어야 합니다. "
            f"현재 설정: {source_database}.{source_collection}"
        )
    if (
        sink.failure_database != FAILURE_DATABASE
        or sink.failure_collection != FAILURE_COLLECTION
    ):
        raise ValueError(
            "표준화 실패 원천 설정은 "
            f"{FAILURE_DATABASE}.{FAILURE_COLLECTION}이어야 합니다."
        )
    effective_batch_size = batch_size or sink.batch_size
    source_alias = sink.database_alias
    source_db = _get_mongo_database(source_alias, source_database)
    success_collection = source_db[source_collection]
    _ensure_run_index(success_collection)

    if normalization_run_id is None:
        normalization_run_id = _select_next_normalization_run_id(
            success_collection,
            target_alias=target_alias,
        )
        if normalization_run_id is None:
            return LoadSummary(
                source_database=source_database,
                source_collection=source_collection,
                normalization_run_id=None,
                target_alias=target_alias,
                source_count=0,
                counts={},
                dry_run=dry_run,
                no_pending=True,
            )
    else:
        normalization_run_id = _normalize_run_id(normalization_run_id)

    if dry_run:
        source_count, target_counts = _count_canonical_collection(
            success_collection,
            normalization_run_id=normalization_run_id,
            batch_size=effective_batch_size,
        )
        return LoadSummary(
            source_database=source_database,
            source_collection=source_collection,
            normalization_run_id=normalization_run_id,
            target_alias=target_alias,
            source_count=source_count,
            counts=target_counts,
            dry_run=True,
        )

    sync_run, skipped = _claim_sync_run(
        target_alias=target_alias,
        normalization_run_id=normalization_run_id,
        source_database=source_database,
        source_collection=source_collection,
    )
    if skipped:
        return LoadSummary(
            source_database=source_database,
            source_collection=source_collection,
            normalization_run_id=normalization_run_id,
            target_alias=target_alias,
            source_count=sync_run.source_count,
            counts=dict(sync_run.loaded_counts or {}),
            dry_run=False,
            skipped=True,
        )

    try:
        with transaction.atomic(using=target_alias):
            source_count, target_counts = _load_canonical_collection(
                success_collection,
                normalization_run_id=normalization_run_id,
                target_alias=target_alias,
                batch_size=effective_batch_size,
            )
            if source_count == 0:
                raise ValueError(
                    "지정한 normalization_run_id에 해당하는 성공 문서가 없습니다: "
                    f"{normalization_run_id}"
                )
            _complete_sync_run(
                sync_run,
                target_alias=target_alias,
                source_count=source_count,
                loaded_counts=target_counts,
            )
    except Exception as error:
        _fail_sync_run(sync_run, error, target_alias=target_alias)
        raise

    return LoadSummary(
        source_database=source_database,
        source_collection=source_collection,
        normalization_run_id=normalization_run_id,
        target_alias=target_alias,
        source_count=source_count,
        counts=target_counts,
        dry_run=False,
    )


def _get_mongo_database(source_alias: str, database_name: str) -> Any:
    """Django가 관리하는 MongoClient에서 지정 DB를 반환한다."""

    source_connection = connections[source_alias]
    source_connection.ensure_connection()
    client = getattr(source_connection, "connection", None)
    if client is None:
        raise RuntimeError(
            f"Django database alias `{source_alias}`가 MongoClient를 열지 못했습니다."
        )
    return client[database_name]


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


def _normalize_run_id(value: Any) -> str:
    """실행 ID를 조회·저장에 사용할 문자열로 정규화한다."""

    if value is None:
        raise ValueError("normalization_run_id는 필수입니다.")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("normalization_run_id는 비어 있을 수 없습니다.")
    return normalized


def _ensure_run_index(collection: Any) -> None:
    """증분 equality 조회를 위한 MongoDB 인덱스를 보장한다."""

    create_index = getattr(collection, "create_index", None)
    if callable(create_index):
        create_index(
            [(NORMALIZATION_RUN_FIELD, 1)],
            name="normalization_run_id_idx",
        )


def _select_next_normalization_run_id(
    collection: Any,
    *,
    target_alias: str,
) -> str | None:
    """아직 SQLite에 완료되지 않은 가장 오래된 실행 ID를 고른다.

    성공/실행 중인 ID는 SQLite 제어 테이블에서 제외하고, 실패한 ID는 다시
    후보에 포함한다. MongoDB에서는 실행 ID만 projection하고 정렬·limit하므로
    실제 표준화 문서 전체를 읽지 않는다. 실행 ID는 파이프라인이 UTC 시각을
    앞에 붙여 생성하므로 문자열 오름차순이 실행 순서와 일치한다.
    """

    blocked_statuses = (
        SqliteSyncRun.Status.SUCCESS,
        SqliteSyncRun.Status.RUNNING,
    )
    blocked_ids = {
        str(run_id)
        for run_id in (
            SqliteSyncRun.objects.using(target_alias)
            .filter(status__in=blocked_statuses)
            .values_list("normalization_run_id", flat=True)
        )
    }

    run_id_query: dict[str, Any] = {
        NORMALIZATION_RUN_FIELD: {"$exists": True},
    }
    if blocked_ids:
        run_id_query[NORMALIZATION_RUN_FIELD]["$nin"] = sorted(blocked_ids)

    projection = {"_id": 0, NORMALIZATION_RUN_FIELD: 1}
    try:
        cursor = collection.find(
            run_id_query,
            projection=projection,
            batch_size=1,
        )
    except TypeError:
        # 단순화된 테스트 double 등 keyword projection을 지원하지 않는
        # collection 구현과도 동작하도록 조회를 한 단계 낮춘다.
        cursor = collection.find(run_id_query, batch_size=1)

    # PyMongo Cursor에서는 인덱스를 활용해 후보 한 건만 읽는다. 테스트용
    # list/iterator처럼 sort·limit이 없는 구현에서는 아래 순회에서 최소값을
    # 선택해 동일한 결정 규칙을 유지한다.
    sorted_cursor = False
    sort = getattr(cursor, "sort", None)
    if callable(sort) and not isinstance(cursor, (list, tuple)):
        try:
            cursor = sort([(NORMALIZATION_RUN_FIELD, 1)])
            sorted_cursor = True
        except (TypeError, ValueError):
            pass
    if sorted_cursor:
        limit = getattr(cursor, "limit", None)
        if callable(limit):
            try:
                cursor = limit(1)
            except (TypeError, ValueError):
                pass

    candidate: str | None = None
    for document in cursor:
        if not isinstance(document, Mapping):
            continue
        try:
            run_id = _normalize_run_id(document.get(NORMALIZATION_RUN_FIELD))
        except ValueError:
            continue
        if run_id in blocked_ids:
            continue
        if candidate is None or run_id < candidate:
            candidate = run_id
        if sorted_cursor:
            break
    return candidate


def _find_run_documents(
    collection: Any,
    *,
    normalization_run_id: str,
    batch_size: int,
) -> Any:
    """성공 collection에서 지정한 표준화 실행만 조회한다."""

    return collection.find(
        {NORMALIZATION_RUN_FIELD: normalization_run_id},
        batch_size=batch_size,
    )


def _build_canonical_models(
    document: Any,
    *,
    normalization_run_id: str,
) -> dict[str, Any]:
    """통합 표준 문서 한 건을 SQLite 대상 Silver 모델들로 변환한다."""

    if not isinstance(document, Mapping):
        raise ValueError("성공 collection 문서가 object가 아닙니다.")

    missing = sorted(
        field
        for field in _CANONICAL_REQUIRED_FIELDS
        if document.get(field) is None
    )
    if missing:
        document_id = document.get("_id", "unknown")
        raise ValueError(
            f"records _id={document_id}: 통합 표준 문서 필드가 없습니다: {missing}"
        )

    actual_run_id = _required_text(
        document,
        NORMALIZATION_RUN_FIELD,
        SUCCESS_COLLECTION,
    )
    if actual_run_id != normalization_run_id:
        document_id = document.get("_id", "unknown")
        raise ValueError(
            f"records _id={document_id}: normalization_run_id가 조회 실행과 다릅니다. "
            f"expected={normalization_run_id}, actual={actual_run_id}"
        )

    models: dict[str, Any] = {}
    for spec in _collection_specs():
        if (
            spec.name == "silver_parent_area"
            and document.get("parent_area_id") is None
            and document.get("parent_area_name") is None
        ):
            continue
        models[spec.name] = _build_document(spec, document)
    return models


def _count_canonical_collection(
    collection: Any,
    *,
    normalization_run_id: str,
    batch_size: int,
) -> tuple[int, dict[str, int]]:
    """지정 실행의 문서를 검증하고 PK 기준 고유 대상 건수를 계산한다."""

    specs = _collection_specs()
    unique_by_model: dict[str, dict[Any, Any]] = {
        spec.name: {} for spec in specs
    }
    source_count = 0
    for document in _find_run_documents(
        collection,
        normalization_run_id=normalization_run_id,
        batch_size=batch_size,
    ):
        source_count += 1
        models = _build_canonical_models(
            document,
            normalization_run_id=normalization_run_id,
        )
        for spec in specs:
            instance = models.get(spec.name)
            if instance is not None:
                _register_unique_instance(
                    unique_by_model[spec.name],
                    instance,
                    spec,
                )

    return source_count, {
        spec.name: len(unique_by_model[spec.name]) for spec in specs
    }


def _load_canonical_collection(
    collection: Any,
    *,
    normalization_run_id: str,
    target_alias: str,
    batch_size: int,
) -> tuple[int, dict[str, int]]:
    """통합 표준 문서를 모델별로 묶어 SQLite에 batch upsert한다."""

    specs = _collection_specs()
    managers = {
        spec.name: spec.model.objects.using(target_alias) for spec in specs
    }
    pending: dict[str, list[Any]] = {spec.name: [] for spec in specs}
    unique_by_model: dict[str, dict[Any, Any]] = {
        spec.name: {} for spec in specs
    }
    source_count = 0

    for document in _find_run_documents(
        collection,
        normalization_run_id=normalization_run_id,
        batch_size=batch_size,
    ):
        source_count += 1
        models = _build_canonical_models(
            document,
            normalization_run_id=normalization_run_id,
        )
        for spec in specs:
            instance = models.get(spec.name)
            if instance is not None:
                _register_unique_instance(
                    unique_by_model[spec.name],
                    instance,
                    spec,
                )
                pending[spec.name].append(instance)

        if any(len(objects) >= batch_size for objects in pending.values()):
            _flush_pending_models(
                pending,
                specs=specs,
                managers=managers,
                batch_size=batch_size,
            )

    _flush_pending_models(
        pending,
        specs=specs,
        managers=managers,
        batch_size=batch_size,
    )
    return source_count, {
        spec.name: len(unique_by_model[spec.name]) for spec in specs
    }


def _flush_pending_models(
    pending: dict[str, list[Any]],
    *,
    specs: Sequence[_CollectionSpec],
    managers: Mapping[str, Any],
    batch_size: int,
) -> None:
    """FK 순서를 지키며 모델별 대기 batch를 upsert한다."""

    for spec in specs:
        objects = pending[spec.name]
        if not objects:
            continue
        unique_objects = _deduplicate_instances(objects, spec)
        _bulk_upsert(
            managers[spec.name],
            unique_objects,
            spec,
            batch_size=batch_size,
        )
        objects.clear()


def _claim_sync_run(
    *,
    target_alias: str,
    normalization_run_id: str,
    source_database: str,
    source_collection: str,
) -> tuple[SqliteSyncRun, bool]:
    """실행 이력을 선점하고, 이미 성공한 실행이면 재처리를 막는다."""

    manager = SqliteSyncRun.objects.using(target_alias)
    now = timezone.now()
    with transaction.atomic(using=target_alias):
        sync_run = manager.select_for_update().filter(
            pk=normalization_run_id
        ).first()
        if sync_run is not None:
            if sync_run.status == SqliteSyncRun.Status.SUCCESS:
                return sync_run, True
            if sync_run.status == SqliteSyncRun.Status.RUNNING:
                raise RuntimeError(
                    "해당 normalization_run_id가 이미 적재 중입니다: "
                    f"{normalization_run_id}"
                )
            sync_run.status = SqliteSyncRun.Status.RUNNING
            sync_run.source_database = source_database
            sync_run.source_collection = source_collection
            sync_run.source_count = 0
            sync_run.loaded_counts = {}
            sync_run.started_at = now
            sync_run.finished_at = None
            sync_run.error_message = ""
            sync_run.attempt_count += 1
            sync_run.save(
                using=target_alias,
                update_fields=[
                    "status",
                    "source_database",
                    "source_collection",
                    "source_count",
                    "loaded_counts",
                    "started_at",
                    "finished_at",
                    "error_message",
                    "attempt_count",
                ],
            )
            return sync_run, False

        sync_run = manager.create(
            normalization_run_id=normalization_run_id,
            status=SqliteSyncRun.Status.RUNNING,
            source_database=source_database,
            source_collection=source_collection,
            started_at=now,
            attempt_count=1,
        )
        return sync_run, False


def _complete_sync_run(
    sync_run: SqliteSyncRun,
    *,
    target_alias: str,
    source_count: int,
    loaded_counts: Mapping[str, int],
) -> None:
    """대상 upsert가 완료된 동일 트랜잭션 안에서 성공 상태를 기록한다."""

    sync_run.status = SqliteSyncRun.Status.SUCCESS
    sync_run.source_count = source_count
    sync_run.loaded_counts = dict(loaded_counts)
    sync_run.finished_at = timezone.now()
    sync_run.error_message = ""
    sync_run.save(
        using=target_alias,
        update_fields=[
            "status",
            "source_count",
            "loaded_counts",
            "finished_at",
            "error_message",
        ],
    )


def _fail_sync_run(
    sync_run: SqliteSyncRun,
    error: Exception,
    *,
    target_alias: str,
) -> None:
    """롤백된 적재를 실패 상태로 남겨 같은 실행을 재시도할 수 있게 한다."""

    try:
        SqliteSyncRun.objects.using(target_alias).filter(
            pk=sync_run.pk,
            status=SqliteSyncRun.Status.RUNNING,
        ).update(
            status=SqliteSyncRun.Status.FAILED,
            finished_at=timezone.now(),
            error_message=str(error),
        )
    except Exception:
        # 원래 적재 예외를 호출자에게 전달해야 하므로 상태 기록 예외로
        # 원인을 가리지 않는다.
        return


def _register_unique_instance(
    registry: dict[Any, Any],
    instance: Any,
    spec: _CollectionSpec,
) -> None:
    """실행 내 동일 PK를 하나로 합치고, 값 충돌은 중단한다."""

    primary_key = spec.model._meta.pk
    key = getattr(instance, primary_key.attname)
    previous = registry.get(key)
    if (
        previous is not None
        and _business_fingerprint(previous) != _business_fingerprint(instance)
    ):
        raise ValueError(
            f"{spec.name}의 동일 PK에 서로 다른 표준값이 있습니다: {key}"
        )
    # 같은 업무값이면 최신 문서의 계보 메타데이터를 보존한다.
    registry[key] = instance


def _deduplicate_instances(
    objects: Sequence[Any],
    spec: _CollectionSpec,
) -> list[Any]:
    """한 SQLite batch 안에서 PK가 중복되지 않도록 정리한다."""

    registry: dict[Any, Any] = {}
    for instance in objects:
        _register_unique_instance(registry, instance, spec)
    return list(registry.values())


def _business_fingerprint(instance: Any) -> tuple[tuple[str, Any], ...]:
    """업무 필드만 비교해 같은 PK의 표준값 충돌을 판별한다."""

    values: list[tuple[str, Any]] = []
    for field in instance._meta.concrete_fields:
        if field.name in _METADATA_FIELDS:
            continue
        values.append((field.attname, getattr(instance, field.attname)))
    return tuple(values)


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
) -> int:
    """SQLite의 ON CONFLICT upsert를 사용하고 구버전에는 ORM fallback을 둔다."""

    if not objects:
        return 0
    objects = _deduplicate_instances(objects, spec)
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
    return len(objects)


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
