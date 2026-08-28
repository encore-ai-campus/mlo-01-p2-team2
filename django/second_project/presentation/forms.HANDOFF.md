# `forms.py` 인수인계

## 책임

퇴직 대상 관리자 ID를 입력받고 형식을 검증한다.

## 입력 계약

- 필드: `manager_id`
- 형식: `EMP` + 숫자 6자리
- 최대 길이: 9
- 앞뒤 공백 제거

## 의존 파일

`views.py`, `review_form.html`, Silver employee ID 표준계약

## 변경 주의

ID 규칙 변경 시 Silver 표준화 규칙, fixture와 Presentation 테스트를 함께 변경한다. 입력값을 결과 URL로 redirect하지 않는다.

