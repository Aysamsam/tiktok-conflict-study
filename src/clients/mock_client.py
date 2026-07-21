"""
Offline mock data source - the "dummy TikTok API".

PURPOSE
-------
This class fabricates posts and comments that are structurally identical to
real TikTok Research API results, without any network access or credentials.
It exists so that:

  * the pipeline can be built, run and demonstrated before the institutional
    Research API application is approved;
  * reviewers can execute the code themselves and inspect real output files
    without needing credentials of their own;
  * the automated tests run in milliseconds, offline, deterministically.

WHAT "STRUCTURALLY IDENTICAL" MEANS AND WHY IT MATTERS
------------------------------------------------------
The mock does not merely return well-formed rows. It deliberately reproduces
the awkward properties of real data, so that code which passes here does not
break on first contact with the real API:

  * ID values are 19-digit numbers (large enough to expose integer-rounding
    bugs in spreadsheet handling);
  * some posts carry no region code, mirroring the API's optional field;
  * some captions contain commas, quotation marks, newlines and emoji, which
    are exactly the characters that break naive CSV writing;
  * engagement counts follow a heavy-tailed distribution, as real virality
    does, rather than being uniformly random;
  * roughly one post in six carries the AI-generated label;
  * a small number of posts return zero comments, mimicking posts with
    comments disabled.

DETERMINISM
-----------
The generator is seeded from the search parameters, so the same study
configuration always produces the same mock dataset. That makes test failures
reproducible and lets two people compare output files meaningfully.

*** THIS DATA IS FICTIONAL. It must never be mixed with, or reported as, real
    collected data. Every mock post carries `MOCK_` in its username and the
    log states clearly which client produced the run. ***
"""

from __future__ import annotations

import hashlib
import logging
import random
from datetime import date, datetime, timezone
from typing import Iterator, List, Optional

from ..models import Comment, Post, unix_to_iso
from .base import TikTokClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fragments used to assemble plausible captions and comments.
#
# These are neutral, synthetic strings chosen to exercise the pipeline's text
# handling (commas, quotes, emoji, non-Latin script). They are not real posts,
# not quotations of anyone, and carry no editorial position.
# ---------------------------------------------------------------------------

_CAPTION_TEMPLATES: List[str] = [
    "Breaking: latest updates from the region tonight {tags}",
    'Analysts say "the situation remains fluid", per state media {tags}',
    "Thread: what we know so far, and what we don't {tags}",
    "My family is safe, thank you for the messages \U0001F64F {tags}",
    "Fact-check: this clip is circulating out of context {tags}",
    "Live footage, unverified. Please do not reshare blindly {tags}",
    "الأخبار العاجلة - translation in comments {tags}",
    "Explainer: the timeline, in 60 seconds {tags}",
]

_COMMENT_TEMPLATES: List[str] = [
    "Where was this filmed? Source please",
    "Stay safe everyone \U0001F499",
    "This is AI generated, look at the hands",
    "I don't think this is from this week, it's older footage",
    "Praying for all civilians on both sides",
    'Someone said "verified" but there is no source',
    "Thanks for covering this, mainstream media is silent",
    "Reported, this is misinformation",
    "My cousin lives there, she says it's calm today",
    "Can you add subtitles?",
]

_EXTRA_HASHTAGS: List[str] = [
    "news", "breaking", "middleeast", "geopolitics",
    "factcheck", "osint", "worldnews", "fyp",
]

# 2026-04-01T00:00:00Z as Unix seconds. Used only as a fallback base time when
# comments are requested for a post this client did not itself generate.
_DEFAULT_WINDOW_START = 1_775_001_600


class MockTikTokClient(TikTokClient):
    """Generates synthetic TikTok data that mirrors the real API's shape."""

    def __init__(self, seed: Optional[int] = None) -> None:
        """Create a mock client.

        Args:
            seed: Fixed seed for the random generator. When None, the seed is
                  derived from the search parameters at query time so that
                  identical searches produce identical results.

        A private `random.Random` instance is used rather than the module-level
        `random.seed()`, so seeding here cannot interfere with randomness used
        anywhere else in the process.
        """
        self._seed = seed
        self._rng = random.Random(seed)

        # Remembers the publication time of every post this client generated,
        # keyed by post_id. `fetch_comments` consults it so that a comment can
        # never be dated BEFORE the video it replies to - an impossible
        # ordering that would corrupt any time-series analysis of the thread.
        self._post_times: dict[str, int] = {}
        logger.info(
            "MockTikTokClient active - all data produced by this run is "
            "SYNTHETIC and must not be treated as collected research data."
        )

    # -- Interface method 1 ------------------------------------------------

    def search_posts(
        self,
        hashtags: List[str],
        start_date: date,
        end_date: date,
        language: str,
        max_posts: int,
    ) -> Iterator[Post]:
        """Yield `max_posts` synthetic posts inside the requested date window.

        The signature and behaviour match `ResearchApiClient.search_posts`
        exactly, which is the whole point: the pipeline cannot tell them apart.
        """
        # Derive a deterministic seed from the query itself when none was
        # supplied. Two runs with the same study configuration then produce
        # byte-identical output, which makes differences meaningful and lets
        # the deduplication logic be tested by simply running twice.
        #
        # NOTE: Python's built-in hash() is NOT usable here. For security
        # reasons it is randomised per process, so it returns a different value
        # for the same string in every run. Using it would silently defeat the
        # determinism this block exists to provide. `hashlib.sha256` is stable
        # across processes, machines and Python versions.
        if self._seed is None:
            fingerprint = f"{sorted(hashtags)}|{start_date}|{end_date}|{language}"
            digest = hashlib.sha256(fingerprint.encode("utf-8")).digest()
            self._rng = random.Random(int.from_bytes(digest[:8], "big"))

        logger.info(
            "Mock search: tags=%s window=%s..%s lang=%s target=%d posts",
            hashtags, start_date, end_date, language, max_posts,
        )

        # Convert the date window to Unix seconds so that generated timestamps
        # can be drawn uniformly across the whole study period.
        window_start = int(datetime(
            start_date.year, start_date.month, start_date.day,
            tzinfo=timezone.utc,
        ).timestamp())
        window_end = int(datetime(
            end_date.year, end_date.month, end_date.day,
            23, 59, 59, tzinfo=timezone.utc,
        ).timestamp())

        for index in range(max_posts):
            yield self._make_post(index, hashtags, window_start, window_end)

    # -- Interface method 2 ------------------------------------------------

    def fetch_comments(self, post_id: str, max_comments: int) -> Iterator[Comment]:
        """Yield a synthetic comment thread for `post_id`.

        The number of comments varies per post, and roughly one post in ten
        returns none at all - reproducing posts that have comments disabled or
        that are simply unengaged. Code which assumes every post has comments
        will therefore fail here rather than in production.
        """
        # Seed from the post ID so that the same post always produces the same
        # comments, even if fetched in a later run.
        rng = random.Random(int(post_id) & 0xFFFFFFFF)

        if rng.random() < 0.10:
            logger.debug("Mock post %s has comments disabled - yielding none.", post_id)
            return

        # Draw a count that is usually modest but occasionally hits the cap, so
        # that the capping logic in the pipeline is genuinely exercised.
        count = min(max_comments, rng.randint(3, max(4, max_comments)))
        logger.debug("Mock post %s: generating %d comments.", post_id, count)

        # Comments must post-date their video. Look up the post's real
        # publication time rather than inventing an independent one; the
        # fallback only applies when comments are requested for a post this
        # client did not generate, which in practice means a unit test.
        post_time = self._post_times.get(post_id)
        if post_time is None:
            logger.debug(
                "No recorded publication time for mock post %s; using a "
                "fallback base time.", post_id,
            )
            post_time = _DEFAULT_WINDOW_START

        # The first comment lands between one minute and two days after the
        # video was published, matching the real pattern where engagement is
        # heavily concentrated in the hours after posting.
        base_time = post_time + rng.randint(60, 172_800)

        for i in range(count):
            comment_id = str(rng.randint(7_000_000_000_000_000_000, 7_999_999_999_999_999_999))

            # Later comments in the thread get later timestamps, so that the
            # data sorts sensibly and reply-ordering is coherent.
            comment_time = base_time + (i * rng.randint(60, 7200))

            # Comment likes are heavily skewed: most get none, a few go viral.
            like_count = int(rng.expovariate(1 / 12.0)) if rng.random() < 0.7 else 0

            # About a fifth of comments are replies to an earlier comment in
            # this same thread, giving the data a realistic reply structure.
            parent_id = ""
            if i > 2 and rng.random() < 0.20:
                parent_id = str(rng.randint(7_000_000_000_000_000_000, 7_999_999_999_999_999_999))

            yield Comment(
                comment_id=comment_id,
                post_id=post_id,
                comment_text=rng.choice(_COMMENT_TEMPLATES),
                create_time=unix_to_iso(comment_time),
                like_count=like_count,
                # Only top-level comments attract replies in this model.
                reply_count=rng.randint(0, 5) if not parent_id else 0,
                parent_comment_id=parent_id,
            )

    # -- Interface method 3 (optional override) ----------------------------

    def resolve_video_download_url(self, post: Post) -> Optional[str]:
        """Return None: the mock has no media file to offer.

        Returning None here (rather than a fake URL) means the mock exercises
        exactly the same "no downloadable media" code path that the real
        Research API will take. See `base.TikTokClient.resolve_video_download_url`
        for the compliance reasoning behind that default.
        """
        return None

    # -- Internal generation helpers ---------------------------------------

    def _make_post(
        self,
        index: int,
        study_hashtags: List[str],
        window_start: int,
        window_end: int,
    ) -> Post:
        """Fabricate one `Post`. Internal helper; not part of the interface."""
        rng = self._rng

        # 19-digit IDs, matching the real format. Any code that treats these as
        # integers will lose precision, so this reproduces that hazard on purpose.
        post_id = str(rng.randint(7_000_000_000_000_000_000, 7_999_999_999_999_999_999))
        username = f"MOCK_user_{rng.randint(1000, 9999)}"

        # Every post carries at least one study hashtag (that is why it matched
        # the search), plus zero to three unrelated tags, as real posts do.
        tags = [rng.choice(study_hashtags)]
        tags.extend(rng.sample(_EXTRA_HASHTAGS, k=rng.randint(0, 3)))
        tag_display = " ".join(f"#{t}" for t in tags)

        caption = rng.choice(_CAPTION_TEMPLATES).format(tags=tag_display)

        # Views follow a heavy-tailed (log-normal-ish) distribution: most posts
        # are seen by hundreds, a rare few by millions. A uniform distribution
        # would hide bugs in any downstream code that handles large numbers.
        view_count = int(rng.lognormvariate(mu=9.5, sigma=2.2))

        # Engagement rates are ratios of views, as they are in reality, so the
        # relationships between the four counters stay plausible.
        like_count = int(view_count * rng.uniform(0.02, 0.15))
        comment_count = int(like_count * rng.uniform(0.01, 0.08))
        share_count = int(like_count * rng.uniform(0.01, 0.20))

        # Roughly one in six posts carries an AI label, split between the two
        # kinds TikTok distinguishes (see Post.ai_label_kind). Creator
        # self-disclosure is modelled as the more common of the two.
        #
        # These rates are plausible placeholders for exercising the pipeline.
        # They are NOT estimates of true prevalence and must never be cited.
        ai_roll = rng.random()
        if ai_roll < 0.11:
            has_ai_label, ai_label_kind = True, "creator_labelled"
        elif ai_roll < 0.17:
            has_ai_label, ai_label_kind = True, "ai_generated"
        else:
            has_ai_label, ai_label_kind = False, ""

        # The region code is genuinely absent for a minority of real posts, so
        # the mock omits it sometimes to force the pipeline to handle blanks.
        author_region = rng.choice(["US", "GB", "IL", "IR", "AE", "DE", "CA", ""])

        create_time_unix = rng.randint(window_start, window_end)

        # Record the publication time so that `fetch_comments` can guarantee
        # every comment on this post is dated after it.
        self._post_times[post_id] = create_time_unix

        return Post(
            post_id=post_id,
            username=username,
            author_region=author_region,
            create_time=unix_to_iso(create_time_unix),
            caption=caption,
            hashtags=tags,
            video_url=f"https://www.tiktok.com/@{username}/video/{post_id}",
            has_ai_label=has_ai_label,
            ai_label_kind=ai_label_kind,
            view_count=view_count,
            like_count=like_count,
            comment_count=comment_count,
            share_count=share_count,
            # Favourites are rarer than likes but correlate with them.
            favorites_count=int(like_count * rng.uniform(0.01, 0.10)),
        )
