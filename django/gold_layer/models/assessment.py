from django.db import models

from .release import GoldReleaseRun


class GoldHrAssessment(models.Model):
    release = models.ForeignKey(
        GoldReleaseRun,
        db_column="release_id",
        on_delete=models.CASCADE,
        related_name="assessments",
    )
    target_employee_id = models.CharField(max_length=9)
    as_of_date = models.DateField()
    area_group_key = models.CharField(max_length=64)
    parent_area_id = models.CharField(max_length=8, null=True, blank=True)
    parent_area_name = models.CharField(max_length=255, blank=True, default="")
    area_ids = models.JSONField(default=list)
    area_names = models.JSONField(default=list)
    target_department_name = models.CharField(max_length=255, blank=True, default="")
    target_position_name = models.CharField(max_length=255, blank=True, default="")
    target_hire_date = models.DateField()
    target_tenure_days = models.IntegerField(null=True, blank=True)
    area_status = models.CharField(max_length=16)
    overall_status = models.CharField(max_length=16)
    confirmed_candidate_count = models.IntegerField(null=True, blank=True)
    held_candidate_count = models.PositiveIntegerField(default=0)
    unique_candidate_count = models.IntegerField(null=True, blank=True)
    warning_codes = models.JSONField(default=list)
    source_record_id = models.CharField(max_length=255)
    normalization_run_id = models.CharField(max_length=255)

    class Meta:
        db_table = "gold_hr_assessment"
        constraints = [
            models.UniqueConstraint(
                fields=["release", "target_employee_id", "area_group_key"],
                name="gold_assessment_grain_uq",
            )
        ]
        indexes = [
            models.Index(fields=["release", "target_employee_id"], name="gold_assessment_target_idx"),
            models.Index(fields=["release", "area_status"], name="gold_assessment_status_idx"),
        ]
