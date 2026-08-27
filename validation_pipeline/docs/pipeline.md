# 파이프라인 (`pipeline.py`)

## 역할

추출 → Bronze 원문·Manifest 보존 → 표준화 → Silver 모델 품질 게이트 → 저장 순서와 실행 건수를 관리합니다.
각 단계의 세부 규칙은 담당 클래스에 맡깁니다.

## 실행 상태

| 상태 | 조건 |
|---|---|
| `SUCCESS` | 모두 통과하거나 추출 결과가 0건 |
| `PARTIAL_SUCCESS` | 일부 문서만 제외 |
| `FAILED` | 모든 문서가 제외되거나 실행 중단, 또는 복구율 게이트 미달 |

0건 추출은 정상적인 증분 실행일 수 있어 성공으로 처리하되 경고를 남깁니다.
반대 정책이 필요하면 `_status_from_counts()`만 수정합니다.

## 수정 지점

canonical 실행은 먼저 원천 문서를 Bronze envelope으로 보존하고 실행별 Manifest를
기록합니다. 이후 `silver.py`에서 네 모델의 PK/FK를 배치 검증하고, 실행 리포트와
`restoration.jsonl`에 Bronze 고유 `source_record_id` 대비 Silver 복구율을 남깁니다.
Bronze 무결성 또는 복구율 게이트가 미달하면 실행 상태는 `FAILED`입니다.

처리 순서와 상태 정책만 이 파일에서 변경합니다. 변환·검증 규칙은 각 담당 모듈에 추가합니다.
