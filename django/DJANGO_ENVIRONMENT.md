# Django 환경 구성

## 1. 문서 목적

이 문서는 `git pull` 이후 Django 프로젝트를 사용할 수 있도록 현재 프로젝트 구성, 가상환경 설정 방법, 데이터베이스, URL 연결 및 계층 구조를 기록한다.

`.venv` 폴더는 GitHub에 업로드하지 않고, 저장소에 포함된 의존성 목록을 기준으로 로컬에서 다시 구성한다.

## 2. Django 프로젝트 구성

- Django 프로젝트 경로: `django/`
- Django 설정 패키지: `config`
- 생성된 앱: `second_project`
- 앱 경로: `django/second_project/`
- Django 실행 파일: `django/manage.py`

`second_project` 앱은 `config/settings.py`의 `INSTALLED_APPS`에 등록되어 있다.

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

현재 Django 설정에는 다음 데이터베이스가 등록되어 있다.

| 데이터베이스 | Django 연결 별칭 | 설정 내용 |
|---|---|---|
| SQLite | `sqlite3` | `django/db.sqlite3` |
| MongoDB | `mongodb` | 로컬 MongoDB 서버 및 `second_project` 데이터베이스 |

### SQLite

- 엔진: `django.db.backends.sqlite3`
- 데이터베이스 파일: `django/db.sqlite3`

### MongoDB

- 엔진: `django_mongodb_backend`
- 기본 접속 주소: `mongodb://127.0.0.1:27017`
- 기본 데이터베이스명: `second_project`
- 접속 주소와 데이터베이스명은 환경 변수로 변경할 수 있다.

```text
MONGODB_URI
MONGODB_NAME
```

## 6. URL 연결

프로젝트 URL과 `second_project` 앱의 URL 연결이 완료되어 있다.

| 경로 | 연결 대상 | 역할 |
|---|---|---|
| `/admin/` | `admin.site.urls` | Django 관리자 페이지 |
| `/second_project/` | `second_project.presentation.urls` | `second_project` 앱 URL 진입점 |

`config/urls.py`에서 앱 URL을 다음과 같이 연결한다.

```python
path("second_project/", include("second_project.presentation.urls"))
```

앱 내부의 세부 URL은 `second_project/presentation/urls.py`에서 관리하고, 연결된 요청은 `second_project/presentation/views.py`에서 처리한다.

## 7. 계층화 구조

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

## 8. Bronze 적재

`crawling`은 수집만 담당하고, 수집 결과를 읽어 검증·로그·MongoDB 적재를 수행하는 코드는 `second_project` 앱 내부에 있다. 기존 `loading/load_raw_records.py`는 같은 앱 로직을 호출하는 호환 진입점이다.

```powershell
cd django
python manage.py migrate second_project --database mongodb --skip-checks
python manage.py load_raw_records --help
python manage.py load_raw_records
```

MongoDB migration은 collection과 인덱스만 준비하며, 실제 JSONL 데이터는 management command가 적재한다. `MONGODB_NAME`의 기본값은 `second_project`이므로 이전의 `db_mount` 데이터베이스와는 별도 데이터베이스다. 실행 로그는 `logs/pipeline.jsonl`에만 남기고 MongoDB에는 적재하지 않는다.

## 9. Git pull 이후 확인

가상환경 설정과 패키지 설치가 완료된 뒤 Django 프로젝트 경로에서 다음 명령어로 구성을 확인할 수 있다.

```powershell
cd django
python manage.py check --database sqlite3
```

오류 없이 명령어가 완료되면 Django 프로젝트 구성이 정상적으로 인식된 상태이다.

## 10. 참고 사항

- `.venv` 폴더 자체는 GitHub에 업로드하지 않는다.
- `requirements.txt`는 GitHub에 업로드하여 가상환경 재구성에 사용한다.
