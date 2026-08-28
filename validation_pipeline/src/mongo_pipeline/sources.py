from __future__ import annotations

import csv
import json
import os
import sys
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol

from .config import SourceConfig
from .yaml_support import load_yaml_file


Document = Mapping[str, Any]


class DocumentSource(Protocol):
    """파이프라인이 데이터 저장소와 무관하게 문서를 읽게 하는 규약이다."""

    @property
    def description(self) -> dict[str, Any]:
        """리포트에 기록할 데이터 소스 정보를 반환한다."""
        ...

    def read(self) -> Iterator[Document]:
        """원본 문서를 한 건씩 반환한다."""
        ...

    def close(self) -> None:
        """데이터 소스가 사용한 연결과 자원을 정리한다."""
        ...


class IterableSource:
    """테스트와 데모에서 일반 반복 객체를 데이터 소스로 사용한다."""

    def __init__(self, documents: Iterable[Document], name: str = "iterable") -> None:
        """반복 가능한 문서와 소스 이름을 저장한다."""

        self._documents = documents
        self._name = name

    @property
    def description(self) -> dict[str, Any]:
        """반복 객체의 소스 이름을 반환한다."""

        return {"type": self._name}

    def read(self) -> Iterator[Document]:
        """전달받은 문서를 순서대로 반환한다."""

        yield from self._documents

    def close(self) -> None:
        """외부 자원이 없으므로 별도 작업 없이 종료한다."""

        return None


class YamlFileSource:
    """YAML 파일의 단일 문서 또는 문서 배열을 데이터 소스로 사용한다."""

    def __init__(self, path: str | Path) -> None:
        """입력 YAML 경로를 저장한다."""

        self._path = Path(path)

    @property
    def description(self) -> dict[str, Any]:
        """리포트에 입력 YAML 절대 경로를 반환한다."""

        return {
            "type": "yaml_file",
            "path": str(self._path.resolve()),
        }

    def read(self) -> Iterator[Document]:
        """YAML 최상위 문서 또는 `documents` 배열을 한 건씩 반환한다."""

        raw = load_yaml_file(self._path)
        if isinstance(raw, Mapping) and "documents" in raw:
            documents = raw["documents"]
        elif isinstance(raw, Mapping):
            documents = [raw]
        elif isinstance(raw, list):
            documents = raw
        else:
            raise ValueError(
                "입력 YAML은 object, object array, 또는 `documents` array여야 합니다."
            )

        if not isinstance(documents, list):
            raise ValueError("입력 YAML의 `documents`는 array여야 합니다.")
        for index, document in enumerate(documents):
            if not isinstance(document, Mapping):
                raise ValueError(f"입력 YAML documents[{index}]는 object여야 합니다.")
            yield document

    def close(self) -> None:
        """외부 연결이 없으므로 별도 작업 없이 종료한다."""

        return None


class JsonlSource:
    """한 줄에 하나의 JSON object가 있는 파일을 데이터 소스로 사용한다.

    JSON 문법 오류나 object가 아닌 줄은 선택적으로 파이프라인에 오류 문서로
    전달한다. 그러면 해당 줄도 다른 레코드와 함께 실패 저장소로 격리할 수 있다.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        encoding: str = "utf-8",
        continue_on_parse_error: bool = True,
        max_line_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        """JSONL 경로와 줄 단위 파싱 정책을 저장한다."""

        self._path = Path(path)
        self._encoding = encoding
        self._continue_on_parse_error = continue_on_parse_error
        self._max_line_bytes = max_line_bytes

    @property
    def description(self) -> dict[str, Any]:
        """리포트에 입력 파일과 파싱 오류 처리 정책을 반환한다."""

        return {
            "type": "jsonl_file",
            "path": str(self._path.resolve()),
            "encoding": self._encoding,
            "parse_error_policy": (
                "quarantine" if self._continue_on_parse_error else "fail_run"
            ),
        }

    def read(self) -> Iterator[Document]:
        """JSONL을 한 줄씩 읽어 object를 반환한다."""

        try:
            file = self._path.open("r", encoding=self._encoding)
        except OSError as error:
            raise RuntimeError(f"JSONL 파일을 읽을 수 없습니다: {self._path}: {error}") from error

        with file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue

                line_size = len(line.encode(self._encoding))
                if line_size > self._max_line_bytes:
                    message = (
                        f"JSONL {line_number}번째 줄이 허용 크기 "
                        f"{self._max_line_bytes}바이트를 초과했습니다."
                    )
                    if not self._continue_on_parse_error:
                        raise ValueError(message)
                    yield self._error_document(
                        line_number=line_number,
                        error_type="line_too_large",
                        message=message,
                        raw_line=line.rstrip("\r\n"),
                    )
                    continue

                try:
                    document = json.loads(line)
                except json.JSONDecodeError as error:
                    message = f"JSON 문법 오류: {error.msg} (column={error.colno})"
                    if not self._continue_on_parse_error:
                        raise ValueError(f"JSONL {line_number}번째 줄: {message}") from error
                    yield self._error_document(
                        line_number=line_number,
                        error_type="json_decode_error",
                        message=message,
                        raw_line=line.rstrip("\r\n"),
                    )
                    continue

                if not isinstance(document, Mapping):
                    message = (
                        f"JSON 최상위 값은 object여야 합니다: "
                        f"actual={type(document).__name__}"
                    )
                    if not self._continue_on_parse_error:
                        raise ValueError(f"JSONL {line_number}번째 줄: {message}")
                    yield self._error_document(
                        line_number=line_number,
                        error_type="not_an_object",
                        message=message,
                        raw_line=line.rstrip("\r\n"),
                    )
                    continue

                yield document

    def close(self) -> None:
        """파일 핸들은 줄 단위 읽기 범위 안에서 닫히므로 별도 작업이 없다."""

        return None

    @staticmethod
    def _error_document(
        *,
        line_number: int,
        error_type: str,
        message: str,
        raw_line: str,
    ) -> dict[str, Any]:
        """파싱 실패 줄을 후속 실패 저장소가 처리할 수 있는 object로 감싼다."""

        return {
            "_source_error": {
                "source_line_no": line_number,
                "type": error_type,
                "message": message,
                "raw_line": raw_line,
            }
        }


class CsvSource:
    """헤더가 있는 CSV 파일을 문서 스트림으로 사용한다.

    CSV 값은 문자열로 읽고 이후 표준화기에서 타입·코드·날짜를 변환한다.
    `source.record_id`처럼 점이 들어간 헤더는 중첩 object로 펼쳐서
    Bronze의 flattened CSV와 일반 업무 CSV를 모두 같은 규칙으로 처리한다.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        encoding: str = "utf-8-sig",
        delimiter: str = ",",
        quotechar: str = '"',
        skipinitialspace: bool = False,
        continue_on_parse_error: bool = True,
        limit: int | None = None,
    ) -> None:
        """입력 경로와 CSV 파싱·오류 처리 정책을 저장한다."""

        if len(delimiter) != 1:
            raise ValueError("CSV delimiter는 한 글자여야 합니다.")
        if len(quotechar) != 1:
            raise ValueError("CSV quotechar는 한 글자여야 합니다.")
        if limit is not None and limit <= 0:
            raise ValueError("CSV limit은 null 또는 1 이상이어야 합니다.")

        self._path = Path(path)
        self._encoding = encoding
        self._delimiter = delimiter
        self._quotechar = quotechar
        self._skipinitialspace = skipinitialspace
        self._continue_on_parse_error = continue_on_parse_error
        self._limit = limit

    @property
    def description(self) -> dict[str, Any]:
        """리포트에 입력 CSV와 파싱 정책을 반환한다."""

        return {
            "type": "csv_file",
            "path": str(self._path.resolve()),
            "encoding": self._encoding,
            "delimiter": self._delimiter,
            "quotechar": self._quotechar,
            "header": True,
            "dotted_headers": "nested_paths",
            "parse_error_policy": (
                "quarantine" if self._continue_on_parse_error else "fail_run"
            ),
        }

    def read(self) -> Iterator[Document]:
        """CSV의 각 행을 object로 변환해 한 건씩 반환한다."""

        try:
            file = self._path.open(
                "r",
                encoding=self._encoding,
                newline="",
            )
        except OSError as error:
            raise RuntimeError(f"CSV 파일을 읽을 수 없습니다: {self._path}: {error}") from error

        with file:
            reader = csv.reader(
                file,
                delimiter=self._delimiter,
                quotechar=self._quotechar,
                skipinitialspace=self._skipinitialspace,
                strict=True,
            )
            try:
                headers = next(reader, None)
            except csv.Error as error:
                yield self._parse_error(
                    line_number=max(reader.line_num, 1),
                    error_type="csv_parse_error",
                    message=f"CSV 헤더를 읽을 수 없습니다: {error}",
                    raw_line="",
                )
                return

            header_error = self._validate_headers(headers)
            if header_error is not None:
                yield self._parse_error(
                    line_number=1,
                    error_type="invalid_header",
                    message=header_error,
                    raw_line=self._delimiter.join(headers or []),
                )
                return

            assert headers is not None
            rows_seen = 0
            while True:
                try:
                    row = next(reader)
                except StopIteration:
                    break
                except csv.Error as error:
                    rows_seen += 1
                    line_number = max(reader.line_num, 1)
                    yield self._parse_error(
                        line_number=line_number,
                        error_type="csv_parse_error",
                        message=f"CSV 문법 오류: {error}",
                        raw_line="",
                    )
                    if self._limit is not None and rows_seen >= self._limit:
                        return
                    continue

                line_number = max(reader.line_num, 1)
                if not row or all(not value.strip() for value in row):
                    continue
                rows_seen += 1

                if len(row) != len(headers):
                    yield self._parse_error(
                        line_number=line_number,
                        error_type="row_shape_error",
                        message=(
                            f"CSV 열 수가 헤더와 다릅니다: "
                            f"expected={len(headers)}, actual={len(row)}"
                        ),
                        raw_line=self._delimiter.join(row),
                    )
                else:
                    try:
                        document = self._row_to_document(headers, row)
                        document = self._attach_source_metadata(document, line_number)
                    except ValueError as error:
                        yield self._parse_error(
                            line_number=line_number,
                            error_type="header_path_error",
                            message=str(error),
                            raw_line=self._delimiter.join(row),
                        )
                    else:
                        yield document

                if self._limit is not None and rows_seen >= self._limit:
                    return

    def close(self) -> None:
        """파일 핸들은 `read`의 스트림 범위 안에서 닫히므로 별도 작업이 없다."""

        return None

    def _parse_error(
        self,
        *,
        line_number: int,
        error_type: str,
        message: str,
        raw_line: str,
    ) -> dict[str, Any]:
        """CSV 파싱 오류를 quarantine 문서로 만들거나 실행 오류로 전환한다."""

        if not self._continue_on_parse_error:
            raise ValueError(f"CSV {line_number}번째 줄: {message}")
        return self._error_document(
            line_number=line_number,
            error_type=error_type,
            message=message,
            raw_line=raw_line,
        )

    @staticmethod
    def _validate_headers(headers: list[str] | None) -> str | None:
        """헤더 존재·중복·중첩 경로 충돌을 확인한다."""

        if not headers:
            return "CSV 파일에는 하나 이상의 헤더가 필요합니다."
        if any(not header.strip() for header in headers):
            return "CSV 헤더에는 빈 이름이 있을 수 없습니다."
        if len(set(headers)) != len(headers):
            return "CSV 헤더에는 중복 이름이 있을 수 없습니다."

        header_set = set(headers)
        for header in headers:
            parts = header.split(".")
            if any(not part for part in parts):
                return f"CSV 헤더 경로가 올바르지 않습니다: {header}"
            for index in range(1, len(parts)):
                if ".".join(parts[:index]) in header_set:
                    return f"CSV 헤더 경로가 충돌합니다: {header}"
        return None

    @staticmethod
    def _row_to_document(headers: list[str], row: list[str]) -> dict[str, Any]:
        """CSV 평면 행을 점 경로를 지원하는 중첩 문서로 변환한다."""

        document: dict[str, Any] = {}
        for header, value in zip(headers, row):
            cursor = document
            parts = header.split(".")
            for part in parts[:-1]:
                nested = cursor.get(part)
                if nested is None:
                    nested = {}
                    cursor[part] = nested
                if not isinstance(nested, dict):
                    raise ValueError(f"CSV 헤더 경로가 충돌합니다: {header}")
                cursor = nested
            cursor[parts[-1]] = value
        return document

    def _attach_source_metadata(
        self,
        document: dict[str, Any],
        line_number: int,
    ) -> dict[str, Any]:
        """계보 키가 없으면 파일명과 행 번호로 안정적인 입력 참조를 만든다."""

        source_value = document.get("source")
        if source_value is None:
            source: dict[str, Any] = {}
        elif isinstance(source_value, Mapping):
            source = dict(source_value)
        else:
            raise ValueError("CSV의 `source` 헤더는 `source.*` 경로와 함께 사용할 수 없습니다.")

        source_record_id = _first_usable_value(
            source.get("record_id"),
            document.get("source_record_id"),
            document.get("record_id"),
            document.get("_id"),
        )
        if source_record_id is None:
            source_record_id = f"{self._path.name}:row:{line_number}"
        source["record_id"] = str(source_record_id)
        source.setdefault("source_row_no", line_number)
        document["source"] = source
        return document

    @staticmethod
    def _error_document(
        *,
        line_number: int,
        error_type: str,
        message: str,
        raw_line: str,
    ) -> dict[str, Any]:
        """파싱 실패 행을 후속 실패 저장소가 처리할 수 있는 object로 감싼다."""

        return {
            "_source_error": {
                "source_line_no": line_number,
                "type": error_type,
                "message": message,
                "raw_line": raw_line,
            }
        }


def _first_usable_value(*values: Any) -> Any | None:
    """빈 문자열·null을 제외한 첫 번째 원본 식별자를 반환한다."""

    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


class DjangoMongoSource:
    """Django의 MongoDB database alias를 재사용해 원본을 읽는다."""

    def __init__(
        self,
        config: SourceConfig,
        *,
        database_alias: str | None = None,
        settings_module: str | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        """Django 설정과 MongoDB 조회 설정을 저장한다."""

        self._config = config
        self._database_alias = database_alias or config.database_alias
        self._settings_module = settings_module or config.settings_module
        configured_root = project_root if project_root is not None else config.project_root
        self._project_root = Path(configured_root) if configured_root is not None else None
        self._client: Any | None = None
        self._django_connection: Any | None = None

    @property
    def description(self) -> dict[str, Any]:
        """DB, 컬렉션, alias, 조회 방식을 리포트용으로 반환한다."""

        return {
            "type": "django_mongodb",
            "database": self._config.database or "<django_alias_database>",
            "collection": self._config.collection,
            "method": "aggregate" if self._config.aggregation is not None else "find",
            "database_alias": self._database_alias,
            "settings_module": self._settings_module,
        }

    def read(self) -> Iterator[Document]:
        """Django가 관리하는 MongoClient로 문서를 스트리밍한다."""

        client = self._ensure_client()
        database_name = self._config.database
        if not database_name:
            assert self._django_connection is not None
            database_name = str(self._django_connection.settings_dict["NAME"])
        collection = client[database_name][self._config.collection]

        if self._config.aggregation is not None:
            stages = list(self._config.aggregation)
            if self._config.limit is not None:
                stages.append({"$limit": self._config.limit})
            cursor = collection.aggregate(stages, batchSize=self._config.batch_size)
        else:
            cursor = collection.find(
                self._config.query,
                self._config.projection,
                batch_size=self._config.batch_size,
            )
            if self._config.limit is not None:
                cursor = cursor.limit(self._config.limit)

        yield from cursor

    def close(self) -> None:
        """Django가 수명 주기를 관리하므로 MongoClient를 닫지 않는다."""

        self._client = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client

        if self._project_root is not None:
            project_root = str(self._project_root.resolve())
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", self._settings_module)

        try:
            import django
            from django.db import connections
        except ImportError as error:
            raise RuntimeError(
                "Django MongoDB source에는 Django와 django-mongodb-backend가 필요합니다."
            ) from error

        try:
            django.setup()
            connection = connections[self._database_alias]
            connection.ensure_connection()
        except Exception as error:
            raise RuntimeError(
                f"Django database alias `{self._database_alias}`에 연결할 수 없습니다."
            ) from error

        client = getattr(connection, "connection", None)
        if client is None:
            raise RuntimeError(
                f"Django database alias `{self._database_alias}`가 MongoClient를 열지 못했습니다."
            )
        client.admin.command("ping")
        self._django_connection = connection
        self._client = client
        return client


class MongoSource:
    """MongoDB에서 문서를 스트리밍하는 데이터 소스다.

    실제 조회 시점에만 pymongo를 불러와 코어 테스트가 MongoDB에 의존하지 않게 한다.
    """

    def __init__(self, config: SourceConfig) -> None:
        """MongoDB 조회 설정을 저장하고 연결 전 상태를 준비한다."""

        self._config = config
        self._client: Any | None = None

    @property
    def description(self) -> dict[str, Any]:
        """DB, 컬렉션, 조회 방식을 리포트용으로 반환한다."""

        return {
            "type": "mongodb",
            "database": self._config.database,
            "collection": self._config.collection,
            "method": "aggregate" if self._config.aggregation is not None else "find",
        }

    def read(self) -> Iterator[Document]:
        """설정에 따라 find 또는 aggregate로 MongoDB 문서를 반환한다."""

        try:
            from pymongo import MongoClient
        except ImportError as error:
            raise RuntimeError(
                "pymongo가 필요합니다. `pip install -e .`로 설치해 주세요."
            ) from error

        uri = os.getenv(self._config.uri_env)
        if not uri:
            raise RuntimeError(
                f"MongoDB URI 환경 변수 `{self._config.uri_env}`가 설정되지 않았습니다."
            )

        self._client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
        self._client.admin.command("ping")

        collection = self._client[self._config.database][self._config.collection]
        if self._config.aggregation is not None:
            stages = list(self._config.aggregation)
            if self._config.limit is not None:
                stages.append({"$limit": self._config.limit})
            cursor = collection.aggregate(stages, batchSize=self._config.batch_size)
        else:
            cursor = collection.find(
                self._config.query,
                self._config.projection,
                batch_size=self._config.batch_size,
            )
            if self._config.limit is not None:
                cursor = cursor.limit(self._config.limit)

        yield from cursor

    def close(self) -> None:
        """열려 있는 MongoDB 클라이언트 연결을 닫는다."""

        if self._client is not None:
            self._client.close()
            self._client = None
