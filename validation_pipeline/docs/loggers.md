# 로깅 (`loggers.py`)

모든 감사 로그는 UTF-8 JSON Lines로 남긴다. 기본 경로는 실행 방식에 따라 설정값을 사용하며, Windows Django 환경의 기준 경로는 프로젝트의 `django/log_lake`다.

```text
log_lake/
├── raw_data/
│   ├── crawling_log.jsonl       # ingest 크롤링 로그
│   └── raw_data_loading_log.jsonl # Bronze 적재 로그
├── pipeline.jsonl       # Silver 표준화 단계 집계
├── quality.jsonl        # 품질 게이트 집계
├── quarantine.jsonl     # 격리 ID·원본 참조·오류 코드
├── standardize.log      # 표준화 text 호환 로그
├── validation.log       # 검증 text 호환 로그
└── restoration.jsonl    # Bronze 대비 RAW_DB 복구율
```

모든 JSONL 레코드는 `timestamp`, `level`, `run_id`, `stage`, `dataset_id`,
`status`, `input_count`, `success_count`, `failure_count`, `quarantine_count`,
`duration_ms`, `message`를 포함한다. 시각은 KST ISO 8601이며, 로그에는 원문·실명·credential을 기록하지 않는다.

허용 stage는 `ingest`, `bronze`, `silver`, `quality`, `quarantine`, `load`이고,
상태는 `success`, `partial_failure`, `failed`다. Python `WARNING`은 규칙에 맞춰
`WARN`으로 기록한다. 건수는 항상 `input_count = success_count + failure_count + quarantine_count`를 만족한다.

8개 누적 로그는 Windows 내장 Python 로거로 공통 회전한다. KST 기준 00:00, 06:00,
12:00, 18:00 경계 이후 첫 레코드가 기록될 때 또는 파일이 10 MiB에 도달할 때
회전하며, 기존처럼 파일별 백업 5개(`.1`~`.5`)를 유지한다. 실행 ID별 재실행 로그는
기존 파일에 누적되며, 각 실행은 새 `run_id`를 사용한다. Linux `logrotate` 명령은
사용하지 않는다.

## 수정 지점

파일명·표준화/검증 회전 정책은 `create_stage_loggers()`에서, ingest와 Bronze 회전은
각 단계의 logging writer에서 관리한다. 단계 집계와 복구율 이벤트는
`Pipeline._log_stage_summaries()` 및 `Pipeline._log_restoration()`에서 관리한다.
