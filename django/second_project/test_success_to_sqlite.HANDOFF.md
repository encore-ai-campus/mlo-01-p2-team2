# `test_success_to_sqlite.py` 인수인계

## 책임

Silver 직원 적재 시 선택형 프로필 이미지 URL의 매핑 회귀를 막는다.

## 시나리오

1. 원본 문서에 이미지 필드가 없으면 `NULL`
2. 이미지 URL이 있으면 모델 인스턴스에 동일한 값 보존

## 실행

```powershell
python manage.py test second_project.test_success_to_sqlite
```
