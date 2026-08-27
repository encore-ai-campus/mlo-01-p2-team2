#!/usr/bin/env python3
"""Run one incremental crawl from the internal records API.

The API key is loaded from a protected project .env file. Its lifetime
metadata is stored separately so a missed 00:01 KST refresh can be recovered
on the next execution. Raw record values are appended to one UTF-8 JSONL file,
and an opaque checkpoint is stored separately only after a successful run.

Examples:

    python manage.py crawl_records
    python crawling/crawl_records.py --log-level DEBUG
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

if __package__:
    from .crawler.api_client import ApiError, ApiKeyNotEffective
    from .crawler.config import default_config
    from .crawler.ingest_logging import configure_ingest_logging, new_run_id
    from .crawler.service import run_crawl
    from .crawler.storage import AlreadyRunningError, StateConsistencyError, StorageError
    from .crawler.validator import ValidationError
else:
    # Direct execution puts ``crawling`` on sys.path, while the shared
    # logging writer lives under the Django project package.
    django_root = str(Path(__file__).resolve().parents[1])
    if django_root not in sys.path:
        sys.path.insert(0, django_root)
    from crawler.api_client import ApiError, ApiKeyNotEffective
    from crawler.config import default_config
    from crawler.ingest_logging import configure_ingest_logging, new_run_id
    from crawler.service import run_crawl
    from crawler.storage import AlreadyRunningError, StateConsistencyError, StorageError
    from crawler.validator import ValidationError


def configure_stdio() -> None:
    """Use UTF-8 for redirected PowerShell and scheduler output."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def parse_args() -> argparse.Namespace:
    defaults = default_config()
    parser = argparse.ArgumentParser(
        description="내부 records API의 신규 공개 데이터를 JSONL에 누적합니다."
    )
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
    return parser.parse_args()


def run_once(
    *,
    base_url: str | None = None,
    data_dir: Path | None = None,
    limit: int | None = None,
    log_level: str = "INFO",
) -> int:
    """Run one crawl and return a scheduler-friendly process exit code."""

    configure_stdio()
    defaults = default_config()
    config = replace(
        defaults,
        base_url=base_url or defaults.base_url,
        data_dir=(data_dir or defaults.data_dir).expanduser().resolve(),
        page_limit=defaults.page_limit if limit is None else limit,
    )
    run_id = new_run_id()
    logger_name = "crawling" if __package__ else "crawler"
    configure_ingest_logging(
        log_level,
        config.crawling_log_path,
        run_id,
        logger_name=logger_name,
    )
    logger = logging.getLogger(logger_name)

    if config.page_limit <= 0:
        logger.error(
            "--limit은 1 이상이어야 합니다.",
        )
        return 2

    logger.info(
        "크롤링을 시작합니다. limit=%s",
        config.page_limit,
    )

    try:
        run_crawl(config, run_id=run_id)
    except AlreadyRunningError:
        logger.warning(
            "다른 크롤러 실행이 진행 중이어서 이번 실행을 건너뜁니다.",
        )
        return 1
    except ApiKeyNotEffective:
        logger.warning(
            "발급받은 API 키가 유효시간에 포함되지 않아 실행을 건너뜁니다.",
        )
        return 1
    except StateConsistencyError:
        logger.error(
            "저장 상태가 안전하지 않아 실행을 중단합니다.",
        )
        return 2
    except (ApiError, ValidationError, StorageError) as exc:
        logger.error(
            "크롤링에 실패했습니다. error_type=%s",
            type(exc).__name__,
        )
        return 1
    except Exception as exc:
        logger.error(
            "예상하지 못한 오류로 크롤링에 실패했습니다. error_type=%s",
            type(exc).__name__,
        )
        return 1

    return 0


def main() -> int:
    args = parse_args()
    return run_once(
        base_url=args.base_url,
        data_dir=args.data_dir,
        limit=args.limit,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    sys.exit(main())
