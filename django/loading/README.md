# Bronze JSONL 적재

실제 Bronze 적재 코드는 `second_project` Django 앱 내부에 있다. `loading` 디렉터리는 기존 실행 경로를 유지하기 위한 호환 래퍼만 제공한다. 크롤러가 생성한 `data/raw_data/records.jsonl`의 payload와 원본 JSONL 문장은 변경하지 않고 `raw_json`, `raw_json_text`, `source_record_sha256`, `source_row_no`, `source_record_id`, `run_id`를 함께 저장한다.

## 실행

프로젝트 루트인 django/에서 실행한다.

    python -m pip install -r requirements.txt
    python manage.py migrate second_project --database mongodb --skip-checks
    python manage.py load_raw_records --help
    python manage.py load_raw_records

기존 스크립트를 호출해야 하는 경우에도 같은 앱 로직을 사용한다.

    python loading/load_raw_records.py

MongoDB 접속 정보는 config/settings.py의 mongodb 별칭을 사용한다.

    MONGODB_URI
    MONGODB_NAME

기본값은 로컬 MongoDB `mongodb://127.0.0.1:27017`, 데이터베이스 `second_project`이다. `MONGODB_URI`와 `MONGODB_NAME` 환경 변수로만 변경하며, 연결 문자열과 비밀번호를 명령행 인자나 로그에 직접 입력하지 않는다.

## 컬렉션

| 컬렉션 | 내용 |
|---|---|
| bronze_raw_records | 원천 레코드와 Bronze 기술 메타데이터 |
| bronze_load_runs | 적재 실행 상태·건수·실패 단계 |
| bronze_manifests | 입력 파일 경로·크기·행 수·SHA-256·상태 |
| bronze_quarantine | Bronze 입력 계약을 통과하지 못한 원본 행과 오류 사유 |

bronze_raw_records의 고유 식별자는 dataset_id + source_record_id이며, 같은 source_record_sha256의 재실행은 중복으로 처리한다. 동일 식별자에 다른 해시가 나타나면 원본을 덮어쓰지 않고 실행을 실패시킨다.

## 로그와 Bronze 기준

적재 실행마다 새로운 UUID `run_id`를 만들고 `logs/pipeline.jsonl`에만 `stage=bronze` 이벤트를 기록한다. 로그는 MongoDB에 적재하지 않는다. 이벤트에는 timestamp, level, run_id, stage, dataset_id, status, input_count, success_count, failure_count, quarantine_count, duration_ms, message를 넣으며 다음 관계를 강제한다.

    input_count = success_count + failure_count + quarantine_count

로그에는 payload, 실명·사번, API 키, MongoDB 연결 문자열, private endpoint와 내부 절대 경로를 기록하지 않는다. 잘못된 원본 행은 로그에 원문을 쓰지 않고 bronze_quarantine에만 보존한다.

## 입력 파일 변경 처리

적재 전후 records.jsonl의 파일 크기와 SHA-256을 비교한다. 실행 중 입력 파일이 변경되면 이미 저장된 Bronze 원본을 삭제하지 않고 실행을 failed로 기록한다. 동일한 파일을 다시 실행하면 고유 인덱스와 upsert로 중복을 만들지 않는다.

## 앱 내부 구조

`second_project/apps.py`가 `repository.models`를 Django에 등록하고, `migrations/0001_initial.py`가 raw collection의 모델 상태를 준비한다. `migrations/0002_bronze_mongodb.py`는 MongoDB 별칭이 `mongodb`일 때 다섯 개 Bronze collection과 인덱스를 준비한다. migration은 데이터 파일을 읽지 않는다.

실제 파일 읽기·검증은 `second_project/service`, MongoDB의 `find`, `bulk_write`, `update_one` 쿼리는 `second_project/repository/mongodb_repository.py`, 실행 진입점은 `second_project/management/commands/load_raw_records.py`가 담당한다. repository는 별도의 MongoClient를 만들지 않고 Django의 `mongodb` 연결을 사용한다. `bronze_load_runs`와 `bronze_manifests`에는 건수·상태 메타데이터를 저장하지만, 로그 본문은 저장하지 않는다.

이전 버전에서 생성된 `pipeline_logs` collection과 기존 로그 문서는 자동 삭제하지 않는다. 새 적재 실행부터는 해당 collection에 로그를 추가하지 않는다.
