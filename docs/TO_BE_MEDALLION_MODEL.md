# TO-BE Medallion 데이터 모델

## 1. 범위와 상태

| 계층 | 상태 | 이번 단계 범위 |
|---|---|---|
| Bronze | 확정 | 원본 불변 보존, manifest, 계보, 체크섬 |
| Silver | 확정 | 표준 컬럼·타입·코드, 품질 게이트, 격리 |
| Gold | 확정·구현 완료 | 추천 피처·후보 점수·서빙 구조 및 화면 구현 완료 |

## 2. 전체 구조

```mermaid
flowchart LR
    SRC["통합 레거시 원천"] --> BR["Bronze raw_records"]
    BR --> MF["Bronze manifest"]
    BR --> QG{"Silver 품질 게이트"}
    QG -->|PASS| EMP["silver_employee"]
    QG -->|PASS| AREA["silver_area"]
    QG -->|PASS| PAR["silver_parent_area"]
    QG -->|PASS| TOP["silver_top_area_detail"]
    QG -->|FAIL| QUAR["quarantine_records"]
    EMP -. 추후 .-> GOLD["Gold/추천 피처 TBD"]
    AREA -. 추후 .-> GOLD
```

## 3. Bronze 설계

### 3.1 원본 저장 원칙

- 원천 payload를 `raw_json`으로 변경 없이 보존한다.
- 재수집 또는 재실행 시 동일 원본을 덮어쓰지 않는다.
- `source_record_id`, `source_row_no`, SHA-256으로 원본을 추적한다.
- `source_record_sha256`은 원천 문서를 canonical JSON(`sort_keys=true`,
  `ensure_ascii=false`, `separators=(',', ':')`)으로 직렬화한 UTF-8 바이트를
  SHA-256으로 계산한다.
- 수집 성공뿐 아니라 `partial_failure`, `failed`도 manifest에 기록한다.
- Bronze 원본 보존 무결성 목표는 100%다.

### 3.2 `bronze_raw_records`

| 컬럼 | 타입 | 제약/설명 |
|---|---|---|
| `record_id` | string | Bronze 레코드 PK |
| `dataset_id` | string | 데이터셋 식별자 |
| `source_record_id` | string | 원천 레코드 ID, 복원율 대조 키 |
| `source_row_no` | integer | 원천 행 번호 |
| `scheduled_release_at` | datetime | 원천 배포 예정시각 |
| `ingested_at` | datetime | KST 포함 적재시각 |
| `raw_json` | json/string | 원천 payload 불변 보존 |
| `source_record_sha256` | string | 원천 레코드 SHA-256 |
| `run_id` | string | 수집 실행 ID |

### 3.3 manifest

manifest는 `manifest-schema.json`을 정본으로 사용하며 최소한 다음을 기록한다.

`run_id`, `source_name`, `source_uri`, `collected_at`, `ingest_date`, `raw_path`, `content_type`, `file_size_bytes`, `row_count`, `checksum_sha256`, `http_status`, `retry_count`, `crawler_version`, `status`

## 4. Silver 설계

### 4.1 논리 ERD

```mermaid
erDiagram
    PARENT_AREA ||--o{ AREA : "하나의 상위영역에 여러 하위영역"
    PARENT_AREA ||--|| TOP_AREA_DETAIL : "동일 조직번호로 1:1 결합"
    EMPLOYEE ||--o{ AREA : "한 관리자가 여러 영역 담당"

    AREA {
        string area_id PK
        string area_name
        datetime area_registered_at
        string parent_area_id FK
        string manager_employee_id FK
        string source_record_id
    }

    PARENT_AREA {
        string parent_area_id PK
        string parent_area_name
    }

    TOP_AREA_DETAIL {
        string top_area_id PK
        string top_area_name
        string top_area_level
        datetime top_area_registered_at
    }

    EMPLOYEE {
        string employee_id PK
        string employee_name
        string department_name
        string position_name
        datetime hire_datetime
        boolean is_active
    }
```

### 4.2 Silver 테이블

| 테이블 | PK | 주요 FK | 설명 |
|---|---|---|---|
| `silver_employee` | `employee_id` | - | 관리자/직원 표준 정보 |
| `silver_area` | `area_id` | `parent_area_id`, `manager_employee_id` | 업무영역과 담당 관리자 |
| `silver_parent_area` | `parent_area_id` | - | 상위영역 |
| `silver_top_area_detail` | `top_area_id` | - | 최상위영역 상세 |
| `silver_area_join_reference` | 복합키 TBD | 원본 4개 확보 후 확정 | 공식 join-ready 비교용, 현재 조건부 |
| `quarantine_records` | `quarantine_id` | `source_record_id` | 품질 실패 원본과 오류코드 |

### 4.3 관계 규칙

- `silver_area.manager_employee_id` → `silver_employee.employee_id`
- `silver_area.parent_area_id` → `silver_parent_area.parent_area_id`
- `silver_parent_area.parent_area_id` = `silver_top_area_detail.top_area_id`의 1:1 결합은 현재 ERD 가정이며, 실제 코드 체계 검증 후 확정한다.
- FK 미매칭은 승인된 예외가 아니면 Quarantine으로 이동한다.

## 5. 변환 순서

1. `raw_json`을 파싱하되 Bronze 값은 변경하지 않는다.
2. 컬럼명을 `DATA_STANDARD_DICTIONARY.md` 기준으로 변환한다.
3. 코드·공백·유니코드·날짜를 표준화한다.
4. 원천값과 보정 코드를 계보에 기록한다.
5. 필수값·PK·FK·도메인·날짜 품질 게이트를 실행한다.
6. 통과 레코드를 Silver에 적재하고 실패 레코드를 Quarantine에 저장한다.
7. Bronze와 Silver의 고유 `source_record_id`를 대조해 RAW_DB 복원율을 산출한다.

## 6. 품질 게이트

| Gate | 통과 기준 |
|---|---|
| 행 수 대사 | `input = pass + quarantine` |
| RAW_DB 복원율 | 95% 이상 |
| Bronze 보존 | 원본 누락·변조 0건 |
| PK | 중복 0건 |
| 필수값 | 승인되지 않은 누락 0건 |
| FK | 승인되지 않은 고아 0건 |
| 도메인 | 미매핑 코드 0건 |
| 날짜 | 파싱 실패 0건 |
| join-ready 비교 | 공식 비교 데이터 확보 시 불일치 0건 |

## 7. `REG_DT` 충돌 정책

- 충돌 시 기준 원본은 추후 결정(TBD)이다.
- 결정 전에는 어떤 값도 임의로 선택하거나 덮어쓰지 않는다.
- 원본별 값을 Bronze에 보존하고 Silver에는 `DATE_CONFLICT` 품질 플래그를 남긴다.
- 기준 확정 후 결정 로그, 변경 전후 값, 회귀검증 결과를 함께 기록한다.

## 8. Gold 보류

다음 항목은 이번 단계에서 설계·구현하지 않는다.

- 관리자 근속 파생변수
- 업무·부서·직위 유사도 점수
- 관리자별 담당 영역 수
- `area_manager_features`
- 후보 Top-K·최소점수·서빙 인터페이스

Gold 설계 시에는 `as_of_date`를 명시하고 퇴직 이후 정보를 학습·평가에 사용하지 않도록 데이터 누수를 방지한다.

