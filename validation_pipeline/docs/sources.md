# 추출 (`sources.py`)

## 역할

원본 문서를 한 건씩 파이프라인에 전달합니다.

- `MongoSource`: MongoDB의 `find` 또는 `aggregation` 실행
- `DjangoMongoSource`: Django `database_alias`가 관리하는 MongoClient로 조회
- `JsonlSource`: 한 줄에 하나의 JSON object가 있는 파일 읽기
- `IterableSource`: 데모와 테스트 데이터 전달
- `YamlFileSource`: 단일 문서, 문서 배열, `documents` 배열 YAML 읽기

`find`는 `query`, `projection`, `batch_size`, `limit`를 사용합니다.
문서 형태를 바꿔 읽어야 하면 설정의 `aggregation`을 사용합니다.
추출은 읽기 전용이므로 `$out`, `$merge`는 허용하지 않습니다.

스케줄러가 MongoDB source를 실행할 때는 설정된 watermark 필드에 대해
`watermark < field <= now-delay` 조회 조건을 추가합니다. 기본 `delay`는
60초라서 아직 적재가 끝나지 않았을 수 있는 최신 1분을 다음 tick으로 미룹니다.

JSONL의 빈 줄은 건너뛰며, JSON 문법 오류나 object가 아닌 줄은
`source.continue_on_parse_error=true`일 때 `_source_error` 문서로 감싸
실패 저장소까지 전달합니다. 따라서 한 줄의 파싱 오류가 전체 실행을 중단시키지
않습니다. 파일을 읽을 수 없는 오류는 실행 자체의 실패로 처리합니다.

## 수정 지점

다른 원천을 연결할 때는 `DocumentSource`의 `read`, `close`, `description`만 구현합니다.
