from __future__ import annotations

import logging
from pathlib import Path


def create_stage_loggers(
    directory: str | Path,
    level: int,
) -> tuple[logging.Logger, logging.Logger]:
    """표준화와 검증 결과를 서로 다른 파일에 기록할 로거를 만든다."""

    log_directory = Path(directory)
    return (
        _create_file_logger("standardize", log_directory / "standardize.log", level),
        _create_file_logger("validation", log_directory / "validation.log", level),
    )


def _create_file_logger(name: str, path: Path, level: int) -> logging.Logger:
    """지정한 파일에 예시 형식으로 한 줄씩 누적하는 로거를 만든다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"mongo_pipeline.{name}")
    logger.setLevel(level)
    logger.propagate = False

    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    return logger
