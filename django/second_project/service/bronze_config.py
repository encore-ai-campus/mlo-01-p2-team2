"""Configuration for the Bronze JSONL to MongoDB loader."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def project_root() -> Path:
    """Return the Django project directory containing manage.py."""

    for parent in Path(__file__).resolve().parents:
        if (parent / "manage.py").is_file():
            return parent
    raise RuntimeError(
        "Django 프로젝트 루트를 찾지 못했습니다. manage.py 위치를 확인하세요."
    )


@dataclass(frozen=True)
class LoaderConfig:
    """Runtime configuration for one Bronze loading execution."""

    project_root: Path
    input_path: Path
    log_path: Path
    batch_size: int = 500
    mongo_alias: str = "mongodb"
    database: str = "second_project"
    raw_collection: str = "bronze_raw_records"
    run_collection: str = "bronze_load_runs"
    manifest_collection: str = "bronze_manifests"
    quarantine_collection: str = "bronze_quarantine"
    source_name: str = "internal_records_api"

    def resolve_paths(self) -> "LoaderConfig":
        """Resolve user-provided paths relative to the Django project root."""

        root = self.project_root.resolve()

        def resolve(path: Path) -> Path:
            candidate = path.expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            return candidate.resolve()

        return LoaderConfig(
            project_root=root,
            input_path=resolve(self.input_path),
            log_path=resolve(self.log_path),
            batch_size=self.batch_size,
            mongo_alias=self.mongo_alias,
            database=self.database,
            raw_collection=self.raw_collection,
            run_collection=self.run_collection,
            manifest_collection=self.manifest_collection,
            quarantine_collection=self.quarantine_collection,
            source_name=self.source_name,
        )

    def validate(self) -> None:
        if self.batch_size <= 0:
            raise ValueError("--batch-size는 1 이상이어야 합니다.")
        if not self.database or self.database.startswith("$"):
            raise ValueError("database 데이터베이스명이 올바르지 않습니다.")
        collection_names = {
            "raw_collection": self.raw_collection,
            "run_collection": self.run_collection,
            "manifest_collection": self.manifest_collection,
            "quarantine_collection": self.quarantine_collection,
        }
        for field_name, collection_name in collection_names.items():
            if not collection_name or collection_name.startswith("$"):
                raise ValueError(f"{field_name} 컬렉션명이 올바르지 않습니다.")


def default_config() -> LoaderConfig:
    root = project_root()
    return LoaderConfig(
        project_root=root,
        input_path=root / "data" / "raw_data" / "records.jsonl",
        log_path=root / "log_lake" / "raw_data" / "raw_data_loading_log.jsonl",
        database=os.environ.get("DASHBOARD_BRONZE_DATABASE", "second_project"),
    )

