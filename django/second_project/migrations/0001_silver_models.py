from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SilverEmployee",
            fields=[
                (
                    "source_record_id",
                    models.CharField(db_index=True, max_length=255),
                ),
                ("dataset_id", models.CharField(max_length=255)),
                ("normalization_run_id", models.CharField(max_length=255)),
                (
                    "correction_codes",
                    models.JSONField(blank=True, default=list),
                ),
                (
                    "standardization",
                    models.JSONField(
                        blank=True,
                        db_column="_standardization",
                        default=list,
                    ),
                ),
                (
                    "employee_id",
                    models.CharField(max_length=9, primary_key=True, serialize=False),
                ),
                ("employee_name", models.CharField(max_length=255)),
                ("department_name", models.CharField(max_length=255)),
                ("position_name", models.CharField(max_length=255)),
                ("hire_datetime", models.DateTimeField()),
                ("is_active", models.BooleanField()),
            ],
            options={"db_table": "silver_employee"},
        ),
        migrations.CreateModel(
            name="SilverParentArea",
            fields=[
                (
                    "source_record_id",
                    models.CharField(db_index=True, max_length=255),
                ),
                ("dataset_id", models.CharField(max_length=255)),
                ("normalization_run_id", models.CharField(max_length=255)),
                (
                    "correction_codes",
                    models.JSONField(blank=True, default=list),
                ),
                (
                    "standardization",
                    models.JSONField(
                        blank=True,
                        db_column="_standardization",
                        default=list,
                    ),
                ),
                (
                    "parent_area_id",
                    models.CharField(max_length=8, primary_key=True, serialize=False),
                ),
                ("parent_area_name", models.CharField(max_length=255)),
            ],
            options={"db_table": "silver_parent_area"},
        ),
        migrations.CreateModel(
            name="SilverTopAreaDetail",
            fields=[
                (
                    "source_record_id",
                    models.CharField(db_index=True, max_length=255),
                ),
                ("dataset_id", models.CharField(max_length=255)),
                ("normalization_run_id", models.CharField(max_length=255)),
                (
                    "correction_codes",
                    models.JSONField(blank=True, default=list),
                ),
                (
                    "standardization",
                    models.JSONField(
                        blank=True,
                        db_column="_standardization",
                        default=list,
                    ),
                ),
                (
                    "top_area_id",
                    models.CharField(max_length=8, primary_key=True, serialize=False),
                ),
                ("top_area_name", models.CharField(max_length=255)),
                ("top_area_level", models.CharField(max_length=32)),
                ("top_area_registered_at", models.DateTimeField()),
            ],
            options={"db_table": "silver_top_area_detail"},
        ),
        migrations.CreateModel(
            name="SilverArea",
            fields=[
                (
                    "source_record_id",
                    models.CharField(db_index=True, max_length=255),
                ),
                ("dataset_id", models.CharField(max_length=255)),
                ("normalization_run_id", models.CharField(max_length=255)),
                (
                    "correction_codes",
                    models.JSONField(blank=True, default=list),
                ),
                (
                    "standardization",
                    models.JSONField(
                        blank=True,
                        db_column="_standardization",
                        default=list,
                    ),
                ),
                (
                    "area_id",
                    models.CharField(max_length=8, primary_key=True, serialize=False),
                ),
                ("area_name", models.CharField(max_length=255)),
                ("area_registered_at", models.DateTimeField()),
                (
                    "manager_employee",
                    models.ForeignKey(
                        db_column="manager_employee_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="managed_areas",
                        to="second_project.silveremployee",
                    ),
                ),
                (
                    "parent_area",
                    models.ForeignKey(
                        blank=True,
                        db_column="parent_area_id",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="child_areas",
                        to="second_project.silverparentarea",
                    ),
                ),
            ],
            options={"db_table": "silver_area"},
        ),
    ]
