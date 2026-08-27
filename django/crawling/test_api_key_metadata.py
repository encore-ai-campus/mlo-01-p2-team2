"""Tests for API key lifetime metadata and scheduler-facing crawl outcomes."""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from crawling.crawl_records import run_once
from crawling.crawler.api_client import ApiKeyNotEffective
from crawling.crawler.config import CrawlConfig
from crawling.crawler.ingest_logging import IngestJsonFormatter
from crawling.crawler.key_store import ApiKeyMetadataStore
from crawling.crawler.service import run_crawl, should_refresh_api_key
from crawling.crawler.storage import AlreadyRunningError


SEOUL = ZoneInfo("Asia/Seoul")


def make_key_metadata(
    *,
    service_date: str = "2026-08-26",
    expires_at: str = "2026-08-28T00:00:00+09:00",
    server_time: str = "2026-08-26T00:01:00+09:00",
) -> dict[str, str]:
    return {
        "service_date": service_date,
        "effective_at": f"{service_date}T00:01:00+09:00",
        "expires_at": expires_at,
        "server_time": server_time,
    }


class ApiKeyMetadataStoreTests(unittest.TestCase):
    def test_save_load_does_not_store_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "api_key_metadata.json"
            store = ApiKeyMetadataStore(path)
            store.save(
                make_key_metadata(),
                refreshed_at=datetime(2026, 8, 26, 0, 1, 2, tzinfo=SEOUL),
            )

            saved = json.loads(path.read_text(encoding="utf-8"))
            loaded = store.load()

        self.assertNotIn("api_key", saved)
        self.assertEqual(loaded["service_date"], "2026-08-26")
        self.assertEqual(
            loaded["last_refreshed_at"],
            "2026-08-26T00:01:02.000000+09:00",
        )

    def test_refresh_event_type_is_written_to_ingest_log_schema(self) -> None:
        record = logging.LogRecord(
            "crawler",
            logging.INFO,
            __file__,
            1,
            "API 키와 메타데이터를 저장했습니다.",
            (),
            None,
        )
        record.event_type = "api_key_refresh"
        event = json.loads(IngestJsonFormatter("run-1").format(record))

        self.assertEqual(event["event_type"], "api_key_refresh")


class ApiKeyRefreshDecisionTests(unittest.TestCase):
    def test_current_metadata_does_not_refresh_before_expiry(self) -> None:
        metadata = make_key_metadata()
        now = datetime(2026, 8, 26, 12, 0, tzinfo=SEOUL)
        metadata["last_refreshed_at"] = "2026-08-26T00:01:00+09:00"

        self.assertFalse(should_refresh_api_key(now, metadata))

    def test_missed_daily_window_refreshes_on_next_run(self) -> None:
        metadata = make_key_metadata()
        metadata["last_refreshed_at"] = "2026-08-26T00:01:00+09:00"
        now = datetime(2026, 8, 27, 0, 4, tzinfo=SEOUL)

        self.assertTrue(should_refresh_api_key(now, metadata))


class CrawlExitCodeTests(unittest.TestCase):
    def _run_with_error(self, error: Exception) -> int:
        with tempfile.TemporaryDirectory() as directory:
            config = CrawlConfig(
                base_url="http://example.test",
                data_dir=Path(directory) / "raw_data",
                env_path=Path(directory) / ".env",
            )
            with (
                patch("crawling.crawl_records.default_config", return_value=config),
                patch("crawling.crawl_records.configure_ingest_logging"),
                patch("crawling.crawl_records.run_crawl", side_effect=error),
            ):
                return run_once()

    def test_lock_skip_is_a_failed_cycle(self) -> None:
        self.assertEqual(
            self._run_with_error(AlreadyRunningError("locked")),
            1,
        )

    def test_api_key_not_effective_is_a_failed_cycle(self) -> None:
        self.assertEqual(
            self._run_with_error(ApiKeyNotEffective("not effective")),
            1,
        )


class FakeApiClient:
    def __init__(self, key_metadata: dict[str, str]) -> None:
        self.key_metadata_response = key_metadata
        self.refresh_count = 0
        self.used_key: str | None = None
        self._persist_key = None

    def configure_key_persistence(self, persistor) -> None:
        self._persist_key = persistor

    def check_ready(self) -> dict:
        return {}

    def refresh_key(self) -> dict[str, str]:
        self.refresh_count += 1
        self._persist_key("test-api-key")
        return self.key_metadata_response

    def use_api_key(self, api_key: str) -> None:
        self.used_key = api_key

    def fetch_meta(self) -> dict:
        return {
            "dataset_id": "dataset-1",
            "name": "test",
            "source_filename": "records.csv",
            "source_sha256": "a" * 64,
            "columns": ["value"],
            "total_rows": 0,
            "released_rows": 0,
            "serving_start_at": "2026-08-01T00:00:00+09:00",
            "serving_end_at": "2026-12-31T23:59:59+09:00",
            "refresh_minutes": 3,
            "next_refresh_at": "2026-08-27T00:07:00+09:00",
            "server_time": self.key_metadata_response["server_time"],
            "timezone": "Asia/Seoul",
        }

    def fetch_records(self, cursor: str | None = None) -> dict:
        return {
            "dataset_id": "dataset-1",
            "items": [],
            "count": 0,
            "has_more": False,
            "next_cursor": None,
            "checkpoint": "checkpoint-1",
            "released_rows": 0,
            "total_rows": 0,
            "next_refresh_at": "2026-08-27T00:07:00+09:00",
            "server_time": self.key_metadata_response["server_time"],
        }

    def close(self) -> None:
        return None


class CrawlMetadataIntegrationTests(unittest.TestCase):
    def test_missed_refresh_is_persisted_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = CrawlConfig(
                base_url="http://example.test",
                data_dir=root / "raw_data",
                env_path=root / ".env",
            )
            first_client = FakeApiClient(
                make_key_metadata(
                    service_date="2026-08-26",
                    server_time="2026-08-26T00:01:00+09:00",
                )
            )
            run_crawl(
                config,
                api_client=first_client,
                now_provider=lambda: datetime(2026, 8, 26, 0, 1, tzinfo=SEOUL),
            )
            second_client = FakeApiClient(
                make_key_metadata(
                    service_date="2026-08-27",
                    server_time="2026-08-27T00:04:00+09:00",
                )
            )
            run_crawl(
                config,
                api_client=second_client,
                now_provider=lambda: datetime(2026, 8, 27, 0, 4, tzinfo=SEOUL),
            )

            metadata = ApiKeyMetadataStore(config.api_key_metadata_path).load()

        self.assertEqual(first_client.refresh_count, 1)
        self.assertEqual(second_client.refresh_count, 1)
        self.assertEqual(metadata["service_date"], "2026-08-27")


if __name__ == "__main__":
    unittest.main()
