"""Orchestration for loading crawler JSONL into the Bronze MongoDB layer."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from second_project.service.bronze_config import LoaderConfig
from second_project.service.fingerprint import FileFingerprint, FileFingerprintError, fingerprint_file, relative_path
from second_project.repository.mongodb_repository import ChecksumConflictError, MongoDependencyError, MongoRepository, MongoStoreError
from second_project.service.record_reader import RecordValidationError, make_quarantine_document, parse_record_line
from second_project.service.structured_logging import StructuredLogWriter, now_iso


SEOUL = ZoneInfo("Asia/Seoul")


class LoaderFailure(RuntimeError):
    """Raised when a Bronze loading run cannot be completed safely."""

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class LoadResult:
    run_id: str
    dataset_id: str
    status: str
    input_count: int
    success_count: int
    failure_count: int
    quarantine_count: int
    inserted_count: int
    duplicate_count: int
    duration_ms: int


class BronzeLoader:
    """Load one append-only crawler output file with restart-safe semantics."""

    def __init__(
        self,
        config: LoaderConfig,
        *,
        run_id: str,
        logger: StructuredLogWriter,
        expected_dataset_id: str | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config.resolve_paths()
        self.config.validate()
        self.run_id = run_id
        self.logger = logger
        self.expected_dataset_id = expected_dataset_id
        self._now = now_provider or (lambda: datetime.now(SEOUL))

    def run(self) -> LoadResult:
        started_monotonic = time.monotonic()
        started_at = self._now()
        mongo_store: MongoRepository | None = None
        run_started_in_mongo = False
        input_fingerprint: FileFingerprint | None = None
        dataset_id = "UNKNOWN"
        source_sha256: str | None = None
        source_filename: str | None = None
        source_run_ids: set[str] = set()
        collected_at_first: str | None = None
        collected_at_last: str | None = None
        crawler_versions: set[str] = set()
        max_retry_count: int | None = None
        total_lines = 0
        inserted_count = 0
        duplicate_count = 0
        quarantine_count = 0
        failure_count = 0
        current_stage = "input_fingerprint"
        raw_batch: list[dict[str, Any]] = []
        quarantine_batch: list[dict[str, Any]] = []

        def flush_raw_batch() -> None:
            nonlocal inserted_count, duplicate_count, current_stage
            if not raw_batch:
                return
            if mongo_store is None:
                raise LoaderFailure("MongoDB 저장소가 준비되지 않았습니다.")
            current_stage = "bronze_raw_write"
            batch = list(raw_batch)
            try:
                result = mongo_store.upsert_raw_batch(batch)
                inserted_documents = [
                    document
                    for document in batch
                    if str(document["_id"]) in result.inserted_ids
                ]
                mongo_store.verify_inserted_batch(inserted_documents)
            except Exception as exc:
                self.logger.error(
                    "Bronze 원본 batch 적재에 실패했습니다.",
                    dataset_id=dataset_id,
                    status="failed",
                    input_count=len(batch),
                    success_count=0,
                    failure_count=len(batch),
                    quarantine_count=0,
                    error_code=getattr(exc, "error_code", None),
                    extra={"failed_stage": current_stage},
                )
                raise
            inserted_count += result.inserted_count
            duplicate_count += result.duplicate_count
            self.logger.info(
                "Bronze 원본 batch를 처리했습니다.",
                dataset_id=dataset_id,
                status="success",
                input_count=len(batch),
                success_count=len(batch),
                failure_count=0,
                quarantine_count=0,
                extra={
                    "inserted_count": result.inserted_count,
                    "duplicate_count": result.duplicate_count,
                },
            )
            raw_batch.clear()

        def flush_quarantine_batch() -> None:
            nonlocal quarantine_count, current_stage
            if not quarantine_batch:
                return
            if mongo_store is None:
                raise LoaderFailure("MongoDB 저장소가 준비되지 않았습니다.")
            current_stage = "bronze_quarantine_write"
            batch = list(quarantine_batch)
            try:
                mongo_store.insert_quarantine_batch(batch)
            except Exception as exc:
                self.logger.error(
                    "Bronze quarantine 적재에 실패했습니다.",
                    dataset_id=dataset_id,
                    status="failed",
                    input_count=len(batch),
                    success_count=0,
                    failure_count=len(batch),
                    quarantine_count=0,
                    error_code=getattr(exc, "error_code", None),
                    extra={"failed_stage": current_stage},
                )
                raise
            quarantine_count += len(batch)
            self.logger.warn(
                "Bronze 입력 오류 행을 quarantine에 보존했습니다.",
                dataset_id=dataset_id,
                status="partial_failure",
                input_count=len(batch),
                success_count=0,
                failure_count=0,
                quarantine_count=len(batch),
            )
            quarantine_batch.clear()

        try:
            input_fingerprint = fingerprint_file(self.config.input_path)
            current_stage = "mongo_connect"
            mongo_store = MongoRepository(self.config)
            mongo_store.ping_and_prepare()

            mongo_store.start_run(
                {
                    "_id": self.run_id,
                    "run_id": self.run_id,
                    "stage": "bronze",
                    "status": "running",
                    "dataset_id": dataset_id,
                    "input_count": 0,
                    "success_count": 0,
                    "failure_count": 0,
                    "quarantine_count": 0,
                    "input_file": relative_path(self.config.input_path, self.config.project_root),
                    "input_file_sha256": input_fingerprint.sha256,
                    "started_at": started_at,
                }
            )
            run_started_in_mongo = True
            self.logger.info(
                "MongoDB 연결과 Bronze 인덱스를 확인했습니다.",
                dataset_id=dataset_id,
                status="success",
                extra={
                    "file_size_bytes": input_fingerprint.size_bytes,
                    "file_sha256": input_fingerprint.sha256,
                },
            )

            current_stage = "bronze_input_read"
            with self.config.input_path.open("r", encoding="utf-8", newline="") as handle:
                for line_no, raw_line in enumerate(handle, start=1):
                    total_lines += 1
                    try:
                        parsed = parse_record_line(
                            raw_line,
                            line_no=line_no,
                            load_run_id=self.run_id,
                            input_file_sha256=input_fingerprint.sha256,
                            ingested_at=self._now(),
                        )
                    except RecordValidationError as exc:
                        quarantine_batch.append(
                            make_quarantine_document(
                                raw_line,
                                line_no=line_no,
                                load_run_id=self.run_id,
                                input_file_sha256=input_fingerprint.sha256,
                                error=exc,
                                created_at=self._now(),
                            )
                        )
                        if len(quarantine_batch) >= self.config.batch_size:
                            flush_quarantine_batch()
                        continue

                    if self.expected_dataset_id and parsed.dataset_id != self.expected_dataset_id:
                        raise LoaderFailure(
                            "입력 레코드의 dataset_id가 실행 인자와 다릅니다.",
                            error_code="CHECKSUM_MISMATCH",
                        )
                    if dataset_id == "UNKNOWN":
                        dataset_id = parsed.dataset_id
                    elif parsed.dataset_id != dataset_id:
                        raise LoaderFailure(
                            "하나의 Bronze 실행에서 여러 dataset_id를 섞을 수 없습니다.",
                            error_code="CHECKSUM_MISMATCH",
                        )

                    if source_filename is None:
                        source_filename = parsed.source_filename
                    elif source_filename != parsed.source_filename:
                        raise LoaderFailure(
                            "동일 dataset_id에 source_filename이 달라졌습니다.",
                            error_code="CHECKSUM_MISMATCH",
                        )

                    if source_sha256 is None:
                        source_sha256 = parsed.source_sha256
                    elif source_sha256 != parsed.source_sha256:
                        raise LoaderFailure(
                            "입력 JSONL 안에서 source_sha256가 변경되었습니다.",
                            error_code="CHECKSUM_MISMATCH",
                        )

                    if parsed.source_run_id:
                        source_run_ids.add(parsed.source_run_id)
                    if parsed.collected_at:
                        if collected_at_first is None:
                            collected_at_first = parsed.collected_at
                        collected_at_last = parsed.collected_at
                    if parsed.crawler_version:
                        crawler_versions.add(parsed.crawler_version)
                    if parsed.retry_count is not None:
                        max_retry_count = (
                            parsed.retry_count
                            if max_retry_count is None
                            else max(max_retry_count, parsed.retry_count)
                        )

                    raw_batch.append(parsed.bronze_document)
                    if len(raw_batch) >= self.config.batch_size:
                        flush_raw_batch()

            flush_raw_batch()
            flush_quarantine_batch()

            current_stage = "input_integrity_check"
            final_fingerprint = fingerprint_file(self.config.input_path)
            if final_fingerprint != input_fingerprint:
                raise LoaderFailure(
                    "적재 전후 입력 파일 체크섬이 달라졌습니다.",
                    error_code="CHECKSUM_MISMATCH",
                )

            success_count = inserted_count + duplicate_count
            if total_lines != success_count + failure_count + quarantine_count:
                raise LoaderFailure(
                    "Bronze 입력·성공·실패·격리 건수가 일치하지 않습니다.",
                    error_code="ROW_COUNT_MISMATCH",
                )

            status = "partial_failure" if quarantine_count else "success"
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            terminal_values = {
                "status": status,
                "dataset_id": dataset_id,
                "input_count": total_lines,
                "success_count": success_count,
                "failure_count": failure_count,
                "quarantine_count": quarantine_count,
                "inserted_count": inserted_count,
                "duplicate_count": duplicate_count,
                "input_file_sha256": input_fingerprint.sha256,
                "ended_at": self._now(),
                "duration_ms": duration_ms,
            }
            mongo_store.update_run(self.run_id, terminal_values)
            mongo_store.save_manifest(self._build_manifest(
                status=status,
                dataset_id=dataset_id,
                source_filename=source_filename,
                source_sha256=source_sha256,
                input_fingerprint=input_fingerprint,
                total_lines=total_lines,
                inserted_count=inserted_count,
                duplicate_count=duplicate_count,
                failure_count=failure_count,
                quarantine_count=quarantine_count,
                started_at=started_at,
                collected_at_first=collected_at_first,
                collected_at_last=collected_at_last,
                source_run_ids=source_run_ids,
                crawler_versions=crawler_versions,
                max_retry_count=max_retry_count,
            ))
            self.logger.emit(
                "WARN" if status == "partial_failure" else "INFO",
                "Bronze MongoDB 적재가 완료되었습니다.",
                dataset_id=dataset_id,
                status=status,
                input_count=total_lines,
                success_count=success_count,
                failure_count=failure_count,
                quarantine_count=quarantine_count,
                duration_ms=duration_ms,
                extra={
                    "inserted_count": inserted_count,
                    "duplicate_count": duplicate_count,
                },
            )
            return LoadResult(
                run_id=self.run_id,
                dataset_id=dataset_id,
                status=status,
                input_count=total_lines,
                success_count=success_count,
                failure_count=failure_count,
                quarantine_count=quarantine_count,
                inserted_count=inserted_count,
                duplicate_count=duplicate_count,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            failure_count = max(0, total_lines - inserted_count - duplicate_count - quarantine_count)
            error_code = getattr(exc, "error_code", None)
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            try:
                self.logger.error(
                    f"Bronze MongoDB 적재가 실패했습니다. failed_stage={current_stage}",
                    dataset_id=dataset_id,
                    status="failed",
                    input_count=total_lines,
                    success_count=inserted_count + duplicate_count,
                    failure_count=failure_count,
                    quarantine_count=quarantine_count,
                    duration_ms=duration_ms,
                    error_code=error_code,
                    extra={"failed_stage": current_stage},
                )
            except Exception:
                pass

            if mongo_store is not None and run_started_in_mongo:
                terminal_values = {
                    "status": "failed",
                    "dataset_id": dataset_id,
                    "input_count": total_lines,
                    "success_count": inserted_count + duplicate_count,
                    "failure_count": failure_count,
                    "quarantine_count": quarantine_count,
                    "inserted_count": inserted_count,
                    "duplicate_count": duplicate_count,
                    "failed_stage": current_stage,
                    "error_code": error_code,
                    "ended_at": self._now(),
                    "duration_ms": duration_ms,
                }
                try:
                    mongo_store.update_run(self.run_id, terminal_values)
                    mongo_store.save_manifest(self._build_manifest(
                        status="failed",
                        dataset_id=dataset_id,
                        source_filename=source_filename,
                        source_sha256=source_sha256,
                        input_fingerprint=input_fingerprint,
                        total_lines=total_lines,
                        inserted_count=inserted_count,
                        duplicate_count=duplicate_count,
                        failure_count=failure_count,
                        quarantine_count=quarantine_count,
                        started_at=started_at,
                        collected_at_first=collected_at_first,
                        collected_at_last=collected_at_last,
                        source_run_ids=source_run_ids,
                        crawler_versions=crawler_versions,
                        max_retry_count=max_retry_count,
                    ))
                except Exception:
                    pass
            if isinstance(exc, LoaderFailure):
                raise
            if isinstance(exc, (FileFingerprintError, MongoDependencyError, MongoStoreError, ChecksumConflictError)):
                raise LoaderFailure(str(exc), error_code=error_code) from exc
            raise LoaderFailure("Bronze 적재 중 예기치 않은 오류가 발생했습니다.", error_code=error_code) from exc
        finally:
            if mongo_store is not None:
                mongo_store.close()

    def _build_manifest(
        self,
        *,
        status: str,
        dataset_id: str,
        source_filename: str | None,
        source_sha256: str | None,
        input_fingerprint: FileFingerprint | None,
        total_lines: int,
        inserted_count: int,
        duplicate_count: int,
        failure_count: int,
        quarantine_count: int,
        started_at: datetime,
        collected_at_first: str | None,
        collected_at_last: str | None,
        source_run_ids: set[str],
        crawler_versions: set[str],
        max_retry_count: int | None,
    ) -> dict[str, Any]:
        return {
            "_id": self.run_id,
            "run_id": self.run_id,
            "source_name": self.config.source_name,
            "source_uri": None,
            "collected_at": collected_at_first,
            "collected_at_first": collected_at_first,
            "collected_at_last": collected_at_last,
            "ingest_date": started_at.astimezone(SEOUL).date().isoformat(),
            "raw_path": relative_path(self.config.input_path, self.config.project_root),
            "content_type": "application/jsonl",
            "file_size_bytes": input_fingerprint.size_bytes if input_fingerprint else None,
            "row_count": total_lines,
            "checksum_sha256": input_fingerprint.sha256 if input_fingerprint else None,
            "source_sha256": source_sha256,
            "source_filename": source_filename,
            "http_status": None,
            "retry_count": max_retry_count,
            "crawler_version": next(iter(crawler_versions)) if len(crawler_versions) == 1 else None,
            "dataset_id": dataset_id,
            "status": status,
            "inserted_count": inserted_count,
            "duplicate_count": duplicate_count,
            "failure_count": failure_count,
            "quarantine_count": quarantine_count,
            "source_run_ids": sorted(source_run_ids),
            "loaded_at": now_iso(),
        }
