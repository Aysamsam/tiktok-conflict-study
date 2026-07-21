"""
The collection pipeline: the orchestrator that ties every component together.

WHAT THIS MODULE DOES - AND DELIBERATELY DOES NOT DO
----------------------------------------------------
This module contains the study's PROCEDURE and nothing else. It decides the
order of operations, what counts as a usable post, and when to stop. It
contains no HTTP code, no CSV escaping, no file I/O details - those live in
`clients/`, `storage.py` and `downloader.py` respectively.

Keeping the procedure separate from the mechanics is what allows a reviewer to
read this one file and understand exactly what the study did, without needing
to follow any networking or file-format code.

THE PROCEDURE, IN ORDER
-----------------------
    1. Open both CSV files, learning which records were already collected.
    2. Ask the client for posts matching the hashtags, dates and language.
    3. For each post:
         a. skip it if it is already in raw_posts.csv (resumable runs);
         b. verify it genuinely matches the study criteria (see _is_eligible);
         c. download the video, if the source can supply one;
         d. write the post row;
         e. fetch its comments, up to the cap, and write those rows.
    4. Stop when the target sample size is reached or the results run out.
    5. Transfer the finished dataset to the server, if configured.

WHY POSTS ARE WRITTEN BEFORE THEIR COMMENTS
-------------------------------------------
`raw_comments.csv` references `raw_posts.csv` by post_id. Writing the post
first means the referenced row always exists on disk before anything points at
it, so an interrupted run can never leave orphan comments - comments whose
parent post was never saved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .clients import TikTokClient, TikTokClientError, build_client
from .config import Settings
from .downloader import VideoDownloader
from .language import LanguageFilter
from .models import Post
from .storage import open_comments_writer, open_posts_writer
from .transfer import TransferError, transfer_dataset

logger = logging.getLogger(__name__)


@dataclass
class CollectionStats:
    """Counters describing what one collection run actually did.

    These are not decoration. A methods section must be able to state how many
    posts were examined, how many were excluded and why, and how many comments
    were captured. Counting during the run is the only reliable way to report
    that accurately afterwards.
    """

    posts_seen: int = 0
    """Posts returned by the data source, before any filtering."""

    posts_saved: int = 0
    """Posts newly written to raw_posts.csv during this run."""

    posts_skipped_duplicate: int = 0
    """Posts skipped because they were already collected by an earlier run."""

    posts_skipped_ineligible: int = 0
    """Posts discarded for failing a study inclusion criterion."""

    comments_saved: int = 0
    """Comment rows newly written during this run."""

    videos_downloaded: int = 0
    """Video files newly saved to disk."""

    errors: int = 0
    """Non-fatal errors that caused one post to be abandoned."""

    started_at: datetime = field(default_factory=datetime.now)

    def summary(self) -> str:
        """Return a multi-line, human-readable end-of-run report."""
        elapsed = datetime.now() - self.started_at
        return (
            "\n"
            "=========================================================\n"
            "  COLLECTION RUN SUMMARY\n"
            "=========================================================\n"
            f"  Duration                  : {elapsed}\n"
            f"  Posts returned by source  : {self.posts_seen}\n"
            f"  Posts saved (new)         : {self.posts_saved}\n"
            f"  Posts skipped (duplicate) : {self.posts_skipped_duplicate}\n"
            f"  Posts skipped (ineligible): {self.posts_skipped_ineligible}\n"
            f"  Comments saved (new)      : {self.comments_saved}\n"
            f"  Videos downloaded         : {self.videos_downloaded}\n"
            f"  Non-fatal errors          : {self.errors}\n"
            "========================================================="
        )


class CollectionPipeline:
    """Runs one complete collection according to the supplied settings."""

    def __init__(self, settings: Settings, client: Optional[TikTokClient] = None) -> None:
        """Prepare the pipeline.

        Args:
            settings: The validated configuration.
            client:   An optional pre-built client. Production code leaves this
                      as None and lets the factory decide from the config; the
                      test suite injects a controlled fake here. This is
                      dependency injection, and it is the reason the pipeline
                      can be tested without any network access at all.
        """
        self.settings = settings
        self.client = client if client is not None else build_client(settings)
        self.stats = CollectionStats()

        # The English-only criterion is enforced locally, because the Research
        # API has no server-side language filter. See src/language.py for the
        # full reasoning and its methodological consequences.
        self.language_filter = LanguageFilter(
            target_language=settings.language,
            mode=settings.language_mode,
        )

    def run(self) -> CollectionStats:
        """Execute the full collection procedure and return its statistics.

        Returns:
            A `CollectionStats` object describing what happened.
        """
        settings = self.settings
        settings.ensure_directories()

        logger.info(settings.describe())
        logger.info("Starting collection.")

        # All three resources are opened in one nested `with` block so that
        # every file handle and network session is closed exactly once, no
        # matter how the run ends - normally, by exception, or by Ctrl-C.
        with open_posts_writer(settings.posts_csv) as posts_writer, \
             open_comments_writer(settings.comments_csv) as comments_writer, \
             VideoDownloader(
                 videos_dir=settings.videos_dir,
                 project_root=settings.output_root,
                 max_retries=settings.max_retries,
             ) as downloader:

            # If the sample is already complete, do not contact the API at all.
            # Without this check a finished collection would still spend quota
            # re-searching, only to discard every result as a duplicate.
            if posts_writer.total_records >= settings.max_posts:
                logger.info(
                    "Sample is already complete: %d posts on disk, target is %d. "
                    "Nothing to collect. Raise STUDY_MAX_POSTS to extend it.",
                    posts_writer.total_records, settings.max_posts,
                )
                self.stats.videos_downloaded = downloader.downloaded_count
                logger.info(self.stats.summary())
                return self.stats

            try:
                # Ask the data source for MORE posts than the target sample
                # size, because some of what it returns will be filtered out
                # locally - most often by the language criterion, which cannot
                # be applied server-side (see src/language.py).
                #
                # Without this over-fetch, requesting 20 posts and rejecting 3
                # as non-English yields a sample of 17, silently undershooting
                # the study design. The pipeline still stops the moment
                # `max_posts` ELIGIBLE posts have been saved, so the extra
                # budget is an upper bound, not a fixed cost.
                search_budget = settings.max_posts * settings.search_overfetch_factor
                logger.info(
                    "Requesting up to %d posts to reach a target sample of %d "
                    "after local filtering.", search_budget, settings.max_posts,
                )

                post_stream = self.client.search_posts(
                    hashtags=settings.hashtags,
                    start_date=settings.start_date,
                    end_date=settings.end_date,
                    language=settings.language,
                    max_posts=search_budget,
                )

                for post in post_stream:
                    self.stats.posts_seen += 1

                    # --- Step 3a: already collected? ----------------------
                    # Checked before any expensive work, so resuming a run does
                    # not re-download videos or re-fetch comment threads.
                    if posts_writer.already_have(post.post_id):
                        self.stats.posts_skipped_duplicate += 1
                        logger.debug("Post %s already collected; skipping.", post.post_id)
                        continue

                    # --- Step 3b: does it meet the study criteria? --------
                    if not self._is_eligible(post):
                        self.stats.posts_skipped_ineligible += 1
                        continue

                    try:
                        self._process_post(post, posts_writer, comments_writer, downloader)
                    except TikTokClientError as exc:
                        # One post failing (deleted mid-run, comments locked,
                        # a transient API fault) must not end the collection.
                        # Count it, log it, continue with the next post.
                        self.stats.errors += 1
                        logger.error("Abandoning post %s: %s", post.post_id, exc)
                        continue

                    # --- Step 4: reached the target sample size? ----------
                    # Measured against the TOTAL number of posts in the
                    # dataset, not just those added during this run. Counting
                    # only this run's additions would mean re-running a
                    # completed collection appends another full sample, growing
                    # the dataset past the size the study design specifies.
                    if posts_writer.total_records >= settings.max_posts:
                        logger.info(
                            "Target sample size of %d posts reached "
                            "(%d already present, %d added this run).",
                            settings.max_posts,
                            posts_writer.total_records - self.stats.posts_saved,
                            self.stats.posts_saved,
                        )
                        break

            except TikTokClientError as exc:
                # An error raised by the SEARCH itself (bad credentials,
                # exhausted quota) is fatal: there is no next post to move on
                # to. Everything already written stays on disk and valid.
                logger.error("Collection stopped early: %s", exc)
                self.stats.errors += 1

            except KeyboardInterrupt:
                # Ctrl-C is a legitimate way to stop a long run. Because every
                # row was flushed as it was written, the partial dataset on
                # disk is complete and consistent up to this point, and a later
                # run will resume from exactly here.
                logger.warning("Interrupted by user. Partial data has been saved.")

            self.stats.videos_downloaded = downloader.downloaded_count
            logger.info(downloader.summary())
            logger.info(self.language_filter.summary())

        logger.info(self.stats.summary())

        # --- Step 5: deliver to the server --------------------------------
        # Runs after the `with` block, so every file is closed and flushed
        # before anything tries to copy it - copying an open, half-flushed CSV
        # would put a truncated file on the server.
        try:
            transfer_dataset(settings)
        except TransferError as exc:
            # The data is safely on the local disk; only delivery failed. That
            # is worth an error in the log, but it must not be reported as a
            # failed collection, and it must not delete anything.
            logger.error(
                "Collection succeeded but the server transfer failed: %s "
                "The local dataset at %s is complete and the transfer can be "
                "retried.", exc, settings.output_root,
            )

        return self.stats

    def _is_eligible(self, post: Post) -> bool:
        """Decide whether `post` belongs in the study sample.

        The data source is asked to filter server-side, but this second check
        runs locally as well, for two reasons:

          * the mock client does no filtering at all, so without this the mock
            would not exercise the same code path as production;
          * server-side filters are best-effort. TikTok's language detection
            in particular is imperfect, and a post can be returned that carries
            none of the requested hashtags in its final metadata.

        Every exclusion is logged at DEBUG level, so the reason any particular
        post is absent from the dataset can always be reconstructed from the
        log - which is exactly what a reviewer asking "why is post X missing?"
        needs.

        Returns:
            True if the post should be collected.
        """
        # Criterion 1: it must carry at least one of the study's hashtags.
        # Comparison is case-insensitive because TikTok preserves the author's
        # capitalisation: #IsraelIran, #israeliran and #ISRAELIRAN are one tag.
        study_tags_lower = {tag.lower() for tag in self.settings.hashtags}
        post_tags_lower = {tag.lower() for tag in post.hashtags}

        if not (study_tags_lower & post_tags_lower):
            logger.debug(
                "Excluding post %s: carries none of the study hashtags (has: %s).",
                post.post_id, post.hashtags,
            )
            return False

        # Criterion 2: the caption must be in the study's target language.
        # Checked here rather than at the API because the video-query endpoint
        # offers no language filter (see src/language.py).
        keep, reason = self.language_filter.matches(post.caption)
        if not keep:
            logger.debug(
                "Excluding post %s on language: %s.", post.post_id, reason,
            )
            return False

        # Criterion 3: it must have a usable identifier. A post without an ID
        # cannot be joined to its comments or its video file, so it is
        # unusable regardless of its content.
        if not post.post_id:
            logger.debug("Excluding a post with no post_id.")
            return False

        # Criterion 4: it must fall inside the study window. The API is asked
        # for this range, but the check is repeated because an off-by-one in a
        # date boundary is both easy to make and very hard to notice later.
        if post.create_time:
            try:
                created = datetime.fromisoformat(post.create_time).date()
                if not (self.settings.start_date <= created <= self.settings.end_date):
                    logger.debug(
                        "Excluding post %s: published %s, outside the window %s..%s.",
                        post.post_id, created,
                        self.settings.start_date, self.settings.end_date,
                    )
                    return False
            except ValueError:
                # An unparseable timestamp is suspicious but not disqualifying;
                # the post is kept and the anomaly recorded for review.
                logger.debug(
                    "Post %s has an unparseable create_time (%r); keeping it.",
                    post.post_id, post.create_time,
                )

        return True

    def _process_post(
        self,
        post: Post,
        posts_writer,
        comments_writer,
        downloader: VideoDownloader,
    ) -> None:
        """Collect one eligible post completely: video, metadata, comments."""
        settings = self.settings

        # --- Step 3c: the video file --------------------------------------
        if settings.download_videos:
            # The client decides whether a downloadable URL exists; the
            # downloader decides what to do about it. Neither knows about the
            # other's constraints, which is why the media-access policy can
            # change without touching this line.
            media_url = self.client.resolve_video_download_url(post)
            post.video_path = downloader.download(post, media_url)
        else:
            logger.debug("DOWNLOAD_VIDEOS is false; skipping media for %s.", post.post_id)
            post.video_path = ""

        # --- Step 3d: the metadata row ------------------------------------
        # Written BEFORE the comments so that the row a comment points at
        # always exists on disk first.
        if posts_writer.write_row(post.to_csv_row()):
            self.stats.posts_saved += 1
            logger.info(
                "Saved post %d/%d: %s by @%s (%d views, AI label: %s)",
                self.stats.posts_saved, settings.max_posts,
                post.post_id, post.username, post.view_count,
                "yes" if post.has_ai_label else "no",
            )

        # --- Step 3e: the comment thread ----------------------------------
        comment_count = 0
        for comment in self.client.fetch_comments(
            post_id=post.post_id,
            max_comments=settings.max_comments_per_post,
        ):
            if comments_writer.write_row(comment.to_csv_row()):
                comment_count += 1
                self.stats.comments_saved += 1

        logger.debug("Saved %d comment(s) for post %s.", comment_count, post.post_id)


def run_collection(settings: Settings, client: Optional[TikTokClient] = None) -> CollectionStats:
    """Convenience wrapper: build a pipeline, run it, close the client.

    Args:
        settings: The validated configuration.
        client:   Optional injected client (used by the tests).

    Returns:
        The run's `CollectionStats`.
    """
    pipeline = CollectionPipeline(settings=settings, client=client)
    try:
        return pipeline.run()
    finally:
        # `finally` ensures the HTTP session is closed even if the run raised,
        # so the process never exits leaving sockets open.
        pipeline.client.close()
