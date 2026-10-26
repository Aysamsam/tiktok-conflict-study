"""
The collection pipeline: the orchestrator that ties every component together.

`pipeline.py` contains the study's procedure - the order of operations, what
counts as a usable post, and when to stop - and nothing else. HTTP code and
CSV escaping live in `clients/` and `storage.py`.

Procedure, in order:
    1. Open both CSV files, learning which records were already collected.
    2. Ask the client for posts matching the hashtags, dates and language.
    3. For each post: skip if already collected, check eligibility, write
       the post row, then fetch and write its comments.
    4. Stop when the target sample size is reached or the results run out.
    5. Transfer the finished dataset to the server, if configured.

Posts are written before their comments, so `raw_comments.csv` (which
references posts by post_id) never ends up pointing at a row that was never
saved.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .clients import TikTokClient, TikTokClientError, build_client
from .config import Settings
from .language import LanguageFilter
from .models import Post
from .storage import open_comments_writer, open_posts_writer
from .transfer import TransferError, transfer_dataset

logger = logging.getLogger(__name__)


@dataclass
class CollectionStats:
    """Counters describing what one collection run actually did."""

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

    posts_skipped_lower_rank: int = 0
    """Eligible posts that lost out to higher-view_count posts (sort_by=view_count only)."""

    posts_skipped_account_cap: int = 0
    """Eligible posts skipped because their account already hit STUDY_MAX_POSTS_PER_ACCOUNT."""

    posts_skipped_hashtag_cap: int = 0
    """Eligible posts skipped because one of their study hashtags already hit STUDY_MAX_POSTS_PER_HASHTAG."""

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
            f"  Posts skipped (lower rank): {self.posts_skipped_lower_rank}\n"
            f"  Posts skipped (account cap): {self.posts_skipped_account_cap}\n"
            f"  Posts skipped (hashtag cap): {self.posts_skipped_hashtag_cap}\n"
            f"  Comments saved (new)      : {self.comments_saved}\n"
            f"  Non-fatal errors          : {self.errors}\n"
            "========================================================="
        )


class CollectionPipeline:
    """Runs one complete collection according to the supplied settings."""

    def __init__(self, settings: Settings, client: Optional[TikTokClient] = None) -> None:
        """Prepare the pipeline.

        Args:
            settings: The validated configuration.
            client:   An optional pre-built client. Production code leaves
                      this as None and lets the factory decide from the
                      config; tests inject a controlled fake here.
        """
        self.settings = settings
        self.client = client if client is not None else build_client(settings)
        self.stats = CollectionStats()

        # The language criterion is enforced locally: the Research API
        # has no server-side language filter. See src/language.py.
        self.language_filter = LanguageFilter(
            target_languages=settings.languages,
            mode=settings.language_mode,
        )

        # Posts saved so far this username has, including prior runs -
        # populated from disk in `run()` once STUDY_MAX_POSTS_PER_ACCOUNT is
        # known to be in use. Keyed by username.
        self._account_counts: Dict[str, int] = {}

        # Saved posts carrying each study hashtag so far, including prior
        # runs - populated from disk in `run()` once STUDY_MAX_POSTS_PER_HASHTAG
        # is known to be in use. Keyed by lowercased study hashtag. A post
        # carrying several study hashtags increments every one of them.
        self._hashtag_counts: Dict[str, int] = {}

    def _load_account_counts(self, posts_csv: Path) -> Dict[str, int]:
        """Count existing posts per username, so the cap holds across resumed runs."""
        counts: Dict[str, int] = {}
        if not posts_csv.exists() or posts_csv.stat().st_size == 0:
            return counts

        with open(posts_csv, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                username = row.get("username", "").strip()
                if username:
                    counts[username] = counts.get(username, 0) + 1
        return counts

    def _account_at_cap(self, username: str) -> bool:
        """True if `username` already has STUDY_MAX_POSTS_PER_ACCOUNT posts."""
        cap = self.settings.max_posts_per_account
        if cap <= 0:
            return False
        return self._account_counts.get(username, 0) >= cap

    def _load_hashtag_counts(self, posts_csv: Path) -> Dict[str, int]:
        """Count, per study hashtag, how many existing posts carry it.

        So the cap holds across resumed runs - mirrors `_load_account_counts`.
        """
        study_tags_lower = {tag.lower() for tag in self.settings.hashtags}
        counts: Dict[str, int] = {}
        if not posts_csv.exists() or posts_csv.stat().st_size == 0:
            return counts

        with open(posts_csv, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                post_tags_lower = {
                    tag.strip().lower()
                    for tag in row.get("hashtags", "").split(",")
                    if tag.strip()
                }
                for tag in post_tags_lower & study_tags_lower:
                    counts[tag] = counts.get(tag, 0) + 1
        return counts

    def _hashtag_at_cap(self, post: Post) -> bool:
        """True if any study hashtag `post` carries already hit STUDY_MAX_POSTS_PER_HASHTAG.

        A post carrying several study hashtags is excluded entirely if any
        one of them is already full - accepting it would push that tag over
        its cap, even if the post's other tags still have room.
        """
        cap = self.settings.max_posts_per_hashtag
        if cap <= 0:
            return False
        study_tags_lower = {tag.lower() for tag in self.settings.hashtags}
        post_tags_lower = {tag.lower() for tag in post.hashtags}
        return any(
            self._hashtag_counts.get(tag, 0) >= cap
            for tag in post_tags_lower & study_tags_lower
        )

    def _record_hashtag_counts(self, post: Post) -> None:
        """Credit `post` toward the count of every study hashtag it carries."""
        study_tags_lower = {tag.lower() for tag in self.settings.hashtags}
        post_tags_lower = {tag.lower() for tag in post.hashtags}
        for tag in post_tags_lower & study_tags_lower:
            self._hashtag_counts[tag] = self._hashtag_counts.get(tag, 0) + 1

    def run(self) -> CollectionStats:
        """Execute the full collection procedure and return its statistics."""
        settings = self.settings
        settings.ensure_directories()

        logger.info(settings.describe())
        logger.info("Starting collection.")

        if settings.max_posts_per_account > 0:
            self._account_counts = self._load_account_counts(settings.posts_csv)

        if settings.max_posts_per_hashtag > 0:
            self._hashtag_counts = self._load_hashtag_counts(settings.posts_csv)

        with open_posts_writer(settings.posts_csv) as posts_writer, \
             open_comments_writer(settings.comments_csv) as comments_writer:

            # If the sample is already complete, skip the API entirely.
            if posts_writer.total_records >= settings.max_posts:
                logger.info(
                    "Sample is already complete: %d posts on disk, target is %d. "
                    "Nothing to collect. Raise STUDY_MAX_POSTS to extend it.",
                    posts_writer.total_records, settings.max_posts,
                )
                logger.info(self.stats.summary())
                return self.stats

            try:
                # Over-fetch, because local filtering (mainly language) drops
                # some results. The loop still stops the moment `max_posts`
                # eligible posts have been saved, so this is an upper bound.
                search_budget = settings.max_posts * settings.search_overfetch_factor
                logger.info(
                    "Requesting up to %d posts to reach a target sample of %d "
                    "after local filtering.", search_budget, settings.max_posts,
                )

                post_stream = self.client.search_posts(
                    hashtags=settings.hashtags,
                    start_date=settings.start_date,
                    end_date=settings.end_date,
                    language=", ".join(settings.languages),
                    max_posts=search_budget,
                    keywords=settings.keywords,
                    random_order=settings.random_order,
                    min_posts_per_term=settings.min_posts_per_hashtag,
                )

                if settings.sort_by == "view_count":
                    self._run_ranked(post_stream, posts_writer, comments_writer)
                else:
                    self._run_streaming(post_stream, posts_writer, comments_writer)

            except TikTokClientError as exc:
                # An error from the search itself is fatal: there is no next
                # post to move on to. Everything already written stays valid.
                logger.error("Collection stopped early: %s", exc)
                self.stats.errors += 1

            except KeyboardInterrupt:
                logger.warning("Interrupted by user. Partial data has been saved.")

            logger.info(self.language_filter.summary())

        logger.info(self.stats.summary())

        # Transfer runs after the `with` block so every file is closed and
        # flushed before anything tries to copy it.
        try:
            transfer_dataset(settings)
        except TransferError as exc:
            logger.error(
                "Collection succeeded but the server transfer failed: %s "
                "The local dataset at %s is complete and the transfer can be "
                "retried.", exc, settings.output_root,
            )

        return self.stats

    def _run_streaming(self, post_stream, posts_writer, comments_writer) -> None:
        """Save eligible posts in the order the source returns them.

        Stops the moment the target sample size is reached, so this is
        the cheapest strategy - it never scans further than it has to.
        """
        settings = self.settings

        for post in post_stream:
            self.stats.posts_seen += 1

            # --- Step 3a: already collected? ----------------------
            if posts_writer.already_have(post.post_id):
                self.stats.posts_skipped_duplicate += 1
                logger.debug("Post %s already collected; skipping.", post.post_id)
                continue

            # --- Step 3b: does it meet the study criteria? --------
            if not self._is_eligible(post):
                self.stats.posts_skipped_ineligible += 1
                continue

            # --- Step 3c: has this account already hit its cap? ---
            if self._account_at_cap(post.username):
                self.stats.posts_skipped_account_cap += 1
                logger.debug(
                    "Post %s skipped: @%s already has %d post(s), at the "
                    "cap of %d.", post.post_id, post.username,
                    self._account_counts.get(post.username, 0),
                    settings.max_posts_per_account,
                )
                continue

            # --- Step 3c2: has one of its study hashtags hit its cap? ----
            if self._hashtag_at_cap(post):
                self.stats.posts_skipped_hashtag_cap += 1
                logger.debug(
                    "Post %s skipped: at least one of its study hashtags "
                    "already hit the cap of %d.",
                    post.post_id, settings.max_posts_per_hashtag,
                )
                continue

            try:
                self._process_post(post, posts_writer, comments_writer)
            except TikTokClientError as exc:
                # One post failing must not end the collection.
                self.stats.errors += 1
                logger.error("Abandoning post %s: %s", post.post_id, exc)
                continue

            self._account_counts[post.username] = (
                self._account_counts.get(post.username, 0) + 1
            )
            self._record_hashtag_counts(post)

            # --- Step 4: reached the target sample size? ----------
            # Measured against the total dataset, not just this run's
            # additions, so re-running a completed collection never
            # grows the dataset past the configured size.
            if posts_writer.total_records >= settings.max_posts:
                logger.info(
                    "Target sample size of %d posts reached "
                    "(%d already present, %d added this run).",
                    settings.max_posts,
                    posts_writer.total_records - self.stats.posts_saved,
                    self.stats.posts_saved,
                )
                break

    def _run_ranked(self, post_stream, posts_writer, comments_writer) -> None:
        """Scan the full search budget, then save the highest-view_count posts.

        Unlike `_run_streaming`, this cannot stop early: the most-viral post
        might be the last one the source returns, so every eligible new post
        must be buffered and ranked before anything is written.
        """
        settings = self.settings
        candidates = []

        for post in post_stream:
            self.stats.posts_seen += 1

            if posts_writer.already_have(post.post_id):
                self.stats.posts_skipped_duplicate += 1
                logger.debug("Post %s already collected; skipping.", post.post_id)
                continue

            if not self._is_eligible(post):
                self.stats.posts_skipped_ineligible += 1
                continue

            candidates.append(post)

        candidates.sort(key=self._rank_score, reverse=True)

        needed = max(0, settings.max_posts - posts_writer.total_records)
        logger.info(
            "Ranking %d eligible candidate(s) by view_count; keeping the top %d.",
            len(candidates), needed,
        )

        # Walk the ranking highest-first, skipping (not stopping for) any
        # candidate whose account or a carried hashtag is already at cap -
        # a capped account/hashtag must not block a lower-ranked candidate
        # from a different one filling the slot instead.
        selected: List[Post] = []
        account_cap_skipped = 0
        hashtag_cap_skipped = 0
        for post in candidates:
            if len(selected) >= needed:
                break
            if self._account_at_cap(post.username):
                account_cap_skipped += 1
                continue
            if self._hashtag_at_cap(post):
                hashtag_cap_skipped += 1
                continue
            selected.append(post)
            self._account_counts[post.username] = (
                self._account_counts.get(post.username, 0) + 1
            )
            self._record_hashtag_counts(post)

        for post in selected:
            try:
                self._process_post(post, posts_writer, comments_writer)
            except TikTokClientError as exc:
                self.stats.errors += 1
                logger.error("Abandoning post %s: %s", post.post_id, exc)
                continue

        self.stats.posts_skipped_account_cap += account_cap_skipped
        self.stats.posts_skipped_hashtag_cap += hashtag_cap_skipped
        self.stats.posts_skipped_lower_rank += max(
            0, len(candidates) - len(selected) - account_cap_skipped - hashtag_cap_skipped
        )

        logger.info(
            "Collected %d new post(s) by view_count; dataset now has %d of target %d.",
            self.stats.posts_saved,
            posts_writer.total_records,
            settings.max_posts,
        )

    def _rank_score(self, post: Post) -> float:
        """Score used to rank candidates when sort_by=view_count.

        Plain view_count, multiplied by boost_multiplier when the post
        carries one of the configured boost_hashtags (e.g. #fyp, #trending -
        tags that don't qualify a post on their own, see _is_eligible, but
        that creators use alongside a real topic tag to chase reach). A
        multiplier rather than a hard tie-break so a handful of boost-tagged
        views can't outrank a genuinely massive post with none.
        """
        if not self.settings.boost_hashtags:
            return post.view_count

        boost_tags_lower = {tag.lower() for tag in self.settings.boost_hashtags}
        post_tags_lower = {tag.lower() for tag in post.hashtags}
        if boost_tags_lower & post_tags_lower:
            return post.view_count * self.settings.boost_multiplier
        return post.view_count

    def _is_eligible(self, post: Post) -> bool:
        """Decide whether `post` belongs in the study sample.

        The data source is asked to filter server-side, but this second check
        runs locally too: the mock client does no filtering at all, and
        server-side filters are best-effort (a post can be returned without
        the requested hashtags in its final metadata). Every exclusion is
        logged at DEBUG level so it can be reconstructed later.

        Returns:
            True if the post should be collected.
        """
        # Criterion 1: at least one study hashtag, OR (when keywords are
        # configured) a study keyword in the caption - matched
        # case-insensitively either way. This must mirror the OR the search
        # query itself uses (see research_client._build_search_body): a post
        # that only matched via keyword would otherwise be wrongly rejected
        # here for "carrying none of the study hashtags".
        study_tags_lower = {tag.lower() for tag in self.settings.hashtags}
        post_tags_lower = {tag.lower() for tag in post.hashtags}
        hashtag_match = bool(study_tags_lower & post_tags_lower)

        keyword_match = False
        if self.settings.keywords:
            caption_lower = post.caption.lower()
            keyword_match = any(
                kw.lower() in caption_lower for kw in self.settings.keywords
            )

        if not (hashtag_match or keyword_match):
            logger.debug(
                "Excluding post %s: carries none of the study hashtags and "
                "matches none of the study keywords (hashtags: %s).",
                post.post_id, post.hashtags,
            )
            return False

        # Criterion 1b: on top of the primary hashtag/keyword match above,
        # at least one of the post's hashtags must contain this substring -
        # a secondary topical-relevance gate distinct from the primary
        # search criteria (e.g. primary = "iranwar" tag/keyword, secondary
        # = any hashtag containing "iran", so a post only reachable via a
        # loosely related keyword match still has to carry an on-topic tag).
        if self.settings.require_hashtag_contains:
            needle = self.settings.require_hashtag_contains.lower()
            if not any(needle in tag.lower() for tag in post.hashtags):
                logger.debug(
                    "Excluding post %s: no hashtag contains %r (hashtags: %s).",
                    post.post_id, self.settings.require_hashtag_contains, post.hashtags,
                )
                return False

        # Criterion 2: caption must be in the study's target language.
        keep, reason = self.language_filter.matches(post.caption)
        if not keep:
            logger.debug(
                "Excluding post %s on language: %s.", post.post_id, reason,
            )
            return False

        # Criterion 3: must have a usable identifier.
        if not post.post_id:
            logger.debug("Excluding a post with no post_id.")
            return False

        # Criterion 4: must meet the minimum engagement threshold.
        if post.comment_count < self.settings.min_comments:
            logger.debug(
                "Excluding post %s: comment_count %d is below the minimum of %d.",
                post.post_id, post.comment_count, self.settings.min_comments,
            )
            return False

        # Criterion 4a: must not exceed the maximum video duration, if set.
        # A duration of 0 means the API did not report one (not that the
        # video is zero-length), so it is kept rather than excluded - the
        # same benefit-of-doubt treatment as an unparseable create_time
        # below.
        if (
            self.settings.max_video_duration_seconds > 0
            and post.video_duration > 0
            and post.video_duration > self.settings.max_video_duration_seconds
        ):
            logger.debug(
                "Excluding post %s: video_duration %ds exceeds the maximum of %ds.",
                post.post_id, post.video_duration, self.settings.max_video_duration_seconds,
            )
            return False

        # Criterion 4b: must carry an AI content label, if required.
        if self.settings.require_ai_label and not post.has_ai_label:
            logger.debug(
                "Excluding post %s: no AI content label, and "
                "STUDY_REQUIRE_AI_LABEL is on.", post.post_id,
            )
            return False

        # Criterion 4c: author_region must be in the allowed set, if restricted.
        # Compared case-insensitively - TikTok returns region codes in mixed
        # case ("US" vs "us") depending on the post, not consistently.
        if self.settings.require_regions and (
            post.author_region or ""
        ).upper() not in self.settings.require_regions:
            logger.debug(
                "Excluding post %s: region %r not in %s.",
                post.post_id, post.author_region, self.settings.require_regions,
            )
            return False

        # Criterion 4c2: author_region must not be in the excluded set, if any.
        if self.settings.exclude_regions and (
            post.author_region or ""
        ).upper() in self.settings.exclude_regions:
            logger.debug(
                "Excluding post %s: region %r is in the excluded set %s.",
                post.post_id, post.author_region, self.settings.exclude_regions,
            )
            return False

        # Criterion 5: must fall inside the study window (rechecked locally
        # even though the API is asked for this range).
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
                # An unparseable timestamp is kept rather than excluded, and
                # the anomaly is recorded for review.
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
    ) -> None:
        """Collect one eligible post completely: metadata, then comments."""
        settings = self.settings

        # --- Step 3c: the metadata row ------------------------------------
        if posts_writer.write_row(post.to_csv_row()):
            self.stats.posts_saved += 1
            logger.info(
                "Saved post %d/%d: %s by @%s (%d views, AI label: %s)",
                self.stats.posts_saved, settings.max_posts,
                post.post_id, post.username, post.view_count,
                "yes" if post.has_ai_label else "no",
            )

        # --- Step 3d: the comment thread ----------------------------------
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
    """Build a pipeline, run it, and close the client.

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
        pipeline.client.close()
