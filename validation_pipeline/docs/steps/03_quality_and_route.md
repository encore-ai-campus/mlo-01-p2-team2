# 03. 성공/실패 분기와 품질

표준화에 성공해도 `quality.required_fields`와 `quality.field_types` 검증을
통과해야 성공 DB에 저장한다.

| 결과 | 저장 위치 | 보존 내용 |
|---|---|---|
| 표준화·검증 통과 | `sink.success_database.success_collection` | 표준 문서 + `_pipeline` |
| 표준화 실패 | `sink.failure_database.failure_collection` | 원본 + 사유 + stage |
| 검증 실패 | 실패 DB 같은 collection | 표준화 결과 + validator 사유 |
| 실행 리포트 | `report_database.report_collection` | 건수, 상태, 품질, schema profile |

실패 문서는 삭제하지 않고 `document_id`, `stage`, `reasons`, `attempt_count`,
`reprocess_status`를 남긴다. 실행 상태는 `SUCCESS`, `PARTIAL_SUCCESS`,
`FAILED`로 구분한다.
