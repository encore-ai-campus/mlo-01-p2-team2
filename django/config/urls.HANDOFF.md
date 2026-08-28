# `urls.py` 인수인계

## 책임

루트 리다이렉트와 로그인·로그아웃·Admin·검토 앱 URL을 연결한다.

## URL 계약

- `/` → 검토 입력 화면
- `/login/` → Django LoginView
- `/logout/` → Django LogoutView, POST
- `/admin/` → 계정·Group 운영
- `/second_project/` → Presentation URL

## 의존 파일

`settings.py`, `templates/registration/login.html`, `presentation/urls.py`

## 변경 주의

- 관리자 ID를 URL path나 query string에 넣지 않는다.
- 로그아웃을 GET 방식으로 바꾸지 않는다.

