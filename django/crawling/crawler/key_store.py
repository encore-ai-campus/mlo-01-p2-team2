"""Store crawler API credentials and non-secret key metadata safely."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .storage import StateConsistencyError, StorageError


API_KEY_ENV_NAME = "CRAWLER_API_KEY"
API_KEY_METADATA_SCHEMA_VERSION = 1
API_KEY_METADATA_FIELDS = (
    "service_date",
    "effective_at",
    "expires_at",
    "server_time",
    "last_refreshed_at",
)
SEOUL = ZoneInfo("Asia/Seoul")


class EnvApiKeyStore:
    """Read and atomically update only the crawler API key entry."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise StateConsistencyError(".env 파일을 UTF-8로 읽지 못했습니다.") from exc

        values: list[str] = []
        for line in lines:
            parsed = self._parse_key_line(line)
            if parsed is not None:
                values.append(parsed)
        if len(values) > 1:
            raise StateConsistencyError(f".env에 {API_KEY_ENV_NAME}가 중복되어 있습니다.")
        if not values:
            return None
        if not values[0]:
            raise StateConsistencyError(f".env의 {API_KEY_ENV_NAME}가 비어 있습니다.")
        return values[0]

    def save(self, api_key: str) -> None:
        if not api_key or any(character in api_key for character in "\r\n"):
            raise StorageError("저장할 API 키가 올바르지 않습니다.")

        existing_lines: list[str] = []
        if self.path.exists():
            try:
                existing_lines = self.path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError) as exc:
                raise StorageError("기존 .env 파일을 읽지 못했습니다.") from exc

        replacement = f"{API_KEY_ENV_NAME}={api_key}"
        updated_lines: list[str] = []
        replaced = False
        for line in existing_lines:
            if self._parse_key_line(line) is not None:
                if replaced:
                    raise StateConsistencyError(
                        f".env에 {API_KEY_ENV_NAME}가 중복되어 있습니다."
                    )
                updated_lines.append(replacement)
                replaced = True
            else:
                updated_lines.append(line)
        if not replaced:
            updated_lines.append(replacement)

        content = "\n".join(updated_lines) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except OSError as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageError(".env 파일에 API 키를 안전하게 저장하지 못했습니다.") from exc

    @staticmethod
    def _parse_key_line(line: str) -> str | None:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        name, separator, value = stripped.partition("=")
        if not separator or name.strip() != API_KEY_ENV_NAME:
            return None
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value


class ApiKeyMetadataStore:
    """Read and atomically update non-secret API key lifetime metadata."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, str] | None:
        """Return validated metadata, or ``None`` when it has not been created."""

        if not self.path.exists():
            return None
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateConsistencyError(
                "api_key_metadata.json을 읽지 못했습니다."
            ) from exc
        if not isinstance(body, dict):
            raise StateConsistencyError("api_key_metadata.json의 최상위 값이 객체가 아닙니다.")
        if body.get("schema_version") != API_KEY_METADATA_SCHEMA_VERSION:
            raise StateConsistencyError(
                "api_key_metadata.json의 schema_version이 지원되지 않습니다."
            )

        missing = [field for field in API_KEY_METADATA_FIELDS if field not in body]
        if missing:
            raise StateConsistencyError(
                "api_key_metadata.json에 필수 필드가 없습니다: "
                + ", ".join(missing)
            )
        metadata = {field: body[field] for field in API_KEY_METADATA_FIELDS}
        if any(not isinstance(value, str) or not value for value in metadata.values()):
            raise StateConsistencyError(
                "api_key_metadata.json의 메타데이터 값이 올바르지 않습니다."
            )
        try:
            date.fromisoformat(metadata["service_date"])
            effective_at = self._parse_datetime(metadata["effective_at"], "effective_at")
            expires_at = self._parse_datetime(metadata["expires_at"], "expires_at")
            self._parse_datetime(metadata["server_time"], "server_time")
            self._parse_datetime(
                metadata["last_refreshed_at"],
                "last_refreshed_at",
            )
        except ValueError as exc:
            raise StateConsistencyError(
                "api_key_metadata.json의 날짜 형식이 올바르지 않습니다."
            ) from exc
        if effective_at >= expires_at:
            raise StateConsistencyError(
                "api_key_metadata.json의 effective_at이 expires_at 이후입니다."
            )
        return metadata

    def save(self, metadata: dict[str, Any], *, refreshed_at: datetime) -> None:
        """Persist API lifetime metadata without ever writing the API key itself."""

        missing = [field for field in API_KEY_METADATA_FIELDS[:-1] if field not in metadata]
        if missing:
            raise StorageError(
                "API 키 메타데이터에 필수 필드가 없습니다: " + ", ".join(missing)
            )
        values = {field: metadata[field] for field in API_KEY_METADATA_FIELDS[:-1]}
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise StorageError("API 키 메타데이터 값이 올바르지 않습니다.")
        try:
            date.fromisoformat(values["service_date"])
            effective_at = self._parse_datetime(values["effective_at"], "effective_at")
            expires_at = self._parse_datetime(values["expires_at"], "expires_at")
            self._parse_datetime(values["server_time"], "server_time")
        except ValueError as exc:
            raise StorageError("API 키 메타데이터의 날짜 형식이 올바르지 않습니다.") from exc
        if effective_at >= expires_at:
            raise StorageError("API 키 메타데이터의 유효 구간이 올바르지 않습니다.")

        payload = {
            "schema_version": API_KEY_METADATA_SCHEMA_VERSION,
            **values,
            "last_refreshed_at": self._format_datetime(refreshed_at),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            if os.name == "posix":
                os.chmod(self.path.parent, 0o700)
                os.chmod(self.path, 0o600)
        except OSError as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageError(
                "api_key_metadata.json을 안전하게 저장하지 못했습니다."
            ) from exc

    @staticmethod
    def _parse_datetime(value: str, field_name: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{field_name}에 시간대가 없습니다.")
        return parsed

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=SEOUL)
        return value.astimezone(SEOUL).isoformat(timespec="microseconds")
