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
- 접속 주소와 데이터베이스명은 환경 변수로 변경할 수 있다.

```text
BOOKSTORE_MONGODB_URI
BOOKSTORE_MONGODB_NAME
```

## 4. Git pull 이후 확인

가상환경이 연결된 상태에서 Django 프로젝트 경로로 이동한 뒤 다음 명령어로 구성을 확인할 수 있다.

```bash
cd django
python manage.py check
```

오류 없이 명령어가 완료되면 Django 프로젝트 구성이 정상적으로 인식된 상태이다.
