"""
The abstract contract that every TikTok data source must satisfy.

THE CENTRAL DESIGN IDEA OF THIS PROJECT
---------------------------------------
The pipeline never talks to TikTok directly. It talks to a `TikTokClient`.
`TikTokClient` is an abstract base class: it declares WHAT a data source must
be able to do, without saying anything about HOW.

Two concrete implementations exist:

    MockTikTokClient      - fabricates plausible data offline (development)
    ResearchApiClient     - calls the real TikTok Research API (production)

Because both satisfy the same interface, the pipeline code is written once and
works with either. Swapping them is a single line in the .env file.

This matters for three practical reasons in a research setting:

  1. The pipeline can be developed, demonstrated and reviewed long before the
     institutional Research API application is approved.
  2. The test suite runs in under a second, offline, with no credentials and
     no rate limits - so the logic can be verified continuously.
  3. If TikTok changes its API, or the study later adds a second platform, the
     change is confined to one new client class. Nothing downstream moves.

This is the "dummy API" the study brief asked for, expressed as a proper
software interface rather than as commented-out code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Iterator, List, Optional

from ..models import Comment, Post


class TikTokClientError(Exception):
    """Raised when a data source fails in a way the pipeline should handle.

    Wrapping provider-specific failures (an HTTP 429, an expired OAuth token, a
    malformed JSON body) in one exception type means the pipeline can decide
    what to do about "the data source failed" without needing to know anything
    about HTTP or about TikTok's error codes.
    """


class TikTokClient(ABC):
    """Abstract interface for any source of TikTok posts and comments.

    A subclass must implement exactly two methods. Everything else in the
    project - deduplication, CSV writing, video downloading, server transfer -
    is provider-independent and lives elsewhere.
    """

    @abstractmethod
    def search_posts(
        self,
        hashtags: List[str],
        start_date: date,
        end_date: date,
        language: str,
        max_posts: int,
    ) -> Iterator[Post]:
        """Yield posts matching the study's search criteria.

        Args:
            hashtags:   Tag names WITHOUT '#'. Matching is OR: a post qualifies
                        if it carries any one of these tags.
            start_date: First day of the collection window, inclusive.
            end_date:   Last day of the collection window, inclusive.
            language:   ISO 639-1 code; posts in other languages are excluded.
            max_posts:  Stop after yielding this many posts.

        Yields:
            Fully populated `Post` objects, already translated out of whatever
            format the underlying source uses.

        Raises:
            TikTokClientError: On an unrecoverable failure of the data source.

        WHY AN ITERATOR RATHER THAN A LIST?
        A list would require holding every result in memory and would force the
        caller to wait for the entire (potentially hours-long, paginated)
        search to finish before processing anything. Yielding one post at a
        time lets the pipeline write each row and download each video as soon
        as it arrives, so an interrupted run still leaves usable data on disk.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_comments(self, post_id: str, max_comments: int) -> Iterator[Comment]:
        """Yield comments belonging to one post, newest-first where supported.

        Args:
            post_id:      The video whose comments to retrieve.
            max_comments: Hard cap on how many to yield. Popular posts in this
                          study can carry six-figure comment counts; the cap
                          bounds both runtime and storage.

        Yields:
            `Comment` objects.

        Raises:
            TikTokClientError: On an unrecoverable failure.

        NOTE: Some posts have comments disabled, and some are unavailable to
        the Research API. An implementation should yield nothing in that case
        rather than raising - a post without comments is a normal, expected
        observation, not an error.
        """
        raise NotImplementedError

    def resolve_video_download_url(self, post: Post) -> Optional[str]:
        """Return a directly downloadable media URL for `post`, or None.

        This is deliberately NOT abstract: it has a default implementation that
        returns None, meaning "this data source cannot supply a media file".

        WHY THE DEFAULT IS None - AN IMPORTANT COMPLIANCE NOTE
        ------------------------------------------------------
        The TikTok Research API returns post METADATA. It does not return a
        direct .mp4 link, and TikTok's Terms of Service restrict scraping the
        media CDN. Downloading the raw video files therefore requires either:

          (a) an explicit media-access provision in your institution's Research
              API agreement, or
          (b) a separate, licensed data provider, or
          (c) documented, ethics-approved manual retrieval.

        Rather than silently doing something questionable, this method returns
        None by default and the pipeline records the video as "not downloaded"
        while keeping every other field intact. When your institution confirms
        which route applies, implement this one method in one client class and
        video downloading begins working - no other file changes.

        Returns:
            A URL string that `downloader.py` can fetch, or None if this source
            cannot provide the media file.
        """
        return None

    def close(self) -> None:
        """Release any resources held by the client (sockets, sessions).

        Default implementation does nothing, which is correct for the mock
        client. Network-backed clients override it to close their HTTP session
        so that the process exits cleanly instead of leaving sockets open.
        """
        return None

    # Supporting `with` blocks means the pipeline can guarantee cleanup even
    # if an exception occurs mid-collection:
    #
    #     with build_client(settings) as client:
    #         ...
    #
    def __enter__(self) -> "TikTokClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
