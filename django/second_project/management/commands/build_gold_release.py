from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from second_project.gold.etl import run_gold_pipeline


class Command(BaseCommand):
    help = "Build, validate, and package the reproducible Gold HR dataset release."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--release-id", required=True)
        parser.add_argument("--dataset-version", required=True)
        parser.add_argument("--as-of-date", required=True)
        parser.add_argument("--contract")
        parser.add_argument("--source-alias")
        parser.add_argument("--target-alias")
        parser.add_argument("--expected-counts", help="Path to a JSON object of expected row counts")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            as_of_date = date.fromisoformat(options["as_of_date"])
        except ValueError as error:
            raise CommandError("--as-of-date must use YYYY-MM-DD") from error
        expected_counts = _load_expected_counts(options.get("expected_counts"))
        try:
            result = run_gold_pipeline(
                release_id=options["release_id"],
                dataset_version=options["dataset_version"],
                as_of_date=as_of_date,
                contract_path=options.get("contract"),
                source_alias=options.get("source_alias"),
                target_alias=options.get("target_alias"),
                dry_run=options["dry_run"],
                expected_counts=expected_counts,
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise CommandError(str(error)) from error
        summary = {
            "release_id": result.release_id,
            "quality_status": result.quality_status,
            "loaded": result.loaded,
            "reused": result.reused,
            "counts": result.counts,
            "release_directory": str(result.release_directory),
            "issue_codes": sorted({issue.code for issue in result.source_issues}),
        }
        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        if not result.passed:
            raise CommandError("Gold release failed quality gates; inspect the generated quality report.")
        self.stdout.write(self.style.SUCCESS("Gold release completed and validated."))


def _load_expected_counts(path: str | None) -> dict[str, int] | None:
    if not path:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CommandError(f"Cannot read --expected-counts: {error}") from error
    if not isinstance(payload, dict) or any(not isinstance(value, int) for value in payload.values()):
        raise CommandError("--expected-counts must be a JSON object with integer values")
    return payload
