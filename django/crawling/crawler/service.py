"""End-to-end incremental crawl orchestration."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .api_client import ApiClient
from .config import CrawlConfig
from .key_store import EnvApiKeyStore
from .storage import (
    AlreadyRunningError,
    CrawlState,
    JsonlStore,
    RunLock,
    StateConsistencyError,
    StateStore,
)
from .validator import ValidationError, validate_meta, validate_records_page


LOGGER = logging.getLogger(__name__)
SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class CrawlResult:
    pages: int
    fetched_records: int
    appended_records: int
    duplicate_records: int
    released_rows: int
    status: str


def _latest_release(current: str | None, items: list[dict[str, Any]]) -> str | None:
    candidates = [current] if current else []
    candidates.extend(str(item["scheduled_release_at"]) for item in items)
    if not candidates:
        return None
    return max(candidates, key=datetime.fromisoformat)


def should_refresh_api_key(now: datetime) -> bool:
    """Refresh the daily key only during 00:01:00-00:01:59 KST."""

    if now.tzinfo is None or now.utcoffset() is None:
        now = now.replace(tzinfo=SEOUL)
    local_time = now.astimezone(SEOUL)
    return local_time.hour == 0 and local_time.minute == 1


def _build_document(
    item: dict[str, Any],
    *,
    meta: dict[str, Any],
    api_server_time: str,
    run_id: str,
) -> dict[str, Any]:
    return {
        "dataset_id": meta["dataset_id"],
        "source_filename": meta["source_filename"],
        "source_sha256": meta["source_sha256"],
        **item,
        "_crawl": {
            "run_id": run_id,
            "collected_at": datetime.now(SEOUL).isoformat(timespec="microseconds"),
            "api_server_time": api_server_time,
        },
    }


def run_crawl(
    config: CrawlConfig,
    *,
    api_client: ApiClient | None = None,
    sleeper: Any = time.sleep,
    now_provider: Any = lambda: datetime.now(SEOUL),
    run_id: str | None = None,
) -> CrawlResult:
    """Run one safe crawl, append raw documents, then advance checkpoint."""

    started_monotonic = time.monotonic()
    effective_run_id = run_id or str(uuid4())
    records_store = JsonlStore(config.records_path)
    state_store = StateStore(config.state_path)
    key_store = EnvApiKeyStore(config.api_key_env_path)
    client = api_client or ApiClient(config)
    owns_client = api_client is None
    client.configure_key_persistence(key_store.save)
    dataset_id = ""
    fetched_count = 0
    appended_count = 0
    duplicate_count = 0

    try:
        with RunLock(config.lock_path):
            records_exists = records_store.exists()
            state_exists = state_store.exists()
            if records_exists and not state_exists and config.records_path.stat().st_size > 0:
                raise StateConsistencyError(
                    "데이터가 있는 records.jsonl은 있지만 crawl_state.json이 없습니다. "
                    "자동으로 이어받지 않습니다."
                )
            if state_exists and not records_exists:
                raise StateConsistencyError(
                    "crawl_state.json은 있지만 records.jsonl이 없습니다. 자동으로 이어받지 않습니다."
                )

            state = state_store.load()
            existing_keys = records_store.scan_record_keys()

            LOGGER.info("서버 준비 상태를 확인합니다.")
            client.check_ready()
            stored_api_key = key_store.load()
            refresh_due = should_refresh_api_key(now_provider())
            if stored_api_key is None or refresh_due:
                key_metadata = client.refresh_key()
                reason = "missing_env_key" if stored_api_key is None else "daily_0001_refresh"
                LOGGER.info(
                    "API 키를 발급받아 .env에 저장했습니다. reason=%s service_date=%s",
                    reason,
                    key_metadata["service_date"],
                )
            else:
                client.use_api_key(stored_api_key)
                LOGGER.info(".env에 저장된 API 키를 메모리에 불러왔습니다.")

            meta = client.fetch_meta()
            payload_columns = validate_meta(meta)
            dataset_id = meta["dataset_id"]
            source_sha256 = meta["source_sha256"]
            LOGGER.info(
                "메타데이터를 확인했습니다. dataset_id=%s released_rows=%s total_rows=%s",
                dataset_id,
                meta["released_rows"],
                meta["total_rows"],
                extra={"dataset_id": dataset_id},
            )

            if state is None:
                records_store.ensure_file()
                bootstrap_time = datetime.now(SEOUL).isoformat(timespec="microseconds")
                state = CrawlState(
                    schema_version=1,
                    dataset_id=dataset_id,
                    source_sha256=source_sha256,
                    checkpoint=None,
                    last_record_id=None,
                    released_rows=0,
                    latest_scheduled_release_at=None,
                    last_server_time=meta["server_time"],
                    updated_at=bootstrap_time,
                )
                state_store.save(state)
                LOGGER.info("최초 수집을 위한 초기 상태를 생성했습니다.")

            if state.dataset_id != dataset_id:
                raise StateConsistencyError(
                    "API dataset_id가 기존 상태와 달라 기존 JSONL에 섞지 않습니다."
                )
            if state.source_sha256 != source_sha256:
                raise StateConsistencyError(
                    "API source_sha256이 기존 상태와 달라 기존 JSONL에 섞지 않습니다."
                )
            cursor: str | None = state.checkpoint
            previous_released_rows = state.released_rows
            latest_scheduled_release_at = state.latest_scheduled_release_at
            last_record_id = state.last_record_id

            seen_cursors = {cursor} if cursor else set()
            page_count = 0
            final_checkpoint: str | None = None
            final_released_rows = previous_released_rows
            final_server_time = meta["server_time"]

            while True:
                page = client.fetch_records(cursor)
                validate_records_page(
                    page,
                    expected_dataset_id=dataset_id,
                    expected_payload_columns=payload_columns,
                )
                page_count += 1
                fetched_count += page["count"]
                final_checkpoint = page["checkpoint"]
                final_released_rows = page["released_rows"]
                final_server_time = page["server_time"]
                latest_scheduled_release_at = _latest_release(
                    latest_scheduled_release_at,
                    page["items"],
                )

                new_documents: list[dict[str, Any]] = []
                for item in page["items"]:
                    record_key = dataset_id, item["record_id"]
                    if record_key in existing_keys:
                        duplicate_count += 1
                        continue
                    existing_keys.add(record_key)
                    last_record_id = (
                        item["record_id"]
                        if last_record_id is None
                        else max(last_record_id, item["record_id"])
                    )
                    new_documents.append(
                        _build_document(
                            item,
                            meta=meta,
                            api_server_time=page["server_time"],
                            run_id=effective_run_id,
                        )
                    )

                appended_count += records_store.append_documents(new_documents)
                LOGGER.info(
                    "페이지를 처리했습니다. page=%s count=%s appended=%s has_more=%s",
                    page_count,
                    page["count"],
                    len(new_documents),
                    page["has_more"],
                    extra={
                        "dataset_id": dataset_id,
                        "input_count": page["count"],
                        "success_count": page["count"],
                    },
                )

                if not page["has_more"]:
                    break
                next_cursor = page["next_cursor"]
                if next_cursor in seen_cursors:
                    raise ValidationError("동일한 next_cursor가 반복되어 수집을 중단합니다.")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                sleeper(config.page_delay_seconds)

            if final_checkpoint is None:
                raise ValidationError("최종 checkpoint를 확인하지 못했습니다.")
            expected_count = final_released_rows - previous_released_rows
            if expected_count < 0:
                raise ValidationError(
                    "API released_rows가 이전 실행보다 감소해 checkpoint를 갱신하지 않습니다."
                )
            if fetched_count != expected_count:
                raise ValidationError(
                    "checkpoint 이후 예상 건수와 실제 응답 건수가 다릅니다. "
                    f"expected={expected_count} fetched={fetched_count}"
                )

            now = datetime.now(SEOUL).isoformat(timespec="microseconds")
            state_store.save(
                CrawlState(
                    schema_version=1,
                    dataset_id=dataset_id,
                    source_sha256=source_sha256,
                    checkpoint=final_checkpoint,
                    last_record_id=last_record_id,
                    released_rows=final_released_rows,
                    latest_scheduled_release_at=latest_scheduled_release_at,
                    last_server_time=final_server_time,
                    updated_at=now,
                )
            )
            if duplicate_count:
                LOGGER.info(
                    "이미 저장된 레코드를 중복 추가하지 않았습니다. duplicates=%s",
                    duplicate_count,
                    extra={"dataset_id": dataset_id},
                )
            status = "no_new_data" if appended_count == 0 else "completed"
            LOGGER.info(
                "크롤링을 완료했습니다. pages=%s fetched=%s appended=%s "
                "duplicates=%s released_rows=%s result=%s",
                page_count,
                fetched_count,
                appended_count,
                duplicate_count,
                final_released_rows,
                status,
                extra={
                    "dataset_id": dataset_id,
                    "status": "success",
                    "input_count": fetched_count,
                    "success_count": fetched_count,
                    "failure_count": 0,
                    "quarantine_count": 0,
                    "duration_ms": int(
                        (time.monotonic() - started_monotonic) * 1000
                    ),
                },
            )
            return CrawlResult(
                pages=page_count,
                fetched_records=fetched_count,
                appended_records=appended_count,
                duplicate_records=duplicate_count,
                released_rows=final_released_rows,
                status=status,
            )
    except Exception as exc:
        if not isinstance(exc, AlreadyRunningError):
            success_count = appended_count + duplicate_count
            LOGGER.error(
                "크롤링 실행이 완료되지 않았습니다. error_type=%s",
                type(exc).__name__,
                extra={
                    "dataset_id": dataset_id,
                    "status": "partial_failure" if success_count else "failed",
                    "input_count": fetched_count,
                    "success_count": success_count,
                    "failure_count": max(0, fetched_count - success_count),
                    "quarantine_count": 0,
                    "duration_ms": int(
                        (time.monotonic() - started_monotonic) * 1000
                    ),
                },
            )
        raise
    finally:
        if owns_client:
            client.close()
