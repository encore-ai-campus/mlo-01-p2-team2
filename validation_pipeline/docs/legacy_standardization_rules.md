# 레거시 조직 컬럼 계약

이 문서는 레거시 표준화 DB의 **컬럼명과 컬럼 순서만** 설명한다.

- 첨부 파일의 행 데이터는 사용하지 않는다.
- 샘플 값, 허용값 목록, enum 매핑, 보정 코드, 오류 통계는 정의하지 않는다.
- 첨부 파일은 수정하지 않는다.
- 실제 값 변환·필수성·도메인 검증은 승인된 업무 YAML에서 별도로 정의한다.

현재 column contract의 출력 순서는 다음과 같다.

```text
id
source_document_id
dataset_id
record_id
source_row_no
scheduled_release_at
crawl_run_id
ingested_at
mgr_no
mgr_nm
mgr_act_yn
mgr_pos_nm
mgr_dept_nm
mgr_hire_dtm
area_no
area_nm
area_reg_dtm
p_area_no
p_area_nm
top_area_no
top_area_nm
top_area_lvl
top_area_reg_dtm
normalization_run_id
normalized_at
correction_codes
```

실행 scaffold는 [`rules/legacy_org_flat.yaml`](../rules/legacy_org_flat.yaml)이다.
이 파일은 출력 projection만 가지고 있으며, 컬럼의 실제 원천 경로 매핑과 값
변환 규칙을 확정하지 않는다. MongoDB 기술 식별자 `_id`는 업무 컬럼 `id`와
구분해 보존한다.

## 운영 적용 순서

1. 실제 원천 컬렉션의 문서 구조와 각 컬럼의 원천 경로를 확정한다.
2. 업무 담당자가 값 형식·필수 여부·허용값·보정 코드를 승인한다.
3. 승인된 YAML을 `standardization.rules_file`에 연결한다.
4. 테스트 source에서 성공/실패 분기와 재처리를 확인한다.

따라서 이 문서는 데이터 판정 규칙이 아니라 스키마의 컬럼 계약이다.
