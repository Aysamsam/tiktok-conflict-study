#!/usr/bin/env python3
"""
Command-line entry point for the TikTok Conflict Study data collector.

This is the only file that should be executed directly. Everything else in the
project is a library module imported from here.

Usage:
    pip install -r requirements.txt
    cp .env.example .env

    python run_collection.py                # pilot run: 20 synthetic posts
    python run_collection.py --dry-run       # validate config, collect nothing
    python run_collection.py --max-posts 500 # real collection (needs TIKTOK_CLIENT=research_api)

Command-line flags override individual .env settings for a single run, without
editing the .env file itself. Every override is logged.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

# Make the project root importable regardless of where the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import src.config   # noqa: E402
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
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run_collection.py                      Run with .env settings\n"
            "  python run_collection.py --max-posts 5        Quick 5-post smoke test\n"
            "  python run_collection.py --client mock        Force the offline mock\n"
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
        help="Override OUTPUT_ROOT - the directory holding data/.",
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
            "resolved settings, then exit without contacting any data source."
        ),
    )

    return parser


def apply_overrides(settings: src.config.Settings, args: argparse.Namespace) -> src.config.Settings:
    """Return a new `Settings` with any command-line overrides applied.

    `Settings` is frozen, so this uses `dataclasses.replace`, which constructs a
    fresh instance and re-runs `__post_init__` - overrides are validated exactly
    as strictly as values from the .env file.

    Args:
        settings: The configuration loaded from the environment.
        args:     Parsed command-line arguments.

    Returns:
        The effective configuration for this run.
    """
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
    if args.log_level:
        changes["log_level"] = args.log_level

    if not changes:
        return settings

    # `replace` cannot set fields declared with `init=False` (the derived
    # paths); `__post_init__` recomputes them automatically.
    return replace(settings, **changes)


def _parse_date_argument(value: str, flag_name: str) -> date:
    """Parse a YYYY-MM-DD command-line date, with a helpful error message."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise src.config.ConfigError(
            f"{flag_name} must be in YYYY-MM-DD format, got '{value}'."
        ) from exc


def main() -> int:
    """Run the collector.

    Exit codes:
        0 - success
        1 - configuration error (fix the .env file or the flags)
        2 - collection error (the run started but did not complete cleanly)
    """
    args = build_argument_parser().parse_args()

    # --- Load and validate configuration ---------------------------------
    try:
        settings = src.config.load_settings()
        settings = apply_overrides(settings, args)
    except src.config.ConfigError as exc:
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
        logger.info("Dry run complete - no data was collected.")
        return 0

    # --- Run the collection ----------------------------------------------
    try:
        stats = run_collection(settings)
    except Exception as exc:
        logger.exception("Collection failed with an unexpected error: %s", exc)
        return 2

    if stats.posts_saved == 0:
        logger.warning(
            "No new posts were saved. Common causes: the sample is already "
            "complete in raw_posts.csv, the date window contains no matching "
            "posts, or the hashtags are misspelled."
        )

    logger.info("Done. Data written under %s", settings.output_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
