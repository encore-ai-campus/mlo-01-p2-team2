# `bootstrap_hr_guide.py` 인수인계

## 책임

인사 요청 검토용 Django Group 두 개를 중복 없이 준비한다.

## 실행

```powershell
python manage.py bootstrap_hr_guide
```

## 생성 대상

- `hr_reviewer`
- `team_manager`

`get_or_create()`를 사용하므로 반복 실행이 가능하다. 사용자 계정과 비밀번호는 만들지 않는다.

## 의존 파일·검증

`presentation/permissions.py`, `test_bootstrap_groups_is_idempotent`, Runbook

## 변경 주의

공용 계정이나 기본 비밀번호 생성 기능을 추가하지 않는다. Group 이름 변경 시 권한 코드, 기존 DB Group, 운영 계정 배정을 함께 변경한다.

