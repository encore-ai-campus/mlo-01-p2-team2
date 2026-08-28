# `test_database_router.py` 인수인계

## 책임

Mongo Bronze와 RDB Silver·Legacy의 분리 계약을 검증한다.

## 현재 시나리오 4건

- Bronze·Silver read/write DB 분리
- 모델 migration DB 제한
- RunPython의 자체 alias guard 허용
- auth 테이블의 Mongo migration 금지

## 실행

```powershell
python manage.py test second_project.test_database_router
```

## 변경 주의

Router 또는 모델 위치 변경 시 이 테스트와 fresh migration 검증을 함께 수행한다. 실제 Mongo collection·index는 별도 통합 환경에서 확인한다.

