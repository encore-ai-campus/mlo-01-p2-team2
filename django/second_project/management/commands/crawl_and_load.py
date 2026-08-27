"""Run one scheduled crawl and load its JSONL output into MongoDB."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError

from crawling.crawl_records import run_once
from crawling.crawler.config import default_config as default_crawl_config
from second_project.service.bronze_config import (
    default_config as default_loader_config,
)
from second_project.service.bronze_loader import LoaderFailure
from second_project.service.loader_runner import run_loader


SEOUL = ZoneInfo("Asia/Seoul")


def _is_crawl_minute(minute: int) -> bool:
    return 1 <= minute <= 59 and (minute - 1) % 3 == 0


def _next_run_at(now: datetime) -> datetime:
    """Return the next 3n+1 minute boundary in KST."""

    local_now = now.astimezone(SEOUL)
    candidate = local_now.replace(second=0, microsecond=0)
    if local_now != candidate:
        candidate += timedelta(minutes=1)
    while not _is_crawl_minute(candidate.minute):
        candidate += timedelta(minutes=1)
    return candidate


def _wait_until(target: datetime) -> None:
    while True:
        remaining = (target - datetime.now(SEOUL)).total_seconds()
        if remaining <= 0:
            return
        sleep(min(remaining, 1.0))


class Command(BaseCommand):
    help = "크롤링 완료 후 JSONL을 second_project MongoDB에 적재합니다."
    # The command owns its own scheduler and does not need project-wide checks.
    requires_system_checks = []

    def add_arguments(self, parser) -> None:
        crawl_defaults = default_crawl_config()
        loader_defaults = default_loader_config()

        parser.add_argument(
            "--once",
            action="store_true",
            help="예약 시각을 기다리지 않고 크롤링과 적재를 즉시 한 번 실행합니다.",
        )
        parser.add_argument(
            "--base-url",
            default=crawl_defaults.base_url,
            help=f"API 서버 기본 주소 (기본값: {crawl_defaults.base_url})",
        )
        parser.add_argument(
            "--data-dir",
            type=Path,
            default=crawl_defaults.data_dir,
            help=f"크롤링 데이터·상태 경로 (기본값: {crawl_defaults.data_dir})",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=crawl_defaults.page_limit,
            help=f"페이지당 요청 건수 (기본값: {crawl_defaults.page_limit})",
        )
        parser.add_argument(
            "--log-level",
            choices=("DEBUG", "INFO", "WARNING", "ERROR"),
            default="INFO",
            help="크롤러 로그 최소 수준 (기본값: INFO)",
        )
        parser.add_argument(
            "--input-file",
            type=Path,
            default=None,
            help="적재할 JSONL 경로 (기본값: --data-dir/records.jsonl)",
        )
        parser.add_argument(
            "--log-file",
            type=Path,
            default=loader_defaults.log_path,
            help=f"적재 pipeline JSONL 로그 경로 (기본값: {loader_defaults.log_path})",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=loader_defaults.batch_size,
            help=f"MongoDB batch 크기 (기본값: {loader_defaults.batch_size})",
        )
        parser.add_argument(
            "--dataset-id",
            help="입력 데이터셋 ID를 지정해 다른 데이터셋 혼입을 차단합니다.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="적재 JSON 로그를 stderr에 출력하지 않습니다.",
        )

    def handle(self, *args, **options) -> None:
        if options["once"]:
            self._run_cycle(options)
            return
        self._run_schedule(options)

    def _run_schedule(self, options) -> None:
        self.stdout.write(
            "크롤링·적재 스케줄러를 시작했습니다. "
            "매시 01,04,...,58분 00초(KST)에 실행합니다. "
            "종료하려면 Ctrl+C를 누르세요."
        )
        try:
            while True:
                target = _next_run_at(datetime.now(SEOUL))
                self.stdout.write(
                    f"다음 크롤링·적재 예정 시각: {target.isoformat(timespec='seconds')}"
                )
                _wait_until(target)
                try:
                    self._run_cycle(options)
                except CommandError as exc:
                    self.stderr.write(
                        f"예약 크롤링·적재가 실패했습니다: {exc} "
                        "다음 예약 시각에 다시 시도합니다."
                    )
        except KeyboardInterrupt:
            self.stdout.write("크롤링·적재 스케줄러를 종료했습니다.")

    def _run_cycle(self, options) -> None:
        crawl_exit_code = run_once(
            base_url=options["base_url"],
            data_dir=options["data_dir"],
            limit=options["limit"],
            log_level=options["log_level"],
        )
        if crawl_exit_code != 0:
            raise CommandError(
                f"크롤링이 종료 코드 {crawl_exit_code}로 끝나 적재하지 않습니다."
            )

        loader_config = self._loader_config(options)
        try:
            result = run_loader(
                loader_config,
                expected_dataset_id=options.get("dataset_id"),
                echo=not options["quiet"],
            )
        except LoaderFailure as exc:
            raise CommandError(f"크롤링 후 MongoDB 적재에 실패했습니다: {exc}") from exc
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        summary = (
            f"crawl_status=success run_id={result.run_id} "
            f"load_status={result.status} input={result.input_count} "
            f"success={result.success_count} failure={result.failure_count} "
            f"quarantine={result.quarantine_count} "
            f"inserted={result.inserted_count} duplicate={result.duplicate_count}"
        )
        if result.status == "success":
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(self.style.WARNING(summary))
            raise CommandError("Bronze 적재가 partial_failure 상태로 종료되었습니다.")

    def _loader_config(self, options):
        defaults = default_loader_config()
        input_path = options["input_file"]
        if input_path is None:
            input_path = (
                Path(options["data_dir"]).expanduser().resolve() / "records.jsonl"
            )
        return replace(
            defaults,
            input_path=input_path,
            log_path=options["log_file"],
            batch_size=options["batch_size"],
        ).resolve_paths()
