# 03. 품질 게이트와 라우팅

표준화 후보는 최종 표준화 결과를 기준으로 기본 validator와 Silver 배치 검증을
통과한 뒤, 설정한 업무 식별자 중복 검사까지 통과해야 정상 저장된다.
원천 문서는 이 단계보다 먼저 Bronze에 보존되므로, 실패 행도 Bronze 원문과
`source_record_id`를 기준으로 추적할 수 있다.

| 결과 | 저장 위치 | 보존 내용 |
|---|---|---|
| 품질 게이트 통과 | 네 Silver 모델별 JSONL/Mongo 컬렉션 | 표준 업무값 + 실행 메타데이터 |
| 표준화·품질 실패 | `rejected.jsonl` 또는 실패 collection | quarantine 메타데이터 + 사유 + 재처리 상태 |
| 실행 결과 | `report.json` 또는 report collection | 상태·건수·모델 PK 건수·복구율 |

검사 항목은 필수값, 타입, PK 중복, `manager_employee_id`/`parent_area_id` FK,
최상위 도메인, KST datetime이다. 실패 코드는 `REQUIRED_VALUE_MISSING`,
`PK_DUPLICATE`, `FK_ORPHAN`, `DOMAIN_UNKNOWN`, `DATETIME_PARSE_FAILED`,
`TYPE_MISMATCH` 등을 사용한다. `quality.unique_fields`가 지정된 경우에는
공백 제거·오류값 보정까지 끝난 최종 값으로 마지막에 중복을 검사하며,
실패 코드는 `DUPLICATE_FINAL_VALUE`이다.

실행 건수는 `input = success + quarantine`로 대사한다. 동일 PK의 동일 업무값은
upsert/dedup으로 재실행 가능하게 처리하고, 충돌하는 업무값은 격리한다.
실행 리포트의 RAW_DB 복구율은 Bronze 고유 `source_record_id` 대비 Silver까지
연결된 고유 ID로 계산한다.
