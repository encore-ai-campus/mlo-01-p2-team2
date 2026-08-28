# `views.py` 인수인계

## 책임

로그인·Group 권한 검사, Form 처리, 판정 서비스 호출, 역할별 DTO·화면 분기와 비캐시 응답을 담당한다.

## 핵심 함수

- `review_request()`: GET 입력, POST 결과
- `_render_result()`: 서비스 실행과 HR·팀 템플릿 분기
- `_require_review_permission()`: 무권한 403
- `_private_response()`: `no-store`, `private`, `no-referrer`

## 데이터 계약

- HR → `ContinuityAssessment`와 `AssessmentGuidance`
- 팀 → 후보 PII를 제거한 `AssessmentSummary`와 `AssessmentGuidance`

## 검증

`test_presentation.py` 8건

## 변경 주의

팀 템플릿에 전체 Assessment를 전달하지 않는다. 관리자 ID를 URL에 넣지 않고 보안 헤더를 유지한다. 팀 오류는 대상 존재·재직 상태를 세분화해 노출하지 않는다.

