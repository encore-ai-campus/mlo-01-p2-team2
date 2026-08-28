# `check_hr_guide_data.py` 인수인계

## 책임

현재 canonical RDB가 화면 판정에 필요한 기술적 최소조건을 충족하는지 JSON으로 점검한다.

## 실행

```powershell
python manage.py check_hr_guide_data
python manage.py check_hr_guide_data --strict
```

## 주요 검사

- Legacy·Silver 테이블 건수
- 재직·비재직 관리자 수
- Parent 없는 AREA와 빈 이름
- 프로필 누락·`DATE_CONFLICT` 보류
- 확정 가능한 재직 동료가 있는 비재직 대상
- demo-sized 여부

## 상태 계약

- `MINIMUM_READY`: 기술적 최소조건 충족
- `NOT_READY`: 최소조건 미충족
- `gate_1_status`는 데이터오너 승인 필요를 항상 알림

## 검증

`test_management_commands.py`의 empty, ready, conflict-only 시나리오

## 변경 주의

서비스 후보 조건을 바꾸면 이 명령의 관계 계산도 같이 바꾼다. `MINIMUM_READY`를 Gate 1이나 HR 승인으로 이름 붙이지 않는다.

