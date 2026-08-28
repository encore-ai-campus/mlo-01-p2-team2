# 실행 명령 (`cli.py`)

## 역할

설정을 읽고 추출기, 표준화기, 검증기, 저장소, 로거를 조립합니다.

```powershell
# 내장 데이터
python -m mongo_pipeline --demo

# MongoDB
mongo-pipeline --config config.json

# cron/Task Scheduler에서 1회 실행하는 tick
mongo-pipeline --config config.json --scheduled-once

# 프로세스 상주 방식
mongo-pipeline --config config.json --schedule

# 운영 보조 명령
mongo-pipeline --config config.json --reprocess
mongo-pipeline --config config.json --backup-once

# 프로젝트 표준 Silver 규칙으로 JSONL 파일 실행
mongo-pipeline --input-jsonl C:/path/to/records.jsonl --rules rules/silver_canonical.yaml

# 프로젝트 표준 Silver 규칙으로 CSV 파일 실행
mongo-pipeline --input-csv C:/path/to/records.csv --rules rules/silver_canonical.yaml

# JSONL을 읽고 로컬 JSONL 결과로만 확인
mongo-pipeline --input-jsonl C:/path/to/records.jsonl `
  --rules rules/silver_canonical.yaml `
  --output output

# JSONL 로그를 Django 프로젝트의 log_lake/raw_data에 저장
mongo-pipeline --input-jsonl C:/path/to/records.jsonl `
  --rules rules/silver_canonical.yaml `
  --output output `
  --log-directory ../django/log_lake/raw_data
```

`--demo`, `--config`, `--input-yaml`, `--input-jsonl`, `--input-csv`는 동시에 사용할 수 없습니다.
`--rules`는 MongoDB 설정의 `standardization.rules_file`보다 우선합니다.
CSV 직접 입력은 `--csv-encoding`, `--csv-delimiter`, `--csv-quotechar`,
`--csv-skipinitialspace`로 파일 형식을 조정할 수 있습니다.
`--log-directory`를 생략한 파일 입력 모드는 프로젝트에 Django 폴더가 있으면
`django/log_lake/raw_data`를 기본값으로 사용합니다. 로그는 `pipeline.jsonl`,
`quality.jsonl`, `quarantine.jsonl`, `restoration.jsonl`로 생성됩니다.
파일 입력을 포함한 신규 실행은 결과 디렉터리에 `bronze_raw_records.jsonl`과
`manifest.json`을 함께 생성하고, 복구율 분모는 Bronze 고유
`source_record_id`입니다. `--reprocess` 실행은 기존 Bronze 원본을 재생성하지
않습니다.

## 수정 지점

새 구현체를 실제 실행에 연결할 때만 `main()`의 조립 부분을 변경합니다.

정상/실패 MongoDB 적재는 `config.django-mongodb.example.json`을 복사해
`source.path`, `sink`의 DB/컬렉션, Django `project_root`를 환경에 맞게
수정한 뒤 `mongo-pipeline --config config.json`으로 실행합니다.

`--scheduled-once`는 다음을 한 번에 수행합니다.

1. 현재시각에서 1분을 뺀 시각을 cutoff으로 사용한 증분 원본 조회
2. 성공/실패 DB 분기 적재
3. 설정이 켜져 있으면 실패 DB 재처리
4. 마지막 백업 시각이 `data_lake.interval_minutes`(이 프로젝트 기본 180분)
   이상 지난 경우 별도 DATA-LAKE DB와 파일에 snapshot 저장

동시 실행 방지는 설정의 lock 파일로 처리합니다. 운영 cron 예시는
[`ops/mongo_pipeline.cron`](../ops/mongo_pipeline.cron)에 있습니다.
