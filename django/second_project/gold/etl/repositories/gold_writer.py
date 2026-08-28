from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from second_project.gold.models import (
    GoldHrAssessment,
    GoldHrCandidateEvidence,
    GoldHrExcludedRecord,
    GoldReleaseRun,
)

from ..types import TransformResult, ValidationResult
from ..validation.output import validate_loaded_release


class ReleaseAlreadySucceeded(RuntimeError):
    pass


class ReleaseIdentityConflict(RuntimeError):
    pass


class GoldWriter:
    def __init__(self, *, using: str) -> None:
        self.using = using

    def write(
        self,
        *,
        release_id: str,
        dataset_name: str,
        dataset_version: str,
        as_of_date: date,
        source_database: str,
        source_dataset_ids: tuple[str, ...],
        source_run_ids: tuple[str, ...],
        config_version: str,
        quality_status: str,
        counts: dict[str, int],
        rows: TransformResult,
    ) -> ValidationResult:
        now = timezone.now()
        existing = GoldReleaseRun.objects.using(self.using).filter(pk=release_id).first()
        if existing and existing.status == GoldReleaseRun.Status.SUCCESS:
            identity = (
                existing.dataset_name == dataset_name
                and existing.dataset_version == dataset_version
                and existing.as_of_date == as_of_date
                and existing.source_database == source_database
                and existing.source_dataset_ids == list(source_dataset_ids)
                and existing.source_run_ids == list(source_run_ids)
                and existing.config_version == config_version
                and existing.counts == counts
            )
            if not identity:
                raise ReleaseIdentityConflict(
                    f"release_id {release_id!r} already belongs to a different snapshot or contract"
                )
            raise ReleaseAlreadySucceeded(release_id)
        if existing:
            existing.dataset_name = dataset_name
            existing.dataset_version = dataset_version
            existing.as_of_date = as_of_date
            existing.source_database = source_database
            existing.source_dataset_ids = list(source_dataset_ids)
            existing.source_run_ids = list(source_run_ids)
            existing.status = GoldReleaseRun.Status.RUNNING
            existing.quality_status = quality_status
            existing.started_at = now
            existing.finished_at = None
            existing.counts = counts
            existing.config_version = config_version
            existing.error_message = ""
            existing.save(using=self.using)
        else:
            GoldReleaseRun.objects.using(self.using).create(
                release_id=release_id,
                dataset_name=dataset_name,
                dataset_version=dataset_version,
                as_of_date=as_of_date,
                source_database=source_database,
                source_dataset_ids=list(source_dataset_ids),
                source_run_ids=list(source_run_ids),
                status=GoldReleaseRun.Status.RUNNING,
                quality_status=quality_status,
                started_at=now,
                counts=counts,
                config_version=config_version,
            )

        try:
            with transaction.atomic(using=self.using):
                GoldHrAssessment.objects.using(self.using).filter(release_id=release_id).delete()
                GoldHrCandidateEvidence.objects.using(self.using).filter(release_id=release_id).delete()
                GoldHrExcludedRecord.objects.using(self.using).filter(release_id=release_id).delete()
                GoldHrAssessment.objects.using(self.using).bulk_create(
                    [GoldHrAssessment(release_id=release_id, **row.__dict__) for row in rows.assessments]
                )
                GoldHrCandidateEvidence.objects.using(self.using).bulk_create(
                    [GoldHrCandidateEvidence(release_id=release_id, **row.__dict__) for row in rows.candidates]
                )
                GoldHrExcludedRecord.objects.using(self.using).bulk_create(
                    [
                        GoldHrExcludedRecord(
                            exclusion_record_id=f"{release_id}:{sequence}:{row.exclusion_record_id}",
                            release_id=release_id,
                            entity_type=row.entity_type,
                            logical_entity_id=row.logical_entity_id,
                            source_record_id=row.source_record_id,
                            reason_type=row.reason_type,
                            reason_codes=row.reason_codes,
                            created_at=timezone.now(),
                        )
                        for sequence, row in enumerate(rows.excluded, start=1)
                    ]
                )
                validation = validate_loaded_release(
                    release_id=release_id,
                    using=self.using,
                    expected_counts=counts,
                )
                if not validation.passed:
                    raise ValueError("Gold post-load validation failed")
                return validation
        except Exception as error:
            GoldReleaseRun.objects.using(self.using).filter(pk=release_id).update(
                status=GoldReleaseRun.Status.FAILED,
                quality_status=GoldReleaseRun.QualityStatus.FAIL,
                finished_at=timezone.now(),
                error_message=str(error)[:2000],
            )
            raise

    def mark_success(self, release_id: str) -> None:
        GoldReleaseRun.objects.using(self.using).filter(pk=release_id).update(
            status=GoldReleaseRun.Status.SUCCESS,
            finished_at=timezone.now(),
            error_message="",
        )

    def mark_failed(self, release_id: str, error: Exception) -> None:
        GoldReleaseRun.objects.using(self.using).filter(pk=release_id).update(
            status=GoldReleaseRun.Status.FAILED,
            quality_status=GoldReleaseRun.QualityStatus.FAIL,
            finished_at=timezone.now(),
            error_message=str(error)[:2000],
        )
