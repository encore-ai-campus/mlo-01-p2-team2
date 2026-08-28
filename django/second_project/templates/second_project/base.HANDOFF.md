# `base.html` 인수인계

## 책임

공통 Header·로그아웃·반응형 CSS와 상태·판단 가이드·표 스타일을 제공한다.

## 상태 CSS 계약

- `status-REVIEWABLE`
- `status-PARTIAL`
- `status-NO_MATCH`
- `status-ON_HOLD`

## 의존 템플릿

로그인, 입력, HR 결과, 팀 결과, 오류 화면이 모두 상속한다.

## 변경 주의

외부 CDN·분석·추적 스크립트를 승인 없이 추가하지 않는다. 상태 이름을 바꾸면 서비스 enum과 테스트를 함께 바꾼다.

