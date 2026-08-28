"""Windows-safe size and six-hour log rotation helpers."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


KST = timezone(timedelta(hours=9))
ROTATION_MAX_BYTES = 10 * 1024 * 1024
ROTATION_BACKUP_COUNT = 5
ROTATION_INTERVAL_SECONDS = 6 * 60 * 60


class TimeAndSizeRotatingFileHandler(RotatingFileHandler):
    """Rotate at a KST six-hour boundary or when the size limit is reached."""

    def __init__(
        self,
        filename: str | Path,
        maxBytes: int = ROTATION_MAX_BYTES,
        backupCount: int = ROTATION_BACKUP_COUNT,
        *,
        interval_seconds: int = ROTATION_INTERVAL_SECONDS,
        encoding: str = "utf-8",
        delay: bool = False,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds는 1 이상이어야 합니다.")
        self.interval_seconds = interval_seconds
        super().__init__(
            filename,
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding=encoding,
            delay=delay,
        )

    def shouldRollover(self, record) -> bool:  # noqa: N802 - logging API name
        if self._time_boundary_reached():
            return True
        return super().shouldRollover(record)

    def _time_boundary_reached(self) -> bool:
        try:
            modified_at = Path(self.baseFilename).stat().st_mtime
        except FileNotFoundError:
            return False
        return modified_at < _rotation_window_start(
            time.time(),
            self.interval_seconds,
        )


class RotatingJsonlWriter:
    """Append complete JSONL records with the same rotation policy."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = ROTATION_MAX_BYTES,
        backup_count: int = ROTATION_BACKUP_COUNT,
        interval_seconds: int = ROTATION_INTERVAL_SECONDS,
    ) -> None:
        if max_bytes < 0:
            raise ValueError("max_bytes는 0 이상이어야 합니다.")
        if backup_count < 0:
            raise ValueError("backup_count는 0 이상이어야 합니다.")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds는 1 이상이어야 합니다.")
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.interval_seconds = interval_seconds
        self._lock = _path_lock(self.path)

    def write(self, line: str) -> None:
        if not line.endswith("\n"):
            line += "\n"
        encoded = line.encode("utf-8")

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self._time_boundary_reached() or self._size_limit_reached(len(encoded)):
                self._rotate()

            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def _time_boundary_reached(self) -> bool:
        try:
            modified_at = self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        return modified_at < _rotation_window_start(
            time.time(),
            self.interval_seconds,
        )

    def _size_limit_reached(self, incoming_bytes: int) -> bool:
        if self.max_bytes == 0:
            return False
        try:
            current_bytes = self.path.stat().st_size
        except FileNotFoundError:
            current_bytes = 0
        return current_bytes + incoming_bytes >= self.max_bytes

    def _rotate(self) -> None:
        if self.backup_count == 0:
            self.path.unlink(missing_ok=True)
            return

        for index in range(self.backup_count - 1, 0, -1):
            source = self._backup_path(index)
            target = self._backup_path(index + 1)
            if source.exists():
                target.unlink(missing_ok=True)
                source.replace(target)

        if self.path.exists():
            target = self._backup_path(1)
            target.unlink(missing_ok=True)
            self.path.replace(target)

    def _backup_path(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{index}")


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _rotation_window_start(epoch_seconds: float, interval_seconds: int) -> float:
    """Return the current KST-aligned rotation window start as epoch seconds."""

    now = datetime.fromtimestamp(epoch_seconds, KST)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((now - midnight).total_seconds())
    boundary = midnight + timedelta(
        seconds=(elapsed // interval_seconds) * interval_seconds,
    )
    return boundary.timestamp()


_PATH_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
