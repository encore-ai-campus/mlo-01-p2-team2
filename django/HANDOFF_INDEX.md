# 내부 인사 요청 검토 가이드 인수인계 색인

파일별 인수인계서는 각 구현 파일과 같은 디렉터리에 `원본파일명.HANDOFF.md`로 배치한다. 구현을 수정할 때 해당 sidecar 문서와 연결된 테스트도 함께 갱신한다.

## 프로젝트 설정

- `config/settings.py` → `config/settings.HANDOFF.md`
- `config/urls.py` → `config/urls.HANDOFF.md`
- `config/database_router.py` → `config/database_router.HANDOFF.md`
- `config/middleware.py` → `config/middleware.HANDOFF.md`

## Presentation

- `second_project/presentation/forms.py` → `forms.HANDOFF.md`
- `second_project/presentation/permissions.py` → `permissions.HANDOFF.md`
- `second_project/presentation/urls.py` → `urls.HANDOFF.md`
- `second_project/presentation/views.py` → `views.HANDOFF.md`

## 업무규칙 서비스

- `second_project/services/continuity_assessment.py` → `continuity_assessment.HANDOFF.md`
- `second_project/services/success_to_sqlite.py` → `success_to_sqlite.HANDOFF.md`

## 데이터 모델

- `second_project/models.py` → `models.HANDOFF.md`

## Management command·migration

- `second_project/management/commands/bootstrap_hr_guide.py` → `bootstrap_hr_guide.HANDOFF.md`
- `second_project/management/commands/check_hr_guide_data.py` → `check_hr_guide_data.HANDOFF.md`
- `second_project/migrations/0003_merge_bronze_silver.py` → `0003_merge_bronze_silver.HANDOFF.md`
- `second_project/migrations/0004_silveremployee_profile_image_url.py` → `0004_silveremployee_profile_image_url.HANDOFF.md`

## 화면 템플릿

- `second_project/templates/registration/login.html` → `login.HANDOFF.md`
- `second_project/templates/second_project/base.html` → `base.HANDOFF.md`
- `review_form.html` → `review_form.HANDOFF.md`
- `team_result.html` → `team_result.HANDOFF.md`
- `hr_result.html` → `hr_result.HANDOFF.md`
- `review_error.html` → `review_error.HANDOFF.md`

## 테스트

- `second_project/test_continuity_assessment.py` → `test_continuity_assessment.HANDOFF.md`
- `second_project/test_presentation.py` → `test_presentation.HANDOFF.md`
- `second_project/test_database_router.py` → `test_database_router.HANDOFF.md`
- `second_project/test_management_commands.py` → `test_management_commands.HANDOFF.md`
- `second_project/test_success_to_sqlite.py` → `test_success_to_sqlite.HANDOFF.md`

## 상위 문서

- `INTERNAL_HR_GUIDE_IMPLEMENTATION_GUIDE.md`: 전체 사업·아키텍처·구현 설명
- `INTERNAL_HR_GUIDE_EXECUTION_PLAN.md`: 승인 Gate와 태스크
- `INTERNAL_HR_GUIDE_UAT.md`: 실사용 승인 체크리스트
- `HR_GUIDE_RUNBOOK.md`: 설치·migration·실행·롤백

## 공통 인계 원칙

- 후보 자동선정·점수·자동 인사결정을 추가하지 않는다.
- `parent_area_id` 조인, 재직 후보, 대상자 제외 계약을 유지한다.
- 팀 관리자에게 후보 PII를 전달하지 않는다.
- 상태·권한·DB 계약 변경 시 코드, 테스트, UAT, sidecar를 함께 변경한다.
- 실제 DB는 백업과 복사본 검증 전 변경하지 않는다.

