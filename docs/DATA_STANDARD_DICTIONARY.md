# 데이터 표준 사전

## 1. 표준 원칙

- 영문 `snake_case`를 사용한다.
- 식별자는 문자열로 보존하고 대문자·구분자 규칙을 표준화한다.
- 날짜·시간은 파싱 후 `YYYY-MM-DDTHH:MM:SS+09:00` 형식으로 저장한다.
- 원본값은 Bronze에서 변경하지 않는다.
- Silver의 모든 변환은 `correction_codes`와 로그로 추적한다.
- 이름 등 개인정보는 로그에 평문으로 남기지 않는다.

## 2. 표준 컬럼

| 원천 컬럼 | Silver 표준 컬럼 | 타입 | 필수 | 규칙 |
|---|---|---|---|---|
| `mgr_no` | `employee_id` | string | Y | `EMP` + 6자리 숫자, 대문자 표준화 |
| `mgr_nm` | `employee_name` | string | Y | 양끝·중복·전각 공백 정리, 로그 마스킹 |
| `mgr_dept_nm` | `department_name` | string | Y | 유니코드 NFC, 공백 정규화 |
| `mgr_pos_nm` | `position_name` | string | Y | 공백·유니코드 정규화 |
| `mgr_hire_dtm` | `hire_datetime` | datetime | Y | 승인된 다중 포맷 파싱 |
| `mgr_act_yn` | `is_active` | boolean | Y | 활성 사전 매핑, 미매핑은 격리 |
| `area_no` | `area_id` | string | Y | `BIZ` + 5자리 숫자, 대문자·구분자 표준화 |
| `area_nm` | `area_name` | string | Y | 유니코드 NFC, 공백 정규화 |
| `area_reg_dtm` | `area_registered_at` | datetime | Y | 승인된 다중 포맷 파싱 |
| `p_area_no` | `parent_area_id` | string | 조건부 | 코드 형식 표준화, 참조 검증 |
| `p_area_nm` | `parent_area_name` | string | 조건부 | 공백·유니코드 정규화 |
| `top_area_no` | `top_area_id` | string | Y | 코드 형식 표준화 |
| `top_area_nm` | `top_area_name` | string | Y | 공백·탭·유니코드 정규화 |
| `top_area_lvl` | `top_area_level` | string | Y | `TOP`으로 표준화, 미매핑은 격리 |
| `top_area_reg_dtm` | `top_area_registered_at` | datetime | Y | 승인된 다중 포맷 파싱 |
| `source.record_id` | `source_record_id` | string | Y | Bronze-Silver 계보 키, 값 변경 금지 |
| `dataset_id` | `dataset_id` | string | Y | 원천 데이터셋 식별자, 값 변경 금지 |
| `normalization_run_id` | `normalization_run_id` | string | Y | 정규화 실행별 유일 ID |
| - | `correction_codes` | array<string> | Y | 적용된 정규화 코드 목록 |

## 3. 코드 도메인

### 3.1 활성 상태

| 원천값 예 | Silver 값 | 보정 코드 |
|---|---|---|
| `Y`, `YES`, `사용`, `재직` | `true` | `ACTIVE_STATUS_NORMALIZED` |
| `N`, `NO`, `미사용`, `비활성` | `false` | `ACTIVE_STATUS_NORMALIZED` |
| 그 외 | Quarantine | `ACTIVE_STATUS_UNKNOWN` |

`false`를 퇴직으로 단정하지 않는다. 퇴직 이벤트 판정 규칙은 별도 승인 전까지 미결이다.

### 3.2 최상위 레벨

| 원천값 예 | Silver 값 | 보정 코드 |
|---|---|---|
| `TOP`, `TOP_LEVEL`, `top_level`, `최상위`, `1` | `TOP` | `TOP_LEVEL_NORMALIZED` |
| 그 외 | Quarantine | `TOP_LEVEL_UNKNOWN` |

## 4. 정규화 보정 코드

| 코드 | 의미 |
|---|---|
| `ACTIVE_STATUS_NORMALIZED` | 활성 상태 표준화 |
| `CODE_FORMAT_NORMALIZED` | 식별자 대소문자·구분자 표준화 |
| `DATETIME_FORMAT_NORMALIZED` | 날짜·시간 포맷 표준화 |
| `WHITESPACE_NORMALIZED` | 양끝·중복·전각 공백 정리 |
| `UNICODE_NORMALIZED` | 유니코드 NFC 변환 |
| `TAB_CHARACTER_ERROR` | 탭 문자 발견 및 처리 |
| `TOP_LEVEL_NORMALIZED` | 최상위 레벨 표준화 |
| `DATE_CONFLICT` | 동일 의미의 등록일 충돌, 기준 원본 TBD |

## 5. 개인정보 처리

- `employee_name`은 Silver 업무 테이블에 보존할 수 있으나 접근 권한을 제한한다.
- 로그와 오류 메시지에는 `employee_id`를 마스킹하거나 `source_record_id`만 기록한다.
- 샘플·발표자료에는 실명 대신 비식별 값을 사용한다.

