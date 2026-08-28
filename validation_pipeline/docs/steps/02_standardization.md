# 02. 표준화와 Silver 모델 분리

프로젝트 기준 실행 규칙은 `rules/silver_canonical.yaml`이다. 이 규칙은 원본
필드명을 `DATA_STANDARD_DICTIONARY.md`의 표준 컬럼으로 바꾸고, NFC·공백·코드·상태·날짜를 처리한다.

```text
원천 문서
  ↓ YAML canonical rule
통합 표준 후보 + correction_codes
  ↓ silver.py
silver_employee / silver_area / silver_parent_area / silver_top_area_detail
```

규칙 핵심:

- 직원 ID는 `EMP` + 6자리, 영역 ID는 `BIZ` + 5자리
- 활성 상태는 승인된 8개 원천값만 `true/false`로 매핑
- 최상위 레벨은 `TOP`, `TOP_LEVEL`, `top_level`, `최상위`, `1`만 `TOP`으로 매핑
- 날짜는 `Asia/Seoul` 기준 `YYYY-MM-DDTHH:MM:SS+09:00`으로 출력
- `source_record_id`, `dataset_id`, `normalization_run_id`는 값 자체를 변경하지 않음
- 보정 코드는 데이터 사전의 승인 목록만 기록

모든 `Pipeline` 실행은 표준화기가 반환한 결과에 현재 실행 ID를 최상위
`normalization_run_id`로 보장한다. canonical 규칙처럼 `_runtime`에서 이미
생성한 값이 있으면 현재 실행 ID와 일치하는지 확인하고, 값이 없으면 현재
`Pipeline.run_id`를 주입한다. 다른 실행 ID가 들어온 문서는 실행 경계가
섞인 것으로 보고 표준화 실패 처리한다. 따라서 MongoDB 성공 문서의
증분 적재는 `_pipeline.run_id`가 아니라 최상위 `normalization_run_id`를
조회 기준으로 사용할 수 있다.

표준화 실패는 `rejected.jsonl`과 `quarantine.jsonl`에 오류 코드·원본 참조와
함께 남긴다. Bronze 원문·Manifest는 이 단계 전에 파이프라인이 보존하며,
표준화기는 Bronze의 `source_record_id`를 Silver 계보 필드로 전달한다.
