# 04. Windows 로그 로테이션

## 적용 범위

로그 로테이션은 `log_lake` 폴더의 모든 파일을 wildcard로 처리하지 않는다.
프로그램이 로그로 기록하는 아래 8개 파일 각각에 같은 정책을 적용한다.

```text
log_lake/raw_data/crawling_log.jsonl
log_lake/raw_data/raw_data_loading_log.jsonl
log_lake/standardized/standardize.log
log_lake/standardized/validation.log
log_lake/standardized/pipeline.jsonl
log_lake/standardized/quality.jsonl
log_lake/standardized/quarantine.jsonl
log_lake/standardized/restoration.jsonl
```

`records.jsonl`, 실행별 `output` 파일, manifest 같은 데이터 파일은 로그가 아니므로
로테이션 대상이 아니다. 표준화·검증 로그는 `--log-directory`에
`log_lake/standardized`를 지정한 실행에서 해당 위치에 생성된다.

## 공통 정책

각 로그 파일은 독립적으로 아래 정책을 적용한다.

1. KST 기준 00:00, 06:00, 12:00, 18:00 경계 이후 첫 로그 기록 시 회전한다.
2. 파일 크기가 10MiB에 도달하면 다음 로그 기록 전에 회전한다.
3. 기존 파일은 `.1`로 이동하고, 기존 백업은 한 단계씩 이동한다.
4. `.1`~`.5`까지만 유지한다. 여섯 번째 회전부터 가장 오래된 `.5`가 삭제되거나
     덮어써진다.

시간 경계에 로그가 기록되지 않으면 빈 백업 파일을 만들지 않는다. 다음 로그 기록이
발생할 때 경계 통과 여부를 확인해 회전한다. 따라서 별도의 timer 프로세스나 Linux
`logrotate` 설정은 필요하지 않다.

## Django 자동화 실행

명령을 실행하면 로그 기록과 로테이션이 같은 프로세스 안에서 자동으로 처리된다.

```powershell
cd C:\encore_project\2nd_project_git\django

# 크롤링 + Bronze 적재를 즉시 한 번 실행
python manage.py crawl_and_load --once

# 크롤링 + Bronze 적재를 3분 주기로 계속 실행
python manage.py crawl_and_load

# 크롤링만 실행
python manage.py crawl_records

# 기존 JSONL을 Bronze에 한 번 적재
python manage.py load_raw_records

# 표준화·검증을 즉시 한 번 실행
python manage.py validation_records --once
```

## 회전 결과 확인

```powershell
cd C:\encore_project\2nd_project_git\django

Get-ChildItem .\log_lake -Recurse -File |
  Where-Object { $_.Name -match '^(crawling_log|raw_data_loading_log|pipeline|quality|quarantine|restoration|standardize|validation)\.(jsonl|log)(\.[1-5])?$' }
```

예를 들어 크롤링 로그는 다음처럼 활성 파일과 백업 파일을 함께 확인한다.

```powershell
Get-ChildItem .\log_lake\raw_data\crawling_log.jsonl*
Get-Content .\log_lake\raw_data\crawling_log.jsonl -Tail 20
```

## 구현 위치와 테스트

공통 정책 구현은 다음 파일에 있다.

| 영역 | 구현 파일 |
|---|---|
| 크롤링·Bronze | `second_project/service/log_rotation.py` |
| 표준화·검증 | `validation_pipeline/src/mongo_pipeline/log_rotation.py` |
| 크롤링 writer 연결 | `crawling/crawler/ingest_logging.py` |
| Bronze writer 연결 | `second_project/service/structured_logging.py` |
| 표준화·검증 writer 연결 | `validation_pipeline/src/mongo_pipeline/loggers.py` |

Django 관련 테스트:

```powershell
cd C:\encore_project\2nd_project_git\django
python -m unittest crawling.test_api_key_metadata loading.test_loading loading.test_bronze_loader -v
```

표준화·검증 로테이션 테스트:

```powershell
cd C:\encore_project\2nd_project_git\validation_pipeline
$env:PYTHONPATH = "$PWD\src"
python -m unittest tests.test_log_rotation -v
```
