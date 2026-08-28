# Gold AI-Ready ETL

`second_project.gold`는 SQLite Silver 정본을 읽어 내부 인력 지속성 검토용 Gold 테이블을 만들고,
적재 전후 품질 검증과 릴리스 메타데이터 패키지를 함께 생성한다. 웹 요청마다 계산하는
기존 서비스와 배치가 `second_project/domain/continuity_policy.py`의 같은 정책을 사용한다.

## 실행 흐름

1. Silver 직원·업무영역·Parent·Top 영역을 한 스냅샷으로 읽는다.
2. PK/FK 관계, 필수 프로필, Top 영역 준비도, 비재직 대상 존재 여부를 사전 점검한다.
3. 비재직 대상별 담당 영역을 `parent_area_id`로 묶고 동일 Parent의 재직 관리자를 후보로 만든다.
4. 대상자 자신과 중복 후보를 제거한다. 부서·직위·근속은 제외 점수가 아니라 표시 근거로만 사용한다.
5. 프로필 누락 또는 upstream `DATE_CONFLICT` 대상은 `ON_HOLD`와 제외 원장에 기록한다.
6. 미래 입사일은 `TENURE_UNAVAILABLE`로 남기되 후보에서 제거하지 않는다.
7. 예상 행 수를 대사한 뒤 하나의 Gold 트랜잭션으로 적재하고, 적재 결과를 다시 조회해 검증한다.
8. manifest, lineage, dataset card, catalog, quality report, validation report를 생성하고 파일 존재와 크기를 확인한다.

정상 후보 0명은 `NO_MATCH`와 `0`, 데이터 누락·충돌은 `ON_HOLD`와 `null`로 저장한다.

## DB와 스키마 교체 지점

- Silver alias: 계약 파일의 `source_alias` 또는 명령의 `--source-alias`
- Gold alias: `DJANGO_GOLD_DB_ALIAS` 또는 `--target-alias`
- Gold SQLite 경로: `DJANGO_GOLD_SQLITE_PATH` (기본 `django/gold.sqlite3`)
- 논리/물리 격리: `etl/repositories/`만 ORM을 다루고 정책은 ORM 모델을 참조하지 않는다.
- 테이블/컬럼 변경: `models/`와 migration, 계약 파일의 `physical_tables`만 맞춰 변경한다.

Silver와 Gold가 다른 DB에 있을 수 있으므로 Gold 모델은 Silver 모델에 외래키를 만들지 않고 원천 ID와
계보 필드를 값으로 보존한다. Gold 내부의 release 관계만 외래키다.

## 최초 준비

```powershell
python manage.py migrate --database=gold second_project
```

실제 DB에 적용하기 전에는 복사본에서 `migrate --plan`과 테스트를 먼저 수행한다.

## Dry-run

```powershell
python manage.py build_gold_release `
  --release-id hr-gold-20260828-dry `
  --dataset-version 2026.08.28 `
  --as-of-date 2026-08-28 `
  --dry-run
```

Dry-run은 Gold 테이블을 변경하지 않지만 동일한 추출·변환·검증을 수행하고 릴리스 보고서를 만든다.

## 적재 및 예상 행 수 검증

```powershell
python manage.py build_gold_release `
  --release-id hr-gold-20260828-r1 `
  --dataset-version 2026.08.28 `
  --as-of-date 2026-08-28 `
  --expected-counts second_project/gold/contracts/expected_counts.example.json
```

예상 수치는 특정 Silver 스냅샷의 승인 기준값이다. Silver가 갱신되면 dry-run 결과를 검토한 후 별도
버전으로 갱신해야 한다. 성공한 같은 `release-id`를 재실행하면 행을 중복 적재하지 않고 기존 릴리스를
재검증한다. 실패한 release는 같은 ID로 다시 실행할 수 있다.

기본 산출 위치는 `django/data/gold_releases/<dataset>/<version>/`이며 다음 파일을 만든다.

- `dataset-manifest.json`
- `lineage.json`
- `dataset-card.md`
- `catalog-final.csv`
- `quality-report.json`
- `gold-validation.json`

이 파일들은 집계와 계보 메타데이터만 포함하며 직원 이름·사번을 포함하지 않는다. 직원 단위 Gold
테이블은 HR 제한 데이터로 취급한다.
