# `models.py` 인수인계

## 책임

MongoDB 성공 컬렉션을 SQLite에서 조회할 수 있도록 Silver 현재값 모델과 관계를 정의한다.

## 직원 사진 계약

- `SilverEmployee.profile_image_url`은 nullable URL 컬럼이다.
- 이미지 바이너리는 DB에 저장하지 않고 사내 HRIS 또는 객체 저장소의 URL만 저장한다.
- 현재 원본에 필드가 없으면 `NULL`로 유지한다.
- URL은 HR 상세 화면에서만 사용하며 팀 관리자 요약 DTO에는 전달하지 않는다.

## 변경 주의

필드 변경 시 migration, `success_to_sqlite.py`, 후보 DTO, 역할별 비노출 테스트를 함께 수정한다.
