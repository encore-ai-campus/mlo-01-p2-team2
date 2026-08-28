from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from django.utils import timezone

from gold_layer.models import GoldReleaseRun

from .artifacts import write_release_package
from .config import GoldContract, load_contract
from .repositories import GoldWriter, SilverReader
from .repositories.gold_writer import ReleaseAlreadySucceeded
from .rules import build_gold_rows
from .types import QualityIssue, ValidationResult
from .validation import validate_loaded_release, validate_source, validate_transform


@dataclass(frozen=True)
class PipelineResult:
    release_id: str
    quality_status: str
    loaded: bool
    reused: bool
    counts: dict[str, int]
    release_directory: Path
    source_issues: tuple[QualityIssue, ...]
    transform_validation: ValidationResult
    load_validation: ValidationResult | None

    @property
    def passed(self) -> bool:
        return self.quality_status != GoldReleaseRun.QualityStatus.FAIL


def run_gold_pipeline(
    *,
    release_id: str,
    dataset_version: str,
    as_of_date: date,
    contract_path: str | Path | None = None,
    source_alias: str | None = None,
    target_alias: str | None = None,
    dry_run: bool = False,
    expected_counts: dict[str, int] | None = None,
) -> PipelineResult:
    contract = load_contract(contract_path)
    effective_source_alias = source_alias or contract.source_alias
    effective_target_alias = target_alias or contract.target_alias
    snapshot = SilverReader(using=effective_source_alias).read()
    issues = validate_source(
        snapshot,
        require_inactive_target=contract.require_inactive_target,
        strict_top_area_integrity=contract.strict_top_area_integrity,
    )
    rows = build_gold_rows(snapshot, as_of_date=as_of_date)
    transform_validation = validate_transform(rows)
    counts = {
        "assessment_rows": len(rows.assessments),
        "candidate_rows": len(rows.candidates),
        "excluded_rows": len(rows.excluded),
        "source_employee_rows": len(snapshot.employees),
        "source_area_rows": len(snapshot.areas),
        "source_parent_area_rows": len(snapshot.parents),
        "source_top_area_rows": len(snapshot.top_areas),
    }
    issues.extend(_expected_count_issues(counts, expected_counts or {}))
    if not transform_validation.passed:
        issues.append(
            QualityIssue(
                code="TRANSFORM_VALIDATION_FAILED",
                severity="ERROR",
                entity_type="DATASET",
                logical_entity_id=contract.dataset_name,
                message="In-memory Gold validation failed",
            )
        )

    has_error = any(issue.severity == "ERROR" for issue in issues)
    if has_error:
        quality_status = GoldReleaseRun.QualityStatus.FAIL
    elif rows.excluded or any(row.area_status == "ON_HOLD" for row in rows.assessments):
        quality_status = GoldReleaseRun.QualityStatus.PASS_WITH_HOLDS
    else:
        quality_status = GoldReleaseRun.QualityStatus.PASS

    load_validation: ValidationResult | None = None
    loaded = False
    reused = False
    if not dry_run and not has_error:
        writer = GoldWriter(using=effective_target_alias)
        try:
            load_validation = writer.write(
                release_id=release_id,
                dataset_name=contract.dataset_name,
                dataset_version=dataset_version,
                as_of_date=as_of_date,
                source_database=effective_source_alias,
                source_dataset_ids=snapshot.dataset_ids,
                source_run_ids=snapshot.normalization_run_ids,
                config_version=contract.config_version,
                quality_status=quality_status,
                counts=counts,
                rows=rows,
            )
            loaded = True
        except ReleaseAlreadySucceeded:
            load_validation = validate_loaded_release(
                release_id=release_id,
                using=effective_target_alias,
                expected_counts=counts,
            )
            reused = True

    generated_at = timezone.now()
    release_directory = write_release_package(
        contract=contract,
        release_id=release_id,
        dataset_version=dataset_version,
        as_of_date=as_of_date,
        generated_at=generated_at,
        source_dataset_ids=snapshot.dataset_ids,
        source_run_ids=snapshot.normalization_run_ids,
        counts=counts,
        quality_status=quality_status,
        source_issues=issues,
        transform_validation=transform_validation,
        load_validation=load_validation,
        dry_run=dry_run,
    )
    return PipelineResult(
        release_id=release_id,
        quality_status=quality_status,
        loaded=loaded,
        reused=reused,
        counts=counts,
        release_directory=release_directory,
        source_issues=tuple(issues),
        transform_validation=transform_validation,
        load_validation=load_validation,
    )


def _expected_count_issues(actual: dict[str, int], expected: dict[str, int]) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    unknown = sorted(set(expected).difference(actual))
    if unknown:
        raise ValueError(f"Unknown expected-count keys: {unknown}")
    for key, expected_value in expected.items():
        if actual[key] != int(expected_value):
            issues.append(
                QualityIssue(
                    code="EXPECTED_COUNT_MISMATCH",
                    severity="ERROR",
                    entity_type="COUNT",
                    logical_entity_id=key,
                    message=f"expected={expected_value}, actual={actual[key]}",
                )
            )
    return issues
