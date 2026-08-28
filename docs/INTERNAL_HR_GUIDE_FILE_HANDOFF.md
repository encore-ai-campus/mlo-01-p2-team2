# 내부 인사 요청 검토 가이드 파일별 구현 인수인계서

- 문서 기준일: `2026-08-28`
- 기준 브랜치: `gold-implement`
- 기준 커밋: `8c18d87`
- 대상 독자: 후속 개발자, 데이터 담당자, QA, IT 운영자
- 관련 통합 문서: `docs/INTERNAL_HR_GUIDE_IMPLEMENTATION_GUIDE.md`

## 1. 인수인계 목적

이 문서는 구현 파일을 인수받는 담당자가 각 파일의 책임과 변경 영향을 빠르게 파악하기 위한 파일 단위 안내서다. 통합 구현 설명서가 제품 전체를 설명한다면, 본 문서는 다음 질문에 답한다.

- 이 파일은 왜 존재하는가?
- 어떤 클래스·함수·설정을 책임지는가?
- 어떤 파일과 연결되는가?
- 어떤 테스트가 이 파일을 보호하는가?
- 수정할 때 무엇을 함께 바꿔야 하는가?
- 현재 남아 있는 위험과 운영상 주의점은 무엇인가?

## 2. 인계 시 지켜야 할 공통 계약

파일을 수정하더라도 다음 계약은 사용자 재승인 없이 바꾸지 않는다.

1. 자동 후보선정·점수·Top-1·자동 승인·반려·채용결정을 추가하지 않는다.
2. `parent_area_id`를 동일 업무영역군의 조인 키로 사용한다.
3. 후보는 현재 재직 관리자만 포함하고 대상자는 제외한다.
4. 부서·직위·근속은 비교 근거이며 기본 탈락조건이 아니다.
5. 후보 없음과 데이터 확인 필요를 구분한다.
6. 팀 관리자에게 후보 PII를 전달하지 않는다.
7. 결과를 별도 업무·이력 테이블에 저장하지 않는다.
8. 실제 RDB migration은 백업과 복사본 검증 후 수행한다.

## 3. 파일 구성 요약

| 계층 | 파일 수 | 핵심 책임 |
|---|---:|---|
| 프로젝트 설정 | 4 | 환경, URL, DB 분리, 로컬 미리보기 CSRF |
| Presentation | 4 | 입력, 권한, 요청 흐름, 역할별 렌더링 |
| Domain Service | 1 | 후보 조회·상태·판단 가이드 |
| Management Command | 2 | 역할 준비, 데이터 준비도 점검 |
| Migration | 1 | Bronze·Silver migration graph 병합 |
| Template | 6 | 로그인, 공통 UI, 입력, HR·팀 결과, 오류 |
| Test | 4 | 규칙·화면·Router·운영 명령 회귀 방지 |
| 문서 | 5 | 통합 설명, 실행계획, UAT, Runbook, 파일별 인계 |

## 4. 프로젝트 설정 파일

### 4.1 `django/config/settings.py`

**책임**

- Django 전역 환경 설정
- RDB·MongoDB alias 정의
- 인증 이동 경로와 보안 설정
- 한국어·한국시간 설정
- 로컬 미리보기 middleware 등록

**핵심 구현**

- `_env_bool()`: 문자열 환경변수를 Boolean으로 변환한다.
- `_env_list()`: 쉼표 구분 환경변수를 list로 변환한다.
- `DEBUG=False`인데 운영 Secret이 없으면 `ImproperlyConfigured`를 발생시킨다.
- `default`와 `sqlite3`는 같은 `RDB_CONFIG`를 사용한다.
- `mongodb`는 `django_mongodb_backend`를 사용한다.
- `DATABASE_ROUTERS`에 `ProjectDatabaseRouter`를 등록한다.
- `LANGUAGE_CODE="ko-kr"`, `TIME_ZONE="Asia/Seoul"`을 사용한다.
- 운영 시 Secure Cookie, SSL redirect와 선택적 HSTS를 적용한다.

**환경변수 계약**

- `DJANGO_DEBUG`
- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_SQLITE_PATH`
- `DJANGO_SECURE_SSL_REDIRECT`
- `DJANGO_SECURE_HSTS_SECONDS`
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS`
- `DJANGO_SECURE_HSTS_PRELOAD`
- `MONGODB_URI`
- `MONGODB_NAME`

**연결 파일**

- `config/database_router.py`
- `config/middleware.py`
- `config/urls.py`
- `second_project/services/success_to_sqlite.py`

**보호 테스트**

- `test_database_router.py`
- 전체 `manage.py check`
- 운영 환경변수 적용 후 `manage.py check --deploy`

**수정 시 주의사항**

- `default`를 비우면 Django auth·session·Silver 조회가 다시 실패한다.
- `sqlite3` 호환 alias를 제거하기 전 기존 적재기의 사용 여부를 확인한다.
- HSTS preload와 subdomain 설정은 코드 편의로 켜지 말고 보안 담당 승인을 받는다.
- `LocalNullOriginMiddleware`는 반드시 CSRF middleware 앞에 있어야 한다.

### 4.2 `django/config/urls.py`

**책임**

- 프로젝트 최상위 URL 라우팅
- 로그인·로그아웃·Admin·검토 화면 연결

**URL 계약**

| URL | 처리 |
|---|---|
| `/` | `second_project:review`로 임시 리다이렉트 |
| `/login/` | Django `LoginView`와 커스텀 로그인 템플릿 |
| `/logout/` | Django `LogoutView`, POST 사용 |
| `/admin/` | 사용자·Group 운영용 Django Admin |
| `/second_project/` | Presentation URL include |

**연결 파일**

- `config/settings.py`의 로그인 redirect 설정
- `templates/registration/login.html`
- `second_project/presentation/urls.py`

**보호 테스트**

- 미인증 로그인 이동
- 로그인 후 검토 화면 접근

**수정 시 주의사항**

- 관리자 ID를 URL path나 query string에 다시 넣지 않는다.
- 로그아웃을 GET으로 변경하지 않는다.

### 4.3 `django/config/database_router.py`

**책임**

- Bronze 모델과 Silver·Legacy·웹 모델의 물리 DB 분리

**핵심 클래스**

- `ProjectDatabaseRouter`

**라우팅 계약**

| 대상 | Read·Write DB | Migration DB |
|---|---|---|
| `BronzeRawRecord` | `mongodb` | `mongodb` |
| Silver·Legacy 모델 | `default` | `default`, 호환 `sqlite3` |
| Django auth·session | Django 기본 | MongoDB 금지 |

`RunPython` operation은 `model_name=None`이므로 Router가 `None`을 반환하고 migration 내부의 connection alias guard가 최종 실행 여부를 결정한다.

**연결 파일**

- `config/settings.py`
- `repository/models.py`
- `migrations/0002_bronze_mongodb.py`
- `migrations/0003_merge_bronze_silver.py`

**보호 테스트**

- `test_database_router.py` 4건
- fresh RDB에서 Bronze 테이블이 생성되지 않는 테스트

**수정 시 주의사항**

- `model_name=None`을 MongoDB에서 `False`로 바꾸면 Bronze collection 준비용 RunPython이 실행되지 않는다.
- 새 Mongo 모델을 추가하면 `bronze_model_names`와 Router 테스트를 같이 갱신한다.

### 4.4 `django/config/middleware.py`

**책임**

- Codex 내장 브라우저의 로컬 `Origin: null` POST를 개발 환경에서만 처리

**핵심 클래스**

- `LocalNullOriginMiddleware`

**허용 조건**

- `settings.DEBUG is True`
- HTTP Origin이 정확히 `null`
- Host가 `127.0.0.1`, `localhost`, `[::1]` 중 하나

세 조건이 모두 맞을 때만 Origin 헤더를 제거해 Django CSRF middleware가 기존 토큰 검증을 계속 수행하도록 한다.

**연결 파일**

- `config/settings.py`의 `MIDDLEWARE`
- `test_presentation.py`

**보호 테스트**

- `test_local_preview_null_origin_can_submit_in_debug_mode`

**수정 시 주의사항**

- 운영 호스트나 `DEBUG=False`에서 null Origin을 허용하지 않는다.
- CSRF middleware 자체를 비활성화하는 대안으로 변경하지 않는다.

## 5. Presentation 파일

### 5.1 `django/second_project/presentation/forms.py`

**책임**

- 퇴직 대상 관리자 ID 입력과 형식 검증

**핵심 클래스**

- `ManagerReviewForm`

**입력 계약**

- 필드명: `manager_id`
- 최대 길이: 9
- 형식: `^EMP[0-9]{6}$`
- 앞뒤 공백 제거

**연결 파일**

- `presentation/views.py`
- `review_form.html`
- upstream Silver ID 계약

**수정 시 주의사항**

- ID 규칙을 바꾸면 Silver 표준계약과 fixture도 함께 바꾼다.
- 입력값을 URL로 redirect하지 않는다.

### 5.2 `django/second_project/presentation/permissions.py`

**책임**

- Django 사용자와 검토 역할 Group 판정

**상수**

- `HR_GROUP = "hr_reviewer"`
- `TEAM_MANAGER_GROUP = "team_manager"`

**함수**

- `is_hr_reviewer()`: superuser 또는 HR Group
- `is_team_manager()`: 팀 관리자 Group
- `can_review()`: 둘 중 하나

**연결 파일**

- `presentation/views.py`
- `bootstrap_hr_guide.py`
- 권한 테스트와 Runbook

**수정 시 주의사항**

- Group 이름 변경은 기존 DB Group, bootstrap 명령, 테스트, 운영 계정 배정을 모두 변경한다.
- Group은 기능 역할만 나타낸다. 팀의 요청 대상 소유권을 증명하지 않는다.

### 5.3 `django/second_project/presentation/urls.py`

**책임**

- 검토 앱 내부 URL 정의

**현재 URL**

- `/second_project/review/`

입력과 결과를 같은 URL에서 처리한다. 사번이 access log와 브라우저 history에 남지 않도록 별도 결과 URL을 두지 않는다.

**연결 파일**

- `presentation/views.py`
- `config/urls.py`

**수정 시 주의사항**

- `/review/<manager_id>/` 형태를 다시 추가하면 PII 로그 정책을 재검토해야 한다.

### 5.4 `django/second_project/presentation/views.py`

**책임**

- 로그인·Group 권한 검사
- Form 처리와 판정 서비스 호출
- HR·팀 관리자 화면 분기
- 오류 정보 최소화
- 민감 결과의 비캐시 응답

**핵심 함수**

- `review_request()`: GET 입력 화면, POST 결과 화면
- `_render_result()`: 서비스 실행과 역할별 템플릿 선택
- `_require_review_permission()`: 403 처리
- `_private_response()`: `no-store`, `private`, `no-referrer` 헤더 적용

**입출력 흐름**

```text
POST manager_id
  -> assess_manager_continuity()
  -> HR: ContinuityAssessment + AssessmentGuidance
  -> Team: AssessmentSummary + AssessmentGuidance
  -> 같은 /review/ URL에서 HTML 반환
```

팀 관리자에게는 존재하지 않는 대상과 재직 대상의 상세 오류를 구분하지 않고 `REQUEST_NOT_AVAILABLE`로 단순화한다.

**연결 파일**

- `forms.py`, `permissions.py`
- `services/continuity_assessment.py`
- 네 개의 검토 관련 템플릿

**보호 테스트**

- `test_presentation.py` 8건

**수정 시 주의사항**

- 팀 분기에서 전체 Assessment를 템플릿에 전달하지 않는다.
- 결과 redirect URL에 ID를 넣지 않는다.
- 결과 응답에서 캐시·referrer 헤더를 제거하지 않는다.
- GET으로 인사 판단 데이터를 변경하지 않는다.

## 6. Domain Service 파일

### 6.1 `django/second_project/services/continuity_assessment.py`

**책임**

이 기능의 핵심 업무규칙을 한 곳에서 수행한다.

- 대상 관리자 검증
- 담당 업무영역 조회와 Parent 그룹화
- 재직 후보 조회·중복 제거
- 데이터 충돌·누락 보류
- 근속 계산과 표시 순서
- 업무영역 및 전체 상태 판정
- 역할별 DTO와 판단 가이드 생성

**공개 API**

#### `assess_manager_continuity(manager_id, as_of_date=None, using="default")`

- 입력: 관리자 ID, 선택적 기준일, DB alias
- 출력: `ContinuityAssessment`
- 예외: `AssessmentNotAvailable`

#### `summarize_assessment(assessment)`

- 입력: HR용 전체 Assessment
- 출력: 후보 개인정보를 제거한 `AssessmentSummary`

#### `guidance_for(status)`

- 입력: `AssessmentStatus`
- 출력: 지속 가능성·인력 방향·대체 완료·다음 행동 문구

**주요 DTO**

| 객체 | 사용처 | PII 포함 |
|---|---|---:|
| `CandidateEvidence` | HR 후보·보류 상세 | 포함 |
| `AreaAssessment` | HR 업무영역별 결과 | 포함 가능 |
| `ContinuityAssessment` | HR 전체 결과 | 포함 |
| `AreaSummary` | 팀 업무영역 요약 | 후보 PII 없음 |
| `AssessmentSummary` | 팀 전체 결과 | 후보 PII 없음 |
| `AssessmentGuidance` | 양쪽 화면 판단 안내 | 없음 |

**상태 계약**

- `REVIEWABLE`: 모든 영역에서 확인 가능한 후보 존재
- `NO_MATCH`: 모든 영역에서 확인 가능한 후보 없음
- `ON_HOLD`: 모든 영역 또는 대상 수준에서 데이터 확인 필요
- `PARTIAL`: 영역 상태가 혼합됨

**blocking 데이터 코드**

- `DATA_CONFLICT`: upstream `DATE_CONFLICT`
- `PROFILE_MISSING`: 이름·부서·직위 빈 값
- AREA Parent 누락 또는 이름 누락

**후보 표시 순서**

1. 부서 일치
2. 직위 일치
3. 근속기간 내림차순
4. 이름·ID

**DB 의존성**

- `SilverEmployee`
- `SilverArea`
- `SilverParentArea`

**보호 테스트**

- `test_continuity_assessment.py` 10건
- `test_presentation.py`의 역할별 렌더링 테스트

**수정 시 주의사항**

- `p_area_nm` 문자열로 후보를 조인하지 않는다.
- 후보 `DATE_CONFLICT`를 0명 또는 정상 후보로 계산하지 않는다.
- PARTIAL·ON_HOLD가 섞였을 때 전체 후보 수를 0명으로 오인시키지 않는다.
- 새 충돌 코드를 추가하면 readiness command와 데이터 사전도 함께 변경한다.
- 유사 직위·점수 로직 추가는 범위 변경 승인이 필요하다.

## 7. Management command 파일

### 7.1 `django/second_project/management/commands/bootstrap_hr_guide.py`

**책임**

- HR·팀 관리자 기본 Group 생성

**명령**

```powershell
python manage.py bootstrap_hr_guide
```

`get_or_create()`를 사용하므로 반복 실행해도 Group이 중복되지 않는다. 사용자 생성과 Group 배정은 수행하지 않는다.

**연결 파일**

- `presentation/permissions.py`
- Runbook

**보호 테스트**

- `test_bootstrap_groups_is_idempotent`

**수정 시 주의사항**

- 공용 사용자나 기본 비밀번호를 생성하지 않는다.
- 운영 계정 배정은 승인된 관리자가 Admin 또는 별도 IAM 절차로 수행한다.

### 7.2 `django/second_project/management/commands/check_hr_guide_data.py`

**책임**

- 화면 판정에 필요한 canonical 데이터의 기술적 최소 준비도 점검

**명령**

```powershell
python manage.py check_hr_guide_data
python manage.py check_hr_guide_data --strict
```

**주요 출력**

- Legacy·Silver 테이블 건수
- 재직·비재직 관리자 수
- Parent 없는 AREA 수
- 빈 프로필·충돌 보류 수
- 이름 없는 AREA·Parent 수
- 확정 가능한 재직 동료가 있는 비재직 대상 수
- `MINIMUM_READY` 또는 `NOT_READY`
- 항상 별도 데이터오너 승인이 필요하다는 Gate 1 상태

`--strict`는 최소조건 미충족 시 비정상 종료한다.

**연결 파일**

- Silver 모델
- 서비스의 blocking 정책
- Runbook과 Gate 1

**보호 테스트**

- 빈 canonical strict 실패
- 정상 최소관계 `MINIMUM_READY`
- conflict-only 후보 strict 실패

**수정 시 주의사항**

- `MINIMUM_READY`를 운영 승인 또는 데이터 품질 전체 통과로 이름 붙이지 않는다.
- 서비스의 후보 규칙을 바꾸면 이 명령의 유효 후보 계산도 함께 바꾼다.

## 8. Migration 파일

### 8.1 `django/second_project/migrations/0003_merge_bronze_silver.py`

**책임**

- 병렬로 존재하던 두 migration leaf를 하나로 병합

**의존성**

- `0002_bronze_mongodb`
- `0002_legacy_org_record`

**operation**

- 없음

업무 테이블이나 컬럼을 만들지 않는 merge-only migration이다.

**검증 이력**

- 테스트 DB 전체 migration 성공
- 운영 DB 복사본 전체 migration 성공
- 기존 Legacy 19,621행 보존
- FK 위반 0
- SQLite Bronze 테이블 0

**수정 시 주의사항**

- 이 migration에 업무 스키마 변경을 섞지 않는다.
- 실제 DB는 잠금 해제·백업·복사본 검증 후 적용한다.

## 9. Template 파일

### 9.1 `django/second_project/templates/registration/login.html`

**책임**

- 승인된 사내 계정 로그인 UI

**의존성**

- Django `LoginView`
- `base.html`

**주의사항**

- 인증 실패 시 계정 존재 여부를 구분해 노출하지 않는다.
- 비밀번호 자동완성 정책 변경은 보안 담당과 협의한다.

### 9.2 `django/second_project/templates/second_project/base.html`

**책임**

- 전체 화면의 공통 레이아웃·반응형 CSS
- Header·로그아웃
- 상태 badge, 표, 판단 가이드 카드 디자인

**의존성**

- 모든 HR Guide 템플릿이 extends

**주의사항**

- 상태 class는 `status-REVIEWABLE`, `status-PARTIAL`, `status-NO_MATCH`, `status-ON_HOLD` 계약을 따른다.
- 외부 CDN이나 사용자 추적 스크립트를 승인 없이 추가하지 않는다.

### 9.3 `django/second_project/templates/second_project/review_form.html`

**책임**

- 퇴직 대상 관리자 ID 입력
- 현재 등록정보 기준과 자동 결정 금지 안내

**입력**

- `ManagerReviewForm`

**주의사항**

- form method는 POST와 CSRF token을 유지한다.
- 후보 자동선정으로 오인할 문구를 추가하지 않는다.

### 9.4 `django/second_project/templates/second_project/team_result.html`

**책임**

- 팀 관리자에게 업무영역·상태·확인된 후보 수·판단 방향만 표시

**Context 계약**

- `assessment`: `AssessmentSummary`
- `guidance`: `AssessmentGuidance`

**금지 필드**

- 후보 이름·ID·부서·직위·근속·개별 경고

**보호 테스트**

- 후보 PII 문자열이 응답 HTML에 없음을 검증
- 보류 후보 상세가 팀 화면에 없음을 검증

**주의사항**

- 템플릿에서 숨기는 방식으로 후보 객체를 추가하지 않는다.
- 후보 수가 `—`이면 0명으로 바꾸지 않는다.

### 9.5 `django/second_project/templates/second_project/hr_result.html`

**책임**

- HR 대상자·후보 상세와 업무영역별 검토 근거 표시
- 데이터 확인 필요 후보의 식별정보와 사유 표시
- 내부대체·충원 검토 방향 안내

**Context 계약**

- `assessment`: `ContinuityAssessment`
- `guidance`: `AssessmentGuidance`

**주요 표현**

- 현재값 확인 대상
- 데이터 확인 필요 대상
- 부서·직위 일치 여부
- 업무 지속 가능성
- 인력 검토 방향
- 대체 완료 여부
- 다음 확인사항

**보호 테스트**

- 후보 상세 표시
- 인사결정 순위·승인·반려 버튼 부재
- 보류 후보와 충돌 사유 표시
- 판단 가이드 표시

**주의사항**

- 첫 행을 우선후보로 표현하지 않는다.
- `NO_MATCH`를 채용 확정으로 표현하지 않는다.
- `REVIEWABLE`을 대체 완료로 표현하지 않는다.

### 9.6 `django/second_project/templates/second_project/review_error.html`

**책임**

- 조회 불가 메시지와 오류코드 표시

HR은 `TARGET_NOT_FOUND`, `TARGET_IS_ACTIVE` 등 구체 오류를 볼 수 있다. 팀 관리자는 View에서 단순화한 `REQUEST_NOT_AVAILABLE`을 받는다.

**주의사항**

- 팀 관리자에게 대상자의 존재·재직 상태를 상세히 구분하는 정보를 추가하지 않는다.

## 10. Test 파일

### 10.1 `django/second_project/test_continuity_assessment.py`

**책임**

- 핵심 후보·상태·근거 규칙 회귀 방지

**현재 테스트 10건**

1. 같은 Parent의 재직 후보만 포함
2. 부서→직위→근속 표시 순서
3. 충돌 후보 보류와 경고 집계
4. 재직 후보 없음 `NO_MATCH`
5. 혼합 영역 `PARTIAL`
6. Parent 누락 `ON_HOLD`
7. 재직 대상 거부
8. 대상 충돌 전체 보류
9. 대상 빈 프로필 보류
10. 내부 지속과 채용결정 문구 분리

**수정 시 주의사항**

- 업무규칙 변경은 먼저 기대 시나리오를 이 파일에 추가한다.
- 날짜 의존 테스트에는 명시적인 `as_of_date`를 사용한다.

### 10.2 `django/second_project/test_presentation.py`

**책임**

- 인증·권한·PII·HTTP 흐름 검증

**현재 테스트 8건**

- 미인증 로그인 이동
- 무권한 403
- 팀 후보 PII 비노출
- HR 후보 상세와 판단 가이드
- 결과 URL ID 비노출·보안 헤더
- 로컬 null Origin
- 재직 대상 400
- HR 보류 상세·팀 비노출

**수정 시 주의사항**

- 새 후보 필드를 추가하면 팀 HTML에 그 값이 없는 negative assertion도 추가한다.
- 보안 헤더와 POST 흐름을 유지한다.

### 10.3 `django/second_project/test_database_router.py`

**책임**

- Mongo Bronze와 RDB Silver의 라우팅 계약 검증

**현재 테스트 4건**

- Read·Write DB 분리
- 모델 migration DB 제한
- RunPython alias guard 허용
- auth 테이블 Mongo migration 금지

**수정 시 주의사항**

- Router 변경은 실제 Mongo 연결 없이도 최소 단위 테스트를 유지한다.
- Mongo integration migration은 별도 검증 환경에서 추가 확인한다.

### 10.4 `django/second_project/test_management_commands.py`

**책임**

- 운영 준비 명령과 migration 안전성 검증

**현재 테스트 5건**

- Group 생성 idempotency
- RDB Bronze 테이블 부재
- 빈 canonical strict 실패
- 정상 최소관계 readiness
- conflict-only 후보 strict 실패

**수정 시 주의사항**

- readiness 조건을 완화하면 실제 서비스가 `REVIEWABLE`을 반환할 수 있는지 함께 증명한다.

## 11. 문서 파일

### 11.1 `docs/INTERNAL_HR_GUIDE_IMPLEMENTATION_GUIDE.md`

**책임**

- 사업 목적부터 아키텍처·규칙·운영 상태까지 전체 구현을 한 문서로 설명

**갱신 시점**

- 범위, 아키텍처, 상태, 테스트 수, 환경변수 또는 운영 차단사항 변경 시

### 11.2 `docs/INTERNAL_HR_GUIDE_EXECUTION_PLAN.md`

**책임**

- Gate 0~3, 태스크 상태, 완료기준, KPI, 정책 결정 기록

**갱신 시점**

- 승인 상태 또는 태스크 상태가 바뀔 때
- 데이터·UAT·운영 Gate가 해제될 때

### 11.3 `docs/INTERNAL_HR_GUIDE_UAT.md`

**책임**

- HR·팀·데이터·보안의 실사용 승인 체크리스트

**갱신 시점**

- 화면 문구, 권한, 상태, 개인정보 노출 또는 운영 절차 변경 시

### 11.4 `django/HR_GUIDE_RUNBOOK.md`

**책임**

- 설치, 환경변수, migration, Group 준비, 실행, Smoke Test, 롤백

**갱신 시점**

- Python·Django 버전, DB, 배포 방식, 보안 설정 또는 명령 변경 시

### 11.5 `docs/INTERNAL_HR_GUIDE_FILE_HANDOFF.md`

**책임**

- 파일 단위 구현 책임과 변경 영향 인계

**갱신 시점**

- 파일 추가·삭제·이동
- 공개 함수·DTO·URL·명령·Context 계약 변경

### 11.6 `README.md`

**책임**

- 프로젝트 개요와 내부 검토 MVP 진입 링크 제공

**주의사항**

- 원 데이터 정비 목표와 후속 내부 MVP를 동일 단계로 오인하지 않게 구분한다.

## 12. 생성 산출물에 포함하지 않는 파일

기준 커밋에는 테스트 실행으로 변경된 다음 `.pyc` 파일이 포함돼 있다.

- `django/config/__pycache__/__init__.cpython-314.pyc`
- `django/config/__pycache__/settings.cpython-314.pyc`
- `django/config/__pycache__/urls.cpython-314.pyc`

이 파일들은 구현 산출물이 아닌 Python bytecode다. `.gitignore`에는 이미 `__pycache__/`, `*.py[cod]`가 있으므로 후속 정리 시 Git 추적 대상에서 제거하는 것을 권장한다. 소스 인계·리뷰·배포 판단에는 포함하지 않는다.

## 13. 변경 영향 매트릭스

| 변경 종류 | 필수 변경 파일 | 필수 검증 |
|---|---|---|
| 후보 적격조건 | 서비스, readiness command | 서비스·command·presentation 테스트 |
| 상태 이름·판단 가이드 | 서비스, HR·팀 템플릿 | 문구 테스트, HR UAT |
| 역할별 필드 | DTO, View, 템플릿, 권한 문서 | PII negative test, 보안 UAT |
| 관리자 ID 형식 | Form, Silver 계약, fixture | Form·service·pipeline 테스트 |
| DB alias·모델 위치 | Settings, Router, migration | Router, fresh migrate, DB 복사본 |
| 새 correction code | 데이터 사전, 서비스, command | conflict fixture와 Gate 1 대사 |
| URL 구조 | config·presentation URL, View | ID 로그·referrer·cache 검증 |
| 배포 보안 | Settings, Runbook | `check --deploy`, Smoke Test |

## 14. 후속 담당자 첫 작업 순서

1. `INTERNAL_HR_GUIDE_IMPLEMENTATION_GUIDE.md`로 전체 범위를 읽는다.
2. 본 문서의 공통 계약과 담당 파일 섹션을 확인한다.
3. `manage.py test second_project` 27건을 기준선으로 실행한다.
4. `manage.py check_hr_guide_data`로 연결 DB 상태를 확인한다.
5. 실제 데이터 작업 전 Runbook의 복사본 migration 절차를 수행한다.
6. 변경하려는 규칙이 Gate 0 승인 범위를 넘으면 사용자에게 재승인을 요청한다.
7. 코드와 함께 실행계획·UAT·본 인수인계서를 갱신한다.

## 15. 현재 미해결 사항

| 항목 | 상태 | 다음 책임자 |
|---|---|---|
| 실사용 canonical 적재 | BLOCKED | 데이터오너 |
| 비활성·실제 퇴직 의미 확정 | WAITING_DECISION | HR 업무오너 |
| 팀 관리자 요청대상 소유권 | WAITING_DECISION | HR·IAM·개인정보 담당 |
| 기준 SQL 대사 | BLOCKED_DATA | 데이터오너·QA |
| 원본 DB migration | BLOCKED | IT 운영 |
| 운영 로그·알람 | NOT_STARTED | IT 운영·보안 |
| HR·팀 UAT | WAITING_UAT | 업무오너·사용자 |

## 16. 인계 완료 기준

- 후속 담당자가 네 상태를 fixture로 재현할 수 있다.
- HR과 팀 화면의 필드 차이를 설명할 수 있다.
- 후보 조회의 Parent ID 기준과 정렬 기준을 설명할 수 있다.
- `MINIMUM_READY`와 Gate 1 승인의 차이를 설명할 수 있다.
- 테스트 27건을 재실행할 수 있다.
- 복사본 DB migration과 롤백 절차를 설명할 수 있다.
- 미해결 P0 항목을 운영 승인으로 오인하지 않는다.
