"""
The abstract contract that every TikTok data source must satisfy.

The pipeline never talks to TikTok directly - it talks to a `TikTokClient`.
Two implementations exist: `MockTikTokClient` (offline synthetic data) and
`ResearchApiClient` (the real TikTok Research API). Because both satisfy the
same interface, the pipeline is written once and swapping between them is a
single line in `.env`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Iterator, List, Optional

from ..models import Comment, Post


class TikTokClientError(Exception):
    """Raised when a data source fails in a way the pipeline should handle."""


class TikTokClient(ABC):
    """Abstract interface for any source of TikTok posts and comments.

    A subclass must implement `search_posts` and `fetch_comments`. Everything
    else - deduplication, CSV writing, server transfer - is provider-independent
    and lives elsewhere.
    """

    @abstractmethod
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
        """Yield posts matching the study's search criteria.

        Args:
            hashtags:   Tag names without '#'. Matching is OR: a post
                        qualifies if it carries any one of these tags.
            start_date: First day of the collection window, inclusive.
            end_date:   Last day of the collection window, inclusive.
            language:   ISO 639-1 code; posts in other languages are excluded.
            max_posts:  Stop after yielding this many posts.
            keywords:   Free-text terms, matched against the caption. When
                        given, a post qualifies via hashtags OR keywords -
                        this widens the search rather than narrowing it.
            random_order: When False (default), results come back in the
                        source's natural order. For the real API this is
                        decreasing video-ID order - effectively newest-first
                        - which can cluster a whole sample into a narrow
                        time window even when the requested date range is
                        wide. True asks for a random sample instead.
            min_posts_per_term: With more than one hashtag/keyword, reserve
                        at least this many posts for each term before
                        round-robin fills the rest of `max_posts` by
                        availability. Guards against a high-volume term
                        crowding out a low-volume one entirely. 0 (default)
                        disables reservation - pure round-robin, as before.

        Yields:
            Fully populated `Post` objects.

        Raises:
            TikTokClientError: On an unrecoverable failure of the data source.

        Yields one post at a time (rather than returning a list) so the
        pipeline can write each row as soon as it arrives, keeping an
        interrupted run's partial output usable.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_posts_by_ids(
        self,
        video_ids: List[str],
        start_date: date,
        end_date: date,
    ) -> Iterator[Post]:
        """Yield the specific posts named by `video_ids` (order not guaranteed).

        Unlike `search_posts`, this looks up known videos directly rather
        than discovering them by hashtag/keyword - the entry point for
        collecting posts a researcher already has URLs for. A video_id with
        no matching post (deleted, private, or outside `start_date`/
        `end_date`) is simply absent from the results, not an error; the
        caller is responsible for noticing which ids never appeared.

        Args:
            video_ids:  The numeric TikTok video IDs to look up.
            start_date: First day to search, inclusive. The Research API
                        requires a window even for an exact-ID lookup.
            end_date:   Last day to search, inclusive.

        Yields:
            One `Post` per video_id that was found.

        Raises:
            TikTokClientError: On an unrecoverable failure of the data source.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_comments(self, post_id: str, max_comments: int) -> Iterator[Comment]:
        """Yield comments belonging to one post, newest-first where supported.

        Args:
            post_id:      The video whose comments to retrieve.
            max_comments: Hard cap on how many to yield.

        Yields:
            `Comment` objects.

        Raises:
            TikTokClientError: On an unrecoverable failure.

        Some posts have comments disabled or are unavailable to the Research
        API; an implementation should yield nothing in that case rather than
        raising - it is a normal observation, not an error.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources held by the client (sockets, sessions)."""
        return None

    def __enter__(self) -> "TikTokClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
