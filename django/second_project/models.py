"""Django model exports for the second_project app."""

# Keep the model definition in the repository layer.  Re-exporting it from
# the conventional Django models module ensures Django imports one model
# class instead of registering a duplicate BronzeRawRecord.
from .repository.models import BronzeRawRecord


<<<<<<< HEAD
class SilverMetadata(models.Model):
    """Silver 공통 계보·표준화 메타데이터."""

    source_record_id = models.CharField(max_length=255, db_index=True)
    dataset_id = models.CharField(max_length=255)
    normalization_run_id = models.CharField(max_length=255)
    correction_codes = models.JSONField(default=list, blank=True)
    standardization = models.JSONField(
        db_column="_standardization",
        default=list,
        blank=True,
    )

    class Meta:
        abstract = True


class LegacyOrgRecord(models.Model):
    """레거시 규칙으로 성공한 행을 보존하는 SQLite staging 테이블."""

    source_record_id = models.CharField(max_length=255, primary_key=True)
    source_document_id = models.CharField(max_length=255, blank=True, null=True)
    dataset_id = models.CharField(max_length=255)
    record_id = models.CharField(max_length=255, blank=True, null=True)
    source_row_no = models.IntegerField(blank=True, null=True)
    crawl_run_id = models.CharField(max_length=255, blank=True, null=True)
    ingested_at_kst = models.DateTimeField(blank=True, null=True)
    release_slot = models.CharField(max_length=64, blank=True, null=True)
    scheduled_release_at = models.DateTimeField(blank=True, null=True)
    source_record_sha256 = models.CharField(max_length=64, blank=True, null=True)

    mgr_no = models.CharField(max_length=9, blank=True, null=True)
    mgr_nm = models.CharField(max_length=255, blank=True, null=True)
    mgr_act_yn = models.CharField(max_length=32, blank=True, null=True)
    mgr_pos_nm = models.CharField(max_length=255, blank=True, null=True)
    mgr_dept_nm = models.CharField(max_length=255, blank=True, null=True)
    mgr_hire_dtm = models.DateTimeField(blank=True, null=True)
    area_no = models.CharField(max_length=8)
    area_nm = models.CharField(max_length=255)
    area_reg_dtm = models.DateTimeField(blank=True, null=True)
    p_area_no = models.CharField(max_length=8, blank=True, null=True)
    p_area_nm = models.CharField(max_length=255, blank=True, null=True)
    top_area_no = models.CharField(max_length=8)
    top_area_nm = models.CharField(max_length=255)
    top_area_lvl = models.CharField(max_length=32)
    top_area_reg_dtm = models.DateTimeField(blank=True, null=True)

    raw_json = models.TextField(blank=True, default="")
    standardization = models.JSONField(
        db_column="_standardization",
        default=list,
        blank=True,
    )

    class Meta:
        db_table = "legacy_org_record"


class SilverEmployee(SilverMetadata):
    """성공 MongoDB의 `silver_employee` 컬렉션에 대응하는 직원 테이블."""

    employee_id = models.CharField(max_length=9, primary_key=True)
    employee_name = models.CharField(max_length=255)
    profile_image_url = models.URLField(max_length=2048, blank=True, null=True)
    department_name = models.CharField(max_length=255)
    position_name = models.CharField(max_length=255)
    hire_datetime = models.DateTimeField()
    is_active = models.BooleanField()

    class Meta:
        db_table = "silver_employee"


class SilverParentArea(SilverMetadata):
    """성공 MongoDB의 `silver_parent_area` 컬렉션에 대응하는 상위영역 테이블."""

    parent_area_id = models.CharField(max_length=8, primary_key=True)
    parent_area_name = models.CharField(max_length=255)

    class Meta:
        db_table = "silver_parent_area"


class SilverTopAreaDetail(SilverMetadata):
    """성공 MongoDB의 `silver_top_area_detail` 컬렉션에 대응하는 최상위영역 테이블."""

    top_area_id = models.CharField(max_length=8, primary_key=True)
    top_area_name = models.CharField(max_length=255)
    top_area_level = models.CharField(max_length=32)
    top_area_registered_at = models.DateTimeField()

    class Meta:
        db_table = "silver_top_area_detail"


class SilverArea(SilverMetadata):
    """성공 MongoDB의 `silver_area` 컬렉션에 대응하는 업무영역 테이블."""

    area_id = models.CharField(max_length=8, primary_key=True)
    area_name = models.CharField(max_length=255)
    manager_employee = models.ForeignKey(
        SilverEmployee,
        db_column="manager_employee_id",
        on_delete=models.PROTECT,
        related_name="managed_areas",
    )
    area_registered_at = models.DateTimeField()
    parent_area = models.ForeignKey(
        SilverParentArea,
        blank=True,
        db_column="parent_area_id",
        null=True,
        on_delete=models.PROTECT,
        related_name="child_areas",
    )

    class Meta:
        db_table = "silver_area"
=======
__all__ = ["BronzeRawRecord"]
>>>>>>> develop
