"""Windows-safe size and six-hour log rotation helpers."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


KST = timezone(timedelta(hours=9))
ROTATION_MAX_BYTES = 10 * 1024 * 1024
ROTATION_BACKUP_COUNT = 5
ROTATION_INTERVAL_SECONDS = 6 * 60 * 60


class TimeAndSizeRotatingFileHandler(RotatingFileHandler):
    """Rotate at a KST six-hour boundary or when the size limit is reached.

    The active file is rotated on the first record written after a boundary.
    The existing ``.1`` ... ``.5`` backup naming and size policy are retained.
    """

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


def _rotation_window_start(epoch_seconds: float, interval_seconds: int) -> float:
    """Return the current KST-aligned rotation window start as epoch seconds."""

    now = datetime.fromtimestamp(epoch_seconds, KST)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((now - midnight).total_seconds())
    boundary = midnight + timedelta(
        seconds=(elapsed // interval_seconds) * interval_seconds,
    )
    return boundary.timestamp()
