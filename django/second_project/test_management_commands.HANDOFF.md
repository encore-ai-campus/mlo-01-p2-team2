# `test_management_commands.py` 인수인계

## 책임

역할 bootstrap, 데이터 준비도 명령과 RDB migration 안전성을 검증한다.

## 현재 시나리오 5건

- Group 생성 idempotency
- fresh RDB에 Bronze 테이블 없음
- 빈 canonical strict 실패
- 정상 최소관계 `MINIMUM_READY`
- conflict-only 후보 strict 실패

## 실행

```powershell
python manage.py test second_project.test_management_commands
```

## 변경 주의

readiness 조건을 완화하면 실제 서비스가 확인 가능한 후보를 반환하는지 함께 증명한다. 테스트 fixture의 `MINIMUM_READY`를 Gate 1 승인으로 간주하지 않는다.

