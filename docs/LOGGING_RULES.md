# 로그 규칙 정의

## 1. 목적

원천 데이터 수집부터 Bronze Layer 저장, 데이터 표준화·품질 검증·정규화, Silver Layer 적재까지 모든 실행 상태를 추적할 수 있도록 공통 로그 규칙을 정의한다.

로그를 통해 다음 내용을 확인할 수 있어야 한다.

* 각 단계의 실행 성공/실패 여부
* 단계별 입력·성공·실패·격리 건수
* 품질 검증 실패 원인
* 오류 발생 단계 및 오류 코드
* `run_id`를 이용한 전체 파이프라인 실행 추적
* Bronze 원본 데이터와 후처리 데이터 간 계보 추적
* RAW_DB 복원율 및 Bronze 원본 무결성

현재 구현 범위는 **Bronze → Silver Layer까지**이며, Gold Layer 관련 로그는 Gold 구조 확정 후 추가한다.

---

## 2. 로그 저장 형식

로그는 **UTF-8 JSON Lines(`.jsonl`)** 형식을 기본으로 사용한다.

JSON Lines는 **한 줄에 하나의 JSON 객체를 저장하는 형식**이다.

예:

```json
{"timestamp":"2026-08-27T10:00:00+09:00","level":"INFO","run_id":"RUN-001","stage":"ingest","message":"크롤링 시작"}
{"timestamp":"2026-08-27T10:01:00+09:00","level":"INFO","run_id":"RUN-001","stage":"ingest","message":"크롤링 완료"}
{"timestamp":"2026-08-27T10:01:10+09:00","level":"INFO","run_id":"RUN-001","stage":"bronze","message":"MongoDB 적재 시작"}
```

일반 JSON 배열과 달리 로그 이벤트가 발생할 때마다 파일 마지막에 한 줄씩 추가할 수 있어 파이프라인 로그 관리에 적합하다.

---

## 3. 기본 원칙

* 모든 로그는 UTF-8 JSON Lines(`.jsonl`) 형식으로 기록한다. 
* 모든 이벤트에 KST 오프셋을 포함한 ISO 8601 시각을 기록한다.
* 하나의 파이프라인 실행에는 동일한 `run_id`를 사용한다.
* 단계는 `stage`를 통해 구분한다.
* 실행 상태는 `success`, `partial_failure`, `failed` 중 하나를 사용한다.
* 모든 단계는 동일한 로그 필드와 상태 정의를 사용한다.
* 오류가 발생해도 Bronze 원본 데이터는 삭제하거나 덮어쓰지 않는다.
* 개인정보, credential, private endpoint, 원본 전체 payload는 로그에 기록하지 않는다.
* 원본 확인이 필요한 경우 `source_record_id` 또는 Bronze 원본 참조 정보만 기록한다.
* 재실행할 경우 새로운 `run_id`를 발급하고 이전 로그는 보존한다.

---

## 4. 로그 레벨

| Level | 의미 | 사용 기준 |
|-------|------|---------|
| `INFO` | 정상적인 실행 흐름 및 처리 결과 | 단계 시작·종료, 연결 성공, 처리 완료, 정상 처리 건수 |
| `WARNING` | 문제가 발생했지만 실행 가능한 상태 | 품질 규칙 위반, 격리 발생, 일부 데이터 처리 실패 |
| `ERROR` | 오류로 인해 정상적인 실행이 불가능한 상태 | DB 연결 실패, 프로그램 오류, 적재 중단, 건수 불일치 등 |

데이터 한 건이 품질 규칙을 통과하지 못해 정상적으로 Quarantine 처리된 경우는 프로그램 장애가 아니므로 기본적으로 `WARN`으로 기록한다.

---

## 5. 공통 필드

| 필드                 | 필수  | 설명                                     |
| ------------------ | --- | -------------------------------------- |
| `timestamp`        | Y   | KST 포함 ISO 8601 시각                     |
| `level`            | Y   | `INFO`, `WARN`, `ERROR`                |
| `run_id`           | Y   | 파이프라인 실행 ID                            |
| `stage`            | Y   | 현재 처리 단계                               |
| `dataset_id`       | Y   | 데이터셋 식별자                               |
| `status`           | Y   | `success`, `partial_failure`, `failed` |
| `input_count`      | Y   | 단계 입력 건수                               |
| `success_count`    | Y   | 정상 처리 건수                               |
| `failure_count`    | Y   | 처리 실패 후 격리되지 않은 건수                     |
| `quarantine_count` | Y   | 품질 문제 등으로 격리된 건수                       |
| `duration_ms`      | Y   | 처리 시간(ms)                              |
| `source_record_id` | 조건부 | 레코드 단위 원본 추적 키                         |
| `rule_id`          | 조건부 | 실패한 품질검증 규칙 ID                         |
| `error_code`       | 조건부 | 표준 오류 코드                               |
| `correction_codes` | 조건부 | 표준화 과정에서 적용된 보정 코드                     |
| `message`          | Y   | 개인정보를 제거한 로그 내용                        |

### 건수 검증 규칙

각 단계의 처리 건수는 다음 관계를 만족해야 한다.

```text
input_count
=
success_count
+ failure_count
+ quarantine_count
```

각 항목은 중복 집계하지 않는다.

* `success_count` : 정상적으로 다음 단계로 전달된 데이터
* `failure_count` : 처리에 실패했으며 Quarantine에도 저장되지 않은 데이터
* `quarantine_count` : 품질 문제 등으로 별도 격리된 데이터

건수가 일치하지 않으면 `ROW_COUNT_MISMATCH` 오류로 처리한다.

---

## 6. Stage 정의

| Stage        | 파이프라인 단계         | 주요 담당 |
| ------------ | ---------------- | ----- |
| `ingest`     | RAW 데이터 크롤링      | 이여찬   |
| `bronze`     | MongoDB 원본 적재    | 이여찬   |
| `silver`     | RAW 데이터 표준화      | 김세진   |
| `quality`    | 데이터 품질 검증        | 김세진   |
| `quarantine` | 품질 실패 데이터 격리     | 김세진   |
| `load`       | 정규화 데이터 MySQL 적재 | 김건우   |

정규화 구조 자체는 팀 전체의 공동 설계 결과이므로 실행 로그보다 **ERD 및 설계 의사결정 문서**에서 관리한다.

---

## 7. 로그 파일

```text
log_lake/
└── raw_data/
    ├── crawling_log.jsonl
    └── raw_data_loading_log.jsonl

logs/
├── quality.jsonl
├── quarantine.jsonl
└── restoration.jsonl
```

| 파일                       | 기록 내용                                       |
| ------------------------ | ------------------------------------------- |
| `log_lake/raw_data/crawling_log.jsonl` | RAW 데이터 크롤링 실행 상태(`stage=ingest`) |
| `log_lake/raw_data/raw_data_loading_log.jsonl` | Bronze 원본 적재 실행 상태(`stage=bronze`) |
| `logs/quality.jsonl`     | 품질 검증 규칙별 통과·실패·격리 결과                       |
| `logs/quarantine.jsonl`  | 격리 데이터 ID, 원본 참조, 실패 규칙 및 오류 코드             |
| `logs/restoration.jsonl` | RAW_DB 복원율 및 Bronze 원본 무결성 검증 결과            |

### 담당별 기록

```text
log_lake/raw_data/crawling_log.jsonl
└─ 이여찬 : ingest

log_lake/raw_data/raw_data_loading_log.jsonl
└─ 이여찬 : bronze

logs/quality.jsonl
└─ 김세진 : quality

logs/quarantine.jsonl
└─ 김세진 : quarantine

logs/restoration.jsonl
└─ RAW_DB 복원율 / Bronze 무결성 검증
```

---

## 8. 품질 오류 코드

| 코드                       | 의미              | 처리            |
| ------------------------ | --------------- | ------------- |
| `REQUIRED_VALUE_MISSING` | 필수값 누락          | Quarantine    |
| `PK_DUPLICATE`           | PK 중복           | Quarantine    |
| `FK_ORPHAN`              | FK 미매칭          | Quarantine    |
| `DOMAIN_UNKNOWN`         | 코드 도메인 미매핑      | Quarantine    |
| `DATETIME_PARSE_FAILED`  | 날짜 파싱 실패        | Quarantine    |
| `CHECKSUM_MISMATCH`      | 원본 해시 불일치       | 실행 실패 및 원본 조사 |
| `DATE_CONFLICT`          | 동일 의미 날짜값 충돌    | 원본 보존 및 결정 대기 |
| `ROW_COUNT_MISMATCH`     | 입력·출력·격리 건수 불일치 | 실행 실패         |
| `SOURCE_PARSE_FAILED`    | 입력 문서 파싱 실패       | Quarantine    |
| `TYPE_MISMATCH`          | 표준 필드 타입 불일치     | Quarantine    |
| `STANDARDIZATION_FAILED` | 표준화 규칙 적용 실패     | Quarantine    |
| `PIPELINE_ERROR`         | 분류되지 않은 파이프라인 오류 | 실행 실패/조사 |

오류 코드는 팀 전체가 동일한 값을 사용하며 담당자가 임의로 새로운 이름을 만들어 사용하지 않는다.

새 오류 코드가 필요한 경우 공통 규칙에 추가한 후 사용한다.

---

## 9. 품질검증 로그 예시

```json
{"timestamp":"2026-08-27T15:01:00+09:00","level":"WARN","run_id":"RUN-001","stage":"quality","dataset_id":"DATASET-001","status":"partial_failure","input_count":100,"success_count":99,"failure_count":0,"quarantine_count":1,"rule_id":"SILVER-DATETIME-001","error_code":"DATETIME_PARSE_FAILED","source_record_id":"MASKED-001","duration_ms":120,"message":"Unparseable datetime; record moved to quarantine"}
```

위 로그를 통해 다음 내용을 확인할 수 있다.

```text
RUN-001 실행
→ quality 단계
→ 입력 100건
→ 정상 99건
→ 격리 1건
→ 날짜 파싱 실패
→ SILVER-DATETIME-001 규칙 위반
```

---

## 10. 복원·무결성 로그

RAW_DB 복원율과 Bronze 원본 무결성을 확인하기 위해 별도의 복원 로그를 기록한다.

```json
{"timestamp":"2026-08-27T15:00:00+09:00","level":"INFO","run_id":"RUN-001","stage":"quality","dataset_id":"DATASET-001","status":"success","bronze_distinct_source_count":10000,"silver_recovered_source_count":9600,"restoration_rate":0.96,"target_rate":0.95,"bronze_integrity_rate":1.0,"message":"RAW_DB restoration gate passed"}
```

실제 건수와 비율은 실행 결과에서 계산하며 문서의 예시 숫자를 실제 검증 결과로 사용하지 않는다.

---

## 11. 개인정보·보안

* `employee_name` 등 실명은 로그에 기록하지 않는다.
* 원본 전체 payload를 로그에 기록하지 않는다.
* 비밀번호, API Token, credential을 기록하지 않는다.
* private endpoint 및 내부 접속 정보를 기록하지 않는다.
* `employee_id` 등 식별자가 필요한 경우 마스킹된 값을 사용한다.
* 예외 Stack Trace는 credential 및 내부 경로를 제거한 뒤 저장한다.
* 로그 접근 권한은 최소 권한 원칙을 적용한다.

---

## 12. 보관 및 운영

* 로그는 실행일과 `run_id`를 기준으로 추적 가능해야 한다.
* 기존 로그를 임의로 수정하거나 삭제하지 않는다.
* 재실행 시 새로운 `run_id`를 발급한다.
* 재실행하더라도 이전 로그와 Bronze 원본은 보존한다.
* 로그 파일이 일정 크기를 초과하면 Log Rotation을 적용한다.
* Rotation 이후에도 `run_id`를 기준으로 과거 실행을 조회할 수 있어야 한다.
* 로그 보관 기간은 개인정보 및 운영 정책 확정 후 결정한다.

---

## 13. 공통 운영 원칙

```text
한 번의 Pipeline 실행
        ↓
    run_id 발급
        ↓
① ingest
        ↓
② bronze
        ↓
③ silver
        ↓
④ quality
    ├─ PASS ───────→ ⑤ 정규화
    │                    ↓
    │                ⑥ load
    │                    ↓
    │              Silver Layer
    │
    └─ FAIL
         ↓
     quarantine

모든 실행 결과
        ↓
    *.jsonl 기록
        ↓
run_id로 전체 실행 추적
```

**모든 담당자는 동일한 `run_id`, `stage`, `status`, 로그 레벨, 오류 코드 및 공통 필드 규칙을 준수한다.**
