# 03. 크롤링·로딩 자동화

## 권장 실행 명령

크롤링과 MongoDB 적재를 한 프로세스에서 이어서 실행하려면 다음 명령을 사용한다.

```powershell
cd C:\encore_project\2nd_project_git\django
python manage.py crawl_and_load
```

이 명령은 기본적으로 장시간 실행되는 내장 스케줄러다. 프로세스가 살아 있는 동안 KST 기준 매시 `01, 04, 07, ... 58분`에 다음 작업을 반복한다.

```text
예약 시각 대기
    │
    ▼
1회 API 크롤링
    │
    ├─ 실패: 적재하지 않고 다음 예약 시각에 재시도
    │
    └─ 정상 종료 코드: records.jsonl을 Bronze MongoDB에 적재
             │
             ▼
        다음 3분 주기 대기
```

예약 시각을 기다리지 않고 한 번만 실행하려면 다음과 같이 한다.

```powershell
python manage.py crawl_and_load --once
```

## 내부 실행 단계

통합 command는 `second_project/management/commands/crawl_and_load.py`에서 다음 순서로 동작한다.

1. 기본 모드에서는 다음 `3n+1`분의 KST 경계까지 대기한다. `--once` 모드에서는 즉시 시작한다.
2. `crawling.crawl_records.run_once()`를 호출한다.
3. 크롤러가 `records.jsonl`, 상태 파일, `crawling_log.jsonl`을 갱신한다.
4. 크롤링이 오류 코드로 끝나면 loader를 호출하지 않는다.
5. 크롤링이 종료 코드 `0`으로 끝날 때만 `run_loader()`를 호출한다.
6. loader가 `records.jsonl`을 읽어 MongoDB Bronze 컬렉션에 적재한다.
7. 적재 결과를 `log_lake/raw_data/raw_data_loading_log.jsonl`에 기록하고 다음 예약 시각을 기다린다.

크롤러 로그와 loader 로그는 서로 다른 파일에 저장된다.

| 단계 | 로그 파일 | stage |
|---|---|---|
| API 크롤링 | `log_lake/raw_data/crawling_log.jsonl` | `ingest` |
| Bronze 로딩 | `log_lake/raw_data/raw_data_loading_log.jsonl` | `bronze` |

## 기존 명령과의 차이

| 명령 | 실행 방식 | 역할 |
|---|---|---|
| `python manage.py crawl_records` | 장시간 실행, 3분 주기 | 크롤링만 수행 |
| `python manage.py load_raw_records` | 1회 실행 후 종료 | 기존 JSONL만 MongoDB에 적재 |
| `python manage.py crawl_and_load` | 장시간 실행, 3분 주기 | 크롤링 성공 후 로딩까지 수행 |
| `python manage.py crawl_and_load --once` | 즉시 1회 실행 후 종료 | 수동 점검·작업 스케줄러용 |
| `python manage.py validation_records` | 장시간 실행, 3분 주기(수집 1분 후) | Bronze 표준화·검증 |
| `python manage.py validation_records --once` | 즉시 1회 실행 후 종료 | 표준화·검증 수동 점검용 |
| `python manage.py load_success_to_sqlite` | 장시간 실행, 3분 주기 | 표준화 성공 MongoDB의 미처리 실행을 모두 SQLite에 적재 |
| `python manage.py load_success_to_sqlite --once` | 즉시 1 cycle 실행 후 종료 | RDB 적재 수동 점검용 |

`crawl_and_load`를 사용할 때는 `crawl_records`를 별도로 동시에 실행하지 않는다. 두 프로세스가 같은 crawler lock을 두고 경쟁할 수 있고, 크롤링과 로딩 순서가 불필요하게 겹칠 수 있다.

`validation_records`는 크롤링·Bronze 적재와 겹치지 않도록 KST 기준 매시
`02, 05, 08, ... 59분 00초`에 실행한다. 즉 `crawl_and_load`의
`01, 04, 07, ... 58분` 실행 시각보다 1분 뒤에 표준화·검증을 시작한다.

## 표준화 성공 데이터의 SQLite 자동 적재

표준화·검증이 성공 collection에 통합 표준 문서를 저장한 뒤 SQLite로 옮기려면
다음 명령을 별도 프로세스로 실행한다.

```powershell
cd C:\encore_project\2nd_project_git\django
python manage.py load_success_to_sqlite `
  --config ..\validation_pipeline\config.json
```

이 명령은 표준화·검증이 끝난 다음 경계인 매시
`00, 03, 06, ..., 57분 00초(KST)`에 cycle을 수행한다. 각 cycle은
`encore_success_experiment.records`에서 아직
`second_project_sync_run`에 `SUCCESS`로 기록되지 않은 표준화 실행 1건을 자동으로
선택하고 적재한다. 한 cycle 안에서 이 과정을 미처리 실행이 없어질 때까지
반복하므로, 실행 ID를 사람이 확인하거나 입력할 필요가 없다.

즉시 한 번만 실행하려면 다음과 같이 한다.

```powershell
python manage.py load_success_to_sqlite `
  --config ..\validation_pipeline\config.json `
  --once
```

SQLite migration은 적재 프로세스를 처음 시작하기 전에 한 번 적용한다.

```powershell
python manage.py migrate --database=sqlite3
```

표준화 성공 collection의 문서 계약, PK upsert, 실패 재시도, 현재 sink와의 단일
collection 전제는 [RDB 적재 문서](05_rdb_loading.md)에 정리되어 있다.

## 로그 로테이션

`crawl_and_load`를 실행하면 크롤링과 Bronze 로딩 로그가 기록되고, 로그 기록 직전에
공통 writer가 로테이션 여부를 확인한다. 따라서 별도의 Linux `logrotate` 명령이나
Windows용 로테이션 프로세스를 추가로 실행할 필요가 없다.

- KST 00:00, 06:00, 12:00, 18:00 경계 이후 첫 로그 기록 시 회전한다.
- 활성 파일이 10MiB에 도달하면 다음 로그 기록 전에 회전한다.
- 파일별로 `.1`~`.5` 백업을 유지하며, 새 회전 때 가장 오래된 백업을 버린다.
- 해당 시간대에 로그가 없으면 빈 백업 파일을 만들지 않고 다음 기록 때 회전한다.

Django 자동화가 직접 관리하는 로그는 다음 두 파일이다.

```text
log_lake/raw_data/crawling_log.jsonl
log_lake/raw_data/raw_data_loading_log.jsonl
```

표준화·검증 pipeline도 `log_lake`를 로그 디렉터리로 지정하면 같은 정책으로
`standardized/standardize.log`, `standardized/validation.log`,
`standardized/pipeline.jsonl`, `standardized/quality.jsonl`,
`standardized/quarantine.jsonl`, `standardized/restoration.jsonl`을 회전한다. 전체 파일 목록과 확인 명령은
[Windows 로그 로테이션](04_log_rotation.md)에 정리되어 있다.

## Windows에서 계속 실행하기

### 내장 스케줄러 방식

PowerShell 창을 열어 다음 명령을 실행하고 창을 유지한다.

```powershell
cd C:\encore_project\2nd_project_git\django
.\.venv\Scripts\python.exe manage.py migrate second_project --database mongodb --skip-checks
.\.venv\Scripts\python.exe manage.py crawl_and_load
```

웹 서버 실행 창과 통합 스케줄러 실행 창은 별도다. 통합 스케줄러를 종료하려면 `Ctrl+C`를 누른다.

SQLite 적재도 별도 PowerShell 창에서 다음처럼 실행한다.

```powershell
cd C:\encore_project\2nd_project_git\django
.\.venv\Scripts\python.exe manage.py load_success_to_sqlite `
  --config ..\validation_pipeline\config.json
```

`load_success_to_sqlite`는 성공 collection에 새 실행이 없으면 아무 행도 변경하지
않고 다음 예약 시각까지 대기한다. 대기 중인 실행이 여러 개면 같은 cycle 안에서
모두 처리한 뒤 다음 예약 시각까지 대기한다.

### Windows 작업 스케줄러 방식

프로세스가 항상 살아 있지 않아도 되거나 재부팅 후 자동 시작이 필요하면 `--once` 모드를 작업 스케줄러에 등록한다.

실행 프로그램:

```text
C:\encore_project\2nd_project_git\django\.venv\Scripts\python.exe
```

인수:

```text
manage.py crawl_and_load --once --quiet
```

시작 위치:

```text
C:\encore_project\2nd_project_git\django
```

작업 주기는 3분으로 설정하되, 이전 작업이 아직 실행 중이면 새 인스턴스를 시작하지 않도록 작업 스케줄러의 중복 실행 정책을 설정한다. 외부 스케줄러를 사용할 때는 장시간 실행되는 기본 모드가 아니라 반드시 `--once`를 사용한다.

## 실행 전 확인

1. MongoDB가 실행 중인지 확인한다.
2. `MONGODB_URI`, `MONGODB_NAME`이 필요한 값으로 설정되어 있는지 확인한다.
3. 다음 migration을 최초 한 번 실행한다.

   ```powershell
   python manage.py migrate second_project --database mongodb --skip-checks
   ```

4. 실제 반복 실행 전에 `--once`로 한 사이클을 점검한다.

   ```powershell
   python manage.py crawl_and_load --once
   ```

6. SQLite 적재를 별도 운영한다면 `--once`로 미처리 실행을 모두 비우는 한 cycle을 점검한다.

   ```powershell
   python manage.py load_success_to_sqlite --once --dry-run `
     --config ..\validation_pipeline\config.json
   ```

7. 로그와 파일을 확인한다.

   ```powershell
   Get-Content .\log_lake\raw_data\crawling_log.jsonl -Tail 10
   Get-Content .\log_lake\raw_data\raw_data_loading_log.jsonl -Tail 10
   Get-Item .\data\raw_data\records.jsonl
   ```

## 실패 시 동작

| 상황 | 동작 |
|---|---|
| API 크롤링 실패 | 해당 주기의 MongoDB 로딩을 생략하고 다음 주기에 다시 시도 |
| 다른 크롤러가 lock 보유 | 종료 코드 `1`로 크롤링·적재를 모두 건너뛰고 다음 주기에 재시도 |
| JSONL 검증 오류 | 해당 행은 `bronze_quarantine`에 저장하고 실행은 `partial_failure`가 될 수 있음 |
| MongoDB 적재 실패 | 실행을 failed로 기록하고 다음 예약 주기에 재시도 |
| 입력 파일 변경 | checksum mismatch로 실행을 중단하고 원본을 삭제하지 않음 |

로더는 현재 JSONL 전체를 매번 읽으므로, 파일이 커진 뒤에는 3분 주기가 MongoDB 응답 시간보다 짧아지지 않는지 확인한다. 필요하면 `--batch-size`를 조정하거나 증분 offset/checkpoint 기반 로딩을 별도로 설계한다.
