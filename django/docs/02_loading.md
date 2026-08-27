# 02. JSONL 로딩과 MongoDB 적재

## 목적

크롤러가 만든 `data/raw_data/records.jsonl`을 읽어 검증한 뒤 MongoDB `second_project` 데이터베이스의 Bronze 컬렉션에 적재한다.
로더는 크롤링 API를 호출하지 않으며, 입력 JSONL만 읽는다.

## 실행 진입점

```powershell
cd C:\encore_project\2nd_project_git\django
python manage.py load_raw_records --help
python manage.py load_raw_records
```

`load_raw_records`는 한 번 실행한 뒤 종료하는 명령이다. 반복 실행은 [자동화 문서](03_automation.md)의 `crawl_and_load` 또는 외부 스케줄러가 담당한다.

주요 코드 위치:

| 역할 | 파일 |
|---|---|
| Django management command | `second_project/management/commands/load_raw_records.py` |
| loader 실행 wrapper | `second_project/service/loader_runner.py` |
| 파일 읽기·검증·Bronze 문서 생성 | `second_project/service/record_reader.py` |
| 파일 SHA-256 계산 | `second_project/service/fingerprint.py` |
| Bronze 적재 orchestration | `second_project/service/bronze_loader.py` |
| MongoDB repository | `second_project/repository/mongodb_repository.py` |
| 로컬 JSONL 로그 writer | `second_project/service/structured_logging.py` |

## 기본 입출력

| 항목 | 기본값 |
|---|---|
| 입력 | `data/raw_data/records.jsonl` |
| 적재 로그 | `log_lake/raw_data/raw_data_loading_log.jsonl` |
| MongoDB alias | `mongodb` |
| MongoDB URI | `mongodb://127.0.0.1:27017` |
| MongoDB database | `second_project` |
| batch size | `500` |

MongoDB URI와 database 이름은 `MONGODB_URI`, `MONGODB_NAME` 환경 변수로 변경할 수 있다.

## 적재 처리 순서

1. 입력 파일의 크기와 SHA-256을 계산한다. 파일이 없으면 실행하지 않는다.
2. Django의 `mongodb` 연결을 사용해 MongoDB에 연결하고 ping한다.
3. 필요한 Bronze 인덱스를 확인하거나 생성한다.
4. `bronze_load_runs`에 `status=running`인 실행 이력을 만든다.
5. `records.jsonl`을 처음부터 끝까지 한 줄씩 읽는다.
6. JSON 형식, 필수 필드, SHA-256, timestamp, payload 구조와 MongoDB key 사용 가능 여부를 검증한다.
7. 유효한 행은 batch로 모아 `bronze_raw_records`에 upsert한다.
8. 유효하지 않은 행은 `bronze_quarantine`에 원문과 오류 사유를 보존한다.
9. 입력 파일을 다시 fingerprint해 적재 전후 파일이 바뀌지 않았는지 확인한다.
10. 처리 건수와 상태를 `bronze_load_runs`에 갱신하고, 파일·checksum·행 수를 `bronze_manifests`에 저장한다.
11. 적재 이벤트를 `log_lake/raw_data/raw_data_loading_log.jsonl`에 기록한다.

## MongoDB 컬렉션

| 컬렉션 | 저장 내용 |
|---|---|
| `bronze_raw_records` | 원천 레코드, 원문 JSON, 원문 text, checksum, 적재 메타데이터 |
| `bronze_load_runs` | 실행 ID, 상태, 입력·성공·실패·중복·quarantine 건수 |
| `bronze_manifests` | 입력 경로, 파일 크기, 파일 SHA-256, dataset, 행 수, 적재 상태 |
| `bronze_quarantine` | 검증에 실패한 원본 행과 오류 코드·사유 |

현재 로더는 로그 본문을 MongoDB에 적재하지 않는다. `log_lake/raw_data/raw_data_loading_log.jsonl`은 로컬 파일에만 기록된다.

`migrations/0002_bronze_mongodb.py`에는 과거 호환성을 위해 `pipeline_logs` 컬렉션과 인덱스가 남아 있을 수 있다. migration 실행으로 컬렉션이 만들어질 수는 있지만, 현재 runtime repository는 이 컬렉션에 새 로그를 insert하지 않는다. 기존 문서는 자동 삭제하지 않는다.

## 중복과 재실행

로더는 매 실행 시 입력 JSONL 전체를 읽는다. MongoDB의 고유 식별자는 `dataset_id + source_record_id`이며, 다음 규칙을 적용한다.

- 같은 source record와 같은 checksum이면 `duplicate`로 처리한다.
- 같은 source record인데 checksum이 다르면 원본을 덮어쓰지 않고 실행을 실패시킨다.
- 파일 전체를 다시 읽더라도 기존 raw 데이터는 중복 insert되지 않는다.
- 입력 파일이 실행 중 변경되면 실행을 `failed`로 기록한다.

따라서 로더는 재실행에 안전하지만, `records.jsonl`이 커질수록 매 실행의 전체 스캔 비용이 증가한다.

## 명령 옵션

```powershell
python manage.py load_raw_records `
  --input-file data/raw_data/records.jsonl `
  --log-file log_lake/raw_data/raw_data_loading_log.jsonl `
  --batch-size 500 `
  --dataset-id <DATASET_ID> `
  --quiet
```

`--dataset-id`를 지정하면 다른 dataset의 레코드가 섞이는 것을 차단한다. `--quiet`는 stderr 출력만 끄며, 로컬 로그 파일 기록은 유지한다.

## migration과 로그 확인

최초 사용 전 MongoDB 컬렉션과 인덱스를 준비한다.

```powershell
python manage.py migrate second_project --database mongodb --skip-checks
```

최근 Bronze 로딩 로그는 다음과 같이 확인한다.

```powershell
Get-Content .\log_lake\raw_data\raw_data_loading_log.jsonl -Tail 20
```

로더 로그의 `stage`는 `bronze`이다. API 크롤러 로그는 `log_lake/raw_data/crawling_log.jsonl`의 `stage=ingest` 이벤트다.
