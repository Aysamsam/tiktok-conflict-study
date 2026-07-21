#!/usr/bin/env python3
"""
Command-line entry point for the TikTok Conflict Study data collector.

This is the ONLY file that should be executed directly. Everything else in the
project is a library module imported from here.

TYPICAL USAGE
-------------
    # 1. First-time setup
    pip install -r requirements.txt
    cp .env.example .env

    # 2. Pilot run: 20 synthetic posts, no credentials needed
    python run_collection.py

    # 3. Inspect what was produced
    python run_collection.py --dry-run

    # 4. Real collection, once TIKTOK_CLIENT=research_api is set in .env
    python run_collection.py --max-posts 500

WHY COMMAND-LINE OVERRIDES EXIST ALONGSIDE THE .env FILE
--------------------------------------------------------
The .env file holds the study's standing configuration - the parameters that
define the research design and should stay stable and documented. The flags
below allow a one-off deviation (a quick 5-post test, a different date window)
WITHOUT editing that file, so a temporary experiment can never accidentally
become the permanent configuration through a forgotten edit.

Every override is logged, so the log file always records the parameters that
were actually used, not merely the ones stored in .env.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

# Make the project root importable regardless of where the script is launched
# from. Without this, running the file from another directory - as a cron job
# on the college server would - fails with ModuleNotFoundError on `src`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import ConfigError, Settings, load_settings   # noqa: E402
from src.logging_setup import setup_logging                    # noqa: E402
from src.pipeline import run_collection                        # noqa: E402

logger = logging.getLogger("run_collection")


def build_argument_parser() -> argparse.ArgumentParser:
    """Define every command-line flag this script accepts."""
    parser = argparse.ArgumentParser(
        prog="run_collection.py",
        description=(
            "Collect TikTok posts and comments for the conflict-content study. "
            "Configuration is read from the .env file; the flags below override "
            "individual settings for a single run."
        ),
        # Preserves the newlines in the epilog so the examples render as typed.
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run_collection.py                      Run with .env settings\n"
            "  python run_collection.py --max-posts 5        Quick 5-post smoke test\n"
            "  python run_collection.py --client mock        Force the offline mock\n"
            "  python run_collection.py --no-videos          Metadata only, no .mp4 files\n"
            "  python run_collection.py --dry-run            Validate config, collect nothing\n"
        ),
    )

    parser.add_argument(
        "--client",
        choices=["mock", "research_api"],
        help=(
            "Override TIKTOK_CLIENT. 'mock' generates synthetic data offline; "
            "'research_api' calls the real TikTok Research API."
        ),
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        metavar="N",
        help="Override STUDY_MAX_POSTS - the number of posts to collect.",
    )
    parser.add_argument(
        "--max-comments",
        type=int,
        metavar="N",
        help="Override STUDY_MAX_COMMENTS_PER_POST - the per-post comment cap.",
    )
    parser.add_argument(
        "--start-date",
        metavar="YYYY-MM-DD",
        help="Override STUDY_START_DATE - the first day of the collection window.",
    )
    parser.add_argument(
        "--end-date",
        metavar="YYYY-MM-DD",
        help="Override STUDY_END_DATE - the last day of the collection window.",
    )
    parser.add_argument(
        "--hashtags",
        metavar="TAG,TAG",
        help="Override STUDY_HASHTAGS - a comma-separated list, '#' optional.",
    )
    parser.add_argument(
        "--output-root",
        metavar="PATH",
        help=(
            "Override OUTPUT_ROOT - the directory holding data/ and downloads/. "
            "Point this at the mounted college server share to write directly to it."
        ),
    )
    parser.add_argument(
        "--no-videos",
        action="store_true",
        help="Collect metadata and comments only; do not download any .mp4 files.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override LOG_LEVEL. Use DEBUG to see every decision the pipeline makes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate the configuration, create the directory tree and print the "
            "resolved settings, then exit without contacting any data source. "
            "Use this to confirm a new .env before starting a long run."
        ),
    )

    return parser


def apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    """Return a new `Settings` with any command-line overrides applied.

    `Settings` is frozen (immutable), so this uses `dataclasses.replace`, which
    constructs a fresh instance from the old one plus the changed fields.
    Crucially, that re-runs `__post_init__`, so an override is validated exactly
    as strictly as a value from the .env file - `--start-date` after
    `--end-date` is rejected here, not discovered mid-run.

    Args:
        settings: The configuration loaded from the environment.
        args:     Parsed command-line arguments.

    Returns:
        The effective configuration for this run.
    """
    # Collect only the fields that were actually supplied. `replace()` is
    # called once with all of them, so validation sees the complete final
    # picture rather than each change in isolation.
    changes = {}

    if args.client:
        changes["client_type"] = args.client
    if args.max_posts is not None:
        changes["max_posts"] = args.max_posts
    if args.max_comments is not None:
        changes["max_comments_per_post"] = args.max_comments
    if args.start_date:
        changes["start_date"] = _parse_date_argument(args.start_date, "--start-date")
    if args.end_date:
        changes["end_date"] = _parse_date_argument(args.end_date, "--end-date")
    if args.hashtags:
        changes["hashtags"] = [
            tag.strip().lstrip("#") for tag in args.hashtags.split(",") if tag.strip()
        ]
    if args.output_root:
        changes["output_root"] = Path(args.output_root).resolve()
    if args.no_videos:
        changes["download_videos"] = False
    if args.log_level:
        changes["log_level"] = args.log_level

    if not changes:
        return settings

    # `replace` cannot set fields declared with `init=False` (the derived
    # paths), and it does not need to: __post_init__ recomputes them from the
    # new output_root automatically.
    return replace(settings, **changes)


def _parse_date_argument(value: str, flag_name: str) -> "datetime.date":
    """Parse a YYYY-MM-DD command-line date, with a helpful error message."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ConfigError(
            f"{flag_name} must be in YYYY-MM-DD format, got '{value}'."
        ) from exc


def main() -> int:
    """Run the collector. Returns a process exit code.

    Exit codes follow the Unix convention, so the college server's job
    scheduler can tell success from failure automatically:
        0 - success
        1 - configuration error (fix the .env file or the flags)
        2 - collection error (the run started but did not complete cleanly)
    """
    args = build_argument_parser().parse_args()

    # --- Load and validate configuration ---------------------------------
    # This happens before logging is configured, because the log directory is
    # itself a configured value. Errors here go to stderr instead.
    try:
        settings = load_settings()
        settings = apply_overrides(settings, args)
    except ConfigError as exc:
        print(f"\nCONFIGURATION ERROR\n  {exc}\n", file=sys.stderr)
        print("Check your .env file (copy .env.example if you have not yet).\n",
              file=sys.stderr)
        return 1

    # --- Configure logging -----------------------------------------------
    settings.ensure_directories()
    log_path = setup_logging(logs_dir=settings.logs_dir, level=settings.log_level)

    logger.info("=" * 60)
    logger.info("TikTok Conflict Study - collection run starting")
    logger.info("Log file: %s", log_path)
    logger.info("=" * 60)

    # --- Dry run: validate and stop --------------------------------------
    if args.dry_run:
        logger.info(settings.describe())
        logger.info("Directory tree ready:")
        logger.info("  posts CSV    : %s", settings.posts_csv)
        logger.info("  comments CSV : %s", settings.comments_csv)
        logger.info("  videos       : %s", settings.videos_dir)
        logger.info("Dry run complete - no data was collected.")
        return 0

    # --- Run the collection ----------------------------------------------
    try:
        stats = run_collection(settings)
    except Exception as exc:
        # A catch-all is appropriate at the top level only: it converts any
        # unhandled crash into a logged traceback and a clean exit code,
        # instead of a raw stack trace on a server console at 3am.
        logger.exception("Collection failed with an unexpected error: %s", exc)
        return 2

    # A run that saved nothing is not a crash, but it is almost certainly not
    # what the researcher wanted, so it is surfaced prominently rather than
    # being buried in the statistics block.
    if stats.posts_saved == 0:
        logger.warning(
            "No new posts were saved. Common causes: the sample is already "
            "complete in raw_posts.csv, the date window contains no matching "
            "posts, or the hashtags are misspelled."
        )

    logger.info("Done. Data written under %s", settings.output_root)
    return 0


if __name__ == "__main__":
    # `sys.exit` with the returned code makes the exit status available to the
    # shell and to any scheduler running this script.
    sys.exit(main())
