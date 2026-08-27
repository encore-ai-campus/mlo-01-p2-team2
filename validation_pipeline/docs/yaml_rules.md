# YAML 규칙 기반 표준화

## 표준 Silver 실행

프로젝트 기준 규칙은 [`rules/silver_canonical.yaml`](../rules/silver_canonical.yaml)이다.

```powershell
$env:PYTHONPATH = "src"
python -m mongo_pipeline `
  --input-yaml examples/silver_input.yaml `
  --rules rules/silver_canonical.yaml `
  --output output
```

실행 결과는 다음 네 모델 파일로도 분리된다.

```text
output/<run-id>/silver_employee.jsonl
output/<run-id>/silver_area.jsonl
output/<run-id>/silver_parent_area.jsonl
output/<run-id>/silver_top_area_detail.jsonl
```

`standardized.jsonl`은 한 원천 행의 통합 표준 후보이고, 모델 파일은 동일 PK의
완전 동일 문서를 실행 내에서 중복 기록하지 않는다. `rejected.jsonl`에는
격리 메타데이터와 사유가 남는다.

## 표준 계약

canonical 규칙은 `config/standardization.yaml`과 다음을 동일하게 사용한다.

- Unicode NFC, 양끝·연속 공백 정리, 탭은 공백으로 치환하고 `TAB_CHARACTER_ERROR` 기록
- 직원 ID `EMP` + 6자리, 영역 ID `BIZ` + 5자리, 구분자는 제거 후 대문자화
- 활성 상태: `Y`, `YES`, `사용`, `재직` → `true`; `N`, `NO`, `미사용`, `비활성` → `false`
- 최상위 레벨: `TOP`, `TOP_LEVEL`, `top_level`, `최상위`, `1` → `TOP`
- 날짜 출력: `Asia/Seoul` 기준 `YYYY-MM-DDTHH:MM:SS+09:00`
- `source_record_id`, `dataset_id`, `normalization_run_id`는 값 자체를 정규화하지 않고 보존
- 보정 코드는 승인 목록(`CODE_FORMAT_NORMALIZED`, `DATETIME_FORMAT_NORMALIZED`,
  `ACTIVE_STATUS_NORMALIZED`, `TOP_LEVEL_NORMALIZED`, `WHITESPACE_NORMALIZED`,
  `UNICODE_NORMALIZED`, `TAB_CHARACTER_ERROR`, `DATE_CONFLICT`)만 사용

`mgr_no → employee_id`, `area_no → area_id`, `p_area_no → parent_area_id`,
`top_area_no → top_area_id` 등 원천-표준 매핑은
`DATA_STANDARD_DICTIONARY.md`에 근거한다. `manager_employee_id`는 `mgr_no`에서
파생되는 `silver_area` FK다.

## 규칙 파일 구조

```yaml
schema_version: 1
name: example-v1
defaults:
  unicode_normalization: NFC
  null_values: ["", "NULL", "NONE", "N/A", "NA", "-"]
  on_error: reject
audit:
  enabled: true
  field: _standardization
  correction_codes_field: correction_codes
fields:
  area_id:
    rule_id: MAP-AREA-ID
    kind: code
    source: [area_id, area_no, payload.area_no, source.payload.area_no]
    prefix: BIZ
    digits: 5
    separator: ""
    required: true
```

지원 `kind`는 `text`, `code`, `enum`, `boolean`, `datetime`, `integer`다.
`preserve_value: true`는 계보 식별자처럼 값 자체를 바꾸면 안 되는 문자열에만 사용한다.

## 오류와 배치 검증

`required: true` 또는 `on_error: reject` 오류는 행을 격리한다. 표준화 이후에는
`silver.py`가 네 모델의 필수값·타입·PK 중복·manager/parent FK·최상위 도메인을
배치로 검사한다. FK 미매칭은 `FK_ORPHAN`, PK 충돌은 `PK_DUPLICATE`로 기록한다.

파이프라인은 표준화 전에 Bronze 원문과 `source_record_id`를 확정한다. 따라서
실행 리포트의 복구율은 Bronze 고유 `source_record_id` 대비 통과 Silver 후보의
고유 ID로 계산되며, 표준화기에는 이 계보 식별자를 변경하지 않고 출력한다.

## 레거시 fixture

`legacy_org.yaml`, `legacy_org_jsonl.yaml`, `legacy_org_flat.yaml`은 기존 공통
파이프라인 테스트용 fixture다. 프로젝트 표준 Silver 실행에는 사용하지 않는다.
