from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .profiler import SchemaProfiler
from .sinks import DocumentSink
from .sources import DocumentSource
from .standardizers import StandardizationError, Standardizer
from .validators import ValidationIssue, Validator


@dataclass(frozen=True)
class PipelineResult:
    """파이프라인 실행 리포트와 저장 경로를 반환한다."""

    report: dict[str, Any]
    report_path: str


class Pipeline:
    """추출, 표준화, 검증, 저장 단계의 실행 순서만 조정한다."""

    def __init__(
        self,
        *,
        source: DocumentSource,
        standardizer: Standardizer,
        validators: Sequence[Validator],
        sink: DocumentSink,
        run_id: str,
        logger: logging.Logger | None = None,
        standardize_logger: logging.Logger | None = None,
        validation_logger: logging.Logger | None = None,
    ) -> None:
        """각 단계의 구현체와 실행 식별자를 주입받는다."""

        self._source = source
        self._standardizer = standardizer
        self._validators = tuple(validators)
        self._sink = sink
        self._run_id = run_id
        self._logger = logger or logging.getLogger(__name__)
        self._standardize_logger = standardize_logger or self._logger
        self._validation_logger = validation_logger or self._logger

    @staticmethod
    def create_run_id() -> str:
        """UTC 시각과 임의 문자열을 조합해 실행 ID를 만든다."""

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{timestamp}-{uuid4().hex[:8]}"

    def run(self) -> PipelineResult:
        """문서를 순차 처리하고 건수·품질·구조 리포트를 생성한다."""

        started_at = datetime.now(timezone.utc)
        counts = Counter(
            extracted=0,
            standardized=0,
            accepted=0,
            rejected=0,
            standardization_failed=0,
            validation_failed=0,
        )
        issue_counts: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        profiler = SchemaProfiler()

        self._logger.info(
            "event=pipeline_started run_id=%s source=%s",
            self._run_id,
            self._source.description,
        )

        try:
            for document in self._source.read():
                counts["extracted"] += 1
                raw_document_id = _document_id(document)
                document_for_standardization = _inject_runtime_context(
                    document,
                    run_id=self._run_id,
                    enabled=getattr(
                        self._standardizer,
                        "requires_runtime_context",
                        False,
                    ),
                )

                try:
                    standardized = self._standardizer.standardize(
                        document_for_standardization
                    )
                    counts["standardized"] += 1
                    # 구조 파악을 위해 이후 검증에서 탈락할 문서도 포함한다.
                    profiler.observe(standardized)
                except StandardizationError as error:
                    counts["rejected"] += 1
                    counts["standardization_failed"] += 1
                    issue_counts["standardization"] += 1
                    self._sink.write_rejected(
                        document_id=raw_document_id,
                        stage="standardization",
                        reasons=[{"rule": "standardization", "message": str(error)}],
                        document=document,
                    )
                    continue

                issues = self._validate(standardized)
                if issues:
                    counts["rejected"] += 1
                    counts["validation_failed"] += 1
                    for issue in issues:
                        issue_counts[issue.rule] += 1
                        category_counts[issue.category] += 1
                    self._sink.write_rejected(
                        document_id=_document_id(standardized),
                        stage="validation",
                        reasons=[issue.to_dict() for issue in issues],
                        document=standardized,
                    )
                    continue

                self._sink.write_success(standardized)
                counts["accepted"] += 1

            if counts["extracted"] != counts["accepted"] + counts["rejected"]:
                raise RuntimeError("추출 건수와 성공/실패 건수가 일치하지 않습니다.")

            status = _status_from_counts(counts)
            report = self._build_report(
                status=status,
                started_at=started_at,
                counts=counts,
                issue_counts=issue_counts,
                category_counts=category_counts,
                profiler=profiler,
            )
            report_path = self._sink.write_report(report)
            self._log_stage_summaries(counts, category_counts)

            if status == "FAILED":
                self._logger.error(
                    "event=all_documents_rejected run_id=%s rejected=%d",
                    self._run_id,
                    counts["rejected"],
                )
            elif counts["extracted"] == 0:
                self._logger.warning(
                    "event=no_documents_extracted run_id=%s",
                    self._run_id,
                )
            elif counts["rejected"]:
                self._logger.warning(
                    "event=documents_rejected run_id=%s rejected=%d",
                    self._run_id,
                    counts["rejected"],
                )
            self._logger.info(
                "event=pipeline_completed run_id=%s status=%s extracted=%d accepted=%d rejected=%d",
                self._run_id,
                status,
                counts["extracted"],
                counts["accepted"],
                counts["rejected"],
            )
            return PipelineResult(report=report, report_path=report_path)
        except Exception:
            self._log_stage_summaries(counts, category_counts, force_error=True)
            report = self._build_report(
                status="FAILED",
                started_at=started_at,
                counts=counts,
                issue_counts=issue_counts,
                category_counts=category_counts,
                profiler=profiler,
            )
            self._sink.write_report(report)
            self._logger.exception("event=pipeline_failed run_id=%s", self._run_id)
            raise
        finally:
            try:
                self._source.close()
            finally:
                self._sink.close()

    def _validate(self, document: dict[str, Any]) -> list[ValidationIssue]:
        """등록된 모든 검증기를 실행해 문제를 하나의 목록으로 모은다."""

        issues: list[ValidationIssue] = []
        for validator in self._validators:
            issues.extend(validator.validate(document))
        return issues

    def _build_report(
        self,
        *,
        status: str,
        started_at: datetime,
        counts: Counter[str],
        issue_counts: Counter[str],
        category_counts: Counter[str],
        profiler: SchemaProfiler,
    ) -> dict[str, Any]:
        """현재 처리 상태를 실행 결과 리포트로 구성한다."""

        finished_at = datetime.now(timezone.utc)
        return {
            "run_id": self._run_id,
            "status": status,
            "started_at": _iso_utc(started_at),
            "finished_at": _iso_utc(finished_at),
            "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
            "source": self._source.description,
            "standardization": getattr(
                self._standardizer,
                "description",
                {"type": type(self._standardizer).__name__},
            ),
            "counts": dict(counts),
            "quality": {
                "issue_counts": dict(sorted(issue_counts.items())),
                "category_counts": dict(sorted(category_counts.items())),
            },
            "schema_profile": profiler.report(),
            "outputs": self._sink.description,
        }

    def _log_stage_summaries(
        self,
        counts: Counter[str],
        category_counts: Counter[str],
        *,
        force_error: bool = False,
    ) -> None:
        """표준화와 검증 집계를 역할별 로그 파일 형식으로 기록한다."""

        metrics = getattr(self._standardizer, "metrics", {})
        standardize_level = _stage_level(
            total=counts["extracted"],
            passed=counts["standardized"],
            failed=counts["standardization_failed"],
            force_error=force_error,
        )
        validation_level = _stage_level(
            total=counts["standardized"],
            passed=counts["accepted"],
            failed=counts["validation_failed"],
            force_error=force_error,
        )

        self._standardize_logger.log(
            standardize_level,
            "컬럼명 변환 %d건 | 타입 변환 %d건 | 규칙 적용 %d건 | "
            "규칙 NULL 처리 %d건 | 규칙 경고 %d건 | 표준화 완료 %d건 | 변환 실패 %d건",
            metrics.get("column_renamed", 0),
            metrics.get("type_converted", 0),
            metrics.get("rule_applied", 0),
            metrics.get("rule_nullified", 0),
            metrics.get("rule_warning", 0),
            counts["standardized"],
            counts["standardization_failed"],
        )
        self._validation_logger.log(
            validation_level,
            "검사 %d건 | PASS %d건 | FAIL %d건 | NULL 오류 %d건 | 형식 오류 %d건",
            counts["standardized"],
            counts["accepted"],
            counts["validation_failed"],
            category_counts["null"],
            category_counts["format"],
        )


def _document_id(document: Any) -> str | None:
    """문서의 `_id`를 로그와 제외 결과에 사용할 문자열로 반환한다."""

    if not hasattr(document, "get"):
        return None
    value = document.get("_id")
    return None if value is None else str(value)


def _inject_runtime_context(
    document: Any,
    *,
    run_id: str,
    enabled: bool,
) -> Any:
    """실행 ID와 원천 문서 ID를 필요한 YAML 규칙에만 주입한다."""

    if not enabled or not hasattr(document, "items"):
        return document

    prepared = dict(document)
    existing = prepared.get("_runtime")
    runtime = dict(existing) if isinstance(existing, Mapping) else {}
    source_id = prepared.get("source_document_id", prepared.get("_id"))
    if source_id is not None:
        runtime["source_document_id"] = str(source_id)
    runtime["normalization_run_id"] = run_id
    runtime["normalized_at"] = _iso_utc(datetime.now(timezone.utc))
    prepared["_runtime"] = runtime
    return prepared


def _status_from_counts(counts: Counter[str]) -> str:
    """입력과 정상·제외 건수로 실행 상태를 결정한다."""

    if counts["extracted"] == 0:
        # 엄격한 전체 적재에서는 FAILED도 가능하지만, 증분 수집의 0건은 정상일 수 있다.
        return "SUCCESS"
    if counts["accepted"] == 0:
        # 관대한 정책에서는 PARTIAL_SUCCESS도 가능하지만, 쓸 결과가 없으므로 실패로 본다.
        return "FAILED"
    if counts["rejected"]:
        return "PARTIAL_SUCCESS"
    return "SUCCESS"


def _stage_level(
    *,
    total: int,
    passed: int,
    failed: int,
    force_error: bool,
) -> int:
    """단계별 처리 결과에 맞는 로그 레벨을 반환한다."""

    if force_error or (total > 0 and passed == 0):
        return logging.ERROR
    if failed:
        return logging.WARNING
    return logging.INFO


def _iso_utc(value: datetime) -> str:
    """날짜를 UTC ISO 8601 문자열로 변환한다."""

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
