from __future__ import annotations

from typing import Any


class ProjectDatabaseRouter:
    """Keep append-only Bronze data in MongoDB and web/RDB data in default."""

    mongo_alias = "mongodb"
    rdb_aliases = {"default", "sqlite3"}
    bronze_model_names = {"bronzerawrecord"}

    def db_for_read(self, model: type[Any], **hints: Any) -> str | None:
        if self._is_bronze_model(model):
            return self.mongo_alias
        if model._meta.app_label == "second_project":
            return "default"
        return None

    def db_for_write(self, model: type[Any], **hints: Any) -> str | None:
        return self.db_for_read(model, **hints)

    def allow_relation(self, obj1: Any, obj2: Any, **hints: Any) -> bool | None:
        database_1 = self.db_for_read(type(obj1))
        database_2 = self.db_for_read(type(obj2))
        if database_1 and database_2:
            return database_1 == database_2
        return None

    def allow_migrate(
        self,
        db: str,
        app_label: str,
        model_name: str | None = None,
        **hints: Any,
    ) -> bool | None:
        normalized_model = (model_name or "").casefold()
        if app_label == "second_project":
            # RunPython operations have no model_name. Returning None lets the
            # operation run so its own connection-alias guard can decide.
            if not normalized_model:
                return None
            if normalized_model in self.bronze_model_names:
                return db == self.mongo_alias
            if db == self.mongo_alias:
                return False
            return db in self.rdb_aliases
        if db == self.mongo_alias:
            return False
        return None

    def _is_bronze_model(self, model: type[Any]) -> bool:
        return (
            model._meta.app_label == "second_project"
            and model._meta.model_name.casefold() in self.bronze_model_names
        )
