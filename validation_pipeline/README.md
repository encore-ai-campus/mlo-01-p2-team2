# MongoDB 공통 데이터 파이프라인

MongoDB 문서를 **추출 → 표준화 → 검증 → 저장**하는 기본 파이프라인입니다.
데이터 구조를 미리 정하지 않고 실행한 뒤, 확인된 업무 규칙만 추가할 수 있게 구성했습니다.

첨부된 표준화 DB 파일은 **헤더명과 컬럼 순서만 컬럼 계약으로 사용**합니다.
행 데이터, 샘플 값, 허용값 목록, 보정 코드, 오류 통계는 시스템 설정이나 YAML에
복사하지 않습니다. 실제 값 변환 규칙은 운영 승인 후 별도의 YAML로 주입합니다.

## 빠른 실행

MongoDB 없이 동작을 확인합니다.

```powershell
$env:PYTHONPATH = "src"
python -m mongo_pipeline --demo
```

레거시 YAML 데이터를 규칙 파일로 바로 표준화합니다.

```powershell
pip install -e .
python -m mongo_pipeline `
  --input-yaml examples/legacy_input.yaml `
  --rules rules/legacy_org.yaml `
  --output output
```

샘플과 같은 JSONL envelope은 `payload.*` 규칙으로 표준화할 수 있습니다.
로컬 파일 결과만 확인할 때는 다음처럼 실행합니다.

아래 `legacy_org.yaml`과 `legacy_org_jsonl.yaml`은 테스트용 독립 fixture입니다.
첨부 표준화 DB에서 추출한 운영 규칙이 아니며, 운영 설정에는 사용하지 않습니다.

```powershell
python -m mongo_pipeline `
  --input-jsonl C:/path/to/records.jsonl `
  --rules rules/legacy_org_jsonl.yaml `
  --output output
```

MongoDB로 실행할 때는 패키지를 설치하고 예제 설정을 복사합니다.

```powershell
pip install -e .
Copy-Item config.example.json config.json
$env:MONGODB_URI = "mongodb://사용자:비밀번호@호스트:27017"
mongo-pipeline --config config.json

# cron/Task Scheduler에서 3분마다 한 tick 실행
mongo-pipeline --config config.json --scheduled-once

# 프로세스를 계속 실행하는 scheduler 방식
mongo-pipeline --config config.json --schedule

# 운영 보조 명령
mongo-pipeline --config config.json --reprocess
mongo-pipeline --config config.json --backup-once
```

Django에 이미 `django_mongodb_backend`를 연결한 환경에서 정상/실패 DB를
분리 적재하려면 [`config.django-mongodb.example.json`](config.django-mongodb.example.json)을
기준으로 설정합니다. 이 설정은 Django의 `mongodb` database alias를
재사용해 `legacy_standardized`와 `legacy_failed`에 각각 upsert합니다.
실제 업무 규칙과 DB명은 예시 파일의 초안 값을 운영 전에 확정해야 합니다.
필요하면 `pip install -e ".[django]"`로 이 패키지의 Django 연동 의존성도
설치할 수 있습니다. 이미 `chapter2/monorepo` 환경에 설치되어 있다면
그 가상환경에서 `PYTHONPATH=src`로 실행하면 됩니다.

`--scheduled-once`는 `ingested_at <= 현재시각-1분` 및 이전 watermark 이후 조건으로
증분 조회하고, 실패 DB 재처리와 시간별 DATA-LAKE 백업 due 여부도 같은 tick에서
확인합니다. 동일 tick 중복 실행은 lock 파일로 차단합니다.

## 기능별 문서

문서 이름은 담당 코드와 같게 두었습니다.

| 담당 코드 | 문서 | 역할 |
|---|---|---|
| `config.py` | [설정](docs/config.md) | 실행 설정 읽기 |
| `sources.py` | [추출](docs/sources.md) | MongoDB 문서 조회 |
| `standardizers.py` | [표준화](docs/standardizers.md) | JSON 호환 값 변환 |
| `rule_standardizer.py` | [YAML 규칙](docs/yaml_rules.md) | YAML 기반 업무 표준화 |
| `validators.py` | [검증](docs/validators.md) | 품질 규칙 검사 |
| `profiler.py` | [프로파일](docs/profiler.md) | 필드와 타입 요약 |
| `sinks.py` | [저장](docs/sinks.md) | 정상·제외·리포트 저장 |
| `loggers.py` | [로깅](docs/loggers.md) | 역할별 로그 설정 |
| `pipeline.py` | [파이프라인](docs/pipeline.md) | 전체 단계 조정 |
| `cli.py` | [실행 명령](docs/cli.md) | 구성요소 조립과 실행 |
| `scheduler.py` | [운영 스케줄](docs/steps/05_scheduler.md) | 3분 증분 실행과 lock |
| `reprocessing.py` | [실패 재처리](docs/steps/04_reprocess.md) | pending/retry/exhausted 처리 |
| `backup.py` | [DATA-LAKE 백업](docs/steps/06_datalake_backup.md) | 시간별 snapshot과 manifest |

업무 규칙은 해당 기능 문서에 적힌 확장 지점에만 추가합니다.

## 설명 자료

코드를 설명하거나 인수인계할 때는 [컨텍스트 요약](context/00_overview.txt)부터 읽습니다.

현재 레거시 조직 데이터 문서는 [컬럼 계약 범위](docs/legacy_standardization_rules.md)만
기록합니다. 실행 가능한 값 변환 YAML 형식은 [YAML 규칙 사용법](docs/yaml_rules.md)을
확인하고, 운영 승인된 규칙 파일을 `standardization.rules_file`에 연결합니다.

## 작업 완료 조건

- [x] 표준화 YAML 적용 전의 실행·분기·상태 관리 시스템
- [x] 추출/표준화/검증/저장/재처리/백업 단계별 MD 문서
- [x] 정상 DB·실패 DB 및 DATA-LAKE backup 관리 코드
- [ ] 운영 MongoDB 접속정보·컬렉션명·DATA-LAKE mount 경로 확정
- [ ] 실제 업무 값 규칙을 별도 YAML로 승인·적용

## 테스트

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```
