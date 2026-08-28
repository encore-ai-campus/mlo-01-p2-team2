# `database_router.py` 인수인계

## 책임

Bronze 모델은 MongoDB로, Silver·Legacy·웹 모델은 RDB로 분리한다.

## 핵심 계약

- `BronzeRawRecord` read/write/migration → `mongodb`
- Silver·Legacy → `default` 또는 호환 `sqlite3`
- auth·session의 Mongo migration 금지
- `model_name=None`인 RunPython은 migration 내부 alias guard가 판단하도록 `None` 반환

## 의존 파일

`settings.py`, `repository/models.py`, `0002_bronze_mongodb.py`, merge migration

## 검증

`test_database_router.py`, fresh RDB의 Bronze 테이블 부재 테스트

## 변경 주의

RunPython을 MongoDB에서 일괄 차단하면 Bronze collection·index 준비가 실행되지 않는다. 새 Mongo 모델을 추가하면 모델명 집합과 테스트를 함께 갱신한다.

