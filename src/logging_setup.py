"""
Logging configuration.

Configures one timestamped log file per run plus live console output, so a
long unattended collection run leaves a permanent, human-readable record of
which parameters were used, which requests failed, and which posts were
skipped and why. The log file is written next to the data, so a dataset and
its provenance record travel together.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(logs_dir: Path, level: str = "INFO") -> Path:
    """Configure logging for the whole application.

    Called once, from the entry point, before any other work begins. Every
    other module then just calls `logging.getLogger(__name__)` and inherits
    this configuration.

    Args:
        logs_dir: Directory in which to create the log file. Created if absent.
        level:    Minimum severity to record: DEBUG, INFO, WARNING or ERROR.

    Returns:
        The path of the log file that was created.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)

    # One log file per run, named by start time, so separate runs are never
    # confused with each other.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"collection_{timestamp}.log"

    # An unrecognised level falls back to INFO rather than crashing.
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    log_format = "%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(log_format, datefmt=date_format)

    # encoding="utf-8" is required: captions and comments contain Arabic,
    # Hebrew, Persian and emoji, which the default Windows encoding does not
    # support.
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # Windows' console defaults to a legacy codepage (e.g. cp1252) that
    # cannot encode most emoji or Arabic/Hebrew/Persian text - both of which
    # appear routinely in this study's captions, hashtags and comments, and
    # now even in a search hashtag (#iran🇮🇷). Without this, printing such a
    # line raises UnicodeEncodeError from inside the logging module itself,
    # silently dropping that log line instead of crashing the run.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        # Not every stream supports reconfigure (e.g. some redirected/piped
        # stdout); falling back to whatever encoding it already has is
        # better than failing logging setup entirely.
        pass

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear any existing handlers so re-running in the same process (as the
    # test suite does) doesn't duplicate every log line.
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # `requests` logs every connection at DEBUG via urllib3; pin it to
    # WARNING so it doesn't drown out our own debug output.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    return log_path
