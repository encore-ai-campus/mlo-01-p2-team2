# Django 환경 구성

크롤링·로딩·자동화 단계별 문서는 [`docs/README.md`](docs/README.md)를 참고한다.

## 1. 문서 목적

이 문서는 `git pull` 이후 기존 개발 환경에서 Django 프로젝트를 사용할 수 있도록 현재 프로젝트 구성과 데이터베이스 설정을 기록한다.

가상환경은 이미 준비되어 있는 상태를 전제로 하며, 이 문서에서는 가상환경의 생성 및 실행 방법을 다루지 않는다.

## 2. Django 프로젝트 구성

- Django 프로젝트 경로: `django/`
- Django 설정 패키지: `config`
- 생성된 앱: `second_project`
- 앱 경로: `django/second_project/`
- Django 실행 파일: `django/manage.py`

`second_project` 앱은 `config/settings.py`의 `INSTALLED_APPS`에 등록되어 있다.

<<<<<<< Updated upstream
## 3. 사용 가능한 데이터베이스
=======
## 3. 프로젝트 구조

```text
django/
├── manage.py
├── config/
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
└── second_project/
    ├── presentation/
    │   ├── urls.py
    │   └── views.py
    ├── service/
    │   ├── bronze_loader.py
    │   ├── loader_runner.py
    │   └── loader_cli.py
    ├── repository/
    │   ├── models.py
    │   └── mongodb_repository.py
    ├── management/commands/
    │   ├── crawl_records.py
    │   ├── crawl_and_load.py
    │   └── load_raw_records.py
    ├── admin.py
    ├── apps.py
    ├── tests.py
    └── migrations/
```

## 4. `.venv` 설정 방법

### `.venv`를 GitHub에 업로드하지 않는 이유

현재 `django/.venv/.gitignore`에 다음 규칙이 설정되어 있어 `.venv` 내부 파일 전체가 Git에서 제외된다.

```text
*
```

가상환경은 운영체제와 Python 설치 경로에 의존하므로 GitHub에 직접 업로드하는 대신 `requirements.txt`만 저장소에서 관리한다.

### Windows PowerShell 기준 설정

저장소 루트에서 다음 명령어를 순서대로 실행한다.

```powershell
cd django
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 5. 사용 가능한 데이터베이스
>>>>>>> Stashed changes

현재 Django 설정에는 다음 데이터베이스가 등록되어 있다.

| 데이터베이스 | Django 연결 별칭 | 설정 내용 |
|---|---|---|
| SQLite | `sqlite3` | `django/db.sqlite3` |
| MongoDB | `mongodb` | 로컬 MongoDB 서버 및 `db_mount` 데이터베이스 |

### SQLite

- 엔진: `django.db.backends.sqlite3`
- 데이터베이스 파일: `django/db.sqlite3`

### MongoDB

- 엔진: `django_mongodb_backend`
- 기본 접속 주소: `mongodb://127.0.0.1:27017`
- 기본 데이터베이스명: `db_mount`
- 접속 주소와 데이터베이스명은 환경 변수로 변경할 수 있다.

```text
BOOKSTORE_MONGODB_URI
BOOKSTORE_MONGODB_NAME
```

## 4. Git pull 이후 확인

가상환경이 연결된 상태에서 Django 프로젝트 경로로 이동한 뒤 다음 명령어로 구성을 확인할 수 있다.

<<<<<<< Updated upstream
```bash
cd django
python manage.py check
=======
| 경로 | 연결 대상 | 역할 |
|---|---|---|
| `/admin/` | `admin.site.urls` | Django 관리자 페이지 |
| `/second_project/` | `second_project.presentation.urls` | `second_project` 앱 URL 진입점 |

`config/urls.py`에서 앱 URL을 다음과 같이 연결한다.

```python
path("second_project/", include("second_project.presentation.urls"))
```

앱 내부의 세부 URL은 `second_project/presentation/urls.py`에서 관리하고, 연결된 요청은 `second_project/presentation/views.py`에서 처리한다.

## 7. MongoDB 대시보드

`/second_project/`는 validation pipeline의 MongoDB 성공·실패 데이터와 최근 실행
상태를 조회하는 읽기 전용 대시보드다. 기본 대상은 현재 적재된 다음 컬렉션이다.

| 표시 영역 | 기본 대상 |
|---|---|
| 성공 데이터 | `encore_success_experiment.records` |
| 실패·격리 데이터 | `encore_failure_experiment.records` |
| 실행 이력 | `encore_success_experiment.pipeline_runs` |
| Bronze 원문 | `second_project.bronze_raw_records` |
| Silver 모델 | 성공 DB의 `silver_*` 컬렉션 |

MongoDB가 실행 중이고 `MONGODB_URI`가 기본값과 다르지 않다면 다음처럼 실행한다.

```powershell
cd django
python manage.py runserver
```

브라우저에서 `http://127.0.0.1:8000/second_project/`를 연다. DB명이나 컬렉션명이
다른 실행 결과를 보려면 서버를 시작하기 전에 다음 환경변수를 설정한다.

```powershell
$env:DASHBOARD_SUCCESS_DATABASE = "encore_success_experiment_v2"
$env:DASHBOARD_FAILURE_DATABASE = "encore_failure_experiment_v2"
$env:DASHBOARD_REPORT_DATABASE = "encore_success_experiment_v2"
$env:DASHBOARD_SILVER_DATABASE = "encore_success_experiment_v2"
python manage.py runserver
```

대시보드는 원문 문서 자체를 출력하지 않고 건수·실행 ID·오류 코드·원천 ID만 표시한다.

## 8. 계층화 구조

`second_project` 앱은 역할에 따라 다음과 같이 구성되어 있다.

| 계층 | 경로 | 역할 |
|---|---|---|
| Presentation | `second_project/presentation/` | URL 연결 및 요청·응답 처리 |
| Service | `second_project/service/` | 비즈니스 로직 처리 |
| Repository | `second_project/repository/` | 데이터 모델 및 데이터 저장소 접근 |

전체 요청 흐름은 다음과 같다.

```text
Client
  ↓
config/urls.py
  ↓
second_project/presentation/urls.py
  ↓
second_project/presentation/views.py
  ↓
second_project/service/
  ↓
second_project/repository/
  ↓
Database
```

## 9. Bronze 적재

`crawling`은 수집만 담당하고, 수집 결과를 읽어 검증·로그·MongoDB 적재를 수행하는 코드는 `second_project` 앱 내부에 있다. 기존 `loading/load_raw_records.py`는 같은 앱 로직을 호출하는 호환 진입점이다.

```powershell
cd django
python manage.py migrate second_project --database mongodb --skip-checks
python manage.py load_raw_records --help
python manage.py load_raw_records
python manage.py crawl_and_load --once
```

MongoDB migration은 collection과 인덱스만 준비하며, 실제 JSONL 데이터는 management command가 적재한다. `MONGODB_NAME`의 기본값은 `second_project`이므로 이전의 `db_mount` 데이터베이스와는 별도 데이터베이스다. 크롤러 로그는 `log_lake/raw_data/crawling_log.jsonl`, Bronze 로더 로그는 `log_lake/raw_data/raw_data_loading_log.jsonl`에 남기며, 로그 본문은 MongoDB에 적재하지 않는다.

`python manage.py crawl_and_load`를 실행하면 기존 크롤러 스케줄과 같은 KST 3분 주기로 크롤링을 수행하고, 크롤링이 성공한 뒤 `records.jsonl`을 MongoDB에 적재한다. `--once`를 붙이면 예약 시각을 기다리지 않고 한 번만 실행한다.

## 10. Git pull 이후 확인

가상환경 설정과 패키지 설치가 완료된 뒤 Django 프로젝트 경로에서 다음 명령어로 구성을 확인할 수 있다.

```powershell
cd django
python manage.py check --database sqlite3
>>>>>>> Stashed changes
```

오류 없이 명령어가 완료되면 Django 프로젝트 구성이 정상적으로 인식된 상태이다.
