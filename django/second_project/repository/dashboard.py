"""Read-only queries for the validation pipeline dashboard."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from django.conf import settings
from django.db import connections


class DashboardConnectionError(RuntimeError):
    """Raised when the configured Django MongoDB connection is unavailable."""


class DashboardConfig:
    """MongoDB database and collection targets used by the dashboard."""

    def __init__(
        self,
        *,
        database_alias: str,
        success_database: str,
        success_collection: str,
        failure_database: str,
        failure_collection: str,
        report_database: str,
        report_collection: str,
        bronze_database: str,
        bronze_collection: str,
        silver_database: str,
        silver_collections: Mapping[str, str],
    ) -> None:
        self.database_alias = database_alias
        self.success_database = success_database
        self.success_collection = success_collection
        self.failure_database = failure_database
        self.failure_collection = failure_collection
        self.report_database = report_database
        self.report_collection = report_collection
        self.bronze_database = bronze_database
        self.bronze_collection = bronze_collection
        self.silver_database = silver_database
        self.silver_collections = dict(silver_collections)

    @classmethod
    def from_settings(cls) -> "DashboardConfig":
        success_database = _setting_text(
            "DASHBOARD_SUCCESS_DATABASE",
            "encore_success_experiment",
        )
        return cls(
            database_alias=_setting_text("DASHBOARD_MONGO_ALIAS", "mongodb"),
            success_database=success_database,
            success_collection=_setting_text(
                "DASHBOARD_SUCCESS_COLLECTION",
                "records",
            ),
            failure_database=_setting_text(
                "DASHBOARD_FAILURE_DATABASE",
                "encore_failure_experiment",
            ),
            failure_collection=_setting_text(
                "DASHBOARD_FAILURE_COLLECTION",
                "records",
            ),
            report_database=_setting_text(
                "DASHBOARD_REPORT_DATABASE",
                success_database,
            ),
            report_collection=_setting_text(
                "DASHBOARD_REPORT_COLLECTION",
                "pipeline_runs",
            ),
            bronze_database=_setting_text(
                "DASHBOARD_BRONZE_DATABASE",
                "second_project",
            ),
            bronze_collection=_setting_text(
                "DASHBOARD_BRONZE_COLLECTION",
                "bronze_raw_records",
            ),
            silver_database=_setting_text(
                "DASHBOARD_SILVER_DATABASE",
                success_database,
            ),
            silver_collections=_setting_mapping(
                "DASHBOARD_SILVER_COLLECTIONS",
                {
                    "silver_employee": "silver_employee",
                    "silver_area": "silver_area",
                    "silver_parent_area": "silver_parent_area",
                    "silver_top_area_detail": "silver_top_area_detail",
                },
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return safe display metadata without a URI or credential."""

        return {
            "alias": self.database_alias,
            "success": f"{self.success_database}.{self.success_collection}",
            "failure": f"{self.failure_database}.{self.failure_collection}",
            "reports": f"{self.report_database}.{self.report_collection}",
            "bronze": f"{self.bronze_database}.{self.bronze_collection}",
            "silver": self.silver_database,
        }


class DashboardRepository:
    """Query dashboard data through Django's existing MongoDB client."""

    def __init__(self, config: DashboardConfig) -> None:
        self.config = config
        try:
            self.connection = connections[config.database_alias]
            self.connection.ensure_connection()
            self.client = getattr(self.connection, "connection", None)
        except Exception as error:  # pragma: no cover - backend-specific
            raise DashboardConnectionError(
                f"Django MongoDB alias `{config.database_alias}`에 연결할 수 없습니다."
            ) from error
        if self.client is None:
            raise DashboardConnectionError(
                f"Django MongoDB alias `{config.database_alias}`가 MongoClient를 열지 못했습니다."
            )

    def snapshot(self) -> dict[str, Any]:
        """Return summary cards, recent runs, and safe failure metadata."""

        success = self._collection(
            self.config.success_database,
            self.config.success_collection,
        )
        failure = self._collection(
            self.config.failure_database,
            self.config.failure_collection,
        )
        reports = self._collection(
            self.config.report_database,
            self.config.report_collection,
        )
        bronze = self._collection(
            self.config.bronze_database,
            self.config.bronze_collection,
        )
        recent_runs = self._recent_runs(reports)
        latest_run = recent_runs[0] if recent_runs else None
        published_query = _published_run_query(latest_run)

        silver_models = [
            {
                "name": model_name,
                "label": SILVER_LABELS.get(model_name, model_name),
                "collection": collection_name,
                "count": _count(collection, published_query),
            }
            for model_name, collection_name in self.config.silver_collections.items()
            for collection in [
                self._collection(self.config.silver_database, collection_name)
            ]
        ]
        success_count = _count(success, published_query)
        failure_count = _count(failure, published_query)
        summary = {
            "success_count": success_count,
            "failure_count": failure_count,
            "restoration": _success_failure_rates(success_count, failure_count),
            "run_count": _count(reports),
            "bronze_count": _count(bronze),
            "silver_total": sum(model["count"] for model in silver_models),
        }
        return {
            "summary": summary,
            "silver_models": silver_models,
            "error_breakdown": self._error_breakdown(failure, published_query),
            "recent_runs": recent_runs,
            "recent_failures": self._recent_failures(failure, published_query),
            "latest_run": latest_run,
            "version": _version_from_run(latest_run),
        }

    def latest_version(self) -> dict[str, Any]:
        """Return a cheap publication marker for the latest completed run."""

        reports = self._collection(
            self.config.report_database,
            self.config.report_collection,
        )
        projection = {
            "_id": 1,
            "run_id": 1,
            "status": 1,
            "finished_at": 1,
        }
        cursor = reports.find({}, projection).sort(
            [("finished_at", -1), ("started_at", -1), ("_id", -1)]
        ).limit(1)
        document = next(iter(cursor), None)
        if document is None:
            return _version_from_run(None)
        return _version_from_document(document)

    def _collection(self, database: str, collection: str) -> Any:
        return self.client[database][collection]

    @staticmethod
    def _error_breakdown(
        collection: Any,
        query: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        pipeline = []
        if query:
            pipeline.append({"$match": dict(query)})
        pipeline.extend(
            [
                {
                    "$project": {
                        "error_code": {"$ifNull": ["$error_code", "UNKNOWN"]},
                    }
                },
                {"$group": {"_id": "$error_code", "count": {"$sum": 1}}},
                {"$sort": {"count": -1, "_id": 1}},
                {"$limit": 8},
            ]
        )
        return [
            {"error_code": str(item.get("_id", "UNKNOWN")), "count": int(item["count"])}
            for item in collection.aggregate(pipeline)
        ]

    @staticmethod
    def _recent_runs(collection: Any) -> list[dict[str, Any]]:
        projection = {
            "_id": 1,
            "run_id": 1,
            "status": 1,
            "started_at": 1,
            "finished_at": 1,
            "counts": 1,
            "restoration": 1,
        }
        runs: list[dict[str, Any]] = []
        cursor = collection.find({}, projection).sort(
            [("finished_at", -1), ("started_at", -1), ("_id", -1)]
        ).limit(10)
        for document in cursor:
            counts = document.get("counts")
            counts = counts if isinstance(counts, Mapping) else {}
            restoration = document.get("restoration")
            restoration = restoration if isinstance(restoration, Mapping) else {}
            runs.append(
                {
                    "run_id": str(document.get("run_id") or document.get("_id") or "-"),
                    "status": str(document.get("status") or "UNKNOWN").upper(),
                    "started_at": _display_value(document.get("started_at")),
                    "finished_at": _display_value(document.get("finished_at")),
                    "extracted": _display_value(
                        counts.get("extracted", document.get("input_count"))
                    ),
                    "accepted": _display_value(
                        counts.get("accepted", document.get("success_count"))
                    ),
                    "rejected": _display_value(
                        counts.get("rejected", document.get("quarantine_count"))
                    ),
                    "restoration_rate": _display_rate(
                        restoration.get("restoration_rate")
                    ),
                }
            )
        return runs

    @staticmethod
    def _recent_failures(
        collection: Any,
        query: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        projection = {
            "_id": 1,
            "source_record_id": 1,
            "document_id": 1,
            "stage": 1,
            "rule_id": 1,
            "error_code": 1,
            "reprocess_status": 1,
            "quarantined_at": 1,
            "reasons": 1,
        }
        failures: list[dict[str, Any]] = []
        cursor = collection.find(query or {}, projection).sort(
            [("quarantined_at", -1), ("created_at", -1), ("_id", -1)]
        ).limit(10)
        for document in cursor:
            error_code = document.get("error_code")
            if not error_code:
                reasons = document.get("reasons")
                if isinstance(reasons, list) and reasons and isinstance(reasons[0], Mapping):
                    error_code = reasons[0].get("error_code")
            failures.append(
                {
                    "source_record_id": _display_value(
                        document.get("source_record_id") or document.get("document_id")
                    ),
                    "stage": _display_value(document.get("stage")),
                    "rule_id": _display_value(document.get("rule_id")),
                    "error_code": _display_value(error_code),
                    "reprocess_status": _display_value(
                        document.get("reprocess_status") or document.get("status")
                    ),
                    "quarantined_at": _display_value(document.get("quarantined_at")),
                }
            )
        return failures


SILVER_LABELS = {
    "silver_employee": "직원",
    "silver_area": "업무영역",
    "silver_parent_area": "상위영역",
    "silver_top_area_detail": "최상위영역 상세",
}


def _setting_text(name: str, default: str) -> str:
    value = getattr(settings, name, default)
    return str(value) if value else default


def _setting_mapping(name: str, default: Mapping[str, str]) -> dict[str, str]:
    value = getattr(settings, name, default)
    if not isinstance(value, Mapping):
        return dict(default)
    return {
        str(key): str(item)
        for key, item in value.items()
        if str(key) and str(item)
    }


def _count(collection: Any, query: Mapping[str, Any] | None = None) -> int:
    return int(collection.count_documents(query or {}))


def _success_failure_rates(success_count: int, failure_count: int) -> dict[str, Any]:
    """Calculate the latest success/failure ratio for the dashboard card."""

    total = success_count + failure_count
    if total <= 0:
        return {
            "evaluated": False,
            "total": 0,
            "success_rate": None,
            "failure_rate": None,
            "success_rate_display": "-",
            "failure_rate_display": "-",
        }

    success_rate = success_count / total
    failure_rate = failure_count / total
    return {
        "evaluated": True,
        "total": total,
        "success_rate": success_rate,
        "failure_rate": failure_rate,
        "success_rate_display": _display_rate(success_rate),
        "failure_rate_display": _display_rate(failure_rate),
    }


def _published_run_query(latest_run: Mapping[str, Any] | None) -> dict[str, Any]:
    """Limit result collections to the latest completed validation run."""

    if not latest_run:
        return {"_id": {"$exists": False}}

    run_id = latest_run.get("run_id")
    if run_id in (None, "", "-"):
        return {"_id": {"$exists": False}}
    return {
        "$or": [
            {"_pipeline.run_id": str(run_id)},
            {"run_id": str(run_id)},
        ]
    }


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _display_rate(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return _display_value(value)


def _version_from_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a MongoDB report marker into a JSON-safe dashboard version."""

    return _version_from_run(
        {
            "run_id": document.get("run_id") or document.get("_id"),
            "status": document.get("status"),
            "finished_at": document.get("finished_at"),
        }
    )


def _version_from_run(run: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build the client-side publication token for a completed run."""

    if not run:
        return {
            "token": "none",
            "run_id": None,
            "status": "NO_DATA",
            "finished_at": None,
        }

    raw_run_id = run.get("run_id")
    run_id = (
        str(raw_run_id)
        if raw_run_id is not None and str(raw_run_id) not in {"", "-"}
        else None
    )
    raw_finished_at = run.get("finished_at")
    finished_at = (
        _display_value(raw_finished_at)
        if raw_finished_at is not None and raw_finished_at != ""
        else None
    )
    return {
        "token": run_id or finished_at or "none",
        "run_id": run_id,
        "status": str(run.get("status") or "UNKNOWN").upper(),
        "finished_at": finished_at,
    }
