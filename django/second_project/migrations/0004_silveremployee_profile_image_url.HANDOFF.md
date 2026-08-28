# `0004_silveremployee_profile_image_url.py` 인수인계

## 책임

`silver_employee` 테이블에 선택형 `profile_image_url` 컬럼을 추가한다.

## 스키마

- 유형: URL 문자열, 최대 2,048자
- `NULL` 허용
- 기존 행은 모두 `NULL`
- 이미지 바이너리 또는 외부 스톡 이미지를 적재하지 않는다.

## 적용

```powershell
python manage.py migrate
```

적용 전 운영 DB 백업과 복사본 migration 검증 원칙을 유지한다.
