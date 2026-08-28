from django.db import models

from .release import GoldReleaseRun


class GoldHrExcludedRecord(models.Model):
    exclusion_record_id = models.CharField(max_length=255, primary_key=True)
    release = models.ForeignKey(
        GoldReleaseRun,
        db_column="release_id",
        on_delete=models.CASCADE,
        related_name="excluded_records",
    )
    entity_type = models.CharField(max_length=32)
    logical_entity_id = models.CharField(max_length=255)
    source_record_id = models.CharField(max_length=255, blank=True, default="")
    reason_type = models.CharField(max_length=32)
    reason_codes = models.JSONField(default=list)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "gold_hr_excluded_record"
        indexes = [
            models.Index(fields=["release", "reason_type"], name="gold_excluded_reason_idx")
        ]
