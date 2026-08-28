"""Tests for the validation scheduler's one-minute crawl offset."""

from __future__ import annotations

from datetime import datetime
import unittest

from second_project.management.commands.validation_records import (
    SEOUL,
    _is_validation_minute,
    _next_run_at,
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


if __name__ == "__main__":
    unittest.main()
