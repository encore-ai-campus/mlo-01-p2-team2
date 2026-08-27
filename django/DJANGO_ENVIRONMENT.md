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

<<<<<<< HEAD
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
=======
## 2-1. 의존성 설치

`django/requirements.txt`에는 Django MongoDB backend와 파이프라인 실행에 필요한
패키지가 포함되어 있다.

```powershell
cd django
python -m pip install -r requirements.txt
```

## 3. 사용 가능한 데이터베이스
>>>>>>> feat/validation-modules

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
<<<<<<< HEAD
- 기본 데이터베이스명: `second_project`
- 접속 주소와 데이터베이스명은 환경 변수로 변경할 수 있다.
=======
- 기본 데이터베이스명: `db_mount`
- 접속 주소와 데이터베이스명은 환경 변수로 변경할 수 있다. `MONGODB_URI`와
  `MONGODB_NAME`을 우선 사용하며, 기존 `BOOKSTORE_MONGODB_URI`와
  `BOOKSTORE_MONGODB_NAME`도 호환된다.
>>>>>>> feat/validation-modules

```text
MONGODB_URI
MONGODB_NAME
<<<<<<< HEAD
```

## 6. URL 연결
=======
BOOKSTORE_MONGODB_URI
BOOKSTORE_MONGODB_NAME
```

파이프라인은 이 `mongodb` alias가 반환하는 MongoClient를 재사용한다. 따라서
`validation_pipeline/config.django-mongodb.example.json`의
`success_database`와 `failure_database`를 서로 다른 이름으로 지정하면
검증 통과 문서는 성공 DB에, 격리 문서는 실패 DB에 저장된다. Bronze와 실행
리포트는 성공 DB의 별도 collection에 두도록 예시를 구성했다.

```powershell
cd ..\validation_pipeline
Copy-Item config.django-mongodb.example.json config.json
$env:MONGODB_URI = "mongodb://127.0.0.1:27017"
$env:MONGODB_NAME = "db_mount"
$env:PYTHONPATH = "src"
python -m mongo_pipeline --config config.json
```

## 5. 성공 DB를 SQLite RDB로 적재

파이프라인이 성공 DB에 만든 네 Silver collection을 읽어 Django ORM으로
`sqlite3` alias에 upsert한다. `legacy_org.yaml`처럼 행 단위 레거시 규칙을 사용한
경우에는 성공 DB의 `records` collection을 `legacy_org_record` staging 테이블로
적재한다. 어느 모드에서도 실패 DB는 이 명령의 입력으로 사용하지 않는다.
먼저 테이블을 생성한다.

```powershell
cd ..\django
python manage.py migrate --database sqlite3
python manage.py load_success_to_sqlite --config ..\validation_pipeline\config.json

# legacy_org.yaml 실행 결과를 staging table로 적재하는 경우
python manage.py load_success_to_sqlite `
  --config ..\validation_pipeline\config.csv-legacy-django-mongodb.example.json
```

재실행해도 각 Silver 모델의 PK 기준으로 갱신되므로 중복되지 않는다. 실제 쓰기
전에 확인하려면 다음처럼 dry-run을 사용할 수 있다.

```powershell
python manage.py load_success_to_sqlite `
  --config ..\validation_pipeline\config.json `
  --dry-run
```

정본 Silver 모드의 테이블은 `silver_employee`, `silver_area`,
`silver_parent_area`, `silver_top_area_detail`이며, `silver_area`는 직원·상위영역
테이블을 외래키로 참조한다. 레거시 행 모드에서는 `legacy_org_record` 한 테이블을
사용하고 `source_record_id`를 PK로 보존한다.

## 4. Git pull 이후 확인
>>>>>>> feat/validation-modules

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
