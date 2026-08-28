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
| `data/raw_data/state/api_key_metadata.json` | API 키의 service date, 유효 구간, 마지막 갱신 시각 |
| `data/raw_data/state/crawler.lock` | 동시에 여러 크롤러가 실행되지 않도록 하는 잠금 파일 |
| `.env` | API key 저장 위치. 로그에는 API key를 기록하지 않음 |
| `log_lake/raw_data/crawling_log.jsonl` | 크롤러 구조화 실행 로그(`stage=ingest`) |

경로는 현재 작업 디렉터리가 아니라 `django/manage.py`가 있는 프로젝트 루트를 기준으로 계산된다.

## 1회 크롤링 처리 순서

한 번의 크롤링은 다음 순서로 진행된다.

1. `crawler.lock`을 획득한다. 다른 크롤러가 실행 중이면 이번 실행을 건너뛴다.
2. `records.jsonl`과 `crawl_state.json`의 존재 여부를 확인한다. 한쪽만 있거나 상태가 맞지 않으면 자동으로 이어받지 않고 중단한다.
3. API readiness endpoint를 확인한다.
4. `.env`의 API key와 `api_key_metadata.json`을 확인한다. key가 없거나 메타데이터가 없거나 만료되었거나 00:01 갱신을 놓쳤으면 API에서 새 key를 받아 `.env`와 메타데이터 파일에 저장한다.
5. API meta를 받아 `dataset_id`, 원본 파일명, `source_sha256`, 공개 행 수와 컬럼 계약을 확인한다.
6. 최초 실행이면 빈 `records.jsonl`과 초기 `crawl_state.json`을 만든다.
7. checkpoint 또는 `next_cursor`를 사용해 records API를 페이지 단위로 반복 호출한다.
8. 각 응답의 필수 필드, `count`, `has_more`, checkpoint, timestamp, payload 컬럼을 검증한다.
9. 이미 저장된 `(dataset_id, record_id)`는 중복으로 세고 건너뛴다.
10. 신규 레코드에 `_crawl.run_id`, 수집 시각, API server time을 추가해 `records.jsonl`에 append한다.
11. 마지막 페이지까지 정상 처리되고 예상 공개 건수와 실제 응답 건수가 일치할 때만 checkpoint와 상태 파일을 갱신한다.
12. 각 단계의 결과를 `log_lake/raw_data/crawling_log.jsonl`에 기록한다.

## API 키 메타데이터와 보충 갱신

API key 자체는 프로젝트 루트 `.env`에 저장하고, key의 수명 정보는
`data/raw_data/state/api_key_metadata.json`에 별도로 저장한다. 메타데이터에는
`service_date`, `effective_at`, `expires_at`, `server_time`, `last_refreshed_at`만
포함하며 API key 값은 포함하지 않는다.

매 실행 시 다음 조건이면 새 API key를 발급받아 `.env`와 메타데이터를 함께 갱신한다.

- `.env` 또는 메타데이터 파일이 없음
- 메타데이터의 `expires_at`이 지남
- KST 00:01 이후 `service_date` 또는 `last_refreshed_at`이 현재 날짜보다 오래됨

따라서 프로세스가 00:01에 중지되어 있어도 다음 실행에서 갱신을 보충한다. 갱신 성공·실패는
`log_lake/raw_data/crawling_log.jsonl`에 `event_type=api_key_refresh`로 기록한다.

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

`run_once()`는 통합 command와 스케줄러가 판단할 수 있도록 종료 코드를 반환한다.

| 종료 코드 | 의미 |
|---:|---|
| `0` | 정상 완료 또는 새 데이터 없음 |
| `1` | API, API 키 갱신, 응답 검증, 저장 실패 또는 잠금/API key 문제로 크롤링 건너뜀 |
| `2` | 잘못된 `--limit` 또는 안전하지 않은 저장 상태 |

정상적으로 페이지를 모두 처리하지 못하면 checkpoint를 갱신하지 않는다. 따라서 다음 실행은 마지막으로 확인된 checkpoint부터 다시 시도한다.

## 로그 확인

PowerShell에서 최근 크롤링 로그를 확인한다.

```powershell
Get-Content .\log_lake\raw_data\crawling_log.jsonl -Tail 20
```

크롤러 로그의 `stage`는 `ingest`이다. Bronze 적재 로그인 `log_lake/raw_data/raw_data_loading_log.jsonl`과 혼동하지 않는다.

이 로그는 Django 자동화 실행 중 기록될 때 KST 6시간 경계 또는 10MiB 기준으로
자동 회전한다. 백업 파일은 `.1`부터 `.5`까지 유지한다. 자세한 정책은
[Windows 로그 로테이션](04_log_rotation.md)을 참고한다.
