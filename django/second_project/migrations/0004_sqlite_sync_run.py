from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # The repository currently contains two equivalent merge migrations.
        # Depending on both heads makes this migration the single next leaf.
        ("second_project", "0003_merge_bronze_and_legacy"),
        ("second_project", "0003_merge_bronze_legacy"),
    ]

    operations = [
        migrations.CreateModel(
            name="SqliteSyncRun",
            fields=[
                (
                    "normalization_run_id",
                    models.CharField(
                        max_length=255,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("RUNNING", "실행 중"),
                            ("SUCCESS", "성공"),
                            ("FAILED", "실패"),
                        ],
                        default="RUNNING",
                        max_length=16,
                    ),
                ),
                ("source_database", models.CharField(max_length=255)),
                ("source_collection", models.CharField(max_length=255)),
                ("source_count", models.PositiveIntegerField(default=0)),
                ("loaded_counts", models.JSONField(blank=True, default=dict)),
                ("attempt_count", models.PositiveIntegerField(default=1)),
                ("started_at", models.DateTimeField()),
                (
                    "finished_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("error_message", models.TextField(blank=True, default="")),
            ],
            options={
                "db_table": "second_project_sync_run",
                "indexes": [
                    models.Index(
                        fields=["status"],
                        name="second_project_sync_status_idx",
                    ),
                ],
            },
        ),
    ]
