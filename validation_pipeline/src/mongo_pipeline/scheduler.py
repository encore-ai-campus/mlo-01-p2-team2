from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .backup import DjangoMongoDataLakeBackup
from .config import AppConfig, SourceConfig


class PipelineScheduler:
    """3분 증분 처리와 1시간 DATA-LAKE backup을 한 프로세스에서 조정한다."""

    def __init__(
        self,
        config: AppConfig,
        *,
        logger: logging.Logger | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """애플리케이션 설정과 테스트 가능한 sleep 함수를 저장한다."""

        self._config = config
        self._schedule = config.schedule
        self._logger = logger or logging.getLogger(__name__)
        self._sleep = sleep

    def run_forever(self, *, max_ticks: int | None = None) -> None:
        """설정된 주기로 계속 실행한다. 운영에서는 프로세스 supervisor와 함께 사용한다."""

        state = _StateStore(self._schedule.watermark_path)
        next_run = datetime.now(timezone.utc)
        if not self._schedule.run_immediately:
            next_run += timedelta(seconds=self._schedule.interval_seconds)
        tick_count = 0

        while max_ticks is None or tick_count < max_ticks:
            now = datetime.now(timezone.utc)
            wait_seconds = (next_run - now).total_seconds()
            if wait_seconds > 0:
                self._sleep(min(wait_seconds, 60.0))
                continue

            try:
                self.run_tick_locked(state)
            except Exception:
                self._logger.exception("event=scheduler_tick_failed")

            tick_count += 1
            next_run += timedelta(seconds=self._schedule.interval_seconds)
            if next_run <= datetime.now(timezone.utc):
                next_run = datetime.now(timezone.utc) + timedelta(
                    seconds=self._schedule.interval_seconds
                )

    def run_tick_locked(self, state: "_StateStore | None" = None) -> dict[str, Any]:
        """파일 잠금을 획득한 뒤 한 tick을 실행한다."""

        lock = _FileLock(self._schedule.lock_path)
        if not lock.acquire():
            self._logger.warning(
                "event=scheduler_tick_skipped reason=lock_exists path=%s",
                self._schedule.lock_path,
            )
            return {
                "status": "SKIPPED",
                "reason": "lock_exists",
                "lock_path": str(self._schedule.lock_path),
            }
        try:
            return self.run_tick(state)
        finally:
            lock.release()

    def run_tick(self, state: "_StateStore | None" = None) -> dict[str, Any]:
        """지연 cutoff 기준으로 원본·재처리·백업을 한 번 실행한다."""

        state = state or _StateStore(self._schedule.watermark_path)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self._schedule.delay_seconds)
        result: dict[str, Any] = {
            "status": "SUCCESS",
            "started_at": _iso_utc(now),
            "cutoff": _iso_utc(cutoff),
            "primary": None,
            "reprocess": None,
            "backup": None,
        }

        source_config = _incremental_source_config(
            self._config.source,
            watermark_field=self._schedule.watermark_field,
            cutoff=cutoff,
            watermark=_parse_timestamp(state.get("watermark")),
        )
        from .cli import run_config_once

        primary = run_config_once(self._config, source_config=source_config)
        result["primary"] = primary.report
        if primary.report.get("status") == "FAILED":
            result["status"] = "FAILED"
        if primary.report.get("status") != "FAILED":
            state.set("watermark", _iso_utc(cutoff))
            state.set("last_primary_run_id", primary.report.get("run_id"))

        if self._config.reprocess.enabled and self._schedule.reprocess_on_tick:
            reprocess_result = run_config_once(self._config, reprocess=True)
            result["reprocess"] = reprocess_result.report
            if reprocess_result.report.get("status") == "FAILED":
                result["status"] = "FAILED"

        if self._config.data_lake.enabled and _backup_due(
            state.get("last_backup_at"),
            now,
            self._config.data_lake.interval_minutes,
        ):
            backup = DjangoMongoDataLakeBackup(
                self._config.data_lake,
                sink_config=self._config.sink,
            )
            try:
                result["backup"] = backup.run(now=now)
                state.set("last_backup_at", _iso_utc(now))
            finally:
                backup.close()

        state.save()
        result["finished_at"] = _iso_utc(datetime.now(timezone.utc))
        return result


def _incremental_source_config(
    source: SourceConfig,
    *,
    watermark_field: str,
    cutoff: datetime,
    watermark: datetime | None,
) -> SourceConfig:
    """source 설정에 `watermark < field <= cutoff` 조건을 추가한다."""

    source_kind = source.kind.lower().replace("-", "_")
    if source_kind not in {"mongodb", "django_mongodb"}:
        raise ValueError("스케줄 실행은 MongoDB 또는 django_mongodb source만 지원합니다.")

    bounds: dict[str, Any] = {"$lte": cutoff}
    if watermark is not None:
        bounds["$gt"] = watermark
    window = {watermark_field: bounds}

    if source.aggregation is not None:
        return replace(source, aggregation=[{"$match": window}, *source.aggregation])
    if source.query:
        query = {"$and": [dict(source.query), window]}
    else:
        query = window
    return replace(source, query=query)


def _backup_due(
    last_backup_value: Any,
    now: datetime,
    interval_minutes: int,
) -> bool:
    last = _parse_timestamp(last_backup_value)
    return last is None or now - last >= timedelta(minutes=interval_minutes)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class _StateStore:
    """로컬 JSON state를 원자적으로 저장한다."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._values: dict[str, Any] = {}
        try:
            with self._path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
            if isinstance(loaded, dict):
                self._values = loaded
        except (OSError, json.JSONDecodeError):
            self._values = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_name(f".{self._path.name}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(self._values, file, ensure_ascii=False, indent=2)
                file.write("\n")
            temp_path.replace(self._path)
        finally:
            temp_path.unlink(missing_ok=True)


class _FileLock:
    """동일한 scheduler tick의 중복 실행을 막는 단순 파일 잠금."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._file: Any | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._file = self._path.open("x", encoding="utf-8")
            self._file.write(str(datetime.now(timezone.utc).isoformat()))
            self._file.flush()
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
            self._path.unlink(missing_ok=True)
