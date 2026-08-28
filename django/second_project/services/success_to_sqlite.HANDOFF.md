# `success_to_sqlite.py` 인수인계

## 책임

성공 MongoDB 컬렉션을 검증하고 Silver SQLite 모델로 일괄 upsert한다.

## 직원 사진 매핑

- 입력 키: `profile_image_url`
- 입력이 없거나 빈 문자열이면 `SilverEmployee.profile_image_url = NULL`
- 값이 있으면 URL 문자열을 보존하고 기존 행 upsert 시에도 갱신한다.
- 이미지 파일 다운로드나 바이너리 저장은 이 적재기의 책임이 아니다.

## 검증

`test_success_to_sqlite.py`에서 필드 미제공과 URL 제공 시나리오를 검증한다.
