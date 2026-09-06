"""
Offline mock data source - the "dummy TikTok API".

Fabricates posts and comments that are structurally identical to real
TikTok Research API results, without any network access or credentials, so
the pipeline can be built, run and demonstrated before institutional API
access is approved, and so tests run offline and deterministically.

The mock deliberately reproduces the awkward properties of real data (19
digit IDs, missing region codes, commas/quotes/emoji in text, heavy-tailed
engagement counts, ~1-in-6 posts AI-labelled, some posts with zero comments)
so that code which passes here does not break on first contact with the
real API. The generator is seeded from the search parameters, so a given
study configuration always produces the same mock dataset.

This data is fictional. Every mock post carries `MOCK_` in its username and
must never be mixed with, or reported as, real collected data.
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
# Fragments used to assemble plausible captions and comments. These are
# neutral, synthetic strings that exercise the pipeline's text handling
# (commas, quotes, emoji, non-Latin script) - not real posts or quotations.
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

# 2026-04-01T00:00:00Z as Unix seconds - fallback base time when comments
# are requested for a post this client did not itself generate.
_DEFAULT_WINDOW_START = 1_775_001_600


class MockTikTokClient(TikTokClient):
    """Generates synthetic TikTok data that mirrors the real API's shape."""

    def __init__(self, seed: Optional[int] = None) -> None:
        """Create a mock client.

        Args:
            seed: Fixed seed for the random generator. When None, the seed
                  is derived from the search parameters at query time so
                  that identical searches produce identical results.

        Uses a private `random.Random` instance so seeding here cannot
        interfere with randomness used elsewhere in the process.
        """
        self._seed = seed
        self._rng = random.Random(seed)

        # Publication time of every post this client generated, keyed by
        # post_id, so `fetch_comments` never dates a comment before its post.
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
        keywords: Optional[List[str]] = None,
        random_order: bool = False,
        min_posts_per_term: int = 0,
    ) -> Iterator[Post]:
        """Yield `max_posts` synthetic posts inside the requested date window.

        `random_order` is accepted for interface parity but has no effect
        here - every mock post's fields are already independently randomised
        per index, so there is no "ID order" for it to change.
        `min_posts_per_term` is likewise accepted but unused - the mock
        generates a single flat stream rather than stratifying per term.

        Signature and behaviour match `ResearchApiClient.search_posts`, so
        the pipeline cannot tell them apart.
        """
        # Derive a deterministic seed from the query when none was supplied,
        # so two runs of the same study configuration produce identical
        # output. Python's built-in hash() is NOT usable here - it is
        # randomised per process - so hashlib.sha256 is used instead.
        if self._seed is None:
            fingerprint = f"{sorted(hashtags)}|{start_date}|{end_date}|{language}"
            digest = hashlib.sha256(fingerprint.encode("utf-8")).digest()
            self._rng = random.Random(int.from_bytes(digest[:8], "big"))

        logger.info(
            "Mock search: tags=%s window=%s..%s lang=%s target=%d posts",
            hashtags, start_date, end_date, language, max_posts,
        )

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

        Roughly one post in ten returns no comments, reproducing posts that
        have comments disabled or are simply unengaged.
        """
        # Seed from the post ID so the same post always produces the same
        # comments, even fetched in a later run.
        rng = random.Random(int(post_id) & 0xFFFFFFFF)

        if rng.random() < 0.10:
            logger.debug("Mock post %s has comments disabled - yielding none.", post_id)
            return

        count = min(max_comments, rng.randint(3, max(4, max_comments)))
        logger.debug("Mock post %s: generating %d comments.", post_id, count)

        # Comments must post-date their video; fall back to a default base
        # time only when the post wasn't generated by this client (in
        # practice, a unit test calling fetch_comments directly).
        post_time = self._post_times.get(post_id)
        if post_time is None:
            logger.debug(
                "No recorded publication time for mock post %s; using a "
                "fallback base time.", post_id,
            )
            post_time = _DEFAULT_WINDOW_START

        base_time = post_time + rng.randint(60, 172_800)

        for i in range(count):
            comment_id = str(rng.randint(7_000_000_000_000_000_000, 7_999_999_999_999_999_999))
            comment_time = base_time + (i * rng.randint(60, 7200))
            like_count = int(rng.expovariate(1 / 12.0)) if rng.random() < 0.7 else 0

            parent_id = ""
            if i > 2 and rng.random() < 0.20:
                parent_id = str(rng.randint(7_000_000_000_000_000_000, 7_999_999_999_999_999_999))

            yield Comment(
                comment_id=comment_id,
                post_id=post_id,
                comment_text=rng.choice(_COMMENT_TEMPLATES),
                create_time=unix_to_iso(comment_time),
                like_count=like_count,
                reply_count=rng.randint(0, 5) if not parent_id else 0,
                parent_comment_id=parent_id,
            )

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

        # 19-digit IDs, matching the real format, to exercise the same
        # integer-rounding hazard as production data.
        post_id = str(rng.randint(7_000_000_000_000_000_000, 7_999_999_999_999_999_999))
        username = f"MOCK_user_{rng.randint(1000, 9999)}"

        # Every post carries at least one study hashtag plus zero to three
        # unrelated tags, as real posts do.
        tags = [rng.choice(study_hashtags)]
        tags.extend(rng.sample(_EXTRA_HASHTAGS, k=rng.randint(0, 3)))
        tag_display = " ".join(f"#{t}" for t in tags)

        caption = rng.choice(_CAPTION_TEMPLATES).format(tags=tag_display)

        # Heavy-tailed view distribution: most posts get hundreds of views,
        # a rare few get millions.
        view_count = int(rng.lognormvariate(mu=9.5, sigma=2.2))
        like_count = int(view_count * rng.uniform(0.02, 0.15))
        comment_count = int(like_count * rng.uniform(0.01, 0.08))
        share_count = int(like_count * rng.uniform(0.01, 0.20))

        # Roughly one in six posts carries an AI label, split between the
        # two kinds TikTok distinguishes (see Post.ai_label_kind). These
        # rates are placeholders for exercising the pipeline, not estimates
        # of true prevalence, and must never be cited.
        ai_roll = rng.random()
        if ai_roll < 0.11:
            has_ai_label, ai_label_kind = True, "creator_labelled"
        elif ai_roll < 0.17:
            has_ai_label, ai_label_kind = True, "ai_generated"
        else:
            has_ai_label, ai_label_kind = False, ""

        # Region code is genuinely absent for a minority of real posts.
        author_region = rng.choice(["US", "GB", "IL", "IR", "AE", "DE", "CA", ""])

        create_time_unix = rng.randint(window_start, window_end)
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
            favorites_count=int(like_count * rng.uniform(0.01, 0.10)),
            video_duration=rng.randint(5, 180),
        )
