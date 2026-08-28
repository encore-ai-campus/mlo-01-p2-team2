from django.db import migrations


class Migration(migrations.Migration):
    """Join the SQLite sync-control and Gold/HR migration branches."""

    dependencies = [
        ("second_project", "0004_sqlite_sync_run"),
        ("second_project", "0005_merge_develop_gold"),
    ]

    operations = []
