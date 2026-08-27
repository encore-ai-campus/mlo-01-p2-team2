# 검증 (`validators.py`)

## 역할

표준화된 문서가 품질 규칙을 만족하는지 검사합니다.

| 검증기 | 검사 내용 | 오류 분류 |
|---|---|---|
| `NonEmptyDocumentValidator` | 빈 문서 | `null` |
| `RequiredFieldsValidator` | 필수 필드 누락·null | `null` |
| `FieldTypeValidator` | 설정한 기본 타입 | `format` |

지원 타입은 `string`, `integer`, `number`, `boolean`, `object`, `array`입니다.
중첩 필드는 `metadata.score`처럼 점으로 표현합니다.

## 수정 지점

날짜 형식, 허용값, 범위 같은 규칙은 `Validator`를 구현한 클래스로 추가합니다.
실패 결과는 `ValidationIssue(rule, category, message, field)`로 반환합니다.
