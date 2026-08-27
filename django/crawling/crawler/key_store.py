"""Store the daily crawler API key in a protected project .env file."""

from __future__ import annotations

import os
from pathlib import Path

from .storage import StateConsistencyError, StorageError


API_KEY_ENV_NAME = "CRAWLER_API_KEY"


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
