from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from second_project.services.success_to_sqlite import load_success_to_sqlite


class Command(BaseCommand):
    help = "성공 MongoDB Silver 또는 legacy 행 데이터를 SQLite에 upsert합니다."

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
            help="Mongo 성공 DB의 문서만 검증하고 SQLite에는 쓰지 않음",
        )

    def handle(self, *args, **options):
        try:
            summary = load_success_to_sqlite(
                options["config"],
                target_alias=options["target_alias"],
                batch_size=options["batch_size"],
                dry_run=options["dry_run"],
            )
        except Exception as error:
            raise CommandError(str(error)) from error

        mode = "검증만" if summary.dry_run else "upsert"
        self.stdout.write(
            self.style.SUCCESS(
                f"성공 DB → {summary.target_alias} {mode} 완료 "
                f"(source={summary.source_database}, total={summary.total})"
            )
        )
        for collection, count in summary.counts.items():
            self.stdout.write(f"- {collection}: {count}건")
