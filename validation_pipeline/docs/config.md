# 설정 (`config.py`)

## 역할

`config.json`을 읽어 추출, 검증, 저장, 로그 설정으로 나눕니다.
예시는 [`config.example.json`](../config.example.json)에 있습니다.

## 주요 설정

| 구역 | 내용 |
|---|---|
| `source` | MongoDB 조회 또는 JSONL 파일 입력 설정 |
| `quality` | 필수 필드와 기본 타입 |
| `standardization.rules_file` | 선택적 YAML 표준화 규칙 파일 |
| `output.directory` | 결과 저장 위치 |
| `sink` | JSONL 또는 Django/PyMongo 기반 MongoDB 저장 위치 |
| `reprocess` | 실패 DB 재처리 대상과 최대 시도 횟수 |
| `schedule` | 3분 주기, 1분 지연, watermark와 lock |
| `data_lake` | 시간별 snapshot 대상과 DATA-LAKE root |
| `logging` | 로그 레벨과 저장 위치 |

상대 경로는 `config.json`이 있는 디렉터리를 기준으로 계산합니다.
접속 URI는 파일에 쓰지 않고 `source.uri_env`가 가리키는 환경 변수로 전달합니다.
`standardization.rules_file`이 상대 경로이면 `config.json` 위치를 기준으로 계산합니다.

`source.type`은 `mongodb`(기본값), `django_mongodb`, `jsonl` 중 하나입니다.
`django_mongodb`는 `database_alias`의 Django MongoClient를 읽기에도
사용합니다. JSONL인 경우 `source.path`를 지정합니다. `sink.type`은
`jsonl`(기본값), `mongodb`, `django_mongodb` 중 하나입니다. `django_mongodb`는 `project_root`를
`sys.path`에 추가하고 `settings_module`을 초기화한 뒤
`database_alias`의 Django MongoClient를 재사용합니다.

정상/실패 DB를 분리하려면 `sink.success_database`와
`sink.failure_database`를 서로 다르게 설정합니다. 두 DB는 같은 Django
MongoClient/클러스터를 사용할 수 있으며, MongoDB 권한은 두 DB에 모두
부여되어야 합니다.

기존 Django 설정이 `BOOKSTORE_MONGODB_URI`와 `BOOKSTORE_MONGODB_NAME`으로
`DATABASES["mongodb"]`를 만들고 있다면 `database_alias: "mongodb"`를
사용합니다. 원본도 Django alias에서 읽을 때는 `source.type`을
`django_mongodb`로 바꾸고 `source.collection`을 지정합니다.

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

`data_lake`는 기본적으로 정상 DB, 실패 DB, pipeline report collection을
`YYYY/MM/DD/HH/<backup_id>/` 아래 JSONL로 내보내고 `manifest.json`에 건수와
SHA-256을 기록합니다. MongoDB 문서는 삭제하지 않으며, `root`를 mounted
DATA-LAKE 경로로 바꾸면 해당 저장소로 이동합니다.

첨부 Excel은 이 설정의 컬럼명/순서를 정하는 참고자료일 뿐입니다. 행의 값이나
값 목록을 설정으로 읽지 않습니다.

## 수정 지점

새 설정은 담당 데이터 클래스와 `AppConfig.from_file()`에 함께 추가합니다.
