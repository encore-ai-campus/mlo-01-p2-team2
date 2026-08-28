from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("second_project", "0001_silver_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="LegacyOrgRecord",
            fields=[
                (
                    "source_record_id",
                    models.CharField(max_length=255, primary_key=True, serialize=False),
                ),
                (
                    "source_document_id",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("dataset_id", models.CharField(max_length=255)),
                ("record_id", models.CharField(blank=True, max_length=255, null=True)),
                ("source_row_no", models.IntegerField(blank=True, null=True)),
                ("crawl_run_id", models.CharField(blank=True, max_length=255, null=True)),
                ("ingested_at_kst", models.DateTimeField(blank=True, null=True)),
                ("release_slot", models.CharField(blank=True, max_length=64, null=True)),
                ("scheduled_release_at", models.DateTimeField(blank=True, null=True)),
                (
                    "source_record_sha256",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                ("mgr_no", models.CharField(blank=True, max_length=9, null=True)),
                ("mgr_nm", models.CharField(blank=True, max_length=255, null=True)),
                ("mgr_act_yn", models.CharField(blank=True, max_length=32, null=True)),
                ("mgr_pos_nm", models.CharField(blank=True, max_length=255, null=True)),
                ("mgr_dept_nm", models.CharField(blank=True, max_length=255, null=True)),
                ("mgr_hire_dtm", models.DateTimeField(blank=True, null=True)),
                ("area_no", models.CharField(max_length=8)),
                ("area_nm", models.CharField(max_length=255)),
                ("area_reg_dtm", models.DateTimeField(blank=True, null=True)),
                ("p_area_no", models.CharField(blank=True, max_length=8, null=True)),
                ("p_area_nm", models.CharField(blank=True, max_length=255, null=True)),
                ("top_area_no", models.CharField(max_length=8)),
                ("top_area_nm", models.CharField(max_length=255)),
                ("top_area_lvl", models.CharField(max_length=32)),
                ("top_area_reg_dtm", models.DateTimeField(blank=True, null=True)),
                ("raw_json", models.TextField(blank=True, default="")),
                (
                    "standardization",
                    models.JSONField(
                        blank=True,
                        db_column="_standardization",
                        default=list,
                    ),
                ),
            ],
            options={"db_table": "legacy_org_record"},
        ),
    ]
