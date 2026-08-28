# `middleware.py` 인수인계

## 책임

Codex 내장 브라우저의 로컬 `Origin: null` POST를 개발 환경에서만 허용한다.

## 허용 조건

- `DEBUG=True`
- Origin이 정확히 `null`
- Host가 `127.0.0.1`, `localhost`, `[::1]`

조건 충족 시 Origin만 제거하며 CSRF token 검증은 계속 수행된다.

## 의존 파일·검증

`settings.py` MIDDLEWARE, `test_local_preview_null_origin_can_submit_in_debug_mode`

## 변경 주의

운영 호스트 또는 `DEBUG=False`에서 허용하지 않는다. CSRF middleware 자체를 비활성화하지 않는다.

