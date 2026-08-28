# PRD · 퇴직 관리자 대체인력 후보 추천 데이터 기반 구축

- document_id: `PRD-HR-RM-001`
- version: `v1.2`
- document_state: `Draft`
- brd_reference: `BRD-HR-RM-001@v1.2`
- team: `mlo-01-p2-team2`
- current_scope: `Bronze + Silver + Gold`
- gold_status: `implemented`

## 1. 제품 정의

현재 제품은 통합 레거시 원천을 추적 가능한 Bronze로 보존
표준 Silver 데이터로 변환한 뒤 Gold 추천 결과까지 제공
Gold 구조와 추천 피처는 구현 완료

## 2. 사용자 필요

| ID | 사용자 필요 |
|---|---|
| `UN-HR-001` | 인사 담당자는 대체인력 후보 검토에 사용할 직원·업무영역 표준 데이터와 추천 결과를 확인하고 싶다. |
| `UN-OPS-001` | 운영 담당자는 원천부터 Silver까지 실패 위치와 재처리 대상을 확인하고 싶다. |
| `UN-QA-001` | 품질·검토 담당자는 RAW_DB 복원율, 원본 무결성, 레코드 계보, 변경 이유와 격리 사유를 확인하고 싶다. |

## 3. 기능 요구사항

| ID | 우선순위 | 요구사항 | 상태 |
|---|---|---|---|
| `FR-BRONZE-001` | Must | 원천 payload와 메타데이터를 변경 없이 Bronze에 저장한다. | active |
| `FR-MANIFEST-001` | Must | 실행별 manifest에 원본 경로·시각·크기·건수·SHA-256·상태를 기록한다. | active |
| `FR-LINEAGE-001` | Must | `source_record_id`로 Bronze와 Silver를 연결한다. | active |
| `FR-STANDARDIZE-001` | Must | 컬럼명, 코드, 공백, 유니코드, 날짜를 표준화한다. | active |
| `FR-SILVER-MODEL-001` | Must | `silver_employee`, `silver_area`, `silver_parent_area`, `silver_top_area_detail`을 생성한다. | active |
| `FR-QUALITY-001` | Must | PK·필수값·FK·도메인·날짜·행 수 대사를 검증한다. | active |
| `FR-QUARANTINE-001` | Must | 실패 레코드를 오류코드와 Bronze 참조와 함께 격리한다. | active |
| `FR-RESTORE-001` | Must | Bronze 고유 원천 레코드 대비 RAW_DB 복원율을 계산한다. | active |
| `FR-LOG-001` | Must | 실행·품질·복원·격리 결과를 JSON Lines로 기록한다. | active |
| `FR-GOLD-001` | Must | Gold·추천 피처·후보 점수 구조를 구현한다. | implemented |

## 4. 비기능 요구사항

| ID | 요구사항 | 합격 기준 |
|---|---|---|
| `NFR-INTEGRITY-001` | Bronze 원본을 변조 없이 보존한다. | 무결성 100% |
| `NFR-RESTORE-001` | RAW_DB를 목표 수준으로 복원한다. | 복원율 95% 이상 |
| `NFR-IDEMP-001` | 동일 원본 재실행이 중복 업무 레코드를 만들지 않는다. | 동일 입력 2회 실행 후 PK 중복 0건 |
| `NFR-TRACE-001` | 레코드와 실행 계보를 추적한다. | Silver의 모든 행이 Bronze `source_record_id`와 연결 |
| `NFR-PRIVACY-001` | 로그에 실명·원문·credential을 남기지 않는다. | 민감정보 검사 위반 0건 |
| `NFR-OBS-001` | 성공·부분실패·실패 상태와 건수를 기록한다. | 필수 로그 필드 누락 0건 |

## 5. Acceptance Criteria

| AC ID | 소분류 | Given | When | Then | Evidence |
|---|---|---|---|---|---|
| `AC-BRONZE-001` | 원문 보관·행 번호·SHA-256·manifest | 승인된 원천 파일/시트 | Bronze 적재 실행 | 원문, 행 번호, SHA-256 해시와 manifest가 저장되고 누락 0건 | manifest, `django/log_lake/raw_data/raw_data_loading_log.jsonl` |
| `AC-INTEGRITY-001` | SHA-256 대조·행 수·컬럼 수 대조 | Bronze 적재 완료 | 해시·건수·컬럼 수 검증 실행 | 원천과 Bronze의 무결성 비율 100% | `django/log_lake/standardized/restoration.jsonl` |
| `AC-SILVER-001` | 컬럼명·코드·날짜·NULL·변환 이력 | Bronze 원천 | 표준화 실행 | 표준 스키마에 적재되고 모든 변환 코드가 추적됨 | Silver 결과, `correction_codes` |
| `AC-QUALITY-001` | 필수값·PK·FK·도메인·날짜·행 수 대사 | Silver 후보 | 품질 게이트 실행 | 입력 건수 = 통과 건수 + 격리 건수, 승인되지 않은 치명 오류 0건 | `django/log_lake/standardized/quality.jsonl`, `django/log_lake/standardized/quarantine.jsonl` |
| `AC-RESTORE-001` | `source_record_id` 대조·전체 Bronze 행 분모·복원율 | Bronze와 Silver 실행 결과 | `source_record_id` 대조 실행 | Bronze 전체 고유 원천 레코드 대비 RAW_DB 복원율 95% 이상 | `django/log_lake/standardized/restoration.jsonl` |
| `AC-IDEMP-001` | checksum·업무 PK·run_id | 동일 checksum 원천 | 동일 파이프라인 2회 실행 | 업무 PK 중복 추가 0건, 실행별 `run_id`가 분리됨 | 재실행 검증 로그 |
| `AC-REGDT-001` | 원본 보존·`DATE_CONFLICT`·임의 덮어쓰기 금지 | 충돌하는 등록일 | Silver 변환 | 원본값을 보존하고 `DATE_CONFLICT`를 기록하며 임의로 덮어쓰지 않음 | 품질 로그·격리/경고 결과 |
| `AC-GOLD-001` | Gold 구조·추천 피처·후보 점수·결과 검증 | Gold 구현 완료 | 결과 검토 실행 | Gold 구조와 추천 후보 결과가 구현되고 검증됨 | Gold 결과, BRD·PRD·설계 문서 |

## 6. 데이터 계약

- 표준 컬럼 정본: [`docs/DATA_STANDARD_DICTIONARY.md`](DATA_STANDARD_DICTIONARY.md)
- 모델 정본: [`docs/TO_BE_MEDALLION_MODEL.md`](TO_BE_MEDALLION_MODEL.md)
- 품질 규칙 정본: [`config/validation-rules.yaml`](../config/validation-rules.yaml)
- manifest 정본: [`config/manifest-schema.json`](../config/manifest-schema.json)
- 로그 정본: [`docs/LOGGING_RULES.md`](LOGGING_RULES.md)

## 7. 실패 처리

| 실패 | 처리 |
|---|---|
| 체크섬 불일치 | 실행 실패, Bronze 원본 조사, 덮어쓰기 금지 |
| 필수값·PK·FK·도메인·날짜 오류 | Quarantine, 오류코드와 원본 참조 기록 |
| `REG_DT` 충돌 | 경고·`DATE_CONFLICT`, 기준 확정 전 원본 유지 |
| 행 수 대사 불일치 | 실행 실패, 통과·실패·격리 건수 재검증 |
| 복원율 95% 미만 | 인수 실패, 누락 `source_record_id` 목록 산출 |

## 8. 추후 반영기획

Gold 구현 이후 다음 항목을 고도화한다.

- 퇴직 이벤트의 정확한 업무 정의
- 업무·부서·직위·근속 점수 규칙
- 근속 계산 `as_of_date`
- 후보 Top-K와 최소점수
- 과거 선정 사례와 현업 평가 방법
- 개인정보 노출·승인·감사 정책

