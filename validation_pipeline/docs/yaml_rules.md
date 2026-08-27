# YAML 규칙 기반 표준화

## 바로 실행

의존성을 설치한 뒤 예제 데이터와 레거시 규칙을 실행합니다.

```powershell
pip install -e .
python -m mongo_pipeline `
  --input-yaml examples/legacy_input.yaml `
  --rules rules/legacy_org.yaml `
  --output output
```

결과는 실행 ID별로 나뉩니다.

```text
output/<run-id>/standardized.jsonl  표준화·검증 통과 문서
output/<run-id>/rejected.jsonl      규칙 실패 문서와 rule_id
output/<run-id>/report.json         실행 건수와 적용 규칙 정보
```

입력 YAML은 다음 세 형태를 지원합니다.

```yaml
# 단일 문서
area_no: BIZ-00001
```

```yaml
# 문서 배열
- area_no: BIZ-00001
- area_no: BIZ-00002
```

```yaml
# documents 배열
documents:
  - area_no: BIZ-00001
  - area_no: BIZ-00002
```

## MongoDB 실행에 규칙 연결

`config.json`에서 규칙 파일을 지정합니다. 상대 경로는 `config.json` 위치 기준입니다.

```json
{
  "standardization": {
    "rules_file": "rules/legacy_org.yaml"
  }
}
```

```powershell
mongo-pipeline --config config.json
```

CLI의 `--rules`를 함께 지정하면 `config.json`의 값을 덮어씁니다.

```powershell
mongo-pipeline --config config.json --rules rules/another.yaml
```

## 규칙 파일 구조

실행 예시는 [`rules/legacy_org.yaml`](../rules/legacy_org.yaml)에 있습니다.

```yaml
schema_version: 1
name: example-v1

defaults:
  unicode_normalization: NFKC
  null_values: ["", "NULL", "N/A"]
  on_error: reject

audit:
  enabled: true
  field: _standardization

record_id:
  enabled: true
  rule_id: META-001
  field: record_id
  source_system: legacy_org
  source_fields: [area_no]
  namespace_uuid: "6ba7b811-9dad-11d1-80b4-00c04fd430c8"
  overwrite: false

fields:
  area_no:
    rule_id: ID-001-area
    kind: code
    prefix: BIZ
    digits: 5
    separator: "_"
    required: true

checks:
  - rule_id: XFIELD-001
    kind: fields_equal
    fields: [top_area_no, p_area_no]
    ignore_null: true
    on_error: reject
```

지원하지 않는 키, 중복 YAML 키, 잘못된 타입은 파이프라인 시작 전에 실패합니다.

## 지원 필드 규칙

### `text`

문자열의 Unicode와 공백을 정리합니다.

```yaml
mgr_nm:
  rule_id: TXT-002
  kind: text
  whitespace: remove_hangul
  invalid_values: ["오류값"]
  on_error: null
```

`whitespace` 값:

| 값 | 동작 |
|---|---|
| `preserve` | 앞뒤 공백만 제거 |
| `collapse` | 연속 공백을 한 칸으로 축소 |
| `remove` | 모든 공백 제거 |
| `remove_hangul` | 한글 음절 사이 공백만 제거 |

자동수정하면 안 되는 의심 문자열은 값은 보존하고 감사 경고를 남길 수 있습니다.

```yaml
area_nm:
  rule_id: TXT-003
  kind: text
  whitespace: collapse
  warning_contains: ["관리관리", "서비스서비스"]
```

### `code`

공백과 `-`, `_`를 제거하고 대문자 접두사와 숫자 자릿수를 검증한 뒤 다시 조립합니다.

```yaml
area_no:
  rule_id: ID-001-area
  kind: code
  prefix: BIZ
  digits: 5
  separator: "_"
```

예: `biz-02168`, `BIZ 02168` → `BIZ_02168`

### `enum`

별칭과 허용값을 하나의 표준값으로 바꿉니다.

```yaml
top_area_lvl:
  rule_id: ENUM-001
  kind: enum
  match: casefold_compact
  aliases:
    "top_level": L1
    "TOP LEVEL": L1
    "1": L1
    "최상위": L1
  allowed_values: [L1]
```

`match` 값:

| 값 | 비교 방식 |
|---|---|
| `exact` | NFKC·trim 후 그대로 비교 |
| `casefold` | 대소문자 무시 |
| `casefold_remove_whitespace` | 대소문자와 모든 공백 무시 |
| `casefold_compact` | 대소문자와 공백·`_`·`-` 무시 |

### `datetime`

허용한 형식만 파싱하고 원천 시간대에서 출력 시간대로 변환합니다.

```yaml
area_reg_dtm:
  rule_id: DATE-001-area
  kind: datetime
  on_error: null
  input_timezone: Asia/Seoul
  output_timezone: UTC
  input_formats:
    - "%Y-%m-%d %H:%M:%S"
    - "%Y%m%d%H%M%S"
```

원천 시간대를 확인하지 않은 상태에서 `Asia/Seoul` 또는 UTC를 임의 적용하면 안 됩니다.

## 오류 정책

| 설정 | 동작 |
|---|---|
| `required: true` | 결측·무효·형식 오류 시 항상 행 격리 |
| `on_error: reject` | 오류 시 행 격리 |
| `on_error: null` | 값을 null로 바꾸고 `_standardization`에 WARNING 기록 |
| `warning_contains` | 값은 보존하고 WARNING 기록 |

감사 이력에는 원본 개인정보를 복제하지 않고 규칙 ID, 필드, 처리 종류만 기록합니다.

```json
{
  "rule_id": "ID-001-area",
  "field": "area_no",
  "action": "NORMALIZED",
  "severity": "INFO"
}
```

## 결정적 `record_id`

`record_id`가 없으면 표준화가 끝난 원천 필드를 이용해 UUIDv5를 생성합니다.

```text
UUIDv5(namespace_uuid, source_system + "|" + canonical_area_no)
```

동일 입력은 재실행해도 같은 ID를 얻습니다. `release_at`은 생성하지 않습니다. 실제 배포 시각이 없다면 수집 계층에서 `ingested_at`과 `batch_id`를 별도로 관리해야 합니다.

## 교차 필드 검사

현재 지원하는 교차 검사는 `fields_equal`입니다.

```yaml
checks:
  - rule_id: XFIELD-001
    kind: fields_equal
    fields: [top_area_no, p_area_no]
    ignore_null: true
    on_error: reject
```

`on_error`는 `reject` 또는 `warn`을 지원합니다. 서로 다른 문서 간 고유성·기준정보 일관성 검사는 스트리밍 표준화기 밖에서 별도의 배치 검증으로 수행해야 합니다.

## 안전 장치와 제한

- PyYAML `SafeLoader` 기반이며 Python 객체 생성 태그를 허용하지 않습니다.
- 같은 mapping의 중복 키를 거부합니다.
- 규칙 파일은 1 MiB로 제한합니다.
- 임의 Python 코드나 사용자 정규식 실행을 지원하지 않습니다.
- 선언하지 않은 필드는 삭제하거나 변경하지 않습니다.
- 점 표기법으로 중첩 필드를 지정할 수 있습니다: `metadata.score`.
- YAML은 파일 전체를 메모리에 읽으므로 대용량 원천은 MongoDB 스트리밍이나 별도 line-oriented 포맷을 권장합니다.
