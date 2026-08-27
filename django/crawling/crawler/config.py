"""Crawler configuration and project-relative paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_BASE_URL = "http://192.168.0.51:8000"


@dataclass(frozen=True)
class CrawlConfig:
    """Runtime settings shared by all crawler modules."""

    base_url: str
    data_dir: Path
    env_path: Path | None = None
    page_limit: int = 1000
    page_delay_seconds: float = 0.5
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0
    max_retries: int = 3
    user_agent: str = "encore-2nd-project-crawler/1.0"

    @property
    def records_path(self) -> Path:
        return self.data_dir / "records.jsonl"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def api_key_env_path(self) -> Path:
        return self.env_path or self.data_dir.parent / ".env"

    @property
    def pipeline_log_path(self) -> Path:
        return project_root() / "logs" / "raw_data_log.jsonl"

    @property
    def state_path(self) -> Path:
        return self.state_dir / "crawl_state.json"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "crawler.lock"

    @property
    def timeout(self) -> tuple[float, float]:
        return self.connect_timeout_seconds, self.read_timeout_seconds


def project_root() -> Path:
    """Return the project root for both the legacy and Django layouts.

    The crawler originally lived below ``src/``.  It now lives below the
    Django project directory, next to ``manage.py``.  Supporting both layouts
    keeps the standalone entry point usable while allowing Django management
    commands and cron to resolve paths independently of the current directory.
    """

    for parent in Path(__file__).resolve().parents:
        if parent.name.casefold() == "src":
            return parent.parent
        if (parent / "manage.py").is_file():
            return parent
    raise RuntimeError(
        "크롤러 프로젝트 루트를 찾지 못했습니다. manage.py 또는 src 디렉터리를 확인하세요."
    )


def default_config() -> CrawlConfig:
    """Build the default cross-platform configuration."""

    return CrawlConfig(
        base_url=DEFAULT_BASE_URL,
        data_dir=project_root() / "data" / "raw_data",
        env_path=project_root() / ".env",
    )
