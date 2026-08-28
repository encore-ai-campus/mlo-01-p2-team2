# 05. 표준화 성공 데이터 RDB(SQLite) 적재

## 목적과 범위

이 문서는 표준화·검증을 통과해 별도로 저장된 MongoDB 성공 collection의 통합
표준 문서를 Django ORM을 통해 SQLite RDB에 적재하는 방법을 설명한다.

이 기능의 원천은 다음 한 곳으로 고정되어 있다.

| 구분 | DB | collection | 처리 여부 |
|---|---|---|---|
| 표준화 성공 | `encore_success_experiment` | `records` | 조회·적재 |
| 표준화 실패 | `encore_failure_experiment` | `records` | 조회하지 않음 |

실패 collection은 설정값이 예상한 이름인지 확인하는 데만 사용한다. 실패 문서,
quarantine 문서, Bronze 문서는 이 RDB 적재 대상이 아니다.

## 전체 흐름

```text
표준화·검증 성공
    │
    ▼
encore_success_experiment.records
    │  normalization_run_id 인덱스
    │  미처리 실행 ID 1건 자동 선택
    ▼
python manage.py load_success_to_sqlite
    │  선택한 실행 ID로 equality 조회
    │  문서 검증·모델 변환
    │  PK 기준 batch upsert
    ▼
SQLite(sqlite3)
    ├─ silver_employee
    ├─ silver_parent_area
    ├─ silver_top_area_detail
    ├─ silver_area
    └─ second_project_sync_run  ← 실행별 적재 상태·재시도 이력
```

한 번의 실행 단위에서는 표준화 실행 하나를 처리하지만, 운영 cycle은 미처리
실행이 없어질 때까지 이 단위를 반복한다. 여러 실행이 대기 중이어도 다음
예약 주기까지 기다리지 않고 현재 cycle에서 모두 처리한다.

## 사전 조건

### SQLite migration

SQLite에 Django migration recorder와 RDB 테이블을 먼저 만든다.

```powershell
cd C:\encore_project\2nd_project_git\django
python manage.py migrate --database=sqlite3
```

현재 migration graph의 두 기존 merge head를 `0004_sqlite_sync_run`이 함께
연결하므로, 별도로 migration 파일을 삭제하거나 다시 만들 필요가 없다.

### MongoDB 연결

`config/settings.py`의 `DATABASES["mongodb"]` alias가 MongoDB에 연결되어야
한다. 현재 설정에서 alias의 기본 데이터베이스 이름은 `second_project`이며,
`db_mount`는 이 RDB 적재의 원천 데이터베이스가 아니다. 연결 alias의
`NAME`은 기본값일 뿐이고, RDB 로더는 아래의 `success_database`를 명시적으로
선택한다.

```powershell
$env:MONGODB_URI = "mongodb://127.0.0.1:27017"
$env:MONGODB_NAME = "second_project"
```

`MONGODB_NAME`을 생략하면 현재 `config/settings.py`의 기본값인
`second_project`가 사용된다. 기존 환경과의 호환을 위해
`BOOKSTORE_MONGODB_URI`와 `BOOKSTORE_MONGODB_NAME`도 fallback으로 지원하지만,
명시적인 `MONGODB_*` 값이 우선한다.

MongoDB database 역할은 다음과 같다.

| 역할 | DB | collection | 선택 방식 |
|---|---|---|---|
| Mongo alias 기본 DB | `second_project` | - | `DATABASES["mongodb"]["NAME"]` |
| 표준화 성공 원천 | `encore_success_experiment` | `records` | `sink.success_database` 명시 |
| 표준화 실패 원천 | `encore_failure_experiment` | `records` | 설정 검증용, RDB 조회 안 함 |
| Bronze 원천 | `second_project` | `bronze_raw_records` | `sink.bronze_database` 명시 |

`validation_pipeline/config.json`의 `sink`는 다음 값을 사용해야 한다.

Bronze MongoDB를 읽는 자동 표준화는 `raw_json`에 Bronze 계보의
`source_record_id`와 `dataset_id`를 병합한다. 숫자형 legacy `record_id`를
canonical 문자열 식별자로 추정해서는 안 된다.

```json
{
  "type": "django_mongodb",
  "database_alias": "mongodb",
  "success_database": "encore_success_experiment",
  "success_collection": "records",
  "failure_database": "encore_failure_experiment",
  "failure_collection": "records"
}
```

### 성공 문서 계약

`records`의 성공 문서는 한 원천 행을 표현하는 통합 표준 문서여야 한다. 특히
`normalization_run_id`는 중첩 객체가 아니라 최상위 필드여야 한다.

표준화 파이프라인은 표준화 결과 직후 현재 실행의 `Pipeline.run_id`를
최상위 `normalization_run_id`로 보장하고, MongoDB Sink도 저장 직전에 같은
값을 확인한다. canonical 규칙이 `_runtime.normalization_run_id`로 만든 값이
있으면 현재 실행 ID와 일치하는지 검증하며, 다른 값이면 실행 경계 혼입으로
판단해 실패 처리한다. 따라서 `_pipeline.run_id`만 있는 문서는 이 로더의
자동 증분 선택 대상이 아니다.

이 필드를 추가하기 전 이미 저장된 성공 문서는 자동으로 수정되지 않는다.
그 문서까지 적재하려면 표준화 파이프라인을 다시 실행하거나, 기존
`_pipeline.run_id`를 최상위 필드로 옮기는 별도 백필 작업을 먼저 수행해야
한다. 백필은 문서 구조와 재표준화 여부를 확인한 뒤 별도로 진행한다.

```json
{
  "_id": "canonical:...",
  "dataset_id": "dataset-001",
  "source_record_id": "source-001",
  "normalization_run_id": "20260827T103100Z-a0d60ea5",
  "correction_codes": [],
  "_standardization": [],
  "employee_id": "EMP000001",
  "employee_name": "홍길동",
  "department_name": "개발팀",
  "position_name": "사원",
  "hire_datetime": "2024-01-01T09:00:00+09:00",
  "is_active": true,
  "area_id": "BIZ00001",
  "area_name": "플랫폼",
  "manager_employee_id": "EMP000001",
  "area_registered_at": "2024-01-01T09:00:00+09:00",
  "parent_area_id": null,
  "parent_area_name": null,
  "top_area_id": "BIZ00001",
  "top_area_name": "플랫폼",
  "top_area_level": "TOP",
  "top_area_registered_at": "2024-01-01T09:00:00+09:00"
}
```

`parent_area_id`와 `parent_area_name`은 함께 값이 있거나 함께 `null`이어야 한다.
최상위 업무영역처럼 부모가 없는 문서는 `silver_parent_area` 행을 만들지 않고
`silver_area.parent_area_id`만 `NULL`로 저장한다.

## SQLite 대상 테이블

모델은 `second_project/repository/models.py`에 정의되어 있다. 공통 계보 필드는
각 Silver 테이블에 반복해서 저장한다.

| 테이블 | PK | 주요 컬럼 및 관계 |
|---|---|---|
| `silver_employee` | `employee_id` | 직원명, 부서명, 직위명, 입사일시, 재직 여부 |
| `silver_parent_area` | `parent_area_id` | 상위 업무영역명 |
| `silver_top_area_detail` | `top_area_id` | 최상위 영역명, 레벨, 등록일시 |
| `silver_area` | `area_id` | 업무영역명, 등록일시, `manager_employee_id` → `silver_employee`, `parent_area_id` → `silver_parent_area` nullable |
| `second_project_sync_run` | `normalization_run_id` | SQLite 적재 제어 정보와 실행 상태 |

모든 Silver 테이블에는 다음 메타데이터가 있다.

| MongoDB 필드 | SQLite 컬럼 | 용도 |
|---|---|---|
| `source_record_id` | `source_record_id` | 원천 레코드 계보 |
| `dataset_id` | `dataset_id` | 데이터셋 계보 |
| `normalization_run_id` | `normalization_run_id` | 표준화 실행 단위 |
| `correction_codes` | `correction_codes` | 적용된 보정 코드 배열 |
| `_standardization` | `standardization` | 표준화 감사 정보 배열 |

`normalization_run_id`는 업무 테이블의 PK가 아니다. 업무 PK는 직원·영역의
식별자이고, 실행 ID는 어느 표준화 실행에서 유입된 값인지 추적하기 위한 제어
식별자다.

## 자동 실행 ID 선택

`--normalization-run-id`를 생략하면 로더가 다음 절차로 실행 ID를 선택한다.

1. `records.normalization_run_id` 인덱스를 확인하거나 생성한다.
2. SQLite의 `second_project_sync_run`에서 `SUCCESS`와 `RUNNING` 상태인 실행 ID를
   제외한다.
3. MongoDB 성공 collection에서 실행 ID 필드만 projection하고 오름차순 정렬한 뒤
   가장 오래된 미처리 ID 한 건을 선택한다.
4. 선택한 ID로 다음 equality 조건을 적용해 실제 표준화 문서만 조회한다.

   ```text
   { "normalization_run_id": "<자동 선택된 실행 ID>" }
   ```

이 선택·적재 단위를 미처리 실행이 더 이상 없을 때까지 반복한다. 따라서 예약
시각마다 처리하는 실행 수는 고정 1건이 아니라 당시 대기 중인 실행 수다.

파이프라인 실행 ID는 UTC 시각이 앞에 붙는 형식이므로 문자열 오름차순이 실행
순서와 일치한다. 실패(`FAILED`) 상태인 실행은 후보에 다시 포함되어 다음 cycle에
재시도된다. 처리할 실행이 없으면 `SQLite 적재 상태는 변경되지 않았습니다.`라는
메시지를 출력하고 정상적으로 다음 cycle을 기다린다.

이 방식은 매번 성공 문서 전체를 애플리케이션으로 읽지 않는다. 실행 ID 후보를
찾을 때는 ID 필드만 읽고, 실제 문서 전체 조회는 선택된 한 실행에 대해서만
수행한다.

## 실행 방법

### 상주 스케줄러

다음 명령은 `crawl_and_load`와 같은 방식으로 프로세스를 계속 실행한다. 각
예약 시각에 미처리 실행을 모두 처리한 후 다음 예약 시각까지 대기한다.

```powershell
cd C:\encore_project\2nd_project_git\django
python manage.py load_success_to_sqlite `
  --config ..\validation_pipeline\config.json
```

스케줄 시각은 KST 기준 매시 `00, 03, 06, ..., 57분 00초`다. 프로세스 시작
시각이 예약 시각이 아니면 다음 예약 경계까지 대기한다. 프로세스를 종료하려면
`Ctrl+C`를 누른다.

### 즉시 1회 실행

개발·점검 시에는 예약 시각을 기다리지 않고 한 번만 실행할 수 있다.

```powershell
python manage.py load_success_to_sqlite `
  --config ..\validation_pipeline\config.json `
  --once
```

### 명령 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--config` | `..\validation_pipeline\config.json` | validation pipeline 설정 JSON |
| `--target-alias` | `sqlite3` | SQLite를 가리키는 Django DB alias |
| `--batch-size` | sink 설정값 | MongoDB cursor와 SQLite upsert 배치 크기 |
| `--dry-run` | 꺼짐 | 선택된 실행을 검증만 하고 SQLite에 쓰지 않음 |
| `--once` | 꺼짐 | 예약을 기다리지 않고, 미처리 실행이 없을 때까지 한 cycle 실행 |
| `--normalization-run-id` | 자동 선택 | 장애 복구나 특정 실행 재처리 시 사용할 선택적 override |

일반 운영에서는 `--normalization-run-id`를 지정하지 않는다. 사람이 MongoDB에서
실행 ID를 확인해야 하는 상황은 특정 실행을 강제로 재처리할 때뿐이며, 이 경우
`--once`와 함께 사용해 한 실행만 처리하는 것을 권장한다.

`--dry-run`은 SQLite의 적재 상태를 바꾸지 않으므로 자동 drain을 수행하지 않고
선택된 미처리 실행 1건만 검증한다. 대기 중인 모든 실행을 검증하려면 실제 적재를
수행하지 않는 별도 검증 작업이 필요하다.

## 적재 처리와 중복 방지

### 모델 변환과 FK 순서

한 통합 표준 문서는 다음 순서로 SQLite 모델에 매핑된다.

```text
canonical document
    ├─ employee_id              → SilverEmployee
    ├─ parent_area_id           → SilverParentArea (부모가 있을 때)
    ├─ top_area_id              → SilverTopAreaDetail
    └─ area_id + manager/parent → SilverArea
```

외래키 오류를 막기 위해 직원과 부모 영역을 먼저 upsert하고, 최상위 영역과 업무
영역을 뒤에서 upsert한다. 부모가 없는 업무영역의 부모 FK는 `NULL`이다.

### PK 기준 upsert

- 직원 PK: `employee_id`
- 상위 영역 PK: `parent_area_id`
- 최상위 영역 PK: `top_area_id`
- 업무 영역 PK: `area_id`

동일한 PK가 같은 실행에 여러 번 나오면 업무값이 같은 경우 하나로 합친다. 같은
PK에 서로 다른 업무값이 나오면 실행 전체를 실패시킨다. PK 자체는 update 대상이
아니므로 변경되지 않는다.

이미 SQLite에 같은 PK가 있으면 업무 컬럼과 계보 메타데이터를 갱신한다. 따라서
재실행해도 중복 행이 생기지 않으며, 표준화 실행 자체는
`second_project_sync_run`의 완료 상태로 다시 처리하지 않는다.

### 트랜잭션

실제 모델 upsert와 해당 실행의 `SUCCESS` 상태 기록은 같은 SQLite transaction에서
처리된다. 오류가 발생하면 Silver 테이블 변경을 rollback하고 실행 이력을
`FAILED`로 남긴다. 다음 cycle은 같은 `normalization_run_id`를 다시 후보로 선택한다.

`second_project_sync_run`의 상태는 다음과 같다.

| 상태 | 의미 | 다음 자동 실행 |
|---|---|---|
| `RUNNING` | 다른 cycle이 현재 실행을 처리 중 | 중복 처리하지 않음 |
| `SUCCESS` | 모든 대상 upsert와 완료 기록이 끝남 | 건너뜀 |
| `FAILED` | transaction이 rollback됨 | 재시도 |

제어 테이블에는 원천 DB·collection, 원천 문서 수, 테이블별 적재 수, 시도 횟수,
시작·종료 시각, 마지막 오류가 저장된다.

## 운영 구성

### 프로세스 분리

SQLite 적재 명령은 크롤링·Bronze 로딩을 수행하지 않는다. 다음 역할을 별도
프로세스로 이해한다.

| 명령 | 역할 |
|---|---|
| `python manage.py crawl_and_load` | API 크롤링 → JSONL → Bronze MongoDB |
| `python manage.py validation_records` | Bronze MongoDB → 표준화·검증 → 성공/실패 MongoDB |
| `python manage.py load_success_to_sqlite` | 성공 MongoDB → SQLite RDB |

세 명령을 모두 상주시킬 경우 각각의 역할과 DB 연결을 확인한다. 같은 명령을 두
번 실행하지 말고, 하나의 SQLite 적재 프로세스만 운영한다. 한 cycle의 drain이
완료되기 전에는 다음 예약 시각으로 넘어가지 않는다.

### 단일 성공 collection 전제

이 로더는 사용자가 정한 운영 계약에 따라 `encore_success_experiment.records`에
통합 표준 문서가 저장되어 있다고 가정한다. 현재 validation pipeline의 일반적인
Silver sink 설정이 canonical 문서를 `silver_employee`, `silver_area`,
`silver_parent_area`, `silver_top_area_detail`로 분리 저장하도록 되어 있다면,
그 상태에서는 `records`에 로더가 기대하는 통합 문서가 없을 수 있다.

실제 운영 전에 다음 중 하나를 확정해야 한다.

- 표준화 성공 sink가 통합 표준 문서를 `encore_success_experiment.records`에
  저장하도록 구성한다.
- 성공 collection의 실제 문서 구조에 맞춰 RDB 로더의 source contract를 변경한다.

현재 RDB 로더는 요청된 단일 성공 collection 계약을 기준으로 하며, 별도
`silver_*` MongoDB collection을 읽지 않는다.

## 문제 해결

| 현상 | 확인할 내용 |
|---|---|
| `no such table: second_project_sync_run` | `python manage.py migrate --database=sqlite3` 실행 |
| 처리할 실행이 없다는 메시지 | `records`에 최상위 `normalization_run_id`가 있는지 확인 |
| 필수 canonical 필드 오류 | 성공 문서의 직원·업무영역·최상위영역 필드와 날짜 타입 확인 |
| 이미 적재 중이라는 오류 | 다른 SQLite 적재 프로세스가 실행 중인지 `second_project_sync_run`에서 확인 |
| 같은 PK의 표준값 충돌 | 동일 PK가 한 실행에 서로 다른 업무값으로 나타나는 원인 확인 |
| 성공 collection은 있는데 대상 행이 없음 | 현재 sink가 통합 문서를 `records`에 저장하는지, `silver_*`로 분리하는지 확인 |
| 성공/실패 DB 설정 오류 | `config.json`의 DB·collection 이름을 운영 계약과 비교 |

최근 SQLite 적재 상태는 Django shell에서 확인할 수 있다.

```powershell
python manage.py shell -c "from second_project.repository.models import SqliteSyncRun; print(list(SqliteSyncRun.objects.using('sqlite3').values('normalization_run_id', 'status', 'source_count', 'attempt_count', 'finished_at').order_by('-started_at')[:20]))"
```

## 구현 위치

| 역할 | 파일 |
|---|---|
| SQLite 대상 모델 | `second_project/repository/models.py` |
| 실행 상태 migration | `second_project/migrations/0004_sqlite_sync_run.py` |
| Mongo 조회·변환·upsert·재시도 | `second_project/services/success_to_sqlite.py` |
| Django 관리 명령·3분 스케줄러 | `second_project/management/commands/load_success_to_sqlite.py` |
| DB alias·Mongo 연결 | `config/settings.py` |
| 성공/실패 source 설정 | `..\validation_pipeline\config.json` |

## 사전 검증 명령

코드 변경이나 환경 변경 후 다음을 순서대로 확인한다.

```powershell
python manage.py check --database=sqlite3
python manage.py check --database=mongodb
python manage.py makemigrations second_project --check --dry-run
python manage.py migrate --database=sqlite3 --check
python manage.py load_success_to_sqlite --once --dry-run `
  --config ..\validation_pipeline\config.json
```

마지막 명령은 선택된 실행의 문서 구조와 PK 중복을 검증하지만 SQLite에는 쓰지
않는다. 실제 적재는 `--dry-run`을 제거한다.
