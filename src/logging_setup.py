"""
Logging configuration.

WHY A DEDICATED MODULE FOR SOMETHING SO SMALL?
----------------------------------------------
Research data collection is a long-running, unattended process. When a run of
several hours produces a dataset, the accompanying log file is the only
evidence of HOW that dataset came to be: which parameters were used, which
requests failed, which posts were skipped and why.

Using `print()` would send that evidence to a terminal window that closes.
Configuring logging properly gives the project:

  * a permanent, timestamped record on disk, one file per run;
  * simultaneous human-readable output on screen while the run is in progress;
  * a single switch (LOG_LEVEL in .env) to make the output more or less
    verbose without editing any code.

The log file is written next to the data, so a dataset and its provenance
record travel together to the college server.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(logs_dir: Path, level: str = "INFO") -> Path:
    """Configure logging for the whole application.

    Called exactly once, from the entry point, before any other work begins.
    Every other module then simply calls `logging.getLogger(__name__)` and
    inherits this configuration - they never configure logging themselves.

    Args:
        logs_dir: Directory in which to create the log file. Created if absent.
        level:    Minimum severity to record: DEBUG, INFO, WARNING or ERROR.

    Returns:
        The path of the log file that was created, so the caller can tell the
        user where to look.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)

    # One log file per run, named by start time. Keeping runs in separate files
    # (rather than appending to one growing file) means a pilot run and the
    # full collection run can never be confused with each other.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"collection_{timestamp}.log"

    # Translate the text level from .env into the logging module's constant.
    # An unrecognised value falls back to INFO rather than crashing, because a
    # typo in a logging setting should never abort a data collection run.
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # The format includes the module name so that, when something goes wrong,
    # the log line says WHICH part of the pipeline produced it.
    log_format = "%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(log_format, datefmt=date_format)

    # Handler 1: the permanent file record.
    # encoding="utf-8" is essential - captions and comments in this study
    # contain Arabic, Hebrew, Persian and emoji, none of which survive the
    # default Windows encoding (cp1252) without raising UnicodeEncodeError.
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # Handler 2: live progress on the terminal, so the operator can see that a
    # long run is still making progress.
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove any handlers already attached. Without this, running the pipeline
    # twice in the same Python process (as the test suite does) would attach a
    # second set of handlers and print every line twice.
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # The `requests` library logs every connection at DEBUG level through
    # urllib3. That is far too noisy to leave on, so it is pinned to WARNING
    # regardless of our own level - we keep OUR debug output without drowning
    # in the HTTP library's internals.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    return log_path
