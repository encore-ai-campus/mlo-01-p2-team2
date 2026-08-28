"""Create and index the Bronze collections owned by second_project."""

from django.db import migrations

try:
    from pymongo.errors import CollectionInvalid, OperationFailure
except ImportError:  # pragma: no cover - requirements install guards this path
    CollectionInvalid = RuntimeError
    OperationFailure = RuntimeError


MONGO_ALIAS = "mongodb"

COLLECTION_NAMES = (
    "bronze_raw_records",
    "bronze_load_runs",
    "bronze_manifests",
    "pipeline_logs",
    "bronze_quarantine",
)

INDEXES = {
    "bronze_raw_records": (
        ([('dataset_id', 1), ('source_record_id', 1)], "uq_dataset_source_record", True),
        ([('source_sha256', 1)], "idx_source_sha256", False),
        ([('source_record_sha256', 1)], "idx_source_record_sha256", False),
        ([('run_id', 1)], "idx_run_id", False),
        ([('scheduled_release_at', 1)], "idx_scheduled_release_at", False),
    ),
    "bronze_load_runs": (
        ([('run_id', 1)], "uq_run_id", True),
        ([('status', 1), ('started_at', -1)], "idx_run_status", False),
    ),
    "bronze_manifests": (
        ([('run_id', 1)], "uq_manifest_run_id", True),
    ),
    "pipeline_logs": (
        ([('run_id', 1), ('timestamp', 1)], "idx_log_run_timestamp", False),
        ([('stage', 1), ('status', 1), ('timestamp', 1)], "idx_log_stage_status", False),
        ([('event_hash', 1)], "uq_log_event_hash", True),
    ),
    "bronze_quarantine": (
        ([('run_id', 1), ('line_no', 1)], "idx_quarantine_run_line", False),
    ),
}


def _database_for(schema_editor):
    if schema_editor.connection.alias != MONGO_ALIAS:
        return None
    connection = schema_editor.connection
    connection.ensure_connection()
    # get_database() may return Django's OperationDebugWrapper when DEBUG is
    # enabled.  Migration setup needs the underlying PyMongo database because
    # it indexes collections by name.
    return connection.database


def _ensure_collection(database, name: str) -> None:
    if name in database.list_collection_names():
        return
    try:
        database.create_collection(name)
    except CollectionInvalid:
        # Another process may have created it between the list and create
        # calls.  Existing data is intentionally never dropped.
        return
    except OperationFailure as exc:
        if getattr(exc, "code", None) != 48:  # NamespaceExists
            raise


def _ensure_index(collection, keys, *, name: str, unique: bool) -> None:
    target_keys = list(keys)
    for existing in collection.list_indexes():
        if list(existing.get("key", {}).items()) != target_keys:
            continue
        if bool(existing.get("unique", False)) != unique:
            raise RuntimeError(
                f"MongoDB 인덱스의 unique 옵션이 설계와 다릅니다: {existing.get('name')}"
            )
        return
    collection.create_index(target_keys, name=name, unique=unique)


def prepare_bronze_collections(apps, schema_editor) -> None:  # noqa: ARG001
    database = _database_for(schema_editor)
    if database is None:
        return

    for name in COLLECTION_NAMES:
        _ensure_collection(database, name)
    for name, index_specs in INDEXES.items():
        collection = database[name]
        for keys, index_name, unique in index_specs:
            _ensure_index(collection, keys, name=index_name, unique=unique)


def preserve_bronze_collections(apps, schema_editor) -> None:  # noqa: ARG001
    # Bronze is append-only.  Reversing a migration must not delete collected
    # raw data, run history, logs, or quarantined rows.
    return


class Migration(migrations.Migration):
    dependencies = [("second_project", "0001_initial")]

    operations = [
        migrations.RunPython(
            prepare_bronze_collections,
            preserve_bronze_collections,
            atomic=False,
        ),
    ]
