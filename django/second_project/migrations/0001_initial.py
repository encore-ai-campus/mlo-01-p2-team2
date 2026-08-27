from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="BronzeRawRecord",
            fields=[
                (
                    "record_id",
                    models.CharField(
                        db_column="_id",
                        max_length=255,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("dataset_id", models.CharField(max_length=255)),
                ("source_record_id", models.CharField(max_length=255)),
                ("source_row_no", models.PositiveIntegerField()),
                ("scheduled_release_at", models.DateTimeField()),
                ("scheduled_release_at_raw", models.CharField(max_length=255)),
                ("ingested_at", models.DateTimeField()),
                ("raw_json", models.JSONField()),
                ("raw_json_text", models.TextField()),
                ("raw_line_sha256", models.CharField(max_length=64)),
                ("source_record_sha256", models.CharField(max_length=64)),
                ("source_sha256", models.CharField(max_length=64)),
                ("source_filename", models.CharField(max_length=255)),
                ("run_id", models.CharField(max_length=255)),
                ("load_run_id", models.CharField(max_length=255)),
                (
                    "source_run_id",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
            ],
            options={"db_table": "bronze_raw_records"},
        ),
    ]
