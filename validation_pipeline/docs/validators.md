# 검증 (`validators.py`, `silver.py`)

표준화된 문서의 기본 품질과 Silver 모델 간 관계를 검증한다.

| 검증기 | 검사 내용 | 오류 코드 |
|---|---|---|
| `NonEmptyDocumentValidator` | 빈 문서 | `REQUIRED_VALUE_MISSING` |
| `RequiredFieldsValidator` | 필수 필드 누락·null | `REQUIRED_VALUE_MISSING` |
| `FieldTypeValidator` | 설정한 기본 타입 | `TYPE_MISMATCH` |
| `validate_silver_models` | 4개 모델 필수값·PK·FK·도메인·날짜 | `PK_DUPLICATE`, `FK_ORPHAN`, `DOMAIN_UNKNOWN`, `DATETIME_PARSE_FAILED` |
| `validate_final_unique_fields` | 최종 표준화 결과의 설정 업무 식별자 중복 | `DUPLICATE_FINAL_VALUE` |

canonical Silver 문서는 한 원천 행에서 다음 모델로 분리된다.

- `silver_employee(employee_id)`
- `silver_area(area_id, manager_employee_id, parent_area_id)`
- `silver_parent_area(parent_area_id)`
- `silver_top_area_detail(top_area_id)`

동일 PK의 완전히 같은 업무값은 idempotent upsert 대상으로 허용한다. 동일 PK에
서로 다른 업무값이 있으면 관련 행을 격리한다. `manager_employee_id`와
`parent_area_id`의 고아 참조는 각각 `silver_employee`, `silver_parent_area`에
대해 검사하며 parent FK의 null은 허용한다.

`validate_final_unique_fields`는 모든 표준화·보정과 기본/Silver 검증이 끝난
마지막 단계에서 `quality.unique_fields`에 지정된 경로를 비교한다. 이 검사는
원천값이 아닌 최종 표준화값을 사용하므로, `area_no`와 `area_id`처럼 업무상
중복될 수 없는 식별자만 설정해야 한다.

실행 리포트에는 모델별 고유 PK 건수와 복구율이 포함된다. 복구율은 Bronze의
고유 `source_record_id`를 분모로, Silver까지 연결된 고유 ID를 분자로 계산한다.
Bronze 레코드 해시·원천 파일/Artifact 검증 결과도 함께 기록하며, 복구율 목표
95% 또는 Bronze 무결성 100%를 충족하지 못하면 실행은 `FAILED`다.
