# 로깅 (`loggers.py`)

## 역할

표준화 결과와 검증 결과를 서로 다른 UTF-8 파일에 한 줄씩 누적합니다.

```text
logs/
├── standardize.log
└── validation.log
```

형식의 날짜와 숫자는 실행 결과로 매번 계산됩니다.

```text
[YYYY-MM-DD HH:MM:SS] 컬럼명 변환 N건 | 타입 변환 N건 | 규칙 적용 N건 | 규칙 NULL 처리 N건 | 규칙 경고 N건 | 표준화 완료 N건 | 변환 실패 N건
[YYYY-MM-DD HH:MM:SS] 검사 N건 | PASS N건 | FAIL N건 | NULL 오류 N건 | 형식 오류 N건
```

- `INFO`: 해당 단계 실패 없음
- `WARNING`: 일부 문서 실패
- `ERROR`: 해당 단계 전체 실패 또는 파이프라인 중단

한 문서에서 문제가 여러 개 발견되면 오류 수가 `FAIL` 문서 수보다 클 수 있습니다.

## 수정 지점

파일명과 출력 형식은 `create_stage_loggers()`에서, 집계 항목은 `Pipeline._log_stage_summaries()`에서 바꿉니다.
