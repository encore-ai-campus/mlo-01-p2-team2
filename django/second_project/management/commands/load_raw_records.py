"""Django management command for the app-owned Bronze loader."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from second_project.service.bronze_config import default_config
from second_project.service.bronze_loader import LoaderFailure
from second_project.service.loader_runner import run_loader


class Command(BaseCommand):
    help = "크롤러 JSONL을 second_project 앱의 Bronze MongoDB 컬렉션에 적재합니다."
    # Avoid unrelated contrib models being checked against the MongoDB
    # backend.  The loader itself validates its input and writes its run log.
    requires_system_checks = []

    def add_arguments(self, parser) -> None:
        defaults = default_config()
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
            "--dataset-id",
            help="입력 데이터셋 ID를 지정해 다른 데이터셋 혼입을 차단합니다.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="stderr에 JSON 로그를 출력하지 않고 파일과 MongoDB에만 저장합니다.",
        )

    def handle(self, *args, **options) -> None:
        defaults = default_config()
        config = replace(
            defaults,
            input_path=options["input_file"],
            log_path=options["log_file"],
            batch_size=options["batch_size"],
        ).resolve_paths()
        try:
            result = run_loader(
                config,
                expected_dataset_id=options.get("dataset_id"),
                echo=not options["quiet"],
            )
        except LoaderFailure as exc:
            raise CommandError(str(exc)) from exc
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        summary = (
            f"run_id={result.run_id} status={result.status} "
            f"input={result.input_count} success={result.success_count} "
            f"failure={result.failure_count} quarantine={result.quarantine_count} "
            f"inserted={result.inserted_count} duplicate={result.duplicate_count}"
        )
        if result.status == "success":
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(self.style.WARNING(summary))
            raise CommandError("Bronze 적재가 partial_failure 상태로 종료되었습니다.")
