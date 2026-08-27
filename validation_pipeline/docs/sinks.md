# 저장 (`sinks.py`)

## 역할

실행 ID별 디렉터리에 처리 결과를 나눠 저장하는 `JsonlSink`와
정상/실패 MongoDB를 분리하는 `MongoSink`, Django가 관리하는 MongoClient를
재사용하는 `DjangoMongoSink`를 제공합니다.

```text
output/<실행 ID>/
├── standardized.jsonl  검증 통과 문서
├── rejected.jsonl      제외 문서와 사유
└── report.json          실행 건수, 품질, 프로파일
```

직렬화할 수 없는 원본 제외 문서는 안전하게 문자열 표현으로 남깁니다.

## Django MongoDB 저장

`DjangoMongoSink`는 `django.db.connections[database_alias]`를 초기화한 뒤
backend가 관리하는 `MongoClient`에서 다음 대상을 사용합니다.

```text
success_database.success_collection  검증 통과 문서
failure_database.failure_collection  표준화/검증/JSONL 오류 문서
report_database.report_collection    실행 리포트
```

정상 문서는 `_id` 기준 upsert하고, `_id`가 없으면 문서 내용의 SHA-256으로
보완합니다. 실패 문서는 `stage`, `reasons`, `document`, `_pipeline`을 함께
저장합니다. 정상 문서의 `_pipeline`에는 `run_id`, `batch_id`, `ingested_at`,
설정 파일에서 전달한 `rule_version`이 기록됩니다. `local_report=true`이면
`output/<run_id>/report.json`도 남깁니다.
실패 문서 저장 중 MongoDB 오류가 발생하면 데이터 유실을 피하기 위해 해당
실행을 전체 실패로 처리합니다.

재처리 모드에서는 기존 실패 wrapper를 새로 복제하지 않습니다. 성공 시 원래
실패 문서의 `reprocess_status`를 `resolved`로 바꾸고, 실패 시
`attempt_count`, `reprocess_history`를 갱신합니다. 최대 시도 횟수를 넘으면
`exhausted`로 남아 운영 검토 대상으로 보존합니다.

## DATA-LAKE 백업

`DjangoMongoDataLakeBackup`은 Django alias의 MongoClient를 사용해 정상 DB,
실패 DB, 실행 리포트를 JSONL snapshot으로 저장합니다. 각 파일의 행 수와
SHA-256은 `manifest.json`에 기록되고, 파일과 manifest는 임시 파일 교체로
완성됩니다. 백업은 원본 DB를 삭제하거나 이동하지 않습니다.

## 수정 지점

DB나 오브젝트 스토리지에 저장할 때는 `DocumentSink`의 쓰기 함수와 `close()`를 구현합니다.
