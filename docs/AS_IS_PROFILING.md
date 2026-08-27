# AS-IS 데이터 프로파일링

## 1. 분석 범위

| 구분 | 관찰 결과 | 역할 판단 |
|---|---|---|
| 통합 레거시 Google Sheet | 15개 업무 컬럼, 비표준 코드·상태·날짜 혼재 | 수집 전 원천 데이터 |
| `mlo_project2_bronze.raw_records.csv` | `raw_json`, 원본 행 번호, 원본 해시, 적재시각 포함 | Bronze 원본 보존·복원 검증 기준 |
| `26.08.26 정규화DB.xlsx` | 업무 컬럼과 정규화 실행 ID·보정 코드 포함 | 정규화 결과 |

실행 시점(2026-08-27)에 Bronze CSV와 정규화DB 파일은 지정 폴더에서 더 이상 확인되지 않았다. 아래 내용은 사전 점검에서 확인된 구조를 근거로 하며, 최종 수치 증적은 파일을 복구한 뒤 재실행해야 한다.

## 2. 통합 레거시 원천 구조

```text
area_no, area_nm, p_area_no, p_area_nm,
top_area_no, top_area_nm, top_area_lvl,
mgr_no, mgr_nm, mgr_dept_nm, mgr_pos_nm,
mgr_hire_dtm, mgr_act_yn,
area_reg_dtm, top_area_reg_dtm
```

### 관찰된 품질 문제

| 규칙 영역 | 관찰 예 | 위험 |
|---|---|---|
| 상태 코드 | `재직`, `Y`, `사용`, `YES` | 활성 관리자 필터 오판 |
| 최상위 코드 | `최상위`, `1`, `top_level`, `TOP_LEVEL` | 계층 레벨 집계 오류 |
| 식별자 형식 | `biz_26652`, `BIZ11608`, `emp000038` | 조인 실패·중복 발생 |
| 날짜 형식 | `/`, `.`, ISO, 지수 표기 혼재 | 날짜 파싱 실패·근속 오계산 |
| 공백·유니코드 | 전각 공백, 탭, 문자열 내부 공백 | 코드·명칭 비교 실패 |

## 3. Bronze 구조 관찰

Bronze CSV에서 확인한 주요 필드:

- 메타데이터: `_id`, `record_id`, `dataset_id`, `ingested_at_kst`
- 원본 보존: `raw_json`
- 계보: `source.record_id`, `source.source_row_no`, `source.release_slot`
- 무결성: `source.source_record_sha256`
- 업무 데이터: `source.payload.*`

Bronze는 원천을 수정하지 않고 원문과 적재 메타데이터를 보존하므로 RAW_DB 복원율의 분모로 사용한다.

## 4. 정규화 결과 관찰

사전 점검 당시 정규화DB는 단일 시트 `complete_rowdb_20260826_0945_ks`, 사용 범위 `A1:Z11267`로 확인됐다. 헤더를 제외하면 11,266행이며 주요 추가 필드는 다음과 같다.

- `normalization_run_id`
- `normalized_at`
- `correction_codes`

이 수치는 현재 파일 부재로 재검증되지 않았으므로 최종 증적에서 다시 산출한다.

## 5. 핵심 데이터 리스크

1. 공식 가이드의 원본 4개 파일이 현재 분리된 형태로 확인되지 않는다.
2. 통합 원천과 Bronze의 전체 건수 대조가 아직 완료되지 않았다.
3. `REG_DT` 충돌 시 기준 원본이 추후 결정(TBD) 상태다.
4. `mgr_act_yn=N`이 퇴직만 의미하는지 비활성·휴직을 포함하는지 확정되지 않았다.
5. Gold 및 추천 피처 구조가 보류되어 추천 정확도 평가는 현재 범위 밖이다.

## 6. 프로파일링 완료 조건

- Bronze 전체 행 수와 고유 `source.record_id` 수 산출
- 중복·누락·해시 불일치 건수 산출
- 정규화 결과와 `record_id` 기준 매칭
- RAW_DB 복원율 95% 이상 확인
- Silver 테이블별 PK/FK·필수값·도메인·날짜 품질 결과 저장
- `REG_DT` 충돌 건수와 원본값을 변경 없이 별도 보고

