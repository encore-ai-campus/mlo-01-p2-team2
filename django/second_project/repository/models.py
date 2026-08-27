from django.db import models


class BronzeRawRecord(models.Model):
    """Django model for the immutable Bronze raw-record collection."""

    # The composite value is dataset_id:source_record_id and is stable across
    # loader reruns, so MongoDB does not need an auto-incrementing key.
    record_id = models.CharField(max_length=255, primary_key=True, db_column="_id")
    dataset_id = models.CharField(max_length=255)
    source_record_id = models.CharField(max_length=255)
    source_row_no = models.PositiveIntegerField()
    scheduled_release_at = models.DateTimeField()
    scheduled_release_at_raw = models.CharField(max_length=255)
    ingested_at = models.DateTimeField()
    raw_json = models.JSONField()
    raw_json_text = models.TextField()
    raw_line_sha256 = models.CharField(max_length=64)
    source_record_sha256 = models.CharField(max_length=64)
    source_sha256 = models.CharField(max_length=64)
    source_filename = models.CharField(max_length=255)
    run_id = models.CharField(max_length=255)
    load_run_id = models.CharField(max_length=255)
    source_run_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "bronze_raw_records"
