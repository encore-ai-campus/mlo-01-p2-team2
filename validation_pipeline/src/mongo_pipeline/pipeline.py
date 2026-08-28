from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .bronze import (
    BronzeIntegrity,
    build_manifest,
    build_bronze_record,
    bronze_integrity,
    is_bronze_record,
    unwrap_bronze_record,
)
from .profiler import SchemaProfiler
from .sinks import DocumentSink
from .silver import (
    calculate_restoration_rate,
    split_silver_models,
    validate_silver_models,
)
from .sources import DocumentSource
from .standardizers import StandardizationError, Standardizer
from .validators import ValidationIssue, Validator, validate_final_unique_fields


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
        bronze_enabled: bool = True,
        unique_fields: Sequence[str] = (),
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
        self._bronze_enabled = bronze_enabled
        self._unique_fields = tuple(unique_fields)
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
            source_failed=0,
            standardization_failed=0,
            validation_failed=0,
        )
        issue_counts: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        profiler = SchemaProfiler()
        bronze_records: list[dict[str, Any]] = []
        bronze_summary = _empty_bronze_summary(self._bronze_enabled)
        candidate_documents: list[dict[str, Any]] = []
        accepted_documents: list[dict[str, Any]] = []
        dataset_ids: set[str] = set()
        restoration = calculate_restoration_rate([], [])

        self._logger.info(
            "event=pipeline_started run_id=%s source=%s",
            self._run_id,
            self._source.description,
        )

        try:
            for document in self._source.read():
                counts["extracted"] += 1
                source_is_bronze = is_bronze_record(document)
                source_document = unwrap_bronze_record(document)
                bronze_record: Mapping[str, Any] | None = None
                if self._bronze_enabled:
                    bronze_record = build_bronze_record(
                        document,
                        run_id=self._run_id,
                        row_number=counts["extracted"],
                        ingested_at=started_at,
                    )
                    bronze_records.append(bronze_record)
                    self._sink.write_bronze(
                        bronze_record,
                        persist=not (
                            source_is_bronze
                            and _source_targets_bronze(
                                self._source.description,
                                self._sink.description,
                            )
                        ),
                    )
                dataset_id = _document_metadata(
                    source_document,
                    "dataset_id",
                    "source.dataset_id",
                )
                if dataset_id:
                    dataset_ids.add(dataset_id)
                raw_document_id = (
                    _document_id(source_document)
                    or _document_metadata(
                        source_document,
                        "source_record_id",
                        "record_id",
                    )
                    or _document_id(document)
                )
                source_error = _source_error(source_document)
                if source_error is not None:
                    counts["rejected"] += 1
                    counts["source_failed"] += 1
                    issue_counts["source_parse"] += 1
                    category_counts["format"] += 1
                    source_line_no = source_error.get("source_line_no")
                    source_document_id = (
                        f"source-line:{source_line_no}"
                        if source_line_no is not None
                        else raw_document_id
                    )
                    reasons = [
                        {
                            "rule": "source_parse",
                            "rule_id": "SOURCE-PARSE-001",
                            "category": "format",
                            "field": "_source_error",
                            "error_code": "SOURCE_PARSE_FAILED",
                            "message": str(
                                source_error.get(
                                    "message",
                                    "입력 문서를 파싱할 수 없습니다.",
                                )
                            ),
                        }
                    ]
                    self._sink.write_rejected(
                        document_id=source_document_id,
                        stage="ingest",
                        reasons=reasons,
                        document=source_document,
                    )
                    self._log_quarantine(
                        stage="ingest",
                        document=source_document,
                        reasons=reasons,
                    )
                    continue

                document_for_standardization = _inject_runtime_context(
                    _inject_bronze_lineage(source_document, bronze_record),
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
                    standardized = _attach_normalization_run_id(
                        standardized,
                        run_id=self._run_id,
                    )
                    counts["standardized"] += 1
                    # 구조 파악을 위해 이후 검증에서 탈락할 문서도 포함한다.
                    profiler.observe(standardized)
                except StandardizationError as error:
                    counts["rejected"] += 1
                    counts["standardization_failed"] += 1
                    issue_counts["standardization"] += 1
                    reasons = [_standardization_reason(error)]
                    self._sink.write_rejected(
                        document_id=raw_document_id,
                        stage="standardization",
                        reasons=reasons,
                        document=source_document,
                    )
                    self._log_quarantine(
                        stage="standardization",
                        document=source_document,
                        reasons=reasons,
                    )
                    continue

                candidate_documents.append(standardized)

            # 모든 후보 문서가 최종 표준화된 뒤, 문서별/Silver 검증을 먼저
            # 수행하고 마지막에 업무 식별자 중복을 검사한다. 중복 키는
            # 원천값이 아니라 표준화 결과를 기준으로 계산된다.
            document_issues = [
                self._validate(standardized)
                for standardized in candidate_documents
            ]
            batch_issues = validate_silver_models(candidate_documents)
            final_unique_issues = validate_final_unique_fields(
                candidate_documents,
                self._unique_fields,
            )
            for index, standardized in enumerate(candidate_documents):
                issues = document_issues[index]
                issues.extend(batch_issues.get(index, []))
                issues.extend(final_unique_issues.get(index, []))
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
                    self._log_quarantine(
                        stage="validation",
                        document=standardized,
                        reasons=[issue.to_dict() for issue in issues],
                    )
                    continue

                self._log_quality_corrections(standardized)
                self._sink.write_success(standardized)
                accepted_documents.append(standardized)
                counts["accepted"] += 1

            if counts["extracted"] != counts["accepted"] + counts["rejected"]:
                raise RuntimeError("추출 건수와 성공/실패 건수가 일치하지 않습니다.")

            restoration = calculate_restoration_rate(
                bronze_records if self._bronze_enabled else candidate_documents,
                accepted_documents,
            )
            status = _status_from_counts(counts)
            if restoration.gate_status == "failed":
                status = "FAILED"
            bronze_summary = self._finalize_bronze(
                status=status,
                started_at=started_at,
                row_count=counts["extracted"],
                bronze_records=bronze_records,
                error_summary=(
                    f"{counts['rejected']} record(s) rejected"
                    if counts["rejected"]
                    else None
                ),
            )
            if isinstance(bronze_summary.get("integrity_rate"), (int, float)):
                restoration = _with_bronze_integrity(
                    restoration,
                    bronze_summary["integrity_rate"],
                )
                if bronze_summary["integrity_rate"] < 1.0:
                    status = "FAILED"
            report = self._build_report(
                status=status,
                started_at=started_at,
                counts=counts,
                issue_counts=issue_counts,
                category_counts=category_counts,
                profiler=profiler,
                restoration=restoration,
                accepted_documents=accepted_documents,
                bronze=bronze_summary,
            )
            report_path = self._sink.write_report(report)
            self._log_stage_summaries(
                counts,
                category_counts,
                started_at=started_at,
                dataset_id=_dataset_id_for_log(dataset_ids),
                restoration=restoration,
                bronze=bronze_summary,
            )

            if status == "FAILED" and counts["accepted"] == 0:
                self._logger.error(
                    "event=all_documents_rejected run_id=%s rejected=%d",
                    self._run_id,
                    counts["rejected"],
                )
            elif status == "FAILED":
                self._logger.error(
                    "event=quality_gate_failed run_id=%s accepted=%d rejected=%d "
                    "restoration_rate=%s target_rate=%s",
                    self._run_id,
                    counts["accepted"],
                    counts["rejected"],
                    restoration.restoration_rate,
                    restoration.target_rate,
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
        except Exception as error:
            try:
                restoration = calculate_restoration_rate(
                    bronze_records if self._bronze_enabled else candidate_documents,
                    accepted_documents,
                )
                bronze_summary = self._finalize_bronze(
                    status="FAILED",
                    started_at=started_at,
                    row_count=counts["extracted"],
                    bronze_records=bronze_records,
                    error_summary=str(error),
                )
                if isinstance(bronze_summary.get("integrity_rate"), (int, float)):
                    restoration = _with_bronze_integrity(
                        restoration,
                        bronze_summary["integrity_rate"],
                    )
            except Exception:
                self._logger.exception(
                    "event=bronze_finalize_failed run_id=%s",
                    self._run_id,
                )
            self._log_stage_summaries(
                counts,
                category_counts,
                started_at=started_at,
                dataset_id=_dataset_id_for_log(dataset_ids),
                restoration=restoration,
                bronze=bronze_summary,
                force_error=True,
            )
            report = self._build_report(
                status="FAILED",
                started_at=started_at,
                counts=counts,
                issue_counts=issue_counts,
                category_counts=category_counts,
                profiler=profiler,
                restoration=restoration,
                accepted_documents=accepted_documents,
                bronze=bronze_summary,
            )
            try:
                self._sink.write_report(report)
            except Exception:
                self._logger.exception(
                    "event=failed_report_write run_id=%s",
                    self._run_id,
                )
            self._logger.exception("event=pipeline_failed run_id=%s", self._run_id)
            raise
        finally:
            try:
                self._source.close()
            finally:
                self._sink.close()

    def _finalize_bronze(
        self,
        *,
        status: str,
        started_at: datetime,
        row_count: int,
        bronze_records: Sequence[Mapping[str, Any]],
        error_summary: str | None,
    ) -> dict[str, Any]:
        """Bronze artifact를 검증하고 Manifest를 저장한다."""

        if not self._bronze_enabled:
            return _empty_bronze_summary(False)

        sink_flush = getattr(self._sink, "flush", None)
        if callable(sink_flush):
            sink_flush()
        sink_description = self._sink.description
        bronze_output = sink_description.get("bronze")
        local_bronze_output = sink_description.get("local_bronze")
        fallback_raw_path = (
            bronze_output
            if isinstance(bronze_output, str)
            else local_bronze_output
            if isinstance(local_bronze_output, str)
            else None
        )
        manifest = build_manifest(
            run_id=self._run_id,
            source_description=self._source.description,
            started_at=started_at,
            row_count=row_count,
            status=status,
            fallback_raw_path=fallback_raw_path,
            error_summary=error_summary,
        )
        integrity: BronzeIntegrity = bronze_integrity(bronze_records, manifest)
        manifest_status = "FAILED" if integrity.rate < 1.0 else status
        if manifest_status != status:
            manifest = build_manifest(
                run_id=self._run_id,
                source_description=self._source.description,
                started_at=started_at,
                row_count=row_count,
                status=manifest_status,
                fallback_raw_path=fallback_raw_path,
                error_summary=error_summary,
            )
        manifest_path = self._sink.write_manifest(manifest)
        source_ids = {
            str(record["source_record_id"])
            for record in bronze_records
            if record.get("source_record_id") not in (None, "")
        }
        return {
            "enabled": True,
            "record_count": len(bronze_records),
            "distinct_source_record_count": len(source_ids),
            "valid_record_count": integrity.valid_record_count,
            "invalid_record_count": integrity.record_count
            - integrity.valid_record_count,
            "integrity_rate": integrity.rate,
            "source_file_verified": integrity.source_file_verified,
            "manifest": manifest,
            "manifest_path": manifest_path,
        }

    def _validate(self, document: dict[str, Any]) -> list[ValidationIssue]:
        """등록된 모든 검증기를 실행해 문제를 하나의 목록으로 모은다."""

        issues: list[ValidationIssue] = []
        for validator in self._validators:
            issues.extend(validator.validate(document))
        return issues

    def _log_quarantine(
        self,
        *,
        stage: str,
        document: Any,
        reasons: Sequence[Mapping[str, Any]],
    ) -> None:
        """원문 없이 격리 식별자와 오류 코드만 JSONL 감사 로그에 남긴다."""

        first_reason = reasons[0] if reasons else {}
        logger = (
            self._validation_logger
            if stage == "validation"
            else self._standardize_logger
        )
        source_record_id = _document_metadata(
            document,
            "source.record_id",
            "source_record_id",
        )
        source_error = _source_error(document)
        if source_record_id is None and source_error is not None:
            line_number = source_error.get("source_line_no")
            if line_number is not None:
                source_record_id = f"source-line:{line_number}"
        source_record_id = source_record_id or _document_id(document) or "unknown"
        event: dict[str, Any] = {
            "run_id": self._run_id,
            "stage": "quarantine",
            "source_stage": stage,
            "dataset_id": _document_metadata(
                document,
                "dataset_id",
                "source.dataset_id",
            ) or "unknown",
            "source_record_id": source_record_id,
            "status": "failed",
            "input_count": 1,
            "success_count": 0,
            "failure_count": 0,
            "quarantine_count": 1,
            "duration_ms": 0,
            "rule_id": _reason_value(first_reason, "rule_id", "rule"),
            "error_code": _reason_value(first_reason, "error_code") or "PIPELINE_ERROR",
            "message": "문서를 quarantine으로 격리했습니다.",
        }
        correction_codes = _reason_value(first_reason, "correction_codes")
        if correction_codes is not None:
            event["correction_codes"] = correction_codes
        logger.warning(
            "문서를 quarantine으로 격리했습니다.",
            extra={"json_event": event, "audit_channel": "quarantine"},
        )

    def _build_report(
        self,
        *,
        status: str,
        started_at: datetime,
        counts: Counter[str],
        issue_counts: Counter[str],
        category_counts: Counter[str],
        profiler: SchemaProfiler,
        restoration: Any | None = None,
        accepted_documents: Sequence[Mapping[str, Any]] = (),
        bronze: Mapping[str, Any] | None = None,
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
            "bronze": dict(
                bronze
                if bronze is not None
                else _empty_bronze_summary(self._bronze_enabled)
            ),
            "restoration": (
                restoration.to_dict()
                if restoration is not None
                else calculate_restoration_rate([], []).to_dict()
            ),
            "silver": {
                "model_counts": _silver_model_counts(accepted_documents),
                "models": [
                    "silver_employee",
                    "silver_area",
                    "silver_parent_area",
                    "silver_top_area_detail",
                ],
            },
            "schema_profile": profiler.report(),
            "outputs": self._sink.description,
        }

    def _log_stage_summaries(
        self,
        counts: Counter[str],
        category_counts: Counter[str],
        *,
        started_at: datetime | None = None,
        dataset_id: str = "unknown",
        restoration: Any | None = None,
        bronze: Mapping[str, Any] | None = None,
        force_error: bool = False,
    ) -> None:
        """표준화와 검증 집계를 역할별 로그 파일 형식으로 기록한다."""

        metrics = getattr(self._standardizer, "metrics", {})
        standardize_level = _stage_level(
            total=counts["extracted"],
            passed=counts["standardized"],
            failed=counts["source_failed"] + counts["standardization_failed"],
            force_error=force_error,
        )
        validation_level = _stage_level(
            total=counts["standardized"],
            passed=counts["accepted"],
            failed=counts["validation_failed"],
            force_error=force_error,
        )
        duration_ms = 0
        if started_at is not None:
            duration_ms = max(
                0,
                int(
                    (datetime.now(timezone.utc) - started_at).total_seconds()
                    * 1000
                ),
            )
        standardize_status = _log_status(
            total=counts["extracted"],
            failed=counts["source_failed"] + counts["standardization_failed"],
            force_error=force_error,
        )
        validation_status = _log_status(
            total=counts["standardized"],
            failed=counts["validation_failed"],
            force_error=force_error,
        )
        bronze_summary = (
            dict(bronze)
            if bronze is not None
            else _empty_bronze_summary(self._bronze_enabled)
        )
        bronze_record_count = int(bronze_summary.get("record_count", 0) or 0)
        bronze_failure_count = max(counts["extracted"] - bronze_record_count, 0)
        bronze_integrity_rate = bronze_summary.get("integrity_rate")
        bronze_failed = bronze_failure_count > 0 or (
            isinstance(bronze_integrity_rate, (int, float))
            and bronze_integrity_rate < 1.0
        )
        bronze_status = (
            "failed"
            if force_error or bronze_failed
            else str(
                bronze_summary.get("manifest", {}).get("status", "success")
                if isinstance(bronze_summary.get("manifest"), Mapping)
                else "success"
            )
        )
        bronze_level = _stage_level(
            total=counts["extracted"],
            passed=bronze_record_count,
            failed=bronze_failure_count,
            force_error=force_error or (
                isinstance(bronze_integrity_rate, (int, float))
                and bronze_integrity_rate < 1.0
            ),
        )
        self._standardize_logger.log(
            bronze_level,
            "Bronze 원본 보존 %d건 | 무결성 %.6f | 보존 실패 %d건",
            bronze_record_count,
            bronze_integrity_rate
            if isinstance(bronze_integrity_rate, (int, float))
            else 0.0,
            bronze_failure_count,
            extra={
                "json_event": {
                    "run_id": self._run_id,
                    "stage": "bronze",
                    "dataset_id": dataset_id,
                    "status": bronze_status,
                    "input_count": counts["extracted"],
                    "success_count": bronze_record_count,
                    "failure_count": bronze_failure_count,
                    "quarantine_count": 0,
                    "duration_ms": duration_ms,
                    "record_integrity_rate": bronze_integrity_rate,
                    "source_file_verified": bronze_summary.get(
                        "source_file_verified"
                    ),
                    "manifest_path": bronze_summary.get("manifest_path"),
                    "message": "Bronze 원본 보존 단계 집계",
                }
            },
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
            extra={
                "json_event": {
                    "run_id": self._run_id,
                    "stage": "silver",
                    "dataset_id": dataset_id,
                    "status": standardize_status,
                    "input_count": counts["extracted"],
                    "success_count": counts["standardized"],
                    "failure_count": 0,
                    "quarantine_count": (
                        counts["source_failed"] + counts["standardization_failed"]
                    ),
                    "duration_ms": duration_ms,
                    "message": "표준화 단계 집계",
                }
            },
        )
        self._validation_logger.log(
            validation_level,
            "검사 %d건 | PASS %d건 | FAIL %d건 | NULL 오류 %d건 | 형식 오류 %d건",
            counts["standardized"],
            counts["accepted"],
            counts["validation_failed"],
            category_counts["null"],
            category_counts["format"],
            extra={
                "json_event": {
                    "run_id": self._run_id,
                    "stage": "quality",
                    "dataset_id": dataset_id,
                    "status": validation_status,
                    "input_count": counts["standardized"],
                    "success_count": counts["accepted"],
                    "failure_count": 0,
                    "quarantine_count": counts["validation_failed"],
                    "duration_ms": duration_ms,
                    "message": "검증 단계 집계",
                }
            },
        )
        if restoration is not None:
            self._log_restoration(
                restoration,
                dataset_id=dataset_id,
                duration_ms=duration_ms,
            )

    def _log_restoration(
        self,
        restoration: Any,
        *,
        dataset_id: str,
        duration_ms: int,
    ) -> None:
        """Bronze 원천 ID 대비 RAW_DB 복구율을 별도 JSONL로 기록한다."""

        if restoration.gate_status == "passed":
            level = logging.INFO
            status = "success"
            message = "RAW_DB restoration gate passed"
        elif restoration.gate_status == "failed":
            level = logging.ERROR
            status = "failed"
            message = "RAW_DB restoration gate failed"
        else:
            level = logging.WARNING
            status = "partial_failure"
            message = "RAW_DB restoration gate is not evaluable"

        source_count = restoration.source_distinct_count
        recovered_count = restoration.silver_recovered_source_count
        self._standardize_logger.log(
            level,
            message,
            extra={
                "json_event": {
                    "run_id": self._run_id,
                    "stage": "quality",
                    "dataset_id": dataset_id,
                    "status": status,
                    "input_count": source_count,
                    "success_count": recovered_count,
                    "failure_count": 0,
                    "quarantine_count": max(source_count - recovered_count, 0),
                    "duration_ms": duration_ms,
                    "source_distinct_count": source_count,
                    "bronze_distinct_source_count": source_count,
                    "silver_recovered_source_count": recovered_count,
                    "restoration_rate": restoration.restoration_rate,
                    "target_rate": restoration.target_rate,
                    "bronze_integrity_rate": restoration.bronze_integrity_rate,
                    "message": message,
                },
                "audit_channel": "restoration",
            },
        )

    def _log_quality_corrections(self, document: Mapping[str, Any]) -> None:
        """격리는 아니지만 품질 검토가 필요한 보정 코드를 quality JSONL에 남긴다."""

        codes = document.get("correction_codes")
        if not isinstance(codes, list) or "DATE_CONFLICT" not in codes:
            return
        self._validation_logger.warning(
            "등록일 충돌을 원본 선택 없이 품질 경고로 기록했습니다.",
            extra={
                "json_event": {
                    "run_id": self._run_id,
                    "stage": "quality",
                    "dataset_id": _document_metadata(
                        document,
                        "dataset_id",
                        "source.dataset_id",
                    ),
                    "source_record_id": _document_metadata(
                        document,
                        "source_record_id",
                        "source.record_id",
                    ),
                    "status": "success",
                    "input_count": 1,
                    "success_count": 1,
                    "failure_count": 0,
                    "quarantine_count": 0,
                    "duration_ms": 0,
                    "rule_id": "DATE-CONFLICT-001",
                    "error_code": "DATE_CONFLICT",
                    "correction_codes": ["DATE_CONFLICT"],
                    "message": "등록일 원본 간 값 충돌을 확인해야 합니다.",
                }
            },
        )


def _document_id(document: Any) -> str | None:
    """문서의 `_id`를 로그와 제외 결과에 사용할 문자열로 반환한다."""

    if not hasattr(document, "get"):
        return None
    value = document.get("_id")
    return None if value is None else str(value)


def _source_targets_bronze(
    source_description: Mapping[str, Any],
    sink_description: Mapping[str, Any],
) -> bool:
    """입력 source와 sink Bronze가 같은 Mongo collection인지 확인한다."""

    bronze = sink_description.get("bronze")
    if not isinstance(bronze, Mapping):
        return False
    source_database = source_description.get("database")
    source_collection = source_description.get("collection")
    return (
        source_description.get("type") in {"mongodb", "django_mongodb"}
        and source_database not in (None, "")
        and source_collection not in (None, "")
        and source_database == bronze.get("database")
        and source_collection == bronze.get("collection")
    )


def _source_error(document: Any) -> Mapping[str, Any] | None:
    """JsonlSource가 감싼 입력 파싱 오류를 반환한다."""

    if not isinstance(document, Mapping):
        return None
    error = document.get("_source_error")
    return error if isinstance(error, Mapping) else None


def _standardization_reason(error: StandardizationError) -> dict[str, Any]:
    """표준화 예외를 재처리 가능한 격리 사유 object로 변환한다."""

    message = str(error)
    match = re.match(r"^\[([^\]]+)\]\s*([^:]+):\s*(.*)$", message)
    if match:
        rule_id = match.group(1)
        field = match.group(2).strip()
        detail = match.group(3).strip()
    else:
        rule_id = "STANDARDIZATION"
        field = None
        detail = message

    if any(token in detail for token in ("날짜", "date", "datetime", "파싱")):
        error_code = "DATETIME_PARSE_FAILED"
    elif any(
        token in detail
        for token in (
            "허용 목록",
            "enum",
            "도메인",
            "코드 형식",
            "true 또는 false",
        )
    ):
        error_code = "DOMAIN_UNKNOWN"
    elif any(token in detail for token in ("필수", "null", "원천 필드")):
        error_code = "REQUIRED_VALUE_MISSING"
    else:
        error_code = "STANDARDIZATION_FAILED"

    return {
        "rule": rule_id,
        "rule_id": rule_id,
        "category": "standardization",
        "field": field,
        "error_code": error_code,
        "message": detail,
    }


def _document_metadata(document: Any, *paths: str) -> str | None:
    """JSONL 로그에 허용할 식별자만 문서에서 추출한다."""

    for path in paths:
        current: Any = document
        for key in path.split("."):
            if not isinstance(current, Mapping) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None and current != "":
            return str(current)
    return None


def _reason_value(reason: Mapping[str, Any], *keys: str) -> Any | None:
    for key in keys:
        value = reason.get(key)
        if value is not None and value != "":
            return value
    return None


def _log_status(*, total: int, failed: int, force_error: bool) -> str:
    """로깅 규칙에서 사용하는 소문자 단계 상태를 반환한다."""

    if force_error or (total > 0 and failed >= total):
        return "failed"
    if failed:
        return "partial_failure"
    return "success"


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


def _attach_normalization_run_id(
    document: Any,
    *,
    run_id: str,
) -> dict[str, Any]:
    """표준화 결과에 현재 실행의 정규화 실행 ID를 보존한다.

    canonical YAML 규칙은 ``_runtime.normalization_run_id``를 통해 같은
    값을 만들지만, 기존 규칙이나 다른 Standardizer가 실행 컨텍스트를
    요구하지 않는 경우에도 성공 문서의 증분 적재 기준을 보장해야 한다.
    이미 다른 실행 ID가 들어온 문서는 실행 경계를 섞을 수 있으므로
    표준화 실패로 처리한다.
    """

    if not isinstance(document, Mapping):
        raise StandardizationError("표준화 결과가 object 형태가 아닙니다.")

    prepared = dict(document)
    current = prepared.get("normalization_run_id")
    if current in (None, ""):
        prepared["normalization_run_id"] = run_id
        return prepared

    normalized_current = str(current).strip()
    if normalized_current != run_id:
        raise StandardizationError(
            "표준화 결과의 normalization_run_id가 현재 실행 ID와 다릅니다: "
            f"expected={run_id}, actual={current}"
        )
    prepared["normalization_run_id"] = normalized_current
    return prepared


def _inject_bronze_lineage(
    document: Any,
    bronze_record: Mapping[str, Any] | None,
) -> Any:
    """Bronze가 부여한 원천 ID를 Silver 표준화 입력에 연결한다."""

    if bronze_record is None or not isinstance(document, Mapping):
        return document
    prepared = dict(document)
    # 표준화 결과의 계보 키는 원천 문서에 있던 후보가 아니라 Bronze가
    # 확정한 식별자를 사용해야 한다.
    prepared["source_record_id"] = bronze_record.get("source_record_id")
    source = prepared.get("source")
    if isinstance(source, Mapping):
        source_copy = dict(source)
        source_copy["record_id"] = bronze_record.get("source_record_id")
        prepared["source"] = source_copy
    if prepared.get("dataset_id") in (None, ""):
        prepared["dataset_id"] = bronze_record.get("dataset_id", "unknown")
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


def _dataset_id_for_log(dataset_ids: set[str]) -> str:
    """공통 로그 필드에 개인정보가 아닌 데이터셋 식별자를 채운다."""

    if not dataset_ids:
        return "unknown"
    if len(dataset_ids) == 1:
        return next(iter(dataset_ids))
    return "MULTIPLE"


def _silver_model_counts(
    documents: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """통과한 표준 후보를 모델별 고유 PK 건수로 요약한다."""

    counts: dict[str, set[Any]] = {
        model_name: set()
        for model_name in (
            "silver_employee",
            "silver_area",
            "silver_parent_area",
            "silver_top_area_detail",
        )
    }
    for document in documents:
        for model_name, model in split_silver_models(document).items():
            primary_key = {
                "silver_employee": "employee_id",
                "silver_area": "area_id",
                "silver_parent_area": "parent_area_id",
                "silver_top_area_detail": "top_area_id",
            }[model_name]
            value = model.get(primary_key)
            if value is not None:
                counts[model_name].add(value)
    return {model_name: len(values) for model_name, values in sorted(counts.items())}


def _empty_bronze_summary(enabled: bool) -> dict[str, Any]:
    """Bronze를 사용하지 않거나 아직 종료되지 않은 실행의 기본 요약이다."""

    return {
        "enabled": enabled,
        "record_count": 0,
        "distinct_source_record_count": 0,
        "valid_record_count": 0,
        "invalid_record_count": 0,
        "integrity_rate": None,
        "source_file_verified": None,
        "manifest": None,
        "manifest_path": None,
    }


def _with_bronze_integrity(restoration: Any, rate: float) -> Any:
    """복구율 결과에 Bronze 무결성 결과를 보강한다."""

    if hasattr(restoration, "bronze_integrity_rate"):
        return replace(restoration, bronze_integrity_rate=rate)
    return restoration
