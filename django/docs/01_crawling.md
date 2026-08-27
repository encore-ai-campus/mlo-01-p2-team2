# 01. API 크롤링

## 목적

내부 `records API`에서 공개된 신규 데이터를 페이지 단위로 받아 `records.jsonl`에 누적한다.
크롤러는 수집만 담당하며 MongoDB에는 직접 쓰지 않는다. MongoDB 적재는 다음 단계인 [JSONL 로딩](02_loading.md)이 담당한다.

## 실행 진입점

```powershell
cd C:\encore_project\2nd_project_git\django
python manage.py crawl_records --help
python manage.py crawl_records
```

주요 코드 위치:

| 역할 | 파일 |
|---|---|
| Django management command와 스케줄러 | `second_project/management/commands/crawl_records.py` |
| 1회 크롤링 진입점 | `crawling/crawl_records.py`의 `run_once()` |
| 크롤링 orchestration | `crawling/crawler/service.py`의 `run_crawl()` |
| HTTP API client | `crawling/crawler/api_client.py` |
| 파일·상태·잠금 저장 | `crawling/crawler/storage.py` |
| API 응답 검증 | `crawling/crawler/validator.py` |

## 기본 저장 위치

| 경로 | 내용 |
|---|---|
| `data/raw_data/records.jsonl` | API에서 받은 원본 레코드의 누적 JSONL |
| `data/raw_data/state/crawl_state.json` | dataset, source hash, checkpoint, 공개 건수 등 이어받기 상태 |
| `data/raw_data/state/crawler.lock` | 동시에 여러 크롤러가 실행되지 않도록 하는 잠금 파일 |
| `.env` | API key 저장 위치. 로그에는 API key를 기록하지 않음 |
| `logs/raw_data_log.jsonl` | 크롤러 구조화 실행 로그(`stage=ingest`) |

경로는 현재 작업 디렉터리가 아니라 `django/manage.py`가 있는 프로젝트 루트를 기준으로 계산된다.

## 1회 크롤링 처리 순서

한 번의 크롤링은 다음 순서로 진행된다.

1. `crawler.lock`을 획득한다. 다른 크롤러가 실행 중이면 이번 실행을 건너뛴다.
2. `records.jsonl`과 `crawl_state.json`의 존재 여부를 확인한다. 한쪽만 있거나 상태가 맞지 않으면 자동으로 이어받지 않고 중단한다.
3. API readiness endpoint를 확인한다.
4. `.env`의 API key를 메모리로 읽는다. key가 없거나 갱신 시간이면 API에서 새 key를 받아 `.env`에 저장한다.
5. API meta를 받아 `dataset_id`, 원본 파일명, `source_sha256`, 공개 행 수와 컬럼 계약을 확인한다.
6. 최초 실행이면 빈 `records.jsonl`과 초기 `crawl_state.json`을 만든다.
7. checkpoint 또는 `next_cursor`를 사용해 records API를 페이지 단위로 반복 호출한다.
8. 각 응답의 필수 필드, `count`, `has_more`, checkpoint, timestamp, payload 컬럼을 검증한다.
9. 이미 저장된 `(dataset_id, record_id)`는 중복으로 세고 건너뛴다.
10. 신규 레코드에 `_crawl.run_id`, 수집 시각, API server time을 추가해 `records.jsonl`에 append한다.
11. 마지막 페이지까지 정상 처리되고 예상 공개 건수와 실제 응답 건수가 일치할 때만 checkpoint와 상태 파일을 갱신한다.
12. 각 단계의 결과를 `logs/raw_data_log.jsonl`에 기록한다.

## JSONL 레코드의 주요 필드

크롤러가 저장하는 한 줄은 API 원본 필드와 수집 메타데이터를 함께 가진다.

```json
{
  "dataset_id": "...",
  "source_filename": "...",
  "source_sha256": "...",
  "record_id": 153751,
  "source_row_no": 1,
  "source_record_sha256": "...",
  "scheduled_release_at": "2026-08-27T16:12:00+09:00",
  "payload": {},
  "_crawl": {
    "run_id": "...",
    "collected_at": "2026-08-27T16:12:00.123456+09:00",
    "api_server_time": "..."
  }
}
```

`records.jsonl`은 append-only 원본 보관 파일이다. 정제 결과로 덮어쓰지 않으며, 다음 로더가 원본 JSON과 원문 한 줄을 보존한다.

## 크롤러 실행 결과와 오류

`run_once()`는 cron 또는 스케줄러가 판단할 수 있도록 종료 코드를 반환한다.

| 종료 코드 | 의미 |
|---:|---|
| `0` | 정상 완료 또는 잠금/API key 유효시간 문제로 의도적으로 건너뜀 |
| `1` | API, 응답 검증, 저장 또는 예기치 않은 오류 |
| `2` | 잘못된 `--limit` 또는 안전하지 않은 저장 상태 |

정상적으로 페이지를 모두 처리하지 못하면 checkpoint를 갱신하지 않는다. 따라서 다음 실행은 마지막으로 확인된 checkpoint부터 다시 시도한다.

## 로그 확인

PowerShell에서 최근 크롤링 로그를 확인한다.

```powershell
Get-Content .\logs\raw_data_log.jsonl -Tail 20
```

크롤러 로그의 `stage`는 `ingest`이다. Bronze 적재 로그인 `logs/pipeline.jsonl`과 혼동하지 않는다.
