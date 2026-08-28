from __future__ import annotations

import logging
import os
import tempfile
import time
import unittest
from pathlib import Path

from mongo_pipeline.log_rotation import TimeAndSizeRotatingFileHandler
from mongo_pipeline.loggers import create_stage_loggers


class LogRotationTest(unittest.TestCase):
    def test_stage_loggers_use_the_common_rotating_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            standardize_logger, validation_logger = create_stage_loggers(
                Path(directory),
                logging.INFO,
            )
            handlers = [
                *standardize_logger.handlers,
                *validation_logger.handlers,
            ]
            unique_handlers = {id(handler): handler for handler in handlers}

            try:
                self.assertEqual(len(unique_handlers), 6)
                self.assertTrue(
                    all(
                        isinstance(handler, TimeAndSizeRotatingFileHandler)
                        for handler in unique_handlers.values()
                    )
                )
            finally:
                for logger in (standardize_logger, validation_logger):
                    for handler in logger.handlers[:]:
                        logger.removeHandler(handler)
                for handler in unique_handlers.values():
                    handler.close()

    def test_rotates_when_size_limit_is_reached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pipeline.jsonl"
            logger = logging.getLogger("test.log_rotation.size")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            handler = TimeAndSizeRotatingFileHandler(
                path,
                maxBytes=100,
                backupCount=2,
                interval_seconds=6 * 60 * 60,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)

            try:
                logger.info("a" * 80)
                logger.info("b" * 80)
            finally:
                logger.removeHandler(handler)
                handler.close()

            self.assertTrue(path.exists())
            self.assertTrue(Path(f"{path}.1").exists())

    def test_rotates_on_the_first_write_after_a_six_hour_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quality.jsonl"
            path.write_text("old\n", encoding="utf-8")
            old_timestamp = time.time() - (7 * 60 * 60)
            os.utime(path, (old_timestamp, old_timestamp))

            logger = logging.getLogger("test.log_rotation.time")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            handler = TimeAndSizeRotatingFileHandler(
                path,
                maxBytes=10 * 1024 * 1024,
                backupCount=2,
                interval_seconds=6 * 60 * 60,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)

            try:
                logger.info("new")
            finally:
                logger.removeHandler(handler)
                handler.close()

            self.assertEqual(Path(f"{path}.1").read_text(encoding="utf-8"), "old\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")


if __name__ == "__main__":
    unittest.main()
