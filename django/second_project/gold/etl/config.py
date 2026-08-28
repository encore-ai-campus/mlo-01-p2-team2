from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings


@dataclass(frozen=True)
class GoldContract:
    dataset_name: str
    config_version: str
    source_alias: str
    target_alias: str
    require_inactive_target: bool
    strict_top_area_integrity: bool
    release_root: Path
    artifact_names: dict[str, str]
    physical_tables: dict[str, str]


def default_contract_path() -> Path:
    return Path(__file__).resolve().parent.parent / "contracts" / "gold_hr_v1.yaml"


def load_contract(path: str | Path | None = None) -> GoldContract:
    contract_path = Path(path) if path else default_contract_path()
    with contract_path.open(encoding="utf-8") as stream:
        raw: dict[str, Any] = yaml.safe_load(stream) or {}

    artifacts = raw.get("artifacts", {})
    required_artifacts = {"manifest", "lineage", "dataset_card", "catalog", "quality", "validation"}
    missing = required_artifacts.difference(artifacts)
    if missing:
        raise ValueError(f"Contract artifact names are missing: {sorted(missing)}")

    return GoldContract(
        dataset_name=str(raw["dataset_name"]),
        config_version=str(raw["config_version"]),
        source_alias=str(raw.get("source_alias", "default")),
        target_alias=str(raw.get("target_alias", settings.GOLD_DATABASE_ALIAS)),
        require_inactive_target=bool(raw.get("quality", {}).get("require_inactive_target", True)),
        strict_top_area_integrity=bool(raw.get("quality", {}).get("strict_top_area_integrity", True)),
        release_root=Path(raw.get("release_root") or settings.GOLD_RELEASE_ROOT),
        artifact_names={str(key): str(value) for key, value in artifacts.items()},
        physical_tables={str(key): str(value) for key, value in raw.get("physical_tables", {}).items()},
    )
