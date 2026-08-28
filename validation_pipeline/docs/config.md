# 설정 (`config.py`)

## 역할

`config.json`을 읽어 추출, 검증, 저장, 로그 설정으로 나눕니다.
예시는 [`config.example.json`](../config.example.json)에 있습니다.

## 주요 설정

| 구역 | 내용 |
|---|---|
| `source` | MongoDB 조회 또는 JSONL/CSV 파일 입력 설정 |
| `quality` | 필수 필드, 기본 타입, 최종 업무 식별자 중복 기준 |
| `standardization.rules_file` | 선택적 YAML 표준화 규칙 파일 |
| `output.directory` | 결과 저장 위치 |
| `sink` | JSONL 또는 Django/PyMongo 기반 MongoDB 저장 위치와 Silver 모델별 collection |
| `reprocess` | 실패 DB 재처리 대상과 최대 시도 횟수 |
| `schedule` | 3분 주기, 1분 지연, watermark와 lock |
| `data_lake` | 시간별 snapshot 대상과 DATA-LAKE root |
| `logging` | 로그 레벨과 저장 위치 |

상대 경로는 `config.json`이 있는 디렉터리를 기준으로 계산합니다.
접속 URI는 파일에 쓰지 않고 `source.uri_env`가 가리키는 환경 변수로 전달합니다.
`standardization.rules_file`이 상대 경로이면 `config.json` 위치를 기준으로 계산합니다.

`source.type`은 `mongodb`(기본값), `django_mongodb`, `jsonl`, `csv` 중 하나입니다.
`django_mongodb`는 `database_alias`의 Django MongoClient를 읽기에도
사용합니다. JSONL·CSV인 경우 `source.path`를 지정합니다. CSV는
`encoding`, `delimiter`, `quotechar`, `skipinitialspace`와
`continue_on_parse_error`를 설정할 수 있습니다. `sink.type`은
`jsonl`(기본값), `mongodb`, `django_mongodb` 중 하나입니다. `django_mongodb`는 `project_root`를
`sys.path`에 추가하고 `settings_module`을 초기화한 뒤
`database_alias`의 Django MongoClient를 재사용합니다.

CSV 설정 예시는 다음과 같습니다.

```json
{
  "source": {
    "type": "csv",
    "path": "examples/silver_input.csv",
    "encoding": "utf-8-sig",
    "delimiter": ",",
    "quotechar": "\"",
    "continue_on_parse_error": true
  }
}
```

정상/실패 DB를 분리하려면 `sink.success_database`와
`sink.failure_database`를 서로 다르게 설정합니다. 두 DB는 같은 Django
MongoClient/클러스터를 사용할 수 있으며, MongoDB 권한은 두 DB에 모두
부여되어야 합니다.

canonical Silver 실행에서는 `sink.silver_database`와
`sink.silver_collections`에 네 모델 collection을 지정합니다. 생략하면 정상 DB에
`silver_employee`, `silver_area`, `silver_parent_area`, `silver_top_area_detail`로
upsert합니다.

모든 신규 수집 실행은 원천 문서를 Bronze에 먼저 보존합니다. MongoDB sink에서는
`sink.bronze_database`(생략 시 `second_project`), `sink.bronze_collection`,
`sink.manifest_collection`으로 Bronze와 실행별 Manifest 대상을 지정합니다.
JSONL sink에서는 각 실행 디렉터리의 `bronze_raw_records.jsonl`과
`manifest.json`으로 생성됩니다.

기존 Django 설정이 `BOOKSTORE_MONGODB_URI`와 `BOOKSTORE_MONGODB_NAME`으로
`DATABASES["mongodb"]`를 만들고 있다면 `database_alias: "mongodb"`를
사용합니다. 원본도 Django alias에서 읽을 때는 `source.type`을
`django_mongodb`로 바꾸고 `source.collection`을 지정합니다.

파이프라인 완료 후 성공 DB를 SQLite RDB로 옮기려면 Django 프로젝트에서
다음 명령을 실행합니다.

```powershell
cd ../django
python manage.py migrate --database sqlite3
python manage.py load_success_to_sqlite --config ../validation_pipeline/config.json
```

이 명령은 `sink.silver_database`(없으면 `sink.success_database`)의
`silver_employee`, `silver_area`, `silver_parent_area`,
`silver_top_area_detail`만 읽습니다. 실패 DB는 RDB 적재 대상이 아닙니다.

`schedule.interval_seconds` 기본값은 180초, `schedule.delay_seconds` 기본값은
60초입니다. scheduler는 `watermark_field`에 대해
`watermark < field <= now-delay` 조건을 만들고 성공한 tick의 watermark를
`watermark_path`에 원자적으로 저장합니다. `lock_path`로 동시 실행을 막습니다.

운영 환경에서는 `--schedule` 장기 실행 프로세스 또는
`--scheduled-once`를 3분마다 호출하는 cron/Task Scheduler 중 하나를 선택합니다.
두 방식을 동시에 등록하지 않습니다.

`reprocess`는 실패 DB의 `pending`/`retry` 문서를 읽고 `max_attempts` 미만만
재처리합니다. 정상화되면 원래 실패 문서를 `resolved`로 표시하고, 다시 실패하면
`retry` 또는 `exhausted`로 누적합니다.

`data_lake`는 기본적으로 `second_project.bronze_raw_records`, 정상 DB, 실패 DB,
pipeline report collection을 별도 `data_lake.database`(기본
`encore_data_lake`)에 실행별 snapshot으로 upsert하고, 동시에
`YYYY/MM/DD/HH/<backup_id>/` 아래 JSONL과 `manifest.json`을 남깁니다.
`data_lake.interval_minutes`를 180으로 설정하면 3시간마다 실행되며, MongoDB
원본은 삭제하지 않습니다. `root`를 mounted DATA-LAKE 경로로 바꾸면 파일
snapshot도 해당 저장소에 기록됩니다.

첨부 Excel은 이 설정의 컬럼명/순서를 정하는 참고자료일 뿐입니다. 행의 값이나
값 목록을 설정으로 읽지 않습니다.

`logging.directory`는 표준화·검증 로그 저장 위치입니다. 로그는 해당 디렉터리에
JSONL 감사 로그 `pipeline.jsonl`, `quality.jsonl`, `quarantine.jsonl`,
`restoration.jsonl`로 기록됩니다. 기존 text 로그는 호환성을 위해 함께 생성될 수
있으며, 인수 증적은 JSONL을 기준으로 합니다. `config.django-mongodb.example.json`은
현재 프로젝트 구조 기준으로 `../django/log_lake/raw_data`를 사용합니다.

`quality.unique_fields`에는 표준화 결과에서 중복되면 안 되는 업무 식별자의
점 표기 경로를 지정합니다. 모든 공백 제거·코드 포맷 보정·오류값 처리가 끝난
뒤 문서별/Silver 검증의 마지막 단계에서 이 값을 비교하며, 지정하지 않으면
일반 중복 검사는 수행하지 않습니다. 예를 들어 중첩 JSONL 결과의 `area_no`는
`["payload.area_no"]`, canonical Silver 결과의 지역 ID는 `["area_id"]`로
설정합니다. `parent_area_id`와 `top_area_id`처럼 여러 행에서 반복될 수 있는
참조값은 unique_fields에 넣지 않습니다.

## 수정 지점

새 설정은 담당 데이터 클래스와 `AppConfig.from_file()`에 함께 추가합니다.
