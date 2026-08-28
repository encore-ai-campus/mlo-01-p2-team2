# 내부 인사 요청 검토 가이드 실행 Runbook

기준일: 2026-08-28
적용 범위: 개발·검증 환경. 운영 배포는 Gate 3 승인 후 수행한다.

## 1. 사전 조건

- Python 3.14 또는 프로젝트가 검증한 호환 버전
- Windows PowerShell
- 승인된 RDB 스냅샷
- 운영에서는 HTTPS가 적용된 내부 호스트
- 실제 HR 데이터 작업 전 DB 백업과 데이터오너 승인

## 2. 개발 환경 구성

`django` 디렉터리에서 실행한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e ..\validation_pipeline
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test second_project
```

소스 저장소에는 `.venv`, 비밀키, 실제 HR 원본 또는 DB 백업을 추가하지 않는다.

## 3. 필수 환경변수

개발 기본값은 로컬 실행 전용이다. 운영에서는 아래 값을 명시한다.

```powershell
$env:DJANGO_DEBUG = 'false'
$env:DJANGO_SECRET_KEY = '<비밀 저장소에서 주입>'
$env:DJANGO_ALLOWED_HOSTS = 'hr-guide.internal.example'
$env:DJANGO_SQLITE_PATH = '<승인된 RDB 절대경로>'
$env:DJANGO_SECURE_SSL_REDIRECT = 'true'
$env:DJANGO_SECURE_HSTS_SECONDS = '31536000'
```

`DJANGO_SECURE_HSTS_SECONDS`는 내부 도메인의 HTTPS와 인증서 갱신이 검증된 뒤에만 0보다 크게 설정한다. Mongo 적재 기능을 함께 쓸 때만 `MONGODB_URI`, `MONGODB_NAME`을 별도로 주입한다.

`manage.py check --deploy`는 `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS`와 `DJANGO_SECURE_HSTS_PRELOAD`를 켜지 않으면 관련 정책 경고를 표시한다. 모든 하위 도메인의 영구 HTTPS가 보장되고 보안 담당자가 승인한 경우에만 각 값을 `true`로 설정한다.

## 4. DB 변경 안전 절차

1. 앱과 DB 파일을 사용하는 프로세스를 정상 종료한다.
2. DB 파일을 시각이 포함된 별도 경로에 복사하여 백업한다.
3. 복사본을 `DJANGO_SQLITE_PATH`로 지정하고 먼저 검증한다.
4. 다음 명령이 모두 성공한 뒤 승인된 원본에 동일 절차를 적용한다.

```powershell
.\.venv\Scripts\python.exe manage.py showmigrations second_project
.\.venv\Scripts\python.exe manage.py migrate --plan
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check_hr_guide_data --strict
```

현재 프로젝트 원본 `db.sqlite3`는 확인 당시 외부 프로세스가 잠그고 있었다. 잠금을 강제로 해제하거나 파일을 덮어쓰지 말고, 점유 프로세스를 정상 종료한 뒤 위 절차를 다시 수행한다.

## 5. 권한 그룹 준비

```powershell
.\.venv\Scripts\python.exe manage.py bootstrap_hr_guide
```

- `hr_reviewer`: 대상자와 후보의 이름·부서·직위·근속·비교 근거 열람
- `team_manager`: 대상 관리자 이름과 업무영역별 검토 대상 수만 열람
- 두 그룹에 속하지 않은 계정: 검토 화면 접근 금지

사용자 계정 생성과 그룹 배정은 Django Admin에서 승인된 운영자가 수행한다. 공용 계정을 만들지 않는다.

## 6. 실행 및 확인

개발 확인:

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Smoke Test:

1. 미인증 사용자가 로그인 화면으로 이동한다.
2. 권한 없는 계정은 검토 URL에서 403을 받는다.
3. 팀 관리자 화면에 후보 이름·ID·부서·직위·근속이 노출되지 않는다.
4. HR 화면에 승인된 후보 상세와 현재 등록정보 기준 문구가 보인다.
5. 재직 대상자는 분석할 수 없다는 오류가 보인다.
6. 후보 없음과 데이터 확인 필요가 구분된다.
7. 승인·반려·채용결정 버튼이나 자동선정 표현이 없다.
8. 관리자 ID가 결과 URL에 포함되지 않고 응답에 `no-store`와 `no-referrer`가 적용된다.
9. 네 상태에서 업무 지속 가능성·인력 검토 방향·대체 완료 여부·다음 확인사항이 서로 구분된다.

## 7. 롤백

migration 또는 데이터 점검 실패 시 서비스를 시작하지 않는다. 앱 프로세스를 종료하고, 실패한 DB 파일을 보존하여 원인을 분석한 뒤, 운영자가 사전 백업본을 원래 경로로 복구한다. 복구 후 `manage.py check`, `showmigrations`, `check_hr_guide_data`를 다시 실행한다.

## 8. 현재 배포 차단 조건

- canonical `silver_employee`, `silver_area`, `silver_parent_area`, `silver_top_area_detail`이 실사용 규모로 적재되지 않음
- 퇴직 대상 관리자 데이터가 0건임
- 실제 DB migration 미적용(파일 잠금)
- HR·팀 관리자 UAT 및 개인정보 담당 검수 미완료
- 팀 관리자 계정과 승인된 인사요청 대상의 소유권 연계 미정
- 운영 로그·알람과 배포 서버 구성이 미완료
