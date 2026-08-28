# `test_presentation.py` 인수인계

## 책임

인증·Group 권한·역할별 PII·POST 결과·보안 헤더와 로컬 미리보기 흐름을 검증한다.

## 현재 시나리오 9건

- 미인증 로그인 이동
- 무권한 403
- 팀 후보 PII 비노출
- HR 후보 상세와 판단 가이드
- 결과 URL ID 비노출와 보안 헤더
- loopback null Origin POST
- 재직 대상 400
- HR 보류 상세와 팀 비노출
- HR 후보 이미지/빈 자리 렌더링과 팀 이미지 URL 비노출

## 실행

```powershell
python manage.py test second_project.test_presentation
```

## 변경 주의

새 후보 필드를 추가하면 팀 HTML에 값이 없다는 negative assertion도 추가한다. POST·CSRF·no-store·no-referrer 계약을 유지한다.

