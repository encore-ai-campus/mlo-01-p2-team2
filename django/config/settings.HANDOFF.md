# `settings.py` 인수인계

## 책임

Django 환경, RDB·MongoDB alias, 인증 이동 경로, 한국어·한국시간 및 운영 보안 설정을 관리한다.

## 핵심 구현

- `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` 환경변수 처리
- `default`와 적재기 호환용 `sqlite3` RDB alias
- Bronze용 `mongodb` alias와 `ProjectDatabaseRouter`
- `Asia/Seoul`, 로그인·로그아웃 redirect
- Secure Cookie, SSL redirect, 선택적 HSTS
- `LocalNullOriginMiddleware`를 CSRF middleware 앞에 배치

## 의존 파일

`database_router.py`, `middleware.py`, `urls.py`, `services/success_to_sqlite.py`

## 검증

`manage.py check`, `manage.py check --deploy`, `test_database_router.py`

## 변경 주의

- 운영 `DEBUG=False`에서 Secret이 없으면 실행을 막는 계약을 유지한다.
- `sqlite3` alias 제거 전 기존 적재기 의존성을 확인한다.
- HSTS subdomain·preload는 보안 담당 승인 없이 기본 활성화하지 않는다.

