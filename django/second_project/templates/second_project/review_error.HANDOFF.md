# `review_error.html` 인수인계

## 책임

조회 불가 메시지와 오류코드를 표시하고 입력 화면으로 복귀시킨다.

## 역할 차이

- HR: `TARGET_NOT_FOUND`, `TARGET_IS_ACTIVE` 등 구체 오류
- 팀 관리자: View에서 일반화된 `REQUEST_NOT_AVAILABLE`

## 변경 주의

팀 관리자에게 대상자의 존재 여부와 재직·퇴직 상태를 상세히 구분하는 정보를 추가하지 않는다. 오류 페이지에도 관리자 ID를 출력하지 않는다.

