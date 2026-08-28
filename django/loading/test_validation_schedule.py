"""Tests for the validation scheduler's one-minute crawl offset."""

from __future__ import annotations

from datetime import datetime
import unittest

from second_project.management.commands.validation_records import (
    SEOUL,
    _is_validation_minute,
    _next_run_at,
)
from second_project.management.commands.load_success_to_sqlite import (
    _is_load_minute,
    _next_run_at as _next_load_at,
)


class ValidationScheduleTests(unittest.TestCase):
    def test_validation_slots_are_one_minute_after_crawl_slots(self) -> None:
        for minute in (2, 5, 8, 59):
            self.assertTrue(_is_validation_minute(minute))
        for minute in (0, 1, 4, 58):
            self.assertFalse(_is_validation_minute(minute))

    def test_next_run_waits_until_the_offset_slot(self) -> None:
        now = datetime(2026, 8, 28, 11, 1, 30, tzinfo=SEOUL)
        self.assertEqual(
            _next_run_at(now),
            datetime(2026, 8, 28, 11, 2, tzinfo=SEOUL),
        )

        now = datetime(2026, 8, 28, 11, 2, 1, tzinfo=SEOUL)
        self.assertEqual(
            _next_run_at(now),
            datetime(2026, 8, 28, 11, 5, tzinfo=SEOUL),
        )

    def test_sqlite_load_slots_are_one_minute_after_validation(self) -> None:
        for minute in (0, 3, 6, 57):
            self.assertTrue(_is_load_minute(minute))
        for minute in (1, 2, 5, 58, 59):
            self.assertFalse(_is_load_minute(minute))

    def test_next_sqlite_load_uses_the_following_offset_slot(self) -> None:
        now = datetime(2026, 8, 28, 11, 2, 30, tzinfo=SEOUL)
        self.assertEqual(
            _next_load_at(now),
            datetime(2026, 8, 28, 11, 3, tzinfo=SEOUL),
        )

        now = datetime(2026, 8, 28, 11, 3, 1, tzinfo=SEOUL)
        self.assertEqual(
            _next_load_at(now),
            datetime(2026, 8, 28, 11, 6, tzinfo=SEOUL),
        )


if __name__ == "__main__":
    unittest.main()
