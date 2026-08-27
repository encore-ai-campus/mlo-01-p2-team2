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
3. 크롤러가 `records.jsonl`, 상태 파일, `raw_data_log.jsonl`을 갱신한다.
4. 크롤링이 오류 코드로 끝나면 loader를 호출하지 않는다.
5. 크롤링 단계가 계속 가능한 종료 코드로 끝나면 `run_loader()`를 호출한다.
6. loader가 `records.jsonl`을 읽어 MongoDB Bronze 컬렉션에 적재한다.
7. 적재 결과를 `pipeline.jsonl`에 기록하고 다음 예약 시각을 기다린다.

크롤러 로그와 loader 로그는 서로 다른 파일에 저장된다.

| 단계 | 로그 파일 | stage |
|---|---|---|
| API 크롤링 | `logs/raw_data_log.jsonl` | `ingest` |
| Bronze 로딩 | `logs/pipeline.jsonl` | `bronze` |

## 기존 명령과의 차이

| 명령 | 실행 방식 | 역할 |
|---|---|---|
| `python manage.py crawl_records` | 장시간 실행, 3분 주기 | 크롤링만 수행 |
| `python manage.py load_raw_records` | 1회 실행 후 종료 | 기존 JSONL만 MongoDB에 적재 |
| `python manage.py crawl_and_load` | 장시간 실행, 3분 주기 | 크롤링 성공 후 로딩까지 수행 |
| `python manage.py crawl_and_load --once` | 즉시 1회 실행 후 종료 | 수동 점검·작업 스케줄러용 |

`crawl_and_load`를 사용할 때는 `crawl_records`를 별도로 동시에 실행하지 않는다. 두 프로세스가 같은 crawler lock을 두고 경쟁할 수 있고, 크롤링과 로딩 순서가 불필요하게 겹칠 수 있다.

## Windows에서 계속 실행하기

### 내장 스케줄러 방식

PowerShell 창을 열어 다음 명령을 실행하고 창을 유지한다.

```powershell
cd C:\encore_project\2nd_project_git\django
.\.venv\Scripts\python.exe manage.py migrate second_project --database mongodb --skip-checks
.\.venv\Scripts\python.exe manage.py crawl_and_load
```

웹 서버 실행 창과 통합 스케줄러 실행 창은 별도다. 통합 스케줄러를 종료하려면 `Ctrl+C`를 누른다.

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

5. 로그와 파일을 확인한다.

   ```powershell
   Get-Content .\logs\raw_data_log.jsonl -Tail 10
   Get-Content .\logs\pipeline.jsonl -Tail 10
   Get-Item .\data\raw_data\records.jsonl
   ```

## 실패 시 동작

| 상황 | 동작 |
|---|---|
| API 크롤링 실패 | 해당 주기의 MongoDB 로딩을 생략하고 다음 주기에 다시 시도 |
| 다른 크롤러가 lock 보유 | 크롤링을 건너뛰며 기존 JSONL을 기준으로 로딩할 수 있으므로, `crawl_records`와 동시 실행하지 않음 |
| JSONL 검증 오류 | 해당 행은 `bronze_quarantine`에 저장하고 실행은 `partial_failure`가 될 수 있음 |
| MongoDB 적재 실패 | 실행을 failed로 기록하고 다음 예약 주기에 재시도 |
| 입력 파일 변경 | checksum mismatch로 실행을 중단하고 원본을 삭제하지 않음 |

로더는 현재 JSONL 전체를 매번 읽으므로, 파일이 커진 뒤에는 3분 주기가 MongoDB 응답 시간보다 짧아지지 않는지 확인한다. 필요하면 `--batch-size`를 조정하거나 증분 offset/checkpoint 기반 로딩을 별도로 설계한다.
