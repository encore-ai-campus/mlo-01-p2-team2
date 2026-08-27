# 00. 범위와 컬럼 계약

첨부 표준화 DB는 헤더명과 헤더 순서를 정하는 참고자료로만 사용한다.

- 행 데이터와 샘플 값은 읽지 않는다.
- 허용값, enum 매핑, 보정 코드, 오류 건수는 첨부 파일에서 만들지 않는다.
- 첨부 파일은 수정하지 않는다.
- 실제 업무 값 규칙은 운영 승인 후 `standardization.rules_file`에 별도 YAML로 연결한다.

현재 column contract YAML은 출력 컬럼 projection만 선언한다. MongoDB의 기술
식별자 `_id`는 업무 컬럼과 별도로 보존한다. `id`를 `_id`로 자동 치환하지 않는다.

```text
raw MongoDB
    ↓
column contract / future YAML
    ↓
quality validation
    ├─ success database
    └─ failure database
```
