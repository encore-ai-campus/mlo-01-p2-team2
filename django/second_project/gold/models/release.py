from django.db import models


class GoldReleaseRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "RUNNING", "실행 중"
        SUCCESS = "SUCCESS", "성공"
        FAILED = "FAILED", "실패"

    class QualityStatus(models.TextChoices):
        PASS = "PASS", "통과"
        PASS_WITH_HOLDS = "PASS_WITH_HOLDS", "보류 포함 통과"
        FAIL = "FAIL", "실패"

    release_id = models.CharField(max_length=255, primary_key=True)
    dataset_name = models.CharField(max_length=128)
    dataset_version = models.CharField(max_length=64)
    as_of_date = models.DateField()
    source_database = models.CharField(max_length=64)
    source_dataset_ids = models.JSONField(default=list)
    source_run_ids = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices)
    quality_status = models.CharField(max_length=32, choices=QualityStatus.choices)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    counts = models.JSONField(default=dict)
    config_version = models.CharField(max_length=64)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "gold_release_run"
        indexes = [
            models.Index(fields=["dataset_name", "dataset_version"], name="gold_release_dataset_idx"),
            models.Index(fields=["status"], name="gold_release_status_idx"),
        ]
