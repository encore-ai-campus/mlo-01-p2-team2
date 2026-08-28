from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .log_rotation import TimeAndSizeRotatingFileHandler


_KST = timezone(timedelta(hours=9))


def create_stage_loggers(
    directory: str | Path,
    level: int,
) -> tuple[logging.Logger, logging.Logger]:
    """표준화·검증용 로그와 JSONL 감사 로그를 함께 만든다.

    ``directory``는 호출자가 정한다. 따라서 Django 연동 환경에서는
    ``../django/log_lake/standardized`` 같은 경로를 설정 파일에 지정할 수 있고, 실행 시
    해당 폴더가 자동으로 생성된다. 파일은 KST 6시간 경계 또는 10MiB 초과 시
    회전하며, 백업 파일 5개를 유지한다.

    """

    log_directory = Path(directory)
    quarantine_path = log_directory / "quarantine.jsonl"
    standardize_logger = _create_file_logger(
        "standardize",
        log_directory / "standardize.log",
        level,
        json_path=log_directory / "pipeline.jsonl",
        quarantine_path=quarantine_path,
        restoration_path=log_directory / "restoration.jsonl",
    )
    quarantine_handler = next(
        (
            handler
            for handler in standardize_logger.handlers
            if Path(getattr(handler, "baseFilename", "")).resolve()
            == quarantine_path.resolve()
        ),
        None,
    )
    validation_logger = _create_file_logger(
        "validation",
        log_directory / "validation.log",
        level,
        json_path=log_directory / "quality.jsonl",
        quarantine_handler=quarantine_handler,
    )
    return standardize_logger, validation_logger


def _create_file_logger(
    name: str,
    path: Path,
    level: int,
    *,
    json_path: Path | None = None,
    quarantine_path: Path | None = None,
    restoration_path: Path | None = None,
    quarantine_handler: logging.Handler | None = None,
) -> logging.Logger:
    """text 호환 로그와 회전 가능한 JSONL 감사 로그를 지정한 파일에 누적한다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"mongo_pipeline.{name}")
    logger.setLevel(level)
    logger.propagate = False

    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    handler = TimeAndSizeRotatingFileHandler(
        path,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)

    if json_path is not None:
        json_handler = TimeAndSizeRotatingFileHandler(
            json_path,
            encoding="utf-8",
        )
        json_handler.addFilter(_ExcludeChannelFilter({"quarantine", "restoration"}))
        json_handler.setFormatter(_JsonLineFormatter())
        logger.addHandler(json_handler)

    if quarantine_handler is not None:
        logger.addHandler(quarantine_handler)
    elif quarantine_path is not None:
        quarantine_handler = TimeAndSizeRotatingFileHandler(
            quarantine_path,
            encoding="utf-8",
        )
        quarantine_handler.addFilter(_ChannelFilter("quarantine"))
        quarantine_handler.setFormatter(_JsonLineFormatter())
        logger.addHandler(quarantine_handler)

    if restoration_path is not None:
        restoration_handler = TimeAndSizeRotatingFileHandler(
            restoration_path,
            encoding="utf-8",
        )
        restoration_handler.addFilter(_ChannelFilter("restoration"))
        restoration_handler.setFormatter(_JsonLineFormatter())
        logger.addHandler(restoration_handler)

    return logger


class _ChannelFilter(logging.Filter):
    """특정 감사 채널의 레코드만 통과시키는 필터다."""

    def __init__(self, channel: str | set[str]) -> None:
        super().__init__()
        self._channel = {channel} if isinstance(channel, str) else set(channel)

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "audit_channel", None) in self._channel


class _ExcludeChannelFilter(logging.Filter):
    """특정 감사 채널을 별도 파일로 분리하기 위한 필터다."""

    def __init__(self, channel: str | set[str]) -> None:
        super().__init__()
        self._channel = {channel} if isinstance(channel, str) else set(channel)

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "audit_channel", None) not in self._channel


class _JsonLineFormatter(logging.Formatter):
    """로그 레코드를 개인정보·원문 없이 JSON 한 줄로 직렬화한다."""

    _DEFAULTS: dict[str, Any] = {
        "run_id": "unknown",
        "stage": "pipeline",
        "dataset_id": "unknown",
        "status": "success",
        "input_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "quarantine_count": 0,
        "duration_ms": 0,
        "message": "",
    }

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "json_event", None)
        payload = dict(self._DEFAULTS)
        payload.update(event if isinstance(event, dict) else {})
        payload.setdefault("timestamp", _now_kst())
        payload.setdefault("level", _log_level(record.levelname))
        if not payload.get("message"):
            payload["message"] = record.getMessage()
        # 이벤트에 timestamp/level이 없거나 null로 들어온 경우에도 필수값을 채운다.
        payload["timestamp"] = payload.get("timestamp") or _now_kst()
        payload["level"] = _log_level(str(payload.get("level") or record.levelname))
        payload["status"] = _log_status(str(payload.get("status") or "success"))
        return json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str)


def _now_kst() -> str:
    return datetime.now(_KST).isoformat(timespec="milliseconds")


def _log_level(value: str) -> str:
    return {
        "WARNING": "WARN",
        "CRITICAL": "ERROR",
    }.get(value.upper(), value.upper())


def _log_status(value: str) -> str:
    return {
        "SUCCESS": "success",
        "PARTIAL_SUCCESS": "partial_failure",
        "PARTIAL_FAILURE": "partial_failure",
        "FAILED": "failed",
        "QUARANTINED": "failed",
    }.get(value.upper(), value.lower())
