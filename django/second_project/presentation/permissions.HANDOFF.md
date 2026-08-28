# `permissions.py` 인수인계

## 책임

Django Group을 HR 검토자와 팀 관리자로 해석한다.

## 역할 계약

- `hr_reviewer`: HR 상세 열람, superuser 포함
- `team_manager`: 후보 PII 없는 요약 열람
- `can_review()`: 두 역할 중 하나인지 판정

## 의존 파일

`views.py`, `bootstrap_hr_guide.py`, Presentation 테스트, Runbook

## 변경 주의

Group 이름 변경은 기존 DB Group과 운영 계정 배정까지 함께 migration해야 한다. Group은 요청 대상 소유권을 증명하지 않으므로 SSO·인사요청 연계 문제를 이 파일만으로 해결하지 않는다.

