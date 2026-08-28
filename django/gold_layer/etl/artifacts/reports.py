from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..config import GoldContract
from ..types import QualityIssue, ValidationResult


def write_release_package(
    *,
    contract: GoldContract,
    release_id: str,
    dataset_version: str,
    as_of_date: date,
    generated_at: datetime,
    source_dataset_ids: tuple[str, ...],
    source_run_ids: tuple[str, ...],
    counts: dict[str, int],
    quality_status: str,
    source_issues: list[QualityIssue],
    transform_validation: ValidationResult,
    load_validation: ValidationResult | None,
    dry_run: bool,
) -> Path:
    release_dir = contract.release_root / contract.dataset_name / dataset_version
    release_dir.mkdir(parents=True, exist_ok=True)
    common = {
        "dataset_name": contract.dataset_name,
        "dataset_version": dataset_version,
        "release_id": release_id,
        "as_of_date": as_of_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        "quality_status": quality_status,
        "dry_run": dry_run,
        "counts": counts,
    }
    manifest = {
        **common,
        "source_dataset_ids": list(source_dataset_ids),
        "source_normalization_run_ids": list(source_run_ids),
        "config_version": contract.config_version,
        "artifacts": sorted(contract.artifact_names.values()),
    }
    lineage = {
        **common,
        "flow": ["SQLite Silver canonical tables", "Gold HR policy transform", "Gold SQLite tables"],
        "join_key": "parent_area_id",
        "source_tables": ["silver_employee", "silver_area", "silver_parent_area", "silver_top_area_detail"],
        "target_tables": list(contract.physical_tables.values()),
        "pii_in_release_artifacts": False,
    }
    quality = {
        **common,
        "source_issue_summary": _issue_summary(source_issues),
        "transform_validation": list(transform_validation.checks),
    }
    validation = {
        **common,
        "transform": {"passed": transform_validation.passed, "checks": list(transform_validation.checks)},
        "load": None if load_validation is None else {"passed": load_validation.passed, "checks": list(load_validation.checks)},
    }
    _write_json(release_dir / contract.artifact_names["manifest"], manifest)
    _write_json(release_dir / contract.artifact_names["lineage"], lineage)
    _write_json(release_dir / contract.artifact_names["quality"], quality)
    _write_json(release_dir / contract.artifact_names["validation"], validation)
    _write_dataset_card(release_dir / contract.artifact_names["dataset_card"], common)
    _write_catalog(release_dir / contract.artifact_names["catalog"], common)
    missing_or_empty = [
        name
        for name in contract.artifact_names.values()
        if not (release_dir / name).is_file() or (release_dir / name).stat().st_size == 0
    ]
    if missing_or_empty:
        raise OSError(f"Release artifacts missing or empty: {missing_or_empty}")
    return release_dir


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_dataset_card(path: Path, common: dict[str, Any]) -> None:
    counts = common["counts"]
    body = f"""# {common['dataset_name']} dataset card

- Version: `{common['dataset_version']}`
- Release: `{common['release_id']}`
- As-of date: `{common['as_of_date']}`
- Quality: `{common['quality_status']}`
- Assessment rows: `{counts['assessment_rows']}`
- Candidate evidence rows: `{counts['candidate_rows']}`
- Excluded/held rows: `{counts['excluded_rows']}`

## Intended use

현재 Silver 등록정보를 기준으로 퇴직 대상 관리자의 업무영역별 내부 후보 검토 근거를 제공한다.
후보 존재는 대체 완료나 채용 불필요를 뜻하지 않으며, 후보 부재도 채용 확정을 뜻하지 않는다.

## Data and limitations

후보 연결은 이름이 아닌 `parent_area_id`를 사용한다. 부서·직위·근속은 탈락 점수가 아니라 비교 근거다.
릴리스 메타데이터 파일에는 직원 이름·사번을 기록하지 않는다.
"""
    path.write_text(body, encoding="utf-8")


def _write_catalog(path: Path, common: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["dataset_name", "dataset_version", "release_id", "as_of_date", "quality_status", "assessment_rows", "candidate_rows", "excluded_rows"])
        writer.writeheader()
        writer.writerow({
            "dataset_name": common["dataset_name"],
            "dataset_version": common["dataset_version"],
            "release_id": common["release_id"],
            "as_of_date": common["as_of_date"],
            "quality_status": common["quality_status"],
            "assessment_rows": common["counts"]["assessment_rows"],
            "candidate_rows": common["counts"]["candidate_rows"],
            "excluded_rows": common["counts"]["excluded_rows"],
        })


def _issue_summary(issues: list[QualityIssue]) -> list[dict[str, Any]]:
    counts = Counter((issue.code, issue.severity, issue.entity_type) for issue in issues)
    return [
        {"code": code, "severity": severity, "entity_type": entity_type, "count": count}
        for (code, severity, entity_type), count in sorted(counts.items())
    ]
