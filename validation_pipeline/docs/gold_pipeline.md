# Gold SQLite ETL 및 Dataset Release Package

## 목적

이 파이프라인은 Django SQLite에 적재된 Silver 성공 데이터를 읽어 Gold 데이터셋과
검증·배포 패키지를 생성한다. 현재 입력 계약은 다음 네 개의 Silver 테이블이다.

| 입력 테이블 | 업무 식별자 | Gold entity |
|---|---|---|
| silver_employee | employee_id | employee |
| silver_area | area_id | area |
| silver_parent_area | parent_area_id | parent_area |
| silver_top_area_detail | top_area_id | top_area |

legacy_org_record는 기존 staging 테이블이므로 기본 Gold 입력에서는 제외한다.

## Canonical 규칙 재사용

rules/silver_canonical.yaml은 원천 한 행을 네 Silver 모델로 만드는 전체 계약이다.
Gold에서는 이 규칙을 원천 표준화기로 다시 실행하지 않는다. RDB에는 이미
표준화된 테이블이 분리되어 있기 때문에, 각 테이블에 존재하는 canonical 필드의
타입·코드·enum·datetime 규칙을 최종 검증 계약으로 적용한다.

따라서 현재 Silver 구조에 없는 필드를 억지로 조합하지 않으면서도 다음 규칙을
재사용한다.

- EMP + 6자리 직원 식별자
- BIZ + 5자리 영역 식별자
- top_area_level의 TOP 도메인
- 필수값과 문자열·boolean 타입
- Asia/Seoul timezone이 포함된 ISO 8601 datetime

## 실행

validation_pipeline 디렉터리에서 실행한다.

    $env:PYTHONPATH = "src;../django/.venv/Lib/site-packages"
    python -m gold_pipeline --config gold_config.example.json

설정을 사용하지 않고 직접 실행할 수도 있다.

    python -m gold_pipeline --sqlite ../django/db.sqlite3 --rules rules/silver_canonical.yaml --output output/gold_release --release-version 0.1.0 --run-id gold-20260828-001

동일한 SQLite snapshot, output 경로, run-id로 다시 실행하면 JSONL에 append하지
않고 파일을 재작성한다. 따라서 동일 실행을 반복해도 데이터 행이 누적되지 않는다.

검증 오류가 있으면 패키지는 생성되지만 release_ready가 false가 되고, CLI는
비정상 종료 코드 2를 반환한다. 오류 행은 data/rejected_records.jsonl에서
확인할 수 있다.

## Release Package 구조

    output/gold_release/
    ├── data/
    │   ├── silver_employee.jsonl
    │   ├── silver_area.jsonl
    │   ├── silver_parent_area.jsonl
    │   ├── silver_top_area_detail.jsonl
    │   ├── gold_area_dataset.jsonl
    │   └── rejected_records.jsonl
    ├── source_schema.json
    ├── schema.json
    ├── validation_report.json
    ├── manifest.json
    ├── data_dictionary.md
    ├── README.md
    └── release_notes.md

gold_area_dataset.jsonl은 silver_area를 중심으로 직원과 상위영역을 조인한
평탄화 AI Ready 데이터셋이다. 현재 SQLite 스키마에는 area와 top_area 사이의
직접 외래키가 없으므로 top_area는 별도 entity snapshot으로 보존하며 임의로
조인하지 않는다.

checksum 파일은 생성하지 않는다.

## 검증 단계

1. SQLite 파일 존재 여부와 네 테이블·필수 컬럼·PK 확인
2. JSON TEXT 메타데이터인 correction_codes와 _standardization 파싱
3. silver_canonical.yaml 기반 필드별 타입·코드·enum·datetime 검증
4. 필수값 및 최종 record key 중복 검증
5. area.manager_employee_id와 area.parent_area_id 참조 무결성 검증
6. area·직원·상위영역 조인 후 Gold 주 데이터셋 생성
7. 검증 패킷과 manifest 생성

validation_report.json에는 source/accepted/rejected 건수, 오류 코드별 건수,
보정 코드 통계, canonical 규칙 파일, release_ready 여부가 기록된다.

## 테스트

외부 DB 없이 테스트용 SQLite를 생성해 실행한다.

    $env:PYTHONPATH = "src;../django/.venv/Lib/site-packages"
    python -m unittest tests.test_gold_pipeline -v

전체 기존 테스트와 함께 실행하려면 다음 명령을 사용한다.

    python -m unittest discover -s tests -v
