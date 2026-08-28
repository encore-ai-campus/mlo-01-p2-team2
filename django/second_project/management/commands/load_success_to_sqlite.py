from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from second_project.services.success_to_sqlite import load_success_to_sqlite


SEOUL = ZoneInfo("Asia/Seoul")


def _is_load_minute(minute: int) -> bool:
    """3n+1분(KST) 예약 시각인지 확인한다."""

    return 1 <= minute <= 59 and (minute - 1) % 3 == 0


def _next_run_at(now: datetime) -> datetime:
    """현재 시각 이후의 다음 3n+1분 경계 시각을 반환한다."""

    local_now = now.astimezone(SEOUL)
    candidate = local_now.replace(second=0, microsecond=0)
    if local_now != candidate:
        candidate += timedelta(minutes=1)
    while not _is_load_minute(candidate.minute):
        candidate += timedelta(minutes=1)
    return candidate


def _wait_until(target: datetime) -> None:
    """예약 시각까지 최대 1초 단위로 대기한다."""

    while True:
        remaining = (target - datetime.now(SEOUL)).total_seconds()
        if remaining <= 0:
            return
        sleep(min(remaining, 1.0))


class Command(BaseCommand):
    help = (
        "encore_success_experiment.records의 미처리 표준화 성공 데이터를 "
        "3n+1분 00초(KST)마다 SQLite에 upsert합니다."
    )
    # crawl_and_load와 동일하게 이 명령 자체가 스케줄을 관리한다.
    requires_system_checks = []

    def add_arguments(self, parser) -> None:
        default_config = (
            Path(settings.BASE_DIR).parent
            / "validation_pipeline"
            / "config.json"
        )
        parser.add_argument(
            "--config",
            default=str(default_config),
            help=(
                "파이프라인 설정 JSON 경로 "
                f"(기본값: {default_config})"
            ),
        )
        parser.add_argument(
            "--normalization-run-id",
            default=None,
            help=(
                "처리할 표준화 실행 ID. 생략하면 SQLite 적재 이력과 "
                "성공 collection을 기준으로 미처리 실행 1건을 자동 선택"
            ),
        )
        parser.add_argument(
            "--target-alias",
            default="sqlite3",
            help="Django RDB database alias (기본값: sqlite3)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=None,
            help="Mongo 조회·SQLite upsert 배치 크기 (기본값: 설정값)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="선택된 실행을 검증만 하고 SQLite에는 쓰지 않음",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help=(
                "예약 시각을 기다리지 않고, 미처리 표준화 실행이 없을 때까지 "
                "한 cycle을 실행합니다."
            ),
        )

    def handle(self, *args, **options):
        if options["once"]:
            self._run_cycle(options)
            return

        self._run_schedule(options)

    def _run_schedule(self, options) -> None:
        self.stdout.write(
            "SQLite 적재 스케줄러를 시작했습니다. "
            "매시 01,04,...,58분 00초(KST)에 미처리 실행을 모두 처리합니다. "
            "종료하려면 Ctrl+C를 누르세요."
        )
        try:
            while True:
                target = _next_run_at(datetime.now(SEOUL))
                self.stdout.write(
                    f"다음 SQLite 적재 예정 시각: "
                    f"{target.isoformat(timespec='seconds')}"
                )
                _wait_until(target)
                try:
                    self._run_cycle(options)
                except CommandError as exc:
                    self.stderr.write(
                        f"예약 SQLite 적재가 실패했습니다: {exc} "
                        "다음 예약 시각에 다시 시도합니다."
                    )
        except KeyboardInterrupt:
            self.stdout.write("SQLite 적재 스케줄러를 종료했습니다.")

    def _run_cycle(self, options) -> None:
        # 기본 운영 모드에서는 한 예약 cycle 안에 미처리 실행을 모두
        # 비운다. 특정 ID 지정 또는 dry-run은 같은 실행을 반복하지 않도록
        # 한 건만 처리한다.
        drain_until_empty = (
            options["normalization_run_id"] is None
            and not options["dry_run"]
        )
        processed_runs = 0

        while True:
            try:
                summary = load_success_to_sqlite(
                    options["config"],
                    normalization_run_id=options["normalization_run_id"],
                    target_alias=options["target_alias"],
                    batch_size=options["batch_size"],
                    dry_run=options["dry_run"],
                )
            except Exception as error:
                raise CommandError(str(error)) from error

            if summary.no_pending:
                if processed_runs:
                    self.stdout.write(
                        f"이번 cycle에서 표준화 실행 {processed_runs}건을 처리했고, "
                        "더 이상 미처리 실행이 없습니다."
                    )
                else:
                    self.stdout.write(
                        "처리할 표준화 성공 실행이 없습니다. "
                        "SQLite 적재 상태는 변경되지 않았습니다."
                    )
                return

            self._write_summary(summary)
            processed_runs += 1
            if not drain_until_empty:
                return

    def _write_summary(self, summary) -> None:
        mode = "검증만" if summary.dry_run else "upsert"
        self.stdout.write(
            self.style.SUCCESS(
                f"{summary.source_database}.{summary.source_collection} → "
                f"{summary.target_alias} {mode} 완료 "
                f"(normalization_run_id={summary.normalization_run_id}, "
                f"source_count={summary.source_count}, "
                f"target_rows={summary.total})"
            )
        )
        if summary.skipped:
            self.stdout.write("- 이미 SUCCESS로 기록된 실행이므로 재적재하지 않았습니다.")
        for collection, count in summary.counts.items():
            self.stdout.write(f"- {collection}: {count}건")
