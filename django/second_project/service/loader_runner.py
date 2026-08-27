"""Public runner used by Django commands and the legacy script wrapper."""

from __future__ import annotations

from uuid import uuid4

from second_project.service.bronze_config import LoaderConfig
from second_project.service.bronze_loader import BronzeLoader, LoadResult
from second_project.service.structured_logging import StructuredLogWriter


def run_loader(
    config: LoaderConfig,
    *,
    expected_dataset_id: str | None = None,
    echo: bool = True,
    run_id: str | None = None,
) -> LoadResult:
    """Run one Bronze load with one UUID shared by every log and collection."""

    resolved_config = config.resolve_paths()
    resolved_config.validate()
    effective_run_id = run_id or str(uuid4())
    logger = StructuredLogWriter(
        resolved_config.log_path,
        effective_run_id,
        stage="bronze",
        echo=echo,
    )
    logger.info(
        "Bronze 적재 실행을 시작합니다.",
        dataset_id=expected_dataset_id or "UNKNOWN",
    )
    return BronzeLoader(
        resolved_config,
        run_id=effective_run_id,
        logger=logger,
        expected_dataset_id=expected_dataset_id,
    ).run()
