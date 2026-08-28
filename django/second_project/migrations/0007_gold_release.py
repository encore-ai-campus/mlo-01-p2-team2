import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def ensure_gold_tables(apps, schema_editor):
    """Create Gold tables on the configured Gold alias when they are absent.

    The previous implementation could already have created the same physical
    tables under the old app label. Keeping this operation conditional makes
    the app-label consolidation safe for both a new Gold database and an
    existing one.
    """
    gold_alias = getattr(settings, "GOLD_DATABASE_ALIAS", "gold")
    if schema_editor.connection.alias != gold_alias:
        return

    # This RunPython operation executes before SeparateDatabaseAndState has
    # applied its state operations, so historical apps does not contain these
    # newly introduced models yet. The current model classes have the exact
    # same schema as the state below and are safe for schema_editor here.
    from second_project.gold.models import (
        GoldHrAssessment,
        GoldHrCandidateEvidence,
        GoldHrExcludedRecord,
        GoldReleaseRun,
    )

    table_names = set(schema_editor.connection.introspection.table_names())
    for model in (
        GoldReleaseRun,
        GoldHrAssessment,
        GoldHrCandidateEvidence,
        GoldHrExcludedRecord,
    ):
        if model._meta.db_table not in table_names:
            schema_editor.create_model(model)
            table_names.add(model._meta.db_table)


class Migration(migrations.Migration):
    dependencies = [("second_project", "0006_merge_silver_gold")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(ensure_gold_tables, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="GoldReleaseRun",
                    fields=[
                        ("release_id", models.CharField(max_length=255, primary_key=True, serialize=False)),
                        ("dataset_name", models.CharField(max_length=128)),
                        ("dataset_version", models.CharField(max_length=64)),
                        ("as_of_date", models.DateField()),
                        ("source_database", models.CharField(max_length=64)),
                        ("source_dataset_ids", models.JSONField(default=list)),
                        ("source_run_ids", models.JSONField(default=list)),
                        ("status", models.CharField(choices=[("RUNNING", "실행 중"), ("SUCCESS", "성공"), ("FAILED", "실패")], max_length=16)),
                        ("quality_status", models.CharField(choices=[("PASS", "통과"), ("PASS_WITH_HOLDS", "보류 포함 통과"), ("FAIL", "실패")], max_length=32)),
                        ("started_at", models.DateTimeField()),
                        ("finished_at", models.DateTimeField(blank=True, null=True)),
                        ("counts", models.JSONField(default=dict)),
                        ("config_version", models.CharField(max_length=64)),
                        ("error_message", models.TextField(blank=True, default="")),
                    ],
                    options={
                        "db_table": "gold_release_run",
                        "indexes": [
                            models.Index(fields=["dataset_name", "dataset_version"], name="gold_release_dataset_idx"),
                            models.Index(fields=["status"], name="gold_release_status_idx"),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="GoldHrAssessment",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("target_employee_id", models.CharField(max_length=9)),
                        ("as_of_date", models.DateField()),
                        ("area_group_key", models.CharField(max_length=64)),
                        ("parent_area_id", models.CharField(blank=True, max_length=8, null=True)),
                        ("parent_area_name", models.CharField(blank=True, default="", max_length=255)),
                        ("area_ids", models.JSONField(default=list)),
                        ("area_names", models.JSONField(default=list)),
                        ("target_department_name", models.CharField(blank=True, default="", max_length=255)),
                        ("target_position_name", models.CharField(blank=True, default="", max_length=255)),
                        ("target_hire_date", models.DateField()),
                        ("target_tenure_days", models.IntegerField(blank=True, null=True)),
                        ("area_status", models.CharField(max_length=16)),
                        ("overall_status", models.CharField(max_length=16)),
                        ("confirmed_candidate_count", models.IntegerField(blank=True, null=True)),
                        ("held_candidate_count", models.PositiveIntegerField(default=0)),
                        ("unique_candidate_count", models.IntegerField(blank=True, null=True)),
                        ("warning_codes", models.JSONField(default=list)),
                        ("source_record_id", models.CharField(max_length=255)),
                        ("normalization_run_id", models.CharField(max_length=255)),
                        ("release", models.ForeignKey(db_column="release_id", on_delete=django.db.models.deletion.CASCADE, related_name="assessments", to="second_project.goldreleaserun")),
                    ],
                    options={
                        "db_table": "gold_hr_assessment",
                        "indexes": [
                            models.Index(fields=["release", "target_employee_id"], name="gold_assessment_target_idx"),
                            models.Index(fields=["release", "area_status"], name="gold_assessment_status_idx"),
                        ],
                        "constraints": [models.UniqueConstraint(fields=("release", "target_employee_id", "area_group_key"), name="gold_assessment_grain_uq")],
                    },
                ),
                migrations.CreateModel(
                    name="GoldHrCandidateEvidence",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("target_employee_id", models.CharField(max_length=9)),
                        ("parent_area_id", models.CharField(max_length=8)),
                        ("candidate_employee_id", models.CharField(max_length=9)),
                        ("candidate_state", models.CharField(choices=[("CONFIRMED", "검증 완료"), ("HELD", "검증 보류")], max_length=16)),
                        ("candidate_department_name", models.CharField(blank=True, default="", max_length=255)),
                        ("candidate_position_name", models.CharField(blank=True, default="", max_length=255)),
                        ("candidate_hire_date", models.DateField()),
                        ("candidate_tenure_days", models.IntegerField(blank=True, null=True)),
                        ("department_match", models.BooleanField()),
                        ("position_match", models.BooleanField()),
                        ("display_order", models.PositiveIntegerField()),
                        ("warning_codes", models.JSONField(default=list)),
                        ("source_record_id", models.CharField(max_length=255)),
                        ("normalization_run_id", models.CharField(max_length=255)),
                        ("release", models.ForeignKey(db_column="release_id", on_delete=django.db.models.deletion.CASCADE, related_name="candidate_evidence", to="second_project.goldreleaserun")),
                    ],
                    options={
                        "db_table": "gold_hr_candidate_evidence",
                        "indexes": [models.Index(fields=["release", "target_employee_id", "parent_area_id"], name="gold_candidate_target_idx")],
                        "constraints": [models.UniqueConstraint(fields=("release", "target_employee_id", "parent_area_id", "candidate_employee_id"), name="gold_candidate_grain_uq")],
                    },
                ),
                migrations.CreateModel(
                    name="GoldHrExcludedRecord",
                    fields=[
                        ("exclusion_record_id", models.CharField(max_length=1024, primary_key=True, serialize=False)),
                        ("entity_type", models.CharField(max_length=32)),
                        ("logical_entity_id", models.CharField(max_length=255)),
                        ("source_record_id", models.CharField(blank=True, default="", max_length=255)),
                        ("reason_type", models.CharField(max_length=32)),
                        ("reason_codes", models.JSONField(default=list)),
                        ("created_at", models.DateTimeField()),
                        ("release", models.ForeignKey(db_column="release_id", on_delete=django.db.models.deletion.CASCADE, related_name="excluded_records", to="second_project.goldreleaserun")),
                    ],
                    options={
                        "db_table": "gold_hr_excluded_record",
                        "indexes": [models.Index(fields=["release", "reason_type"], name="gold_excluded_reason_idx")],
                    },
                ),
            ],
        ),
    ]
