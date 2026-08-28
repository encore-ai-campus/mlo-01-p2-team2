"""Run the JSONL validation pipeline from Django and cron."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


SEOUL = ZoneInfo("Asia/Seoul")


def _is_validation_minute(minute: int) -> bool:
    """Return whether a minute is one of the 3n+1 KST schedule slots."""

    return 1 <= minute <= 59 and (minute - 1) % 3 == 0


def _next_run_at(now: datetime) -> datetime:
    """Return the next 3n+1 minute boundary in KST."""

    local_now = now.astimezone(SEOUL)
    candidate = local_now.replace(second=0, microsecond=0)
    if local_now != candidate:
        candidate += timedelta(minutes=1)
    while not _is_validation_minute(candidate.minute):
        candidate += timedelta(minutes=1)
    return candidate


def _wait_until(target: datetime) -> None:
    while True:
        remaining = (target - datetime.now(SEOUL)).total_seconds()
        if remaining <= 0:
            return
        sleep(min(remaining, 1.0))


def _read_state_timestamp(state_path: Path, key: str) -> datetime | None:
    """Read one UTC timestamp from a scheduler state file."""

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    value = payload.get(key)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _last_completed_at(state_path: Path) -> datetime | None:
    """Read the last successfully completed scheduled run timestamp."""

    return _read_state_timestamp(state_path, "last_completed_at")


def _is_interval_due(state_path: Path, interval_hours: float) -> bool:
    """Return whether the minimum elapsed time since the last run has passed."""

    if interval_hours <= 0:
        return True
    last_run = _last_completed_at(state_path)
    if last_run is None:
        return True
    return datetime.now(timezone.utc) - last_run >= timedelta(hours=interval_hours)


def _save_state_timestamp(state_path: Path, key: str) -> None:
    """Persist one scheduler timestamp without removing other state values."""

    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str] = {}
    try:
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload.update(
                {str(name): str(value) for name, value in loaded.items()}
            )
    except (OSError, json.JSONDecodeError):
        pass
    payload = {
        **payload,
        key: datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    temporary_path = state_path.with_name(f"{state_path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(state_path)


def _save_completed_at(state_path: Path) -> None:
    """Persist the completed-run timestamp atomically for restart-safe throttling."""

    _save_state_timestamp(state_path, "last_completed_at")


def _is_data_lake_due(state_path: Path, interval_minutes: int) -> bool:
    """Return whether the Bronze Data Lake snapshot interval has elapsed."""

    if interval_minutes <= 0:
        return True
    last_backup = _read_state_timestamp(state_path, "last_backup_at")
    if last_backup is None:
        return True
    return datetime.now(timezone.utc) - last_backup >= timedelta(
        minutes=interval_minutes
    )


def _save_data_lake_completed_at(state_path: Path) -> None:
    """Persist the successful Bronze Data Lake backup timestamp."""

    _save_state_timestamp(state_path, "last_backup_at")


def _validation_pipeline_root() -> Path:
    """Return the validation_pipeline directory next to the Django project."""

    return Path(settings.BASE_DIR).parent / "validation_pipeline"


class Command(BaseCommand):
    help = (
        "Bronze MongoDB 데이터를 3분 간격의 예약 시각에 "
        "mongo_pipeline으로 표준화·검증합니다."
    )
    # Project-wide model checks are unnecessary; the sink uses the explicit
    # Django MongoDB connection configured below.
    requires_system_checks = []

    def add_arguments(self, parser) -> None:
        validation_root = _validation_pipeline_root()
        django_root = Path(settings.BASE_DIR)
        parser.add_argument(
            "--input-jsonl",
            type=Path,
            default=None,
            help=(
                "호환용 입력 JSONL 경로. 지정하지 않으면 Bronze MongoDB를 읽습니다. "
                f"(기존 파일: {django_root / 'data' / 'raw_data' / 'records.jsonl'})"
            ),
        )
        parser.add_argument(
            "--source-database",
            default=getattr(
                settings,
                "DASHBOARD_BRONZE_DATABASE",
                "second_project",
            ),
            help="입력 Bronze MongoDB 데이터베이스 (기본값: second_project)",
        )
        parser.add_argument(
            "--source-collection",
            default=getattr(
                settings,
                "DASHBOARD_BRONZE_COLLECTION",
                "bronze_raw_records",
            ),
            help="입력 Bronze MongoDB 컬렉션 (기본값: bronze_raw_records)",
        )
        parser.add_argument(
            "--rules",
            type=Path,
            default=validation_root / "rules" / "legacy_org_jsonl.yaml",
            help=(
                "표준화 규칙 YAML 경로 "
                f"(기본값: {validation_root / 'rules' / 'legacy_org_jsonl.yaml'})"
            ),
        )
        parser.add_argument(
            "--output",
            type=Path,
            default=validation_root / "output",
            help=(
                "실행별 결과 디렉터리 "
                f"(기본값: {validation_root / 'output'})"
            ),
        )
        parser.add_argument(
            "--log-directory",
            type=Path,
            default=django_root / "log_lake" / "standardized",
            help=(
                "표준화·검증 로그 디렉터리 "
                f"(기본값: {django_root / 'log_lake' / 'standardized'})"
            ),
        )
        parser.add_argument(
            "--database-alias",
            default="mongodb",
            help="Django MongoDB database alias (기본값: mongodb)",
        )
        parser.add_argument(
            "--success-database",
            default=getattr(
                settings,
                "DASHBOARD_SUCCESS_DATABASE",
                "legacy_standardized",
            ),
            help="표준화 성공 문서를 저장할 MongoDB 데이터베이스",
        )
        parser.add_argument(
            "--failure-database",
            default=getattr(
                settings,
                "DASHBOARD_FAILURE_DATABASE",
                "legacy_failed",
            ),
            help="표준화 실패 문서를 저장할 MongoDB 데이터베이스",
        )
        parser.add_argument(
            "--bronze-database",
            default=getattr(
                settings,
                "DASHBOARD_BRONZE_DATABASE",
                "second_project",
            ),
            help="검증 전 원문 Bronze를 저장할 MongoDB 데이터베이스",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="MongoDB upsert 배치 크기 (기본값: 500)",
        )
        parser.add_argument(
            "--min-interval-hours",
            type=float,
            default=0,
            help=(
                "마지막 성공 실행 후 재적재를 허용할 최소 시간. "
                "예: 3이면 3시간마다 한 번 (기본값: 제한 없음)"
            ),
        )
        parser.add_argument(
            "--schedule-state-file",
            type=Path,
            default=django_root
            / "data"
            / "raw_data"
            / "state"
            / "validation_state.json",
            help="최소 실행 간격을 기록할 상태 파일 경로",
        )
        parser.add_argument(
            "--data-lake-config",
            type=Path,
            default=validation_root / "config.json",
            help=(
                "Bronze Data Lake 연결 설정 JSON 경로 "
                f"(기본값: {validation_root / 'config.json'})"
            ),
        )
        parser.add_argument(
            "--data-lake-state-file",
            type=Path,
            default=django_root
            / "data"
            / "raw_data"
            / "state"
            / "data_lake_state.json",
            help=(
                "마지막 Bronze Data Lake 백업 시각을 기록할 상태 파일 경로 "
                f"(기본값: {django_root / 'data' / 'raw_data' / 'state' / 'data_lake_state.json'})"
            ),
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="예약 시각을 기다리지 않고 한 번만 실행합니다.",
        )

    def handle(self, *args, **options) -> None:
        if options["min_interval_hours"] < 0:
            raise CommandError("--min-interval-hours는 0 이상이어야 합니다.")
        if options["once"]:
            exit_code = self._run_if_due(options)
            if exit_code:
                raise CommandError(
                    f"validation pipeline이 종료 코드 {exit_code}로 끝났습니다."
                )
            return

        self.stdout.write(
            "validation_records 스케줄러를 시작했습니다. "
            "매시 01,04,...,58분 00초(KST)에 실행합니다. "
            "종료하려면 Ctrl+C를 누르세요."
        )
        try:
            while True:
                target = _next_run_at(datetime.now(SEOUL))
                self.stdout.write(
                    f"다음 검증 예정 시각: {target.isoformat(timespec='seconds')}"
                )
                _wait_until(target)
                exit_code = self._run_if_due(options)
                if exit_code:
                    self.stderr.write(
                        f"예약 validation pipeline이 종료 코드 {exit_code}로 끝났습니다. "
                        "다음 예약 시각에 다시 시도합니다."
                    )
        except KeyboardInterrupt:
            self.stdout.write("validation_records 스케줄러를 종료했습니다.")

    def _run_if_due(self, options) -> int:
        """Run validation when due and independently snapshot Bronze every 3 hours."""

        interval_hours = float(options["min_interval_hours"])
        state_path = Path(options["schedule_state_file"]).expanduser().resolve()
        pipeline_exit_code = 0
        pipeline_ran = False
        if not _is_interval_due(state_path, interval_hours):
            self.stdout.write(
                f"최소 실행 간격 {interval_hours:g}시간이 지나지 않아 이번 적재를 건너뜁니다. "
                f"state={state_path}"
            )
        else:
            pipeline_ran = True
            pipeline_exit_code = self._run_pipeline(options)
        if pipeline_ran and pipeline_exit_code == 0 and interval_hours > 0:
            try:
                _save_completed_at(state_path)
            except OSError as error:
                self.stderr.write(
                    "검증 완료 시각을 저장하지 못했습니다. "
                    f"다음 실행에서 중복 적재될 수 있습니다: {error}"
                )
                pipeline_exit_code = 1

        data_lake_exit_code = self._run_data_lake_if_due(options)
        if pipeline_exit_code:
            return pipeline_exit_code
        return data_lake_exit_code

    def _run_data_lake_if_due(self, options) -> int:
        """Snapshot only the configured Bronze source when its 3-hour window is due."""

        # --input-jsonl is a compatibility override and is not the Bronze source.
        if options["input_jsonl"] is not None:
            return 0

        validation_root = _validation_pipeline_root()
        config_path = Path(options["data_lake_config"]).expanduser().resolve()
        state_path = (
            Path(options["data_lake_state_file"]).expanduser().resolve()
        )
        if not config_path.is_file():
            self.stderr.write(
                f"Data Lake 설정 파일을 찾을 수 없습니다: {config_path}"
            )
            return 2

        source_directory = validation_root / "src"
        if not source_directory.is_dir():
            self.stderr.write(
                f"mongo_pipeline 소스 경로를 찾을 수 없습니다: {source_directory}"
            )
            return 2
        source_string = str(source_directory)
        if source_string not in sys.path:
            sys.path.insert(0, source_string)

        try:
            from mongo_pipeline.backup import DjangoMongoDataLakeBackup
            from mongo_pipeline.config import AppConfig, DataLakeCollectionConfig

            app_config = AppConfig.from_file(config_path)
            data_lake_config = app_config.data_lake
            if not data_lake_config.enabled:
                self.stdout.write("Data Lake 백업이 비활성화되어 있어 건너뜁니다.")
                return 0
            if not _is_data_lake_due(
                state_path,
                data_lake_config.interval_minutes,
            ):
                return 0

            # The connected backup is intentionally limited to legacy Bronze.
            data_lake_config = replace(
                data_lake_config,
                collections=(
                    DataLakeCollectionConfig(
                        database=options["source_database"],
                        collection=options["source_collection"],
                        name="bronze_raw_records",
                    ),
                ),
            )
            self.stdout.write(
                "Bronze Data Lake 백업을 실행합니다. "
                f"source={options['source_database']}.{options['source_collection']} "
                f"destination={data_lake_config.database} "
                f"interval={data_lake_config.interval_minutes}분"
            )
            backup = DjangoMongoDataLakeBackup(
                data_lake_config,
                sink_config=app_config.sink,
            )
            try:
                report = backup.run()
            finally:
                backup.close()
            try:
                _save_data_lake_completed_at(state_path)
            except OSError as error:
                self.stderr.write(
                    "Bronze 백업은 완료됐지만 백업 시각을 저장하지 못했습니다. "
                    f"다음 실행에서 중복 백업될 수 있습니다: {error}"
                )
                return 1
            self.stdout.write(
                "Bronze Data Lake 백업 완료: "
                f"{report['manifest_path']}"
            )
            return 0
        except Exception as error:
            self.stderr.write(
                "Bronze Data Lake 백업 중 예외가 발생했습니다. "
                f"error_type={type(error).__name__}: {error}"
            )
            return 1

    def _run_pipeline(self, options) -> int:
        """Run JSONL validation and persist accepted/rejected documents in MongoDB."""

        validation_root = _validation_pipeline_root()
        source_directory = validation_root / "src"
        input_path = (
            Path(options["input_jsonl"]).expanduser().resolve()
            if options["input_jsonl"] is not None
            else None
        )
        rules_path = Path(options["rules"]).expanduser().resolve()
        output_directory = Path(options["output"]).expanduser().resolve()
        log_directory = Path(options["log_directory"]).expanduser().resolve()

        if not source_directory.is_dir():
            self.stderr.write(
                f"mongo_pipeline 소스 경로를 찾을 수 없습니다: {source_directory}"
            )
            return 2
        if input_path is not None and not input_path.is_file():
            self.stderr.write(f"입력 JSONL을 찾을 수 없습니다: {input_path}")
            return 2
        if not rules_path.is_file():
            self.stderr.write(f"표준화 규칙 파일을 찾을 수 없습니다: {rules_path}")
            return 2

        source_string = str(source_directory)
        if source_string not in sys.path:
            sys.path.insert(0, source_string)

        try:
            from mongo_pipeline.cli import run_config_once
            from mongo_pipeline.config import (
                AppConfig,
                OutputConfig,
                QualityConfig,
                SinkConfig,
                SourceConfig,
                StandardizationConfig,
            )
        except ImportError as error:
            self.stderr.write(
                "mongo_pipeline을 불러오지 못했습니다. "
                f"validation_pipeline 의존성을 확인하세요: {error}"
            )
            return 1

        if input_path is not None:
            source_config = SourceConfig(
                kind="jsonl",
                path=input_path,
                encoding="utf-8",
                continue_on_parse_error=True,
                project_root=Path(settings.BASE_DIR),
            )
            source_description = f"input={input_path}"
        else:
            source_config = SourceConfig(
                kind="django_mongodb",
                database=options["source_database"],
                collection=options["source_collection"],
                database_alias=options["database_alias"],
                settings_module="config.settings",
                project_root=Path(settings.BASE_DIR),
                aggregation=[
                    {
                        "$match": {
                            "raw_json": {
                                "$exists": True,
                                "$type": "object",
                            }
                        }
                    },
                    {"$replaceWith": "$raw_json"},
                ],
                batch_size=options["batch_size"],
            )
            source_description = (
                "source="
                f"{options['source_database']}.{options['source_collection']}"
            )

        self.stdout.write(
            "MongoDB Bronze validation pipeline을 실행합니다. "
            f"{source_description} rules={rules_path} "
            f"success_db={options['success_database']} "
            f"failure_db={options['failure_database']} "
            f"bronze_db={options['bronze_database']}"
        )
        try:
            config = AppConfig(
                source=source_config,
                quality=QualityConfig(
                    required_fields=(),
                    field_types={},
                ),
                standardization=StandardizationConfig(
                    rules_file=rules_path,
                ),
                output=OutputConfig(directory=output_directory),
                sink=SinkConfig(
                    kind="django_mongodb",
                    database_alias=options["database_alias"],
                    settings_module="config.settings",
                    project_root=Path(settings.BASE_DIR),
                    success_database=options["success_database"],
                    success_collection="records",
                    failure_database=options["failure_database"],
                    failure_collection="records",
                    bronze_database=options["bronze_database"],
                    bronze_collection="bronze_raw_records",
                    manifest_collection="bronze_manifest",
                    report_database=options["success_database"],
                    report_collection="pipeline_runs",
                    silver_database=options["success_database"],
                    silver_collections={
                        "silver_employee": "silver_employee",
                        "silver_area": "silver_area",
                        "silver_parent_area": "silver_parent_area",
                        "silver_top_area_detail": "silver_top_area_detail",
                    },
                    batch_size=options["batch_size"],
                    local_report=True,
                ),
                log_level="INFO",
                log_directory=log_directory,
            )
            # 입력이 이미 second_project.bronze_raw_records에서 온 경우
            # 같은 Bronze 컬렉션에 다시 포장해 쓰지 않는다. 그렇지 않으면
            # 예약 실행마다 Bronze 원문이 증식한다.
            result = run_config_once(config, bronze_enabled=input_path is not None)
            status = str(result.report.get("status", "FAILED")).upper()
            self.stdout.write(f"상태: {status}")
            self.stdout.write(f"리포트: {result.report_path}")
            return 1 if status == "FAILED" else 0
        except Exception as error:
            self.stderr.write(
                "validation pipeline 실행 중 예외가 발생했습니다. "
                f"error_type={type(error).__name__}: {error}"
            )
            return 1
