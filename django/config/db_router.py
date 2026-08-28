"""Database routing for the explicit SQLite/MongoDB model split.

The application deliberately uses ``QuerySet.using()`` for reads and writes.
This router only prevents migrations and backend checks from applying a model
to the wrong database alias.
"""


class DatabaseRouter:
    """Keep Mongo-only and SQLite-only models on their intended aliases."""

    SQLITE_ALIASES = frozenset({"default", "sqlite3"})
    MONGO_ALIAS = "mongodb"
    # Mongo collections are created idempotently by
    # second_project.0002_bronze_mongodb. Skipping CreateModel operations is
    # intentional: Bronze may already have been created by the loader before
    # Django migrations are run.
    MONGO_MODELS = frozenset()
    SQLITE_MODELS = frozenset(
        {
            "legacyorgrecord",
            "silveremployee",
            "silverparentarea",
            "silvertopareadetail",
            "silverarea",
            "sqlitesyncrun",
        }
    )

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """Allow each application model only on its designated alias.

        ``model_name`` is absent for database-level migration operations such
        as the Bronze collection/index setup migration. Those operations are
        intentionally allowed for ``second_project`` so the migration can
        inspect its database alias.
        """

        normalized_model_name = model_name.lower() if model_name else None

        if db == self.MONGO_ALIAS:
            if app_label != "second_project":
                return False
            if normalized_model_name is None:
                return True
            return normalized_model_name in self.MONGO_MODELS

        if db in self.SQLITE_ALIASES:
            if app_label != "second_project":
                return True
            if normalized_model_name is None:
                return True
            return normalized_model_name in self.SQLITE_MODELS

        return None
