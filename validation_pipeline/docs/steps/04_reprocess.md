# 04. 실패 DB 재처리

`reprocess.enabled=true`이면 실패 DB에서 `pending` 또는 `retry` 문서를 읽는다.
`attempt_count < max_attempts` 조건도 함께 적용한다.

재처리 흐름은 다음과 같다.

1. 실패 wrapper 안의 `document`를 꺼낸다.
2. 기존 실패 문서 ID와 시도 횟수를 runtime context에 붙인다.
3. 동일 표준화·검증·성공 적재 경로를 다시 실행한다.
4. 성공하면 원래 실패 문서를 `resolved`로 갱신한다.
5. 실패하면 사유와 이력을 누적하고 `retry` 또는 `exhausted`로 갱신한다.

재처리 실행은 이미 보존된 Bronze 원문과 Manifest를 기준으로 Silver 경로만
재실행하며, 동일 Bronze 원본을 새로 만들거나 덮어쓰지 않는다.

최대 횟수를 초과한 문서는 자동 삭제하지 않는다. 운영자가 원인을 확인한 뒤
별도 조치할 수 있도록 실패 DB에 보존한다.
