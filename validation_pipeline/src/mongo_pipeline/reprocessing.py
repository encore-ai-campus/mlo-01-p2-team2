from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from .config import ReprocessConfig, SourceConfig
from .sinks import DocumentSink, MongoSink
from .sources import DjangoMongoSource, DocumentSource


class DjangoMongoReprocessSource:
    """실패 DB에서 아직 해결되지 않은 원본 문서를 재처리 입력으로 읽는다."""

    def __init__(self, config: ReprocessConfig) -> None:
        """실패 큐 조회 조건과 재시도 상한을 준비한다."""

        if not config.enabled:
            raise ValueError("재처리 source는 reprocess.enabled=true여야 합니다.")
        self._config = config
        self._max_attempts = config.max_attempts
        source_config = SourceConfig(
            kind="django_mongodb",
            database_alias=config.database_alias,
            settings_module=config.settings_module,
            project_root=config.project_root,
            database=config.database,
            collection=config.collection,
            query=_pending_query(config.query, config.max_attempts),
            batch_size=config.batch_size,
            limit=config.limit,
        )
        self._source: DocumentSource = DjangoMongoSource(source_config)

    @property
    def description(self) -> dict[str, Any]:
        """실패 DB와 재처리 정책을 실행 리포트에 남긴다."""

        return {
            "type": "django_mongodb_reprocess",
            "database": self._config.database,
            "collection": self._config.collection,
            "database_alias": self._config.database_alias,
            "settings_module": self._config.settings_module,
            "max_attempts": self._max_attempts,
            "query_policy": "pending_or_retry_and_attempt_count_below_max",
        }

    def read(self) -> Iterator[Mapping[str, Any]]:
        """실패 wrapper의 document만 꺼내고 재처리 컨텍스트를 표시한다."""

        for failure in self._source.read():
            if not isinstance(failure, Mapping):
                continue
            failure_id = failure.get("_id")
            attempt_count = _next_attempt(failure.get("attempt_count", 0))
            context = {
                "failure_id": failure_id,
                "attempt_count": attempt_count,
                "max_attempts": self._max_attempts,
                "document_id": failure.get("document_id"),
            }
            raw_document = failure.get("document")
            if isinstance(raw_document, Mapping):
                document = dict(raw_document)
            else:
                document = {
                    "_source_error": {
                        "type": "failure_document_missing",
                        "message": "실패 문서 wrapper에 재처리할 document가 없습니다.",
                    }
                }
            document["_reprocess_context"] = context
            yield document

    def close(self) -> None:
        """Django가 관리하는 Mongo 연결을 유지하면서 source를 정리한다."""

        self._source.close()


class ReprocessSink:
    """정상 적재 후 실패 큐 상태까지 원자적 흐름으로 갱신하는 sink wrapper."""

    def __init__(self, sink: MongoSink, *, max_attempts: int) -> None:
        """Mongo sink와 재시도 상한을 주입받는다."""

        if max_attempts <= 0:
            raise ValueError("ReprocessSink의 max_attempts는 1 이상이어야 합니다.")
        self._sink = sink
        self._max_attempts = max_attempts
        self._successful_contexts: list[dict[str, Any]] = []
        self._processed = 0
        self._resolved = 0
        self._retried = 0
        self._exhausted = 0

    @property
    def description(self) -> dict[str, Any]:
        """기존 sink 위치와 재처리 모드를 함께 반환한다."""

        description = dict(self._sink.description)
        description["reprocess"] = {
            "enabled": True,
            "max_attempts": self._max_attempts,
            "status_field": "reprocess_status",
        }
        return description

    def write_success(self, document: dict[str, Any]) -> None:
        """정상 문서를 저장 대상으로 넘기고 원본 실패 ID를 기억한다."""

        context = _get_context(document)
        clean_document = _without_context(document)
        self._sink.write_success(clean_document)
        if context is not None:
            self._processed += 1
            self._successful_contexts.append(context)

    def write_rejected(
        self,
        *,
        document_id: str | None,
        stage: str,
        reasons: list[dict[str, Any]],
        document: Any | None = None,
    ) -> None:
        """재처리 실패를 새 wrapper로 복제하지 않고 기존 queue에 누적한다."""

        context = _get_context(document)
        if context is None:
            self._sink.write_rejected(
                document_id=document_id,
                stage=stage,
                reasons=reasons,
                document=document,
            )
            return

        failure_id = context.get("failure_id")
        if failure_id is None:
            self._sink.write_rejected(
                document_id=document_id,
                stage=stage,
                reasons=reasons,
                document=_without_context(document),
            )
            return

        attempt_count = _next_attempt(context.get("attempt_count", 1) - 1)
        status = self._sink.record_reprocess_failure(
            failure_id=failure_id,
            reprocess_run_id=_run_id_from_sink(self._sink),
            attempt_count=attempt_count,
            max_attempts=self._max_attempts,
            stage=stage,
            reasons=reasons,
            document=_without_context(document),
        )
        self._processed += 1
        if status == "exhausted":
            self._exhausted += 1
        else:
            self._retried += 1

    def write_report(self, report: dict[str, Any]) -> str:
        """정상 저장을 확인한 뒤 성공한 failure queue를 resolved로 표시한다."""

        self._sink.flush()
        for context in self._successful_contexts:
            self._sink.mark_reprocess_resolved(
                failure_id=context["failure_id"],
                reprocess_run_id=_run_id_from_sink(self._sink),
                attempt_count=_next_attempt(context.get("attempt_count", 1) - 1),
                success_document_id=context.get("document_id"),
            )
            self._resolved += 1

        enriched_report = dict(report)
        enriched_report["reprocess"] = {
            "processed": self._processed,
            "resolved": self._resolved,
            "retry": self._retried,
            "exhausted": self._exhausted,
            "max_attempts": self._max_attempts,
        }
        return self._sink.write_report(enriched_report)

    def close(self) -> None:
        """하위 sink의 버퍼와 연결을 정리한다."""

        self._sink.close()


def _pending_query(base_query: dict[str, Any], max_attempts: int) -> dict[str, Any]:
    """기존 조회 조건에 재처리 대기 조건을 AND로 결합한다."""

    pending = {
        "$or": [
            {"reprocess_status": {"$exists": False}},
            {"reprocess_status": {"$in": ["pending", "retry"]}},
        ],
        "$and": [
            {
                "$or": [
                    {"attempt_count": {"$exists": False}},
                    {"attempt_count": {"$lt": max_attempts}},
                ]
            }
        ],
    }
    if not base_query:
        return pending
    return {"$and": [dict(base_query), pending]}


def _get_context(document: Any) -> dict[str, Any] | None:
    if not isinstance(document, Mapping):
        return None
    context = document.get("_reprocess_context")
    return dict(context) if isinstance(context, Mapping) else None


def _without_context(document: Any) -> Any:
    if not isinstance(document, Mapping):
        return document
    clean = dict(document)
    clean.pop("_reprocess_context", None)
    return clean


def _next_attempt(value: Any) -> int:
    try:
        return max(int(value), 0) + 1
    except (TypeError, ValueError):
        return 1


def _run_id_from_sink(sink: MongoSink) -> str:
    """MongoSink의 공개되지 않은 run ID를 metadata에서 안전하게 읽는다."""

    value = getattr(sink, "_run_id", None)
    if not isinstance(value, str) or not value:
        raise RuntimeError("재처리 sink에 run_id가 없습니다.")
    return value
