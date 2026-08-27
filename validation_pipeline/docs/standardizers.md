# 표준화 (`standardizers.py`)

## 역할

문서 구조를 유지하면서 Python·BSON 값을 JSON 호환 값으로 바꿉니다.

- 공통 변환기의 날짜와 시간 → UTC ISO 문자열
- `ObjectId`, `UUID` → 문자열
- `Decimal128`, `Decimal` → 문자열
- `Binary`, `bytes` → Base64 객체
- 중첩 객체와 배열 → 재귀 변환

필드 삭제, 업무상 필드명 변경, 평탄화는 의미가 달라질 수 있어 기본 동작에 넣지 않았습니다.
현재 집계하는 값은 필드명 변환 횟수와 타입 변환 횟수입니다.

## 수정 지점

- 타입 하나를 추가할 때: `CommonStandardizer`의 변환 규칙 또는 `custom_converters`
- 문서 구조를 바꿀 때: `Standardizer`를 구현한 별도 클래스

처리할 수 없는 데이터는 `StandardizationError`로 알려야 해당 문서만 제외됩니다.

업무 필드 규칙을 YAML로 적용할 때는 `YamlRuleStandardizer`를 사용합니다.
지원 형식과 실행 방법은 [YAML 규칙 사용법](yaml_rules.md)에 있습니다.
프로젝트 Silver 업무 날짜는 `rules/silver_canonical.yaml`에서 Asia/Seoul
`+09:00` 형식으로 명시하므로 공통 변환기의 UTC 기본값과 구분합니다.
