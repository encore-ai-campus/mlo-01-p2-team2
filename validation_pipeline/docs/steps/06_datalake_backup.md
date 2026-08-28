# 06. DATA-LAKE 백업

`DjangoMongoDataLakeBackup`은 매 tick마다 마지막 백업 시각을 확인하고,
`data_lake.interval_minutes`(이 프로젝트 기본 180분)가 지나면 snapshot을 만든다.

기본 대상은 `second_project.bronze_raw_records`, 정상 collection, 실패
collection, pipeline report collection이다.
설정의 `data_lake.collections`로 대상을 명시할 수도 있다.

```text
<root>/YYYY/MM/DD/HH/<backup_id>/
├── success_records.jsonl
├── failure_records.jsonl
├── pipeline_runs.jsonl
└── manifest.json
```

각 JSONL의 건수와 SHA-256을 manifest에 기록한다. 동시에 별도
`data_lake.database`의 대상별 snapshot collection과
`data_lake.manifest_collection`에 같은 실행을 저장한다. 임시 파일을 완성 파일로
교체한 뒤 manifest를 기록하므로 부분 snapshot을 성공으로 표시하지 않는다.
백업은 원본 MongoDB에서 문서를 삭제하지 않으며, `root`를 mounted DATA-LAKE
경로로 지정하면 파일 snapshot도 그 경로에 저장된다.
