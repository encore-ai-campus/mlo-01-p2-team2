# 05. 3분 스케줄러

`schedule.interval_seconds=180`, `delay_seconds=60`으로 설정한다. DATA-LAKE는
`data_lake.interval_minutes=180`으로 3시간 주기를 사용한다.

운영 방식은 둘 중 하나를 선택한다.

- 상주 방식: `python -m mongo_pipeline --config config.json --schedule`
- cron/Task Scheduler: 3분마다 `--scheduled-once` 실행

한 tick에서 원본 증분 처리, 실패 DB 재처리, 백업 due 확인을 수행한다. lock 파일이
이미 있으면 중복 실행을 건너뛴다. watermark는 원본 처리가 실패한 경우 전진하지
않으며, 다음 tick에 같은 구간을 재조회할 수 있다.

Django에서 SQLite 적재만 별도로 상주시킬 때는 다음 명령을 사용한다. 명령은
동일한 `3n+1분 00초(KST)` 예약 시각마다 성공 collection의 미처리
`normalization_run_id`를 하나씩 선택해, 미처리 실행이 없어질 때까지 반복 처리한다.

```powershell
cd ../django
python manage.py load_success_to_sqlite --config ../validation_pipeline/config.json
```

즉시 한 번만 실행하려면 `--once`를 추가한다.

상주 프로세스는 supervisor가 재시작하도록 구성하고, cron 방식은
[`ops/mongo_pipeline.cron`](../../ops/mongo_pipeline.cron)을 참고한다.
