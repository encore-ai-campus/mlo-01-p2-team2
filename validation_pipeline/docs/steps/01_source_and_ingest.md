# 01. 원본 조회와 증분 적재

원본과 결과가 모두 Django가 관리하는 MongoDB 연결을 사용하도록
`django_mongodb` source/sink를 설정한다.

1. Django `database_alias`의 MongoClient를 재사용한다.
2. `source.database.collection`에서 read-only로 읽는다.
3. 표준화·검증 후 성공 DB 또는 실패 DB에 저장한다.
4. 성공한 tick만 watermark를 전진시킨다.

기본 tick 조건은 다음과 같다.

```text
previous_watermark < ingested_at <= current_utc - 60 seconds
```

최신 1분을 유예하므로 적재 중인 문서를 다음 tick으로 넘길 수 있다. 최초 실행은
watermark가 없으므로 cutoff 이전 문서를 조회한다.
