"""JSONL, state, and cross-platform process-lock storage."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


class StorageError(RuntimeError):
    """Raised when crawler output cannot be read or written safely."""


class StateConsistencyError(StorageError):
    """Raised when records and state files cannot be safely paired."""


class AlreadyRunningError(StorageError):
    """Raised when another crawler process owns the run lock."""


@dataclass(frozen=True)
class CrawlState:
    schema_version: int
    dataset_id: str
    source_sha256: str
    checkpoint: str | None
    last_record_id: int | None
    released_rows: int
    latest_scheduled_release_at: str | None
    last_server_time: str
    updated_at: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CrawlState":
        required = {field_name for field_name in cls.__dataclass_fields__}
        missing = sorted(required - value.keys())
        if missing:
            raise StateConsistencyError(
                f"crawl_state.json에 필수 필드가 없습니다: {', '.join(missing)}"
            )
        try:
            state = cls(**{name: value[name] for name in required})
        except TypeError as exc:
            raise StateConsistencyError("crawl_state.json 구조가 올바르지 않습니다.") from exc
        if state.schema_version != 1:
            raise StateConsistencyError(
                f"지원하지 않는 상태 파일 버전입니다: {state.schema_version}"
            )
        if not state.dataset_id or not state.source_sha256:
            raise StateConsistencyError("crawl_state.json의 데이터셋 식별자가 비어 있습니다.")
        if type(state.released_rows) is not int or state.released_rows < 0:
            raise StateConsistencyError("crawl_state.json의 released_rows가 올바르지 않습니다.")
        if state.checkpoint is None:
            if state.released_rows != 0:
                raise StateConsistencyError(
                    "checkpoint가 없는 초기 상태의 released_rows는 0이어야 합니다."
                )
        elif not isinstance(state.checkpoint, str) or not state.checkpoint:
            raise StateConsistencyError("crawl_state.json의 checkpoint가 올바르지 않습니다.")
        if state.last_record_id is not None and type(state.last_record_id) is not int:
            raise StateConsistencyError("crawl_state.json의 last_record_id가 올바르지 않습니다.")
        return state


class JsonlStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def ensure_file(self) -> None:
        """Create an empty JSONL file before the first page is appended."""

        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("x", encoding="utf-8") as handle:
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            return
        except OSError as exc:
            raise StorageError(f"records.jsonl을 생성하지 못했습니다: {self.path}") from exc

    def scan_record_keys(self) -> set[tuple[str, int]]:
        """Validate existing JSONL and return its unique record keys."""

        keys: set[tuple[str, int]] = set()
        if not self.path.exists():
            return keys
        try:
            with self.path.open("r", encoding="utf-8", newline="") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        raise StateConsistencyError(
                            f"records.jsonl의 {line_number}번째 줄이 비어 있습니다."
                        )
                    try:
                        document = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise StateConsistencyError(
                            f"records.jsonl의 {line_number}번째 줄이 올바른 JSON이 아닙니다."
                        ) from exc
                    if not isinstance(document, dict):
                        raise StateConsistencyError(
                            f"records.jsonl의 {line_number}번째 줄이 JSON 객체가 아닙니다."
                        )
                    dataset_id = document.get("dataset_id")
                    record_id = document.get("record_id")
                    if not isinstance(dataset_id, str) or type(record_id) is not int:
                        raise StateConsistencyError(
                            f"records.jsonl의 {line_number}번째 줄에 식별자가 없습니다."
                        )
                    key = dataset_id, record_id
                    if key in keys:
                        raise StateConsistencyError(
                            f"records.jsonl 안에 중복 record_id가 있습니다: {record_id}"
                        )
                    keys.add(key)
        except UnicodeDecodeError as exc:
            raise StateConsistencyError("records.jsonl을 UTF-8로 읽지 못했습니다.") from exc
        except OSError as exc:
            raise StorageError(f"records.jsonl을 읽지 못했습니다: {self.path}") from exc
        return keys

    def append_documents(self, documents: Iterable[dict[str, Any]]) -> int:
        """Append complete UTF-8 JSON documents and flush them to disk."""

        serialized = [
            json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
            for document in documents
        ]
        if not serialized:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.writelines(serialized)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise StorageError(f"records.jsonl에 데이터를 기록하지 못했습니다: {self.path}") from exc
        return len(serialized)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> CrawlState | None:
        if not self.path.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                body = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConsistencyError(f"crawl_state.json을 읽지 못했습니다: {self.path}") from exc
        if not isinstance(body, dict):
            raise StateConsistencyError("crawl_state.json의 최상위 값이 객체가 아닙니다.")
        return CrawlState.from_dict(body)

    def save(self, state: CrawlState) -> None:
        """Atomically replace state only after its temporary file is durable."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(asdict(state), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except OSError as exc:
            raise StorageError(f"crawl_state.json을 안전하게 저장하지 못했습니다: {self.path}") from exc


class RunLock:
    """Use an OS-level non-blocking lock that is released on process exit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            self._handle = os.fdopen(descriptor, "r+", encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"잠금 파일을 열지 못했습니다: {self.path}") from exc
        try:
            self._lock()
            self._handle.seek(0)
            self._handle.write(f"pid={os.getpid()}\n")
            self._handle.truncate()
            self._handle.flush()
        except Exception:
            self._handle.close()
            self._handle = None
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._handle is None:
            return
        try:
            self._unlock()
        finally:
            self._handle.close()
            self._handle = None

    def _lock(self) -> None:
        if os.name == "nt":
            import msvcrt

            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write("0")
                self._handle.flush()
            self._handle.seek(0)
            try:
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise AlreadyRunningError("다른 크롤러 실행이 진행 중입니다.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise AlreadyRunningError("다른 크롤러 실행이 진행 중입니다.") from exc

    def _unlock(self) -> None:
        if os.name == "nt":
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
