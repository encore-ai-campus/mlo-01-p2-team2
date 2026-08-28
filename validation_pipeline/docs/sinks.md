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

표준화 성공 데이터를 RDB로 옮길 때는 Django 프로젝트의
`load_success_to_sqlite` 관리 명령을 사용한다. 이 명령은
`encore_success_experiment.records`에서 `normalization_run_id`별 문서를 읽어
`sqlite3` alias의 Django ORM 모델에 PK 기준으로 upsert한다. 실패 collection은
조회하지 않는다.

파이프라인은 표준화기가 반환한 모든 성공 문서에 현재 실행의
`normalization_run_id`를 최상위 필드로 보장한다. canonical 규칙이
`_runtime.normalization_run_id`로 만든 값이 있으면 현재 실행 ID와 일치하는지
검사하고, 기존 규칙처럼 값이 없으면 `Pipeline.run_id`를 주입한다. Mongo Sink도
저장 직전에 같은 검사를 수행하므로 실행 ID가 `_pipeline.run_id` 안에만 남지
않는다. 이 필드는 실행 메타데이터이므로 자동 생성 `_id`의 내용 해시에서는
제외한다.

실행 ID를 지정하지 않으면 MongoDB의 `normalization_run_id` 인덱스와 SQLite의
`second_project_sync_run` 적재 이력을 비교해 가장 오래된 미처리 실행 1건을
자동 선택한다. 선택·적재를 미처리 실행이 없어질 때까지 반복한다. 이미 `SUCCESS`인
실행은 건너뛰고, `FAILED`인 실행은 재시도한다.
따라서 운영에서는 실행 ID를 사람이 확인하지 않고 다음 명령만 호출하면 된다.

```powershell
cd ../django
python manage.py migrate --database sqlite3
python manage.py load_success_to_sqlite `
  --config ../validation_pipeline/config.json
```

위 명령은 `crawl_and_load`와 같이 상주하며 매시 01, 04, 07, ...분 00초
(KST)에 한 cycle을 실행한다. 한 cycle 안에서는 미처리 실행이 없어질 때까지
반복한다. 즉시 한 cycle만 실행하거나 테스트하려면 `--once`를 추가한다.

```powershell
python manage.py load_success_to_sqlite `
  --config ../validation_pipeline/config.json `
  --once
```

장애 복구나 특정 실행 재처리가 필요한 경우에만
`--normalization-run-id <실행 ID>`를 추가할 수 있다.

## DATA-LAKE 백업

`DjangoMongoDataLakeBackup`은 기존 MongoDB 대상의 시간별 snapshot과 별도 백업
manifest를 담당한다. 파이프라인의 실행별 Bronze/Manifest는 위 sink가 직접
기록하고, DATA-LAKE 백업 manifest와는 별도 증적이다.
