# `0003_merge_bronze_silver.py` 인수인계

## 책임

Bronze와 Silver의 병렬 migration leaf를 하나로 합치는 merge-only migration이다.

## 의존성

- `0002_bronze_mongodb`
- `0002_legacy_org_record`

## Operation

없음. 업무 테이블·컬럼·데이터를 추가하지 않는다.

## 검증 이력

- 테스트 DB migration 성공
- 운영 DB 복사본 migration 성공
- Legacy 19,621행 보존
- FK 위반 0
- SQLite Bronze 테이블 0

## 변경 주의

스키마 변경을 이 merge migration에 섞지 않는다. 실제 DB 적용 전 백업·복사본 검증·잠금 해제를 완료한다.

