# `urls.py` 인수인계

## 책임

검토 입력과 결과를 처리하는 단일 `/second_project/review/` URL을 제공한다.

## 의존 파일

`views.py`, 프로젝트 `config/urls.py`

## 보안 의도

결과를 같은 POST URL에서 렌더링하여 관리자 ID가 access log와 브라우저 history에 남지 않게 한다.

## 변경 주의

`/review/<manager_id>/` 또는 ID query string을 추가하려면 개인정보 로그·referrer 정책을 다시 검토한다.

