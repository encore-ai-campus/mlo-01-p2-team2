# 내부 공개 데이터 증분 크롤러

단계별 운영 문서는 [크롤링·로딩 운영 문서](../docs/README.md)에서 확인한다.

이 프로젝트의 크롤러는 내부 API에서 공개된 레코드를 수집해 UTF-8 JSON Lines(JSONL) 파일에 누적한다. 최초 실행에서는 현재 공개분 전체를 수집하고, 이후 실행에서는 서버가 반환한 `checkpoint`를 `cursor`로 전달해 새로 공개된 레코드만 수집한다.

API의 `payload` 값은 정제하거나 표준화하지 않는다. 앞뒤 공백, 탭, ID 표기, 날짜 형식 등은 원천 값 그대로 보존하고 정제는 이후 단계에서 별도로 수행한다.

## 주요 기능

- `/health/ready`를 이용한 서버 준비 상태 확인
- `/public/v1/key`에서 24시간 유효한 API 키 발급
- 프로젝트 루트의 `.env`에 API 키를 저장하고 Git과 로그에서 제외
- `.env`에 키가 없거나 한국 시간 00:01인 경우에만 신규 키 발급
- `/api/v1/meta`를 이용한 데이터셋·컬럼·공개 건수 확인
- `next_cursor`를 이용한 페이지 반복 수집
- `checkpoint`를 이용한 실행 간 증분 수집
- JSONL 한 파일에 레코드 단위 누적
- `dataset_id + record_id` 기준 중복 방지
- UTF-8 명시 해석과 한글 원문 보존
- HTTP `429`, `5xx` 재시도 및 기타 HTTP 오류 처리
- 운영체제 파일 잠금을 이용한 중복 실행 방지
- `logs/raw_data_log.jsonl`에 INFO, WARN, ERROR 구조화 로그 누적
- Windows PowerShell과 Ubuntu에서 공통으로 동작하는 경로 처리
- `python manage.py crawl_records` Django management command 제공

## 처리 흐름

```text
실행 잠금 획득
    ↓
기존 JSONL과 crawl_state 확인
    ↓
서버 준비 상태 확인
    ↓
`.env` 키 확인
    ↓
`.env`에 키가 없거나 00:01이면 키 발급·저장
    ↓
meta 조회 및 데이터셋 검증
    ↓
저장된 checkpoint를 cursor로 records 요청
    ↓
has_more=true 동안 next_cursor로 반복
    ↓
응답 검증 및 신규 record_id만 JSONL에 추가
    ↓
모든 기록이 성공한 후 checkpoint 갱신
    ↓
실행 잠금 해제
```

최초 실행에는 checkpoint가 없으므로 cursor 없이 시작한다. 최초 실행 도중 실패하면 초기 상태를 유지하고, 다음 실행에서 처음부터 다시 요청한 뒤 이미 기록된 `record_id`를 건너뛴다.

## 프로젝트 구조

```text
django/
├─ .env                     # 실행 시 생성되는 API 키(비밀 파일, Git 제외)
├─ manage.py
├─ crawling/
│  ├─ README.md              # 크롤러 설명과 Ubuntu/WSL 운영 방법
│  ├─ crawl_records.py       # 독립 실행 진입점과 공통 실행 함수
│  └─ crawler/
│     ├─ config.py           # 기본 주소, 제한 시간, 저장 경로
│     ├─ api_client.py       # API 키와 HTTP 요청·재시도
│     ├─ key_store.py        # .env API 키 읽기·안전한 저장
│     ├─ ingest_logging.py   # ingest JSONL 로그 형식과 비밀값 제거
│     ├─ validator.py        # meta와 records 응답 검증
│     ├─ storage.py          # JSONL, 상태 파일, 실행 잠금
│     └─ service.py          # 전체 수집 순서 조정
├─ second_project/
│  ├─ apps.py
│  └─ management/
│     └─ commands/
│        ├─ crawl_records.py                # 크롤링 스케줄러
│        ├─ crawl_and_load.py               # 크롤링 후 Bronze 적재까지 연결
│        └─ load_raw_records.py             # JSONL Bronze 적재
├─ data/
│  └─ raw_data/
│     ├─ records.jsonl       # 실행 시 생성되는 누적 raw 레코드
│     └─ state/
│        ├─ crawl_state.json # 실행 시 생성되는 마지막 성공 상태
│        └─ crawler.lock     # 중복 실행 방지용 잠금 파일
├─ logs/
│  ├─ raw_data_log.jsonl     # 크롤러 구조화 로그
│  └─ cron.log                # cron/Django 표준 오류 로그
└─ requirements.txt
```

`.env`, `records.jsonl`, `crawl_state.json`, `crawler.lock`, `logs/`는 Git에 포함되지 않는다.

## 저장 형식

### records.jsonl

한 줄에 MongoDB 문서 한 건으로 사용할 수 있는 JSON 객체 하나를 저장한다. 일반 JSON 배열과 달리 파일 전체를 `[`와 `]`로 감싸지 않고 줄 사이에 쉼표도 넣지 않는다.

```json
{"dataset_id":"데이터셋 ID","source_filename":"원본 파일명","source_sha256":"원본 파일 해시","record_id":153751,"source_row_no":1,"source_record_sha256":"레코드 해시","release_slot":0,"scheduled_release_at":"2026-08-24T16:12:00+09:00","payload":{"area_no":"원천 값","mgr_no":"원천 값"},"_crawl":{"run_id":"크롤링 실행 ID","collected_at":"수집 시각","api_server_time":"API 서버 시각"}}
```

`payload` 내부 값은 raw 데이터이므로 수집 단계에서 공백 제거, 날짜 변환, ID 표준화 등을 수행하지 않는다.

### crawl_state.json

다음 실행이 이어받을 위치와 데이터셋 정보를 저장한다.

```json
{
  "schema_version": 1,
  "dataset_id": "데이터셋 ID",
  "source_sha256": "원본 데이터셋 SHA-256",
  "checkpoint": "다음 실행에 사용할 checkpoint",
  "last_record_id": 166273,
  "released_rows": 12523,
  "latest_scheduled_release_at": "최근 공개 기준 시각",
  "last_server_time": "마지막 API 서버 시각",
  "updated_at": "상태 파일 갱신 시각"
}
```

checkpoint는 해석하거나 수정하지 않고 서버가 반환한 문자열 그대로 저장한다. 모든 JSONL 기록과 건수 검증이 성공한 뒤에만 상태 파일을 갱신한다.

### crawler.lock

cron 실행이 겹쳤을 때 두 크롤러가 동시에 파일을 수정하지 못하도록 하는 잠금 파일이다. 파일에는 프로세스 ID만 기록되며 API 키, checkpoint, 개인정보는 포함되지 않는다.

크롤러가 종료된 후에도 파일은 남아 있을 수 있다. 파일 존재 여부가 아니라 운영체제의 실제 잠금 상태로 중복 실행을 판단하므로 직접 삭제할 필요가 없다.

### .env와 API 키 갱신

API 키는 프로젝트 루트의 `.env`에 다음 형식으로 저장된다.

```dotenv
CRAWLER_API_KEY=발급된_API_키
```

키 선택 규칙은 다음과 같다.

- `.env`가 없거나 `CRAWLER_API_KEY`가 없으면 실행 시각과 관계없이 키를 발급받아 저장한다.
- 한국 시간 00:01:00∼00:01:59에 실행하면 기존 키가 있어도 새 키를 발급받아 교체한다.
- 그 외의 시각에는 `.env`의 키를 메모리에 불러와 사용한다.
- HTTP 401이 발생해도 예외적으로 키를 재발급하지 않고 해당 실행을 실패로 기록한다. 키 요청 조건은 `.env` 키 누락 또는 00:01로 제한된다.

`.env`는 비밀 파일이므로 공유하거나 Git에 커밋하지 않는다. 크롤러가 새로 생성하는 파일은 가능한 운영체제에서 현재 사용자만 읽고 쓸 수 있도록 `0600`으로 설정한다.

## 실행 요구사항

- Python 3.14.6
- `requests==2.34.2`
- 내부 API `http://192.168.0.51:8000`에 접근 가능한 네트워크
- JSONL과 로그를 저장할 충분한 디스크 공간

## Ubuntu 환경 준비

아래 명령은 Django 프로젝트 루트에서 실행한다.

```bash
cd /mnt/c/encore_project/2nd_project_git/django
```

Ubuntu 패키지로 Python 3.14를 설치했다면 가상환경 지원 패키지가 필요할 수 있다.

```bash
sudo apt update
sudo apt install python3.14-venv
```

가상환경을 만들고 의존성을 설치한다.

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

설치 결과를 확인한다.

```bash
.venv/bin/python --version
.venv/bin/python -c "import requests; print(requests.__version__)"
```

프로젝트가 WSL의 Windows 드라이브에 있다면 다음처럼 접근할 수 있다.

```text
Windows: C:\encore_project\2nd_project_git\django
WSL:     /mnt/c/encore_project/2nd_project_git/django
```

가상환경 디렉터리 이름은 운영체제와 관계없이 `.venv`로 고정한다. 단, Windows용 `.venv`와 Ubuntu/WSL용 `.venv`는 실행 파일 형식이 달라 서로 공유할 수 없으므로 현재 실행 환경에서 `.venv`를 생성한다.

## 테스트

실제 내부 API나 `data/raw_data`를 변경하지 않고 가짜 응답과 임시 폴더로 기능을 검사한다.

Ubuntu/WSL:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

PowerShell:

```powershell
python -m unittest discover -s tests -v
```

정상 결과:

```text
Ran 14 tests
OK
```

테스트 범위에는 UTF-8 한글 처리, HTTP 재시도, 401 시 키 미갱신, `.env` 보존 저장, 00:01 갱신 판정, 최초·증분 수집, 새 데이터 없음, 다중 페이지 실패 복구, 중복 방지, 응답 건수 불일치, 상태 파일 불일치, 중복 실행 잠금, ingest JSONL 로그 필드·비밀값 제거가 포함된다.

## Django 명령으로 수동 실행

프로젝트 경로는 현재 저장소 기준으로 `django/`이다. 먼저 실제 API를 호출하지 않는 `--help`로 Django management command 등록 상태를 확인한다.

```bash
cd /mnt/c/encore_project/2nd_project_git/django
.venv/bin/python manage.py crawl_records --help
```

Django management command는 기본적으로 내장 스케줄러 모드로 실행된다. 사용자가 아래 명령을 직접 입력했을 때만 시작된다.

Windows에서는 별도의 Windows 작업 스케줄러나 외부 Scheduler 없이 아래 명령만 실행한다. 명령을 실행한 프로세스가 살아 있는 동안 매시 `01, 04, 07, ... 58분 00초(KST)`에 내장 스케줄러가 반복 실행한다.

```powershell
cd C:\encore_project\2nd_project_git\django
.\.venv\Scripts\python.exe manage.py crawl_records
```

종료하려면 `Ctrl+C`를 누른다. 이 방식은 Django 웹 서버와 별도의 management command 프로세스이므로, 웹 서버를 실행하는 터미널과 스케줄러를 실행하는 터미널을 각각 유지해야 한다.

크롤링이 성공한 뒤 MongoDB Bronze 적재까지 이어서 수행하려면 통합 명령을 사용한다.
통합 명령도 같은 3분 주기로 동작하므로 `crawl_records`와 동시에 실행하지 않는다.

```powershell
cd C:\encore_project\2nd_project_git\django
.\.venv\Scripts\python.exe manage.py crawl_and_load
```

실제 API와 MongoDB를 즉시 한 번만 확인하려면 `--once`를 붙인다.

```powershell
.\.venv\Scripts\python.exe manage.py crawl_and_load --once
```

기본 저장 위치는 `django/`를 기준으로 계산되므로 현재 작업 디렉터리와 관계없이 `django/data/raw_data`와 `django/logs`를 사용한다.

기존 독립 실행 진입점도 사용할 수 있다.

```bash
.venv/bin/python crawling/crawl_records.py
```

옵션 확인:

```bash
.venv/bin/python manage.py crawl_records --help
```

지원 옵션:

- `--base-url`: API 서버 기본 주소 변경
- `--data-dir`: JSONL과 상태 파일 저장 경로 변경
- `--limit`: 페이지당 요청 건수 변경, 기본값 1000
- `--log-level`: DEBUG, INFO, WARNING, ERROR 중 선택

## 로그

로그는 ingest 기준에 맞춰 `logs/raw_data_log.jsonl`에 UTF-8 JSON Lines 형식으로 쌓인다. 실행할 때마다 새 `run_id`를 발급하며 `stage`는 `ingest`로 기록한다. 각 줄은 독립적인 JSON 객체이므로 실행 중에도 끝에 계속 추가할 수 있다.

공통 필드는 다음과 같다.

- `timestamp`, `level`, `run_id`, `stage`, `dataset_id`, `status`
- `input_count`, `success_count`, `failure_count`, `quarantine_count`, `duration_ms`
- `message`, 문서에 정의된 표준 오류 코드가 해당할 때만 `error_code`

`status`는 `success`, `partial_failure`, `failed`만 사용하고 로그 레벨은 `INFO`, `WARN`, `ERROR`를 사용한다. `input_count = success_count + failure_count + quarantine_count` 관계를 유지한다.

로그에는 다음 실행 정보가 포함된다.

- 실행 시작과 완료
- 데이터셋 ID
- 공개 건수와 실제 수집 건수
- 페이지 번호와 페이지별 건수
- 신규 추가 및 중복 건수
- 재시도와 실패 원인
- 실패 유형(문서에 정의된 표준 오류 코드가 있는 경우에만 `error_code` 포함)

다음 정보는 로그에 출력하지 않는다.

- API 키
- cursor와 checkpoint 전체 문자열
- 이름, 사번 등 payload의 실제 값
- 인증 요청 헤더
- private endpoint와 내부 파일 경로
- 정제하지 않은 traceback

종료 코드는 다음과 같다.

| 코드 | 의미 |
|---:|---|
| 0 | 정상 완료, 새 데이터 없음, 키가 아직 유효하지 않음, 중복 실행 건너뜀 |
| 1 | API 요청, 응답 검증 또는 파일 저장 실패 |
| 2 | 실행 설정 또는 기존 저장 상태 불일치 |

## cron 자동 실행

현재 경로가 Windows(`C:\encore_project\2nd_project_git`)이므로 `crontab`은 WSL/Ubuntu에서 실행한다. WSL에서는 Windows 경로를 `/mnt/c/encore_project/2nd_project_git`으로 사용하며, WSL 환경에서도 가상환경 디렉터리 이름은 `.venv`로 고정한다. 단, Windows와 WSL의 `.venv`는 실행 파일 형식이 달라 각각 해당 환경에서 생성해야 한다.

WSL/Ubuntu에서 다음을 한 번 실행한다.

```bash
cd /mnt/c/encore_project/2nd_project_git/django
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p logs data/raw_data
chmod 700 data/raw_data logs
```

현재 사용자 crontab을 연다.

```bash
crontab -e
```

다음 항목을 등록한다. 매시 `01, 04, 07, ... 58분`, 즉 `3n+1`분에 독립 실행 진입점을 한 번 실행한다. Django management command는 장시간 실행되므로 cron에서는 사용하지 않는다.

```cron
SHELL=/bin/bash
CRON_TZ=Asia/Seoul
1-59/3 * * * * umask 077 && cd /mnt/c/encore_project/2nd_project_git/django && /mnt/c/encore_project/2nd_project_git/django/.venv/bin/python crawling/crawl_records.py >> /mnt/c/encore_project/2nd_project_git/django/logs/cron.log 2>&1
```

- `1-59/3`: 매시 `3n+1`분에 실행하여 00:01에 새 API 키를 발급
- cron은 매시 해당 분의 00초에 실행을 시작하도록 요청
- `umask 077`: 새 데이터와 로그를 현재 사용자만 읽고 쓸 수 있게 생성
- 절대 Python 경로: cron의 제한된 PATH 환경에서도 WSL용 가상환경 사용
- `>> .../logs/cron.log 2>&1`: Django/cron 표준 출력과 오류를 별도 로그에 누적
- 크롤러 자체의 구조화 실행 로그는 `logs/raw_data_log.jsonl`에도 계속 누적

`CRON_TZ`를 지원하지 않는 cron에서는 WSL 시간대를 한국 시간으로 설정하거나, `timedatectl`로 `Asia/Seoul` 여부를 확인한다.

```bash
timedatectl
date
```

등록 내용을 확인한다.

```bash
crontab -l
sudo service cron status
```

cron 서비스가 실행 중이 아니라면 활성화한다.

```bash
sudo service cron start
```

로그를 실시간으로 확인한다.

```bash
tail -f /mnt/c/encore_project/2nd_project_git/django/logs/raw_data_log.jsonl
```

경고와 오류만 확인한다.

```bash
grep -E '"level":"(WARN|ERROR)"' /mnt/c/encore_project/2nd_project_git/django/logs/raw_data_log.jsonl
```

cron 서비스 기록은 다음처럼 확인할 수 있다.

```bash
journalctl -u cron --since today
```

WSL에서 cron을 사용한다면 Windows 재부팅 후 WSL과 cron 서비스가 실제로 시작됐는지도 확인해야 한다. WSL을 시작할 때 cron을 자동으로 띄우려면 WSL 배포판의 systemd 설정을 사용하거나 Windows 작업 스케줄러에서 `wsl.exe -d Ubuntu -- sudo service cron start`를 등록한다.

## logrotate 설정

3분마다 로그를 추가하면 파일이 계속 커지므로 일 단위 로그 순환을 권장한다.

설정 파일을 연다.

```bash
sudo nano /etc/logrotate.d/encore-crawler
```

아래 블록의 경로, `USER`, `GROUP`을 실제 값으로 교체해 저장한다. 사용자명과 기본 그룹은 각각 `id -un`, `id -gn`으로 확인할 수 있다.

```text
/mnt/c/encore_project/2nd_project_git/django/logs/raw_data_log.jsonl
/mnt/c/encore_project/2nd_project_git/django/logs/cron.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    su USER GROUP
    create 0600 USER GROUP
}
```

logrotate 설정에는 Python 실행 명령을 넣지 않는다. 첫 줄에는 로그 파일의 절대 경로가 와야 한다.

실제 파일을 변경하지 않고 설정을 검증한다.

```bash
sudo logrotate -d /etc/logrotate.d/encore-crawler
```

정상 설정은 `Handling 1 logs`를 표시한다. 아직 순환 시점이 아니면 `log does not need rotating`이 나올 수 있으며 이는 오류가 아니다.

## 데이터 품질 및 실패 처리

다음 조건은 checkpoint를 갱신하지 않고 실행을 중단한다.

- `count`와 실제 `items` 개수가 다름
- `has_more=true`인데 `next_cursor`가 없음
- 동일한 `next_cursor`가 반복됨
- 페이지의 `dataset_id`가 meta와 다름
- `payload` 컬럼이 meta의 `columns`와 다름
- `released_rows`가 이전 실행보다 감소함
- checkpoint 이후 예상 건수와 실제 수집 건수가 다름
- 기존 `dataset_id` 또는 `source_sha256`이 변경됨
- JSONL과 상태 파일의 조합이 안전하지 않음

데이터셋이 변경된 경우 기존 JSONL에 자동으로 섞지 않는다. 새로운 파일로 분리할지 기존 데이터를 보관하고 초기화할지는 데이터 변경 이유를 확인한 후 결정한다.

## MongoDB 적재

JSONL은 각 줄이 독립된 JSON 객체이므로 MongoDB에 직접 가져올 수 있다.

```bash
mongoimport \
  --uri "MongoDB 연결 주소" \
  --db "데이터베이스명" \
  --collection "raw_records" \
  --type json \
  --file data/raw_data/records.jsonl
```

중복 방지를 위해 MongoDB에는 `dataset_id + record_id` 조합의 고유 인덱스를 두는 것을 권장한다. 비밀번호와 연결 문자열은 코드, README, 로그, Git에 저장하지 않는다.

## 운영 시 주의사항

- 내부 API 이용 권한과 데이터 사용 범위를 확인한다.
- JSONL과 로그에는 이름과 사번 등 개인정보가 포함될 수 있으므로 접근 권한과 보관 기간을 관리한다.
- `records.jsonl`을 정제 결과로 덮어쓰지 않는다.
- `crawl_state.json`을 임의로 수정하거나 checkpoint를 직접 생성하지 않는다.
- 실행 중 `crawler.lock`을 삭제하지 않는다.
- cron 적용 후 최초 2~3회는 로그와 수집 건수를 직접 확인한다.
