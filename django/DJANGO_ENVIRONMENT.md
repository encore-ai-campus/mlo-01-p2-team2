# Django 환경 구성

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

## 2-1. 의존성 설치

`django/requirements.txt`에는 Django MongoDB backend와 파이프라인 실행에 필요한
패키지가 포함되어 있다.

```powershell
cd django
python -m pip install -r requirements.txt
```

## 3. 사용 가능한 데이터베이스

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
- 접속 주소와 데이터베이스명은 환경 변수로 변경할 수 있다. `MONGODB_URI`와
  `MONGODB_NAME`을 우선 사용하며, 기존 `BOOKSTORE_MONGODB_URI`와
  `BOOKSTORE_MONGODB_NAME`도 호환된다.

```text
MONGODB_URI
MONGODB_NAME
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

가상환경이 연결된 상태에서 Django 프로젝트 경로로 이동한 뒤 다음 명령어로 구성을 확인할 수 있다.

```bash
cd django
python manage.py check
```

오류 없이 명령어가 완료되면 Django 프로젝트 구성이 정상적으로 인식된 상태이다.
