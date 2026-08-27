"""Run the incremental records crawler from Django and cron."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand

from crawling.crawl_records import run_once
from crawling.crawler.config import default_config


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
    help = "내부 records API를 3n+1분 00초(KST)에 반복 수집합니다."
    # The crawler does not use Django's databases.  Skipping project-wide
    # checks also keeps cron independent of optional database backends.
    requires_system_checks = []

    def add_arguments(self, parser) -> None:
        defaults = default_config()
        parser.add_argument(
            "--base-url",
            default=defaults.base_url,
            help=f"API 서버 기본 주소 (기본값: {defaults.base_url})",
        )
        parser.add_argument(
            "--data-dir",
            type=Path,
            default=defaults.data_dir,
            help=f"JSONL과 상태를 저장할 경로 (기본값: {defaults.data_dir})",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=defaults.page_limit,
            help=f"페이지당 요청 건수 (기본값: {defaults.page_limit})",
        )
        parser.add_argument(
            "--log-level",
            choices=("DEBUG", "INFO", "WARNING", "ERROR"),
            default="INFO",
            help="출력할 최소 로그 수준 (기본값: INFO)",
        )

    def handle(self, *args, **options) -> None:
        self._run_schedule(options)

    def _run_schedule(self, options) -> None:
        self.stdout.write(
            "크롤링 스케줄러를 시작했습니다. "
            "매시 01,04,...,58분 00초(KST)에 실행합니다. 종료하려면 Ctrl+C를 누르세요."
        )
        try:
            while True:
                target = _next_run_at(datetime.now(SEOUL))
                self.stdout.write(
                    f"다음 크롤링 예정 시각: {target.isoformat(timespec='seconds')}"
                )
                _wait_until(target)
                exit_code = run_once(
                    base_url=options["base_url"],
                    data_dir=options["data_dir"],
                    limit=options["limit"],
                    log_level=options["log_level"],
                )
                if exit_code:
                    self.stderr.write(
                        f"예약 크롤링이 종료 코드 {exit_code}로 끝났습니다. "
                        "다음 예약 시각에 다시 시도합니다."
                    )
        except KeyboardInterrupt:
            self.stdout.write("크롤링 스케줄러를 종료했습니다.")
