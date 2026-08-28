"""SQLite Silver 데이터를 Gold release package로 만드는 파이프라인."""

from .pipeline import GoldPipeline, GoldRunResult, run_gold_pipeline
from .canonical_contract import CanonicalRuleCatalog
from .rdb_source import (
    DEFAULT_SILVER_TABLES,
    SQLiteGoldSource,
    SourceSchemaError,
    TableSpec,
)

__all__ = [
    "DEFAULT_SILVER_TABLES",
    "CanonicalRuleCatalog",
    "GoldPipeline",
    "GoldRunResult",
    "SQLiteGoldSource",
    "SourceSchemaError",
    "TableSpec",
    "run_gold_pipeline",
]
