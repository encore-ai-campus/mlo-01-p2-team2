# `review_form.html` 인수인계

## 책임

퇴직 대상 관리자 ID 입력과 현재 등록정보·자동결정 금지 안내를 표시한다.

## Context

- `form`: `ManagerReviewForm`
- `is_hr`: 현재 역할 표시용

## 보안 계약

Form은 POST와 CSRF token을 사용한다.

## 변경 주의

자동선정·자동 승인·채용결정으로 오인할 표현을 넣지 않는다. ID를 GET query나 action URL에 넣지 않는다.

