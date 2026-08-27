from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .backup import DjangoMongoDataLakeBackup
from .config import AppConfig, SinkConfig
from .loggers import create_stage_loggers
from .pipeline import Pipeline, PipelineResult
from .reprocessing import DjangoMongoReprocessSource, ReprocessSink
from .sinks import DjangoMongoSink, DocumentSink, JsonlSink, MongoSink
from .rule_standardizer import RuleConfigurationError, YamlRuleStandardizer
from .sources import DjangoMongoSource, IterableSource, JsonlSource, MongoSource, YamlFileSource
from .standardizers import CommonStandardizer
from .yaml_support import YamlLoadError
from .validators import build_default_validators


def build_parser() -> argparse.ArgumentParser:
    """설정 실행과 데모 실행에 필요한 명령행 옵션을 정의한다."""

    parser = argparse.ArgumentParser(
        description="문서를 추출, 표준화, 검증한 뒤 JSONL 또는 MongoDB에 저장합니다."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config", help="입력·표준화·저장 실행용 JSON 설정 파일 경로")
    mode.add_argument(
        "--demo",
        action="store_true",
        help="MongoDB 없이 내장 샘플 데이터로 실행",
    )
    mode.add_argument(
        "--input-yaml",
        help="표준화할 YAML 데이터 파일 경로",
    )
    mode.add_argument(
        "--input-jsonl",
        help="표준화할 JSONL 데이터 파일 경로",
    )
    parser.add_argument(
        "--rules",
        help="적용할 YAML 표준화 규칙 파일 (`config`의 rules_file보다 우선)",
    )
    parser.add_argument(
        "--output",
        default="output",
        help="--demo/--input-yaml/--input-jsonl에서 사용할 출력 디렉터리 (기본값: output)",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="config의 3분 증분 처리·재처리·시간별 DATA-LAKE 스케줄러 실행",
    )
    parser.add_argument(
        "--scheduled-once",
        action="store_true",
        help="스케줄러 한 tick만 실행하고 종료(cron/Task Scheduler용)",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="config의 실패 DB에서 pending/retry 문서를 한 번 재처리",
    )
    parser.add_argument(
        "--backup-once",
        action="store_true",
        help="config의 DATA-LAKE 대상을 한 번 백업",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """설정을 읽고 각 구성요소를 조립한 뒤 파이프라인을 실행한다."""

    args = build_parser().parse_args(argv)
    scheduled_modes = args.schedule or args.scheduled_once
    if (scheduled_modes or args.reprocess or args.backup_once) and not args.config:
        build_parser().error(
            "--schedule/--scheduled-once/--reprocess/--backup-once는 --config와 함께 사용해야 합니다."
        )
    if args.schedule and (args.scheduled_once or args.reprocess or args.backup_once):
        build_parser().error(
            "--schedule은 다른 운영 실행 옵션과 함께 사용할 수 없습니다."
        )
    if args.scheduled_once and (args.reprocess or args.backup_once):
        build_parser().error(
            "--scheduled-once는 --reprocess 또는 --backup-once와 함께 사용할 수 없습니다."
        )

    if scheduled_modes or args.reprocess or args.backup_once:
        app_config = AppConfig.from_file(args.config)
        if args.schedule or args.scheduled_once:
            from .scheduler import PipelineScheduler

            scheduler = PipelineScheduler(app_config)
            if args.scheduled_once:
                tick = scheduler.run_tick_locked()
                _print_tick(tick)
                return 1 if tick.get("status") == "FAILED" else 0
            scheduler.run_forever()
            return 0
        if args.backup_once:
            backup = DjangoMongoDataLakeBackup(
                app_config.data_lake,
                sink_config=app_config.sink,
            )
            try:
                report = backup.run()
            finally:
                backup.close()
            print(f"DATA-LAKE manifest: {report['manifest_path']}")
            return 0

        try:
            result = run_config_once(app_config, reprocess=True)
        except Exception:
            return 1
        _print_result(result)
        return 1 if result.report["status"] == "FAILED" else 0

    rules_path = Path(args.rules) if args.rules else None
    app_config: AppConfig | None = None
    if args.demo:
        log_level = "INFO"
        output_directory = Path(args.output)
        log_directory = output_directory / "logs"
        source = IterableSource(
            [
                {
                    "_id": 1,
                    "title": "첫 번째 문서",
                    "created_at": datetime(2026, 8, 26, tzinfo=timezone.utc),
                },
                {"_id": 2, "title": "두 번째 문서", "tags": ["mongo", "data"]},
                {"title": "ID가 없어 검증에서 제외되는 문서"},
            ],
            name="demo",
        )
        validators = build_default_validators(
            required_fields=["_id"],
            field_types={},
        )
    elif args.input_yaml:
        log_level = "INFO"
        output_directory = Path(args.output)
        log_directory = output_directory / "logs"
        source = YamlFileSource(args.input_yaml)
        validators = build_default_validators(
            required_fields=[],
            field_types={},
        )
    elif args.input_jsonl:
        log_level = "INFO"
        output_directory = Path(args.output)
        log_directory = output_directory / "logs"
        source = JsonlSource(args.input_jsonl)
        validators = build_default_validators(
            required_fields=[],
            field_types={},
        )
    else:
        app_config = AppConfig.from_file(args.config)
        log_level = app_config.log_level
        output_directory = app_config.output.directory
        log_directory = app_config.log_directory
        source_kind = app_config.source.kind.lower().replace("-", "_")
        if source_kind == "jsonl":
            if app_config.source.path is None:
                print("JSONL source의 path가 없습니다.", file=sys.stderr)
                return 2
            source = JsonlSource(
                app_config.source.path,
                encoding=app_config.source.encoding,
                continue_on_parse_error=app_config.source.continue_on_parse_error,
                max_line_bytes=app_config.source.max_line_bytes,
            )
        elif source_kind == "django_mongodb":
            source = DjangoMongoSource(app_config.source)
        else:
            source = MongoSource(app_config.source)
        validators = build_default_validators(
            required_fields=app_config.quality.required_fields,
            field_types=app_config.quality.field_types,
        )
        if rules_path is None:
            rules_path = app_config.standardization.rules_file

    try:
        standardizer = (
            YamlRuleStandardizer.from_file(rules_path)
            if rules_path is not None
            else CommonStandardizer()
        )
    except (RuleConfigurationError, YamlLoadError, RuntimeError) as error:
        print(f"표준화 규칙 파일 오류: {error}", file=sys.stderr)
        return 2

    level = getattr(logging, log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    standardize_logger, validation_logger = create_stage_loggers(log_directory, level)

    run_id = Pipeline.create_run_id()
    sink = _build_sink(
        app_config.sink if app_config is not None else None,
        output_directory=output_directory,
        run_id=run_id,
        rule_version=(
            getattr(standardizer, "description", {}).get("name")
            if isinstance(getattr(standardizer, "description", {}), dict)
            else None
        ),
    )
    pipeline = Pipeline(
        source=source,
        standardizer=standardizer,
        validators=validators,
        sink=sink,
        run_id=run_id,
        standardize_logger=standardize_logger,
        validation_logger=validation_logger,
    )

    try:
        result = pipeline.run()
    except Exception:
        return 1

    print(f"상태: {result.report['status']}")
    print(f"리포트: {result.report_path}")
    return 1 if result.report["status"] == "FAILED" else 0


def run_config_once(
    app_config: AppConfig,
    *,
    source_config: object | None = None,
    reprocess: bool = False,
) -> PipelineResult:
    """설정 파일 기반 실행을 한 번 수행한다(스케줄러와 CLI가 공유)."""

    rules_path = app_config.standardization.rules_file
    standardizer = (
        YamlRuleStandardizer.from_file(rules_path)
        if rules_path is not None
        else CommonStandardizer()
    )

    if reprocess:
        source = DjangoMongoReprocessSource(app_config.reprocess)
    else:
        configured_source = source_config or app_config.source
        if not isinstance(configured_source, type(app_config.source)):
            raise TypeError("source_config은 SourceConfig이어야 합니다.")
        source = _build_source(configured_source)

    validators = build_default_validators(
        required_fields=app_config.quality.required_fields,
        field_types=app_config.quality.field_types,
    )
    run_id = Pipeline.create_run_id()
    sink = _build_sink(
        app_config.sink,
        output_directory=app_config.output.directory,
        run_id=run_id,
        rule_version=(
            getattr(standardizer, "description", {}).get("name")
            if isinstance(getattr(standardizer, "description", {}), dict)
            else None
        ),
    )
    if reprocess:
        if not isinstance(sink, MongoSink):
            raise ValueError("재처리는 MongoDB sink에서만 사용할 수 있습니다.")
        sink = ReprocessSink(sink, max_attempts=app_config.reprocess.max_attempts)

    standardize_logger, validation_logger = create_stage_loggers(
        app_config.log_directory,
        getattr(logging, app_config.log_level, logging.INFO),
    )
    pipeline = Pipeline(
        source=source,
        standardizer=standardizer,
        validators=validators,
        sink=sink,
        run_id=run_id,
        standardize_logger=standardize_logger,
        validation_logger=validation_logger,
    )
    return pipeline.run()


def _build_source(source_config: object) -> object:
    """SourceConfig 종류에 맞는 source 구현체를 만든다."""

    # 런타임 타입 검증은 run_config_once에서 수행하며, 이 함수는 CLI 조립만 담당한다.
    config = source_config
    source_kind = config.kind.lower().replace("-", "_")  # type: ignore[attr-defined]
    if source_kind == "jsonl":
        if config.path is None:  # type: ignore[attr-defined]
            raise ValueError("JSONL source의 path가 없습니다.")
        return JsonlSource(
            config.path,  # type: ignore[attr-defined]
            encoding=config.encoding,  # type: ignore[attr-defined]
            continue_on_parse_error=config.continue_on_parse_error,  # type: ignore[attr-defined]
            max_line_bytes=config.max_line_bytes,  # type: ignore[attr-defined]
        )
    if source_kind == "django_mongodb":
        return DjangoMongoSource(config)  # type: ignore[arg-type]
    if source_kind == "mongodb":
        return MongoSource(config)  # type: ignore[arg-type]
    raise ValueError(f"지원하지 않는 source.type입니다: {config.kind}")  # type: ignore[attr-defined]


def _print_result(result: PipelineResult) -> None:
    print(f"상태: {result.report['status']}")
    print(f"리포트: {result.report_path}")


def _print_tick(tick: dict[str, object]) -> None:
    """cron용 단일 tick의 핵심 상태만 출력한다."""

    primary = tick.get("primary")
    primary_status = primary.get("status") if isinstance(primary, dict) else None
    print(f"스케줄 tick 상태: {tick.get('status', primary_status or 'SUCCESS')}")
    if primary_status:
        print(f"원본 처리 상태: {primary_status}")
    reprocess = tick.get("reprocess")
    if isinstance(reprocess, dict):
        print(f"재처리 상태: {reprocess.get('status', 'UNKNOWN')}")
    backup = tick.get("backup")
    if isinstance(backup, dict):
        print(f"DATA-LAKE manifest: {backup.get('manifest_path', '')}")


def _build_sink(
    config: SinkConfig | None,
    *,
    output_directory: Path,
    run_id: str,
    rule_version: str | None = None,
) -> DocumentSink:
    """CLI 실행 모드에 맞는 JSONL 또는 MongoDB sink를 만든다."""

    if config is None or config.kind.lower() == "jsonl":
        return JsonlSink(output_directory, run_id)

    sink_kind = config.kind.lower().replace("-", "_")
    local_report_root = output_directory if config.local_report else None
    common_kwargs = {
        "success_database": config.success_database,
        "success_collection": config.success_collection,
        "failure_database": config.failure_database,
        "failure_collection": config.failure_collection,
        "run_id": run_id,
        "report_database": config.report_database or config.success_database,
        "report_collection": config.report_collection,
        "batch_size": config.batch_size,
        "rule_version": rule_version,
        "local_report_root": local_report_root,
    }
    if sink_kind == "mongodb":
        return MongoSink(uri_env=config.uri_env, **common_kwargs)
    if sink_kind == "django_mongodb":
        return DjangoMongoSink(
            database_alias=config.database_alias,
            settings_module=config.settings_module,
            project_root=config.project_root,
            **common_kwargs,
        )
    raise ValueError(f"지원하지 않는 sink.type입니다: {config.kind}")


if __name__ == "__main__":
    raise SystemExit(main())
