from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceConfig:
    """MongoDB, JSONL 또는 CSV 입력에 필요한 설정을 보관한다."""

    kind: str = "mongodb"
    uri_env: str = "MONGODB_URI"
    database: str = ""
    collection: str = ""
    path: Path | None = None
    encoding: str = "utf-8-sig"
    delimiter: str = ","
    quotechar: str = '"'
    skipinitialspace: bool = False
    continue_on_parse_error: bool = True
    max_line_bytes: int = 10 * 1024 * 1024
    database_alias: str = "mongodb"
    settings_module: str = "config.settings"
    project_root: Path | None = None
    query: dict[str, Any] = field(default_factory=dict)
    projection: dict[str, Any] | None = None
    aggregation: list[dict[str, Any]] | None = None
    batch_size: int = 500
    limit: int | None = None

    def __post_init__(self) -> None:
        """잘못된 조회 설정을 실행 전에 확인한다."""

        source_kind = self.kind.lower().replace("-", "_")
        if source_kind not in {"mongodb", "django_mongodb", "jsonl", "csv"}:
            raise ValueError(
                "source.type은 mongodb, django_mongodb, jsonl 또는 csv여야 합니다."
            )
        if source_kind == "mongodb" and (
            not self.uri_env or not self.database or not self.collection
        ):
            raise ValueError("MongoDB source의 uri_env, database, collection은 필수입니다.")
        if source_kind == "django_mongodb" and (
            not self.database_alias or not self.settings_module or not self.collection
        ):
            raise ValueError(
                "Django MongoDB source의 database_alias, settings_module, collection은 필수입니다."
            )
        if source_kind in {"jsonl", "csv"} and self.path is None:
            raise ValueError(f"{source_kind.upper()} source의 path는 필수입니다.")
        if not self.encoding:
            raise ValueError("source.encoding은 비어 있을 수 없습니다.")
        if len(self.delimiter) != 1:
            raise ValueError("source.delimiter는 한 글자여야 합니다.")
        if len(self.quotechar) != 1:
            raise ValueError("source.quotechar는 한 글자여야 합니다.")
        if self.max_line_bytes <= 0:
            raise ValueError("source.max_line_bytes는 1 이상이어야 합니다.")
        if self.batch_size <= 0:
            raise ValueError("source.batch_size는 1 이상이어야 합니다.")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("source.limit은 null 또는 1 이상이어야 합니다.")
        if self.aggregation is not None and (self.query or self.projection is not None):
            raise ValueError(
                "aggregation 사용 시 query/projection 대신 $match/$project 단계를 사용하세요."
            )
        if any(
            "$out" in stage or "$merge" in stage
            for stage in self.aggregation or []
        ):
            raise ValueError("추출 단계에서는 $out과 $merge를 사용할 수 없습니다.")


@dataclass(frozen=True)
class QualityConfig:
    """필수 필드와 기대 타입 같은 기본 품질 규칙을 보관한다."""

    required_fields: tuple[str, ...] = ("_id",)
    field_types: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OutputConfig:
    """파이프라인 결과를 저장할 디렉터리를 보관한다."""

    directory: Path = Path("output")


@dataclass(frozen=True)
class SinkConfig:
    """JSONL 또는 정상/실패 MongoDB 저장소 설정을 보관한다."""

    kind: str = "jsonl"
    uri_env: str = "MONGODB_URI"
    success_database: str = "encore_success_experiment"
    success_collection: str = "records"
    failure_database: str = "encore_failure_experiment"
    failure_collection: str = "records"
    bronze_database: str = "second_project"
    bronze_collection: str = "bronze_raw_records"
    manifest_collection: str = "bronze_manifest"
    report_database: str = ""
    report_collection: str = "_pipeline_runs"
    silver_database: str = ""
    silver_collections: dict[str, str] = field(default_factory=dict)
    batch_size: int = 500
    local_report: bool = True
    database_alias: str = "mongodb"
    settings_module: str = "config.settings"
    project_root: Path | None = None

    def __post_init__(self) -> None:
        """저장소 종류와 MongoDB 대상 설정을 실행 전에 확인한다."""

        sink_kind = self.kind.lower()
        if sink_kind not in {"jsonl", "mongodb", "django_mongodb", "django-mongodb"}:
            raise ValueError(
                "sink.type은 jsonl, mongodb 또는 django_mongodb이어야 합니다."
            )
        if sink_kind in {"mongodb", "django_mongodb", "django-mongodb"}:
            required = {
                "sink.success_database": self.success_database,
                "sink.success_collection": self.success_collection,
                "sink.failure_database": self.failure_database,
                "sink.failure_collection": self.failure_collection,
                "sink.bronze_database": self.bronze_database,
                "sink.bronze_collection": self.bronze_collection,
                "sink.manifest_collection": self.manifest_collection,
                "sink.report_collection": self.report_collection,
            }
            if sink_kind == "mongodb":
                required["sink.uri_env"] = self.uri_env
            missing = [label for label, value in required.items() if not value]
            if missing:
                raise ValueError(f"MongoDB sink 설정이 비어 있습니다: {missing}")
        if sink_kind in {"django_mongodb", "django-mongodb"} and (
            not self.database_alias or not self.settings_module
        ):
            raise ValueError(
                "Django MongoDB sink의 database_alias와 settings_module은 필수입니다."
            )
        allowed_silver_models = {
            "silver_employee",
            "silver_area",
            "silver_parent_area",
            "silver_top_area_detail",
        }
        unknown_silver_models = sorted(
            set(self.silver_collections) - allowed_silver_models
        )
        if unknown_silver_models:
            raise ValueError(
                f"sink.silver_collections에 지원하지 않는 모델이 있습니다: {unknown_silver_models}"
            )
        if any(not name for name in self.silver_collections.values()):
            raise ValueError("sink.silver_collections의 컬렉션명은 비어 있을 수 없습니다.")
        if self.batch_size <= 0:
            raise ValueError("sink.batch_size는 1 이상이어야 합니다.")


@dataclass(frozen=True)
class ReprocessConfig:
    """실패 DB 재처리 큐와 재시도 정책을 보관한다."""

    enabled: bool = False
    database_alias: str = "mongodb"
    settings_module: str = "config.settings"
    project_root: Path | None = None
    database: str = ""
    collection: str = "records"
    batch_size: int = 500
    max_attempts: int = 3
    query: dict[str, Any] = field(default_factory=dict)
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.enabled and (not self.database or not self.collection):
            raise ValueError("reprocess.enabled일 때 database와 collection은 필수입니다.")
        if not self.database_alias or not self.settings_module:
            raise ValueError("reprocess의 database_alias와 settings_module은 필수입니다.")
        if self.batch_size <= 0:
            raise ValueError("reprocess.batch_size는 1 이상이어야 합니다.")
        if self.max_attempts <= 0:
            raise ValueError("reprocess.max_attempts는 1 이상이어야 합니다.")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("reprocess.limit은 null 또는 1 이상이어야 합니다.")


@dataclass(frozen=True)
class ScheduleConfig:
    """증분 실행 주기와 적재 지연·워터마크 설정을 보관한다."""

    enabled: bool = False
    interval_seconds: int = 180
    delay_seconds: int = 60
    watermark_path: Path = Path("state/ingest_watermark.json")
    watermark_field: str = "ingested_at"
    lock_path: Path = Path("state/pipeline.lock")
    run_immediately: bool = True
    reprocess_on_tick: bool = True

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("schedule.interval_seconds는 1 이상이어야 합니다.")
        if self.delay_seconds < 0:
            raise ValueError("schedule.delay_seconds는 0 이상이어야 합니다.")
        if not self.watermark_field or self.watermark_field.startswith("$"):
            raise ValueError("schedule.watermark_field가 올바르지 않습니다.")


@dataclass(frozen=True)
class DataLakeCollectionConfig:
    """DATA-LAKE로 내보낼 MongoDB collection을 표현한다."""

    database: str
    collection: str
    name: str | None = None

    def __post_init__(self) -> None:
        if not self.database or not self.collection:
            raise ValueError("data_lake.collections의 database와 collection은 필수입니다.")


@dataclass(frozen=True)
class DataLakeConfig:
    """시간별 파일·MongoDB 스냅샷을 저장할 DATA-LAKE 설정을 보관한다."""

    enabled: bool = False
    root: Path = Path("data_lake")
    interval_minutes: int = 180
    database: str = "encore_data_lake"
    manifest_collection: str = "data_lake_manifests"
    database_alias: str = "mongodb"
    settings_module: str = "config.settings"
    project_root: Path | None = None
    collections: tuple[DataLakeCollectionConfig, ...] = ()
    batch_size: int = 500

    def __post_init__(self) -> None:
        if self.interval_minutes <= 0:
            raise ValueError("data_lake.interval_minutes는 1 이상이어야 합니다.")
        if not self.database or not self.manifest_collection:
            raise ValueError(
                "data_lake.database와 data_lake.manifest_collection은 필수입니다."
            )
        if self.batch_size <= 0:
            raise ValueError("data_lake.batch_size는 1 이상이어야 합니다.")
        if not self.database_alias or not self.settings_module:
            raise ValueError("data_lake의 database_alias와 settings_module은 필수입니다.")


@dataclass(frozen=True)
class StandardizationConfig:
    """선택적으로 적용할 YAML 표준화 규칙 파일을 보관한다."""

    rules_file: Path | None = None


@dataclass(frozen=True)
class AppConfig:
    """파이프라인 실행에 필요한 전체 설정을 묶는다."""

    source: SourceConfig
    quality: QualityConfig = field(default_factory=QualityConfig)
    standardization: StandardizationConfig = field(default_factory=StandardizationConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    sink: SinkConfig = field(default_factory=SinkConfig)
    reprocess: ReprocessConfig = field(default_factory=ReprocessConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    data_lake: DataLakeConfig = field(default_factory=DataLakeConfig)
    log_level: str = "INFO"
    log_directory: Path = Path("logs")

    @classmethod
    def from_file(cls, path: str | Path) -> "AppConfig":
        """JSON 설정 파일을 읽어 검증된 애플리케이션 설정을 만든다."""

        config_path = Path(path)
        with config_path.open("r", encoding="utf-8") as file:
            raw = json.load(file)

        source_raw = dict(raw["source"])
        source_kind = source_raw.pop("type", source_raw.pop("kind", "mongodb"))
        source_path = source_raw.get("path")
        if source_path:
            source_path = Path(source_path)
            if not source_path.is_absolute():
                source_path = config_path.parent / source_path
            source_raw["path"] = source_path
        source_project_root = source_raw.get("project_root")
        if source_project_root:
            source_project_root = Path(source_project_root)
            if not source_project_root.is_absolute():
                source_project_root = config_path.parent / source_project_root
            source_raw["project_root"] = source_project_root
        source = SourceConfig(kind=source_kind, **source_raw)

        quality_raw = raw.get("quality", {})
        quality = QualityConfig(
            required_fields=tuple(quality_raw.get("required_fields", ["_id"])),
            field_types=dict(quality_raw.get("field_types", {})),
        )

        standardization_raw = raw.get("standardization", {})
        rules_value = standardization_raw.get("rules_file")
        rules_file = Path(rules_value) if rules_value else None
        if rules_file is not None and not rules_file.is_absolute():
            rules_file = config_path.parent / rules_file

        output_raw = raw.get("output", {})
        output_directory = Path(output_raw.get("directory", "output"))
        if not output_directory.is_absolute():
            output_directory = config_path.parent / output_directory

        logging_raw = raw.get("logging", {})
        log_level = str(logging_raw.get("level", "INFO")).upper()
        log_directory = Path(logging_raw.get("directory", "logs"))
        if not log_directory.is_absolute():
            log_directory = config_path.parent / log_directory

        sink_raw = dict(raw.get("sink", {}))
        sink_kind = sink_raw.pop("type", sink_raw.pop("kind", "jsonl"))
        sink_project_root = sink_raw.get("project_root")
        if sink_project_root:
            sink_project_root = Path(sink_project_root)
            if not sink_project_root.is_absolute():
                sink_project_root = config_path.parent / sink_project_root
            sink_raw["project_root"] = sink_project_root
        sink = SinkConfig(kind=sink_kind, **sink_raw)

        reprocess_raw = dict(raw.get("reprocess", {}))
        reprocess_project_root = reprocess_raw.get("project_root")
        if reprocess_project_root:
            reprocess_project_root = Path(reprocess_project_root)
            if not reprocess_project_root.is_absolute():
                reprocess_project_root = config_path.parent / reprocess_project_root
            reprocess_raw["project_root"] = reprocess_project_root
        reprocess = ReprocessConfig(**reprocess_raw)

        schedule_raw = dict(raw.get("schedule", {}))
        for key in ("watermark_path", "lock_path"):
            value = schedule_raw.get(key)
            if value:
                resolved = Path(value)
                if not resolved.is_absolute():
                    resolved = config_path.parent / resolved
                schedule_raw[key] = resolved
        schedule = ScheduleConfig(**schedule_raw)

        data_lake_raw = dict(raw.get("data_lake", raw.get("backup", {})))
        data_lake_root = data_lake_raw.get("root")
        if data_lake_root:
            data_lake_root = Path(data_lake_root)
            if not data_lake_root.is_absolute():
                data_lake_root = config_path.parent / data_lake_root
            data_lake_raw["root"] = data_lake_root
        data_lake_project_root = data_lake_raw.get("project_root")
        if data_lake_project_root:
            data_lake_project_root = Path(data_lake_project_root)
            if not data_lake_project_root.is_absolute():
                data_lake_project_root = config_path.parent / data_lake_project_root
            data_lake_raw["project_root"] = data_lake_project_root
        raw_collections = data_lake_raw.get("collections", [])
        data_lake_raw["collections"] = tuple(
            DataLakeCollectionConfig(**dict(item)) for item in raw_collections
        )
        data_lake = DataLakeConfig(**data_lake_raw)
        return cls(
            source=source,
            quality=quality,
            standardization=StandardizationConfig(rules_file=rules_file),
            output=OutputConfig(directory=output_directory),
            sink=sink,
            reprocess=reprocess,
            schedule=schedule,
            data_lake=data_lake,
            log_level=log_level,
            log_directory=log_directory,
        )
