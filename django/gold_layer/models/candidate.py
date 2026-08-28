from django.db import models

from .release import GoldReleaseRun


class GoldHrCandidateEvidence(models.Model):
    class CandidateState(models.TextChoices):
        CONFIRMED = "CONFIRMED", "검증 완료"
        HELD = "HELD", "검증 보류"

    release = models.ForeignKey(
        GoldReleaseRun,
        db_column="release_id",
        on_delete=models.CASCADE,
        related_name="candidate_evidence",
    )
    target_employee_id = models.CharField(max_length=9)
    parent_area_id = models.CharField(max_length=8)
    candidate_employee_id = models.CharField(max_length=9)
    candidate_state = models.CharField(max_length=16, choices=CandidateState.choices)
    candidate_department_name = models.CharField(max_length=255, blank=True, default="")
    candidate_position_name = models.CharField(max_length=255, blank=True, default="")
    candidate_hire_date = models.DateField()
    candidate_tenure_days = models.IntegerField(null=True, blank=True)
    department_match = models.BooleanField()
    position_match = models.BooleanField()
    display_order = models.PositiveIntegerField()
    warning_codes = models.JSONField(default=list)
    source_record_id = models.CharField(max_length=255)
    normalization_run_id = models.CharField(max_length=255)

    class Meta:
        db_table = "gold_hr_candidate_evidence"
        constraints = [
            models.UniqueConstraint(
                fields=["release", "target_employee_id", "parent_area_id", "candidate_employee_id"],
                name="gold_candidate_grain_uq",
            )
        ]
        indexes = [
            models.Index(
                fields=["release", "target_employee_id", "parent_area_id"],
                name="gold_candidate_target_idx",
            )
        ]
