# `test_continuity_assessment.py` 인수인계

## 책임

핵심 후보 조회·상태·판단 가이드 규칙의 회귀를 막는다.

## 현재 시나리오 10건

1. 같은 Parent의 재직 후보만 포함
2. 부서→직위→근속 표시 순서
3. 충돌 후보 보류와 경고 집계
4. 후보 없음 `NO_MATCH`
5. 혼합 영역 `PARTIAL`
6. Parent 누락 `ON_HOLD`
7. 재직 대상 거부
8. 대상 충돌 전체 보류
9. 대상 빈 프로필 보류
10. 내부 지속과 채용결정 문구 분리

## 실행

```powershell
python manage.py test second_project.test_continuity_assessment
```

## 변경 주의

업무규칙 변경 전 기대 시나리오를 이 파일에 먼저 추가한다. 날짜 의존 테스트에는 명시적 `as_of_date`를 사용한다.

