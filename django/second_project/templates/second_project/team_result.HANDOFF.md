# `team_result.html` 인수인계

## 책임

팀 관리자에게 후보 개인정보 없이 대상 이름, 업무영역, 확인된 후보 수, 상태와 인력 검토 방향만 표시한다.

## Context 계약

- `assessment`: `AssessmentSummary`
- `guidance`: `AssessmentGuidance`

## 금지 데이터

후보 이름·ID·부서·직위·근속·개별 경고를 표시하거나 HTML에 포함하지 않는다.

## 검증

`test_team_manager_response_contains_counts_but_no_candidate_pii`, 보류 후보 비노출 테스트

## 변경 주의

숨김 CSS로 PII를 가리는 방식은 허용하지 않는다. 후보 수가 `—`인 보류 상태를 0명으로 바꾸지 않는다.

