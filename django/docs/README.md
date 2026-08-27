# 크롤링·로딩 운영 문서

이 디렉터리는 `second_project` 데이터 파이프라인의 수집, Bronze 로딩, 자동화 실행을 단계별로 설명한다.
명령은 모두 Django 프로젝트 디렉터리인 `django/`에서 실행하는 것을 기준으로 한다.

## 문서 순서

1. [01. API 크롤링](01_crawling.md)
2. [02. JSONL 로딩과 MongoDB 적재](02_loading.md)
3. [03. 크롤링·로딩 자동화](03_automation.md)

## 전체 흐름

```text
records API
    │
    ▼
크롤링·적재 자동화: python manage.py crawl_and_load
    │  data/raw_data/records.jsonl
    │  data/raw_data/state/crawl_state.json
    │  data/raw_data/state/api_key_metadata.json
    │  log_lake/raw_data/crawling_log.jsonl
    ▼
로딩: python manage.py load_raw_records
    │  log_lake/raw_data/raw_data_loading_log.jsonl
    │  MongoDB second_project
    ▼
bronze_raw_records / bronze_load_runs / bronze_manifests / bronze_quarantine
```

크롤링과 로딩을 한 프로세스에서 연결하려면 [통합 자동화 명령](03_automation.md)을 사용한다.
