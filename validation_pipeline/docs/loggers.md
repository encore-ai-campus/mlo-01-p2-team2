# 로깅 (`loggers.py`)

모든 감사 로그는 UTF-8 JSON Lines로 남긴다. 기본 경로는 실행 방식에 따라
설정값을 사용하며, 파일 입력 모드에서는 프로젝트의 `django/log_lake/raw_data`를 우선한다.

```text
log_lake/raw_data/
├── pipeline.jsonl       # Silver 표준화 단계 집계
├── quality.jsonl        # 품질 게이트 집계
├── quarantine.jsonl     # 격리 ID·원본 참조·오류 코드
└── restoration.jsonl    # Bronze 대비 RAW_DB 복구율
```

모든 JSONL 레코드는 `timestamp`, `level`, `run_id`, `stage`, `dataset_id`,
`status`, `input_count`, `success_count`, `failure_count`, `quarantine_count`,
`duration_ms`, `message`를 포함한다. 시각은 KST ISO 8601이며, 로그에는 원문·실명·credential을 기록하지 않는다.

허용 stage는 `ingest`, `bronze`, `silver`, `quality`, `quarantine`, `load`이고,
상태는 `success`, `partial_failure`, `failed`다. Python `WARNING`은 규칙에 맞춰
`WARN`으로 기록한다. 건수는 항상 `input_count = success_count + failure_count + quarantine_count`를 만족한다.

`RotatingFileHandler`로 파일별 10 MiB·5개 백업을 사용한다. 실행 ID별 재실행 로그는
기존 파일에 누적되며, 각 실행은 새 `run_id`를 사용한다.

## 수정 지점

파일명·회전 정책은 `create_stage_loggers()`에서, 단계 집계와 복구율 이벤트는
`Pipeline._log_stage_summaries()` 및 `Pipeline._log_restoration()`에서 관리한다.
