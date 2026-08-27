"""JSON Lines logging required for the ingest pipeline stage."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")
URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/][^\s]+")
UNIX_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s/]+/)+[^\s]*")
API_KEY_PATTERN = re.compile(r"brz_\d{8}_[A-Za-z0-9_-]+")


def new_run_id() -> str:
    return str(uuid.uuid4())


def _safe_message(message: str) -> str:
    result = API_KEY_PATTERN.sub("<credential>", message)
    result = URL_PATTERN.sub("<private-endpoint>", result)
    result = WINDOWS_PATH_PATTERN.sub("<internal-path>", result)
    return UNIX_PATH_PATTERN.sub("<internal-path>", result)


class IngestJsonFormatter(logging.Formatter):
    """Emit all LOGGING_RULES common fields in one JSON object per line."""

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        level = "WARN" if record.levelname == "WARNING" else record.levelname
        default_status = (
            "failed"
            if record.levelno >= logging.ERROR
            else "partial_failure"
            if record.levelno >= logging.WARNING
            else "success"
        )
        event: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, SEOUL).isoformat(
                timespec="milliseconds"
            ),
            "level": level,
            "run_id": self.run_id,
            "stage": "ingest",
            "dataset_id": getattr(record, "dataset_id", ""),
            "status": getattr(record, "status", default_status),
            "input_count": int(getattr(record, "input_count", 0)),
            "success_count": int(getattr(record, "success_count", 0)),
            "failure_count": int(getattr(record, "failure_count", 0)),
            "quarantine_count": int(getattr(record, "quarantine_count", 0)),
            "duration_ms": int(getattr(record, "duration_ms", 0)),
            "message": _safe_message(record.getMessage()),
        }
        if hasattr(record, "error_code"):
            event["error_code"] = record.error_code
        return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def configure_ingest_logging(level_name: str, path: Path, run_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)
    formatter = IngestJsonFormatter(run_id)

    file_handler = logging.FileHandler(path, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)
    root_logger.setLevel(level_name)
