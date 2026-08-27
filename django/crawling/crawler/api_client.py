"""HTTP client for the internal records API."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import requests

from .config import CrawlConfig


LOGGER = logging.getLogger(__name__)


class ApiError(RuntimeError):
    """Raised when the API cannot provide a valid response."""


class ApiKeyNotEffective(ApiError):
    """Raised when the announced key is not currently effective."""


def parse_api_datetime(value: Any, field_name: str) -> datetime:
    """Parse an ISO timestamp and require an explicit UTC offset."""

    if not isinstance(value, str):
        raise ApiError(f"{field_name} 값이 문자열이 아닙니다.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(f"{field_name} 값이 ISO 날짜 형식이 아닙니다.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApiError(f"{field_name} 값에 시간대가 없습니다.")
    return parsed


class ApiClient:
    """Use an API key in memory while making validated GET requests."""

    def __init__(
        self,
        config: CrawlConfig,
        *,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self._owns_session = session is None
        self._sleep = sleeper
        self._api_key: str | None = None
        self._key_persistor: Callable[[str], None] | None = None
        self.key_metadata: dict[str, Any] | None = None
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": config.user_agent,
            }
        )

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def configure_key_persistence(self, persistor: Callable[[str], None]) -> None:
        """Persist each newly issued key without exposing it to logs."""

        self._key_persistor = persistor

    def use_api_key(self, api_key: str) -> None:
        """Load a previously persisted key into process memory."""

        if not isinstance(api_key, str) or not api_key:
            raise ApiError("저장된 API 키가 비어 있습니다.")
        self._api_key = api_key

    def check_ready(self) -> dict[str, Any]:
        return self._request_json("/health/ready")

    def refresh_key(self) -> dict[str, Any]:
        body = self._request_json("/public/v1/key")
        required = {
            "service_date",
            "api_key",
            "effective_at",
            "expires_at",
            "server_time",
        }
        missing = sorted(required - body.keys())
        if missing:
            raise ApiError(f"API 키 응답에 필수 필드가 없습니다: {', '.join(missing)}")

        api_key = body.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise ApiError("API 키 응답의 api_key가 비어 있습니다.")

        effective_at = parse_api_datetime(body["effective_at"], "effective_at")
        expires_at = parse_api_datetime(body["expires_at"], "expires_at")
        server_time = parse_api_datetime(body["server_time"], "server_time")
        if not effective_at <= server_time < expires_at:
            raise ApiKeyNotEffective(
                "공지된 API 키가 현재 유효시간에 포함되지 않습니다. "
                f"server_time={server_time.isoformat()} "
                f"effective_at={effective_at.isoformat()} "
                f"expires_at={expires_at.isoformat()}"
            )

        self._api_key = api_key
        if self._key_persistor is not None:
            self._key_persistor(api_key)
        self.key_metadata = {
            "service_date": body["service_date"],
            "effective_at": body["effective_at"],
            "expires_at": body["expires_at"],
            "server_time": body["server_time"],
        }
        return self.key_metadata

    def fetch_meta(self) -> dict[str, Any]:
        self._require_key()
        return self._request_json("/api/v1/meta", authenticated=True)

    def fetch_records(self, cursor: str | None = None) -> dict[str, Any]:
        self._require_key()
        params: dict[str, str | int] = {"limit": self.config.page_limit}
        if cursor:
            params["cursor"] = cursor
        return self._request_json(
            "/api/v1/records",
            params=params,
            authenticated=True,
        )

    def _require_key(self) -> None:
        if not self._api_key:
            raise ApiError("인증 요청 전에 API 키를 발급받아야 합니다.")

    def _request_json(
        self,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}{path}"

        for attempt in range(self.config.max_retries + 1):
            headers: dict[str, str] = {}
            if authenticated:
                self._require_key()
                headers["X-API-Key"] = self._api_key or ""

            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.config.timeout,
                )
            except requests.RequestException as exc:
                if attempt >= self.config.max_retries:
                    raise ApiError(
                        f"API 연결에 실패했습니다. attempts={attempt + 1}"
                    ) from exc
                delay = 2**attempt
                LOGGER.warning(
                    "API 연결 오류가 발생했습니다. retry_in=%ss attempt=%s/%s",
                    delay,
                    attempt + 1,
                    self.config.max_retries,
                )
                self._sleep(delay)
                continue

            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt >= self.config.max_retries:
                    raise ApiError(
                        "API 재시도 횟수를 초과했습니다. "
                        f"status_code={response.status_code}"
                    )
                delay = self._retry_delay(response, attempt)
                LOGGER.warning(
                    "일시적인 API 오류입니다. status_code=%s "
                    "retry_in=%ss attempt=%s/%s",
                    response.status_code,
                    delay,
                    attempt + 1,
                    self.config.max_retries,
                )
                self._sleep(delay)
                continue

            if response.status_code != 200:
                raise ApiError(
                    f"API 요청이 거절되었습니다. status_code={response.status_code}"
                )

            content_type = response.headers.get("Content-Type", "")
            if "application/json" not in content_type.casefold():
                LOGGER.warning(
                    "응답 Content-Type이 application/json이 아닙니다. content_type=%s",
                    content_type or "<없음>",
                )
            try:
                decoded = response.content.decode("utf-8")
                body = json.loads(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApiError("API 응답을 UTF-8 JSON으로 해석하지 못했습니다.") from exc
            if not isinstance(body, dict):
                raise ApiError("API 응답의 최상위 값이 객체가 아닙니다.")
            return body

        raise ApiError("API 요청을 완료하지 못했습니다.")

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        return float(2**attempt)
