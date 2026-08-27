# 운영 실행 예시

## Linux cron

[`mongo_pipeline.cron`](mongo_pipeline.cron)의 경로와 가상환경을 운영 서버에 맞게
수정한 뒤 crontab에 등록한다.

`--scheduled-once` 한 번이 원본 증분 처리, 설정된 실패 DB 재처리, 백업 due 확인을
담당한다. 별도 1시간 cron을 추가하지 않아도 `data_lake.interval_minutes`가
백업 시점을 제어한다.

## 상주 프로세스

cron 대신 다음 명령을 systemd, Windows 서비스 또는 컨테이너 supervisor로
상주시킬 수 있다.

```text
python -m mongo_pipeline --config config.json --schedule
```

두 방식을 중복 등록하지 않는다.
