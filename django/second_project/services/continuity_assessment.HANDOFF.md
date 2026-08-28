# `continuity_assessment.py` 인수인계

## 책임

퇴직 대상 검증, Parent별 재직 후보 조회, 근거 정렬, 데이터 보류, 네 상태와 판단 가이드, 역할별 DTO 생성을 담당하는 핵심 업무규칙 서비스다.

## 공개 API

- `assess_manager_continuity()`: HR용 전체 결과 생성
- `summarize_assessment()`: 팀 관리자용 후보 비식별 결과 생성
- `guidance_for()`: 지속 가능성·충원 방향·대체 완료·다음 행동 문구 생성

## 핵심 계약

- 대상은 현재 비재직 상태여야 한다.
- 후보는 같은 `parent_area_id`의 `is_active=True` 관리자다.
- 대상자는 제외하고 같은 Parent 안에서 중복 제거한다.
- 부서→직위→근속→이름·ID 순으로 표시하되 추천순위가 아니다.
- `DATE_CONFLICT`, 빈 프로필, AREA·Parent 누락은 보류한다.
- `REVIEWABLE`, `PARTIAL`, `NO_MATCH`, `ON_HOLD`를 구분한다.
- `ON_HOLD`는 `판단 보류 · 데이터 정정 필요`로 표시하고, 내부 대체·신규채용을 모두 보류하도록 안내한다.
- `ON_HOLD` 결과는 대체가 진행 중이거나 완료되었다는 의미가 아님을 별도 문장으로 명시한다.
- HR 후보 근거에는 선택형 `profile_image_url`을 포함하지만 팀 관리자 요약에는 포함하지 않는다.

## 주요 DTO

`CandidateEvidence`, `AreaAssessment`, `ContinuityAssessment`, `AreaSummary`, `AssessmentSummary`, `AssessmentGuidance`

## 검증

업무규칙·Presentation 관련 테스트 18건

## 변경 주의

- 이름이 아니라 Parent ID로 조인한다.
- 후보 없음과 데이터 부족을 합치지 않는다.
- 새 충돌 코드는 readiness command와 데이터 사전에도 반영한다.
- 유사직위·점수·자동선정은 사용자 재승인 없이 추가하지 않는다.

