#!/usr/bin/env python3
"""
Command-line entry point for collecting specific, already-known TikTok posts.

Unlike `run_collection.py` (which discovers posts by hashtag/keyword search
and applies the study's eligibility filters), this looks up an exact list of
URLs a researcher already has and saves them and their comments unfiltered -
they were chosen on purpose, so min_comments/duration/language/etc. do not
apply. Output goes to data/url_posts.csv and data/url_comments.csv, kept
separate from raw_posts.csv/raw_comments.csv so hand-picked posts never mix
into the systematically-sampled main dataset.

Usage:
    python collect_by_urls.py https://www.tiktok.com/@user/video/123...
    python collect_by_urls.py --file data/urls.txt
    python collect_by_urls.py --file data/urls.txt --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import src.config                                               # noqa: E402
from src.clients import TikTokClientError, build_client         # noqa: E402
from src.logging_setup import setup_logging                     # noqa: E402
from src.storage import open_comments_writer, open_posts_writer  # noqa: E402
from src.urls import extract_video_ids                          # noqa: E402

logger = logging.getLogger("collect_by_urls")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="collect_by_urls.py",
        description=(
            "Collect specific TikTok posts by URL (and their comments), "
            "unfiltered, into data/url_posts.csv and data/url_comments.csv."
        ),
    )
    parser.add_argument(
        "urls", nargs="*", metavar="URL",
        help="One or more TikTok video URLs (or bare video ids).",
    )
    parser.add_argument(
        "--file", metavar="PATH",
        help="Path to a text file with one URL (or id) per line.",
    )
    parser.add_argument(
        "--max-comments", type=int, metavar="N",
        help="Override STUDY_MAX_COMMENTS_PER_POST for this run.",
    )
    parser.add_argument(
        "--start-date", metavar="YYYY-MM-DD",
        help="Search window start. Defaults to STUDY_START_DATE.",
    )
    parser.add_argument(
        "--end-date", metavar="YYYY-MM-DD",
        help="Search window end. Defaults to STUDY_END_DATE.",
    )
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override LOG_LEVEL.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and report the URLs, then exit without contacting any data source.",
    )
    return parser


def _collect_urls(args: argparse.Namespace) -> list:
    urls = list(args.urls)
    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            raise src.config.ConfigError(f"--file path does not exist: {file_path}")
        with open(file_path, encoding="utf-8") as handle:
            urls.extend(line.strip() for line in handle if line.strip())
    return urls


def main() -> int:
    args = build_argument_parser().parse_args()

    try:
        settings = src.config.load_settings()
    except src.config.ConfigError as exc:
        print(f"\nCONFIGURATION ERROR\n  {exc}\n", file=sys.stderr)
        return 1

    settings.ensure_directories()
    log_path = setup_logging(
        logs_dir=settings.logs_dir, level=args.log_level or settings.log_level,
    )

    logger.info("=" * 60)
    logger.info("TikTok Conflict Study - collect-by-URL run starting")
    logger.info("Log file: %s", log_path)
    logger.info("=" * 60)

    raw_urls = _collect_urls(args)
    if not raw_urls:
        print(
            "No URLs given. Pass one or more URLs, or --file PATH.\n",
            file=sys.stderr,
        )
        return 1

    video_ids, unparseable = extract_video_ids(raw_urls)
    if unparseable:
        logger.warning(
            "%d URL(s) could not be parsed (need the full "
            "tiktok.com/@user/video/<id> permalink, not a short link): %s",
            len(unparseable), unparseable,
        )
    logger.info("%d unique video id(s) to look up.", len(video_ids))

    start_date = (
        _parse_date(args.start_date, "--start-date") if args.start_date
        else settings.start_date
    )
    end_date = (
        _parse_date(args.end_date, "--end-date") if args.end_date
        else settings.end_date
    )
    max_comments = args.max_comments or settings.max_comments_per_post

    posts_csv = settings.data_dir / "url_posts.csv"
    comments_csv = settings.data_dir / "url_comments.csv"

    if args.dry_run:
        logger.info("Would look up %d video(s), window=%s..%s", len(video_ids), start_date, end_date)
        logger.info("Posts would be written to: %s", posts_csv)
        logger.info("Comments would be written to: %s", comments_csv)
        logger.info("Dry run complete - no data was collected.")
        return 0

    if not video_ids:
        logger.warning("No parseable video ids; nothing to collect.")
        return 1

    client = build_client(settings)
    posts_saved = 0
    comments_saved = 0
    found_ids = set()

    try:
        with open_posts_writer(posts_csv) as posts_writer, \
             open_comments_writer(comments_csv) as comments_writer:

            try:
                for post in client.fetch_posts_by_ids(
                    video_ids=video_ids, start_date=start_date, end_date=end_date,
                ):
                    found_ids.add(post.post_id)

                    if posts_writer.already_have(post.post_id):
                        logger.debug("Post %s already collected; skipping.", post.post_id)
                        continue

                    if posts_writer.write_row(post.to_csv_row()):
                        posts_saved += 1
                        logger.info(
                            "Saved post %s by @%s (%d views, AI label: %s)",
                            post.post_id, post.username, post.view_count,
                            "yes" if post.has_ai_label else "no",
                        )

                    for comment in client.fetch_comments(
                        post_id=post.post_id, max_comments=max_comments,
                    ):
                        if comments_writer.write_row(comment.to_csv_row()):
                            comments_saved += 1

            except TikTokClientError as exc:
                logger.error("Collection stopped early: %s", exc)

        missing = [v for v in video_ids if v not in found_ids]
        logger.info(
            "\n=========================================================\n"
            "  COLLECT-BY-URL RUN SUMMARY\n"
            "=========================================================\n"
            f"  Video ids requested       : {len(video_ids)}\n"
            f"  Posts found               : {len(found_ids)}\n"
            f"  Posts saved (new)         : {posts_saved}\n"
            f"  Comments saved (new)      : {comments_saved}\n"
            f"  Not found (deleted/private/unparseable URL count: {len(unparseable)}) : {len(missing)}\n"
            "========================================================="
        )
        if missing:
            logger.warning("Video id(s) not found: %s", missing)

    finally:
        client.close()

    logger.info("Done. Data written to %s and %s", posts_csv, comments_csv)
    return 0


def _parse_date(value: str, flag_name: str):
    from datetime import datetime
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise src.config.ConfigError(
            f"{flag_name} must be in YYYY-MM-DD format, got '{value}'."
        ) from exc


if __name__ == "__main__":
    sys.exit(main())
