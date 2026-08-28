# 저장 (`sinks.py`)

`JsonlSink`는 실행 ID별 결과를, `MongoSink`와 `DjangoMongoSink`는 정상·실패
저장소를 분리해 기록한다.

```text
output/<run-id>/
├── bronze_raw_records.jsonl   원천 문서 envelope·raw_json·계보·해시
├── manifest.json              실행별 원천 경로·건수·크기·SHA-256·상태
├── standardized.jsonl          통합 표준 후보
├── silver_employee.jsonl
├── silver_area.jsonl
├── silver_parent_area.jsonl
├── silver_top_area_detail.jsonl
├── rejected.jsonl              격리 메타데이터와 사유
└── report.json
```

canonical 문서는 MongoDB에서 `sink.silver_database`와
`sink.silver_collections`에 지정한 네 컬렉션으로 분리 upsert한다. 지정하지 않으면
`silver_employee`, `silver_area`, `silver_parent_area`, `silver_top_area_detail`을
컬렉션명으로 사용한다. 업무 PK를 Mongo `_id`로 사용하므로 동일 입력 재실행은
중복 대신 upsert가 된다.

실패 레코드는 `quarantine_id`, `run_id`, `source_record_id`, `rule_id`,
`error_code`, `quarantined_at`, `raw_reference`, `reprocess_status`를 보존한다.
로그에는 원문을 복사하지 않으며, 결과 파일의 격리 문서만 재처리 목적으로 보존한다.

## Django MongoDB 저장

`DjangoMongoSink`는 `django.db.connections[database_alias]`의 연결을 재사용한다.
정상 Silver 모델은 위 네 컬렉션에, 격리 문서는
`failure_database.failure_collection`에, 실행 리포트는
`report_database.report_collection`에 저장한다. `local_report=true`이면
실행별 `report.json`도 남긴다.
실행별 `report.json`, `bronze_raw_records.jsonl`, `manifest.json`도 남긴다.
Bronze는 `bronze_database.bronze_collection`, Manifest는 같은 DB의
`manifest_collection`에 실행 ID를 `_id`로 upsert한다. `bronze_database`를
생략하면 `second_project`를 사용한다.

성공 Silver 데이터를 RDB로 옮길 때는 Django 프로젝트의
`load_success_to_sqlite` 관리 명령을 사용한다. 이 명령은 `silver_database`의
네 collection만 읽고 `sqlite3` alias의 Django ORM 모델에 PK 기준으로 upsert한다.
실패 collection은 조회하지 않는다.

```powershell
cd ../django
python manage.py migrate --database sqlite3
python manage.py load_success_to_sqlite `
  --config ../validation_pipeline/config.json
```

## DATA-LAKE 백업

`DjangoMongoDataLakeBackup`은 기존 MongoDB 대상의 시간별 snapshot과 별도 백업
manifest를 담당한다. 파이프라인의 실행별 Bronze/Manifest는 위 sink가 직접
기록하고, DATA-LAKE 백업 manifest와는 별도 증적이다.
