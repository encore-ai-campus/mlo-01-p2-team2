"""Standalone compatibility CLI for the app-owned Bronze loader."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django import setup  # noqa: E402

from second_project.service.bronze_config import LoaderConfig, default_config  # noqa: E402
from second_project.service.bronze_loader import LoaderFailure  # noqa: E402
from second_project.service.loader_runner import run_loader  # noqa: E402


def configure_stdio() -> None:
    """Keep console output UTF-8, matching the JSONL log encoding."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = default_config()
    parser = argparse.ArgumentParser(
        description="크롤러 JSONL을 Bronze MongoDB 컬렉션에 적재합니다."
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=defaults.input_path,
        help=f"입력 JSONL 경로 (기본값: {defaults.input_path})",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=defaults.log_path,
        help=f"적재 JSONL 로그 경로 (기본값: {defaults.log_path})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=defaults.batch_size,
        help=f"MongoDB batch 크기 (기본값: {defaults.batch_size})",
    )
    parser.add_argument(
        "--database",
        default=defaults.database,
        help=f"Bronze 대상 MongoDB 데이터베이스 (기본값: {defaults.database})",
    )
    parser.add_argument(
        "--dataset-id",
        help="입력 데이터셋 ID를 지정해 다른 데이터셋 혼입을 차단합니다.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="stderr에 JSON 로그를 출력하지 않고 파일과 MongoDB에만 저장합니다.",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> LoaderConfig:
    defaults = default_config()
    return replace(
        defaults,
        input_path=args.input_file,
        log_path=args.log_file,
        batch_size=args.batch_size,
        database=args.database,
    ).resolve_paths()


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    try:
        args = parse_args(argv)
        config = build_config(args)
        setup()
        result = run_loader(
            config,
            expected_dataset_id=args.dataset_id,
            echo=not args.quiet,
        )
    except LoaderFailure:
        return 1
    except ValueError:
        return 2
    except Exception:
        return 1
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
