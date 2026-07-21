"""
Live data source: the official TikTok Research API.

WHAT THIS IS
------------
TikTok operates a Research API for academic institutions
(https://developers.tiktok.com/products/research-api/). Access requires an
approved application from a qualifying institution. It is the only route to
TikTok data that is both permitted by TikTok's terms and defensible to an
ethics board, which is why this project targets it rather than web scraping.

STATUS OF THIS FILE
-------------------
Every request is written against TikTok's published v2 specification and is
ready to run. It cannot be exercised end-to-end until real credentials exist,
so the module is written defensively: each response field is read through a
helper that tolerates absence, because the API omits fields rather than
returning nulls, and it has changed field names between versions before.

THE THREE ENDPOINTS USED
------------------------
  1. POST /v2/oauth/token/
        Exchanges the client key and secret for a short-lived bearer token.
  2. POST /v2/research/video/query/
        Searches videos by hashtag, date window and language. Paginated.
  3. POST /v2/research/video/comment/list/
        Lists comments for one video. Paginated.

THREE API CONSTRAINTS THAT SHAPE THIS CODE
------------------------------------------
  * A single video query may span AT MOST 30 DAYS. The study window is four
    months, so `search_posts` splits it into consecutive 30-day chunks and
    queries each one. This is handled automatically; the caller passes the
    full window.
  * Pagination is cursor-based and stateful: the response returns a `cursor`
    and a `search_id`, and BOTH must be echoed back to get the next page.
  * Rate limits are strict. Every request goes through `_request`, which
    applies a politeness delay and retries with exponential backoff on 429.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, Iterator, List, Optional

import requests

from ..models import Comment, Post, unix_to_iso
from .base import TikTokClient, TikTokClientError

logger = logging.getLogger(__name__)


# Fields requested from the video query endpoint. The API returns ONLY the
# fields named here, so this list is effectively part of the study's data
# schema. Requesting fewer fields also makes responses smaller and faster.
_VIDEO_FIELDS: List[str] = [
    "id",                # -> Post.post_id
    "username",          # -> Post.username
    "region_code",       # -> Post.author_region
    "create_time",       # -> Post.create_time (Unix seconds)
    "video_description", # -> Post.caption
    "hashtag_names",     # -> Post.hashtags
    "view_count",        # -> Post.view_count
    "like_count",        # -> Post.like_count
    "comment_count",     # -> Post.comment_count
    "share_count",       # -> Post.share_count
    "favorites_count",   # -> Post.favorites_count
    "video_tag",         # -> Post.has_ai_label / Post.ai_label_kind  (KEY FIELD)
    "video_duration",    # not stored, but useful for sanity-checking results
]

# Fields requested from the comment endpoint.
_COMMENT_FIELDS: List[str] = [
    "id",                 # -> Comment.comment_id
    "video_id",           # -> Comment.post_id
    "text",               # -> Comment.comment_text
    "like_count",         # -> Comment.like_count
    "reply_count",        # -> Comment.reply_count
    "parent_comment_id",  # -> Comment.parent_comment_id
    "create_time",        # -> Comment.create_time
]

# Field names the video-query endpoint accepts inside a query CONDITION.
# This is NOT the same list as the response fields above, and confusing the two
# is the single easiest way to get an opaque HTTP 400 from this API.
#
# Note in particular that there is NO language field here. Filtering the sample
# to English must therefore happen locally, after retrieval - see
# `src/language.py` and the note in `_build_search_body`.
_QUERYABLE_FIELDS = frozenset({
    "create_date", "username", "region_code", "video_id",
    "hashtag_name", "keyword", "music_id", "effect_id",
    "video_length", "view_count", "comment_count",
})

# TikTok caps a single video-query page at 100 results and a comment page at
# 100. Requesting the maximum minimises the number of round trips, which
# matters because the rate limit counts REQUESTS, not records.
_MAX_PAGE_SIZE = 100

# The API rejects any video query whose date range exceeds 30 days.
_MAX_QUERY_WINDOW_DAYS = 30


class ResearchApiClient(TikTokClient):
    """Live client for the TikTok Research API v2."""

    def __init__(
        self,
        client_key: str,
        client_secret: str,
        oauth_url: str,
        video_query_url: str,
        comment_query_url: str,
        request_delay_seconds: float = 1.0,
        max_retries: int = 3,
        daily_request_quota: int = 1000,
    ) -> None:
        """Store credentials and prepare a reusable HTTP session.

        Args:
            client_key:            OAuth client key from the research portal.
            client_secret:         OAuth client secret. Never logged.
            oauth_url:             Token endpoint.
            video_query_url:       Video search endpoint.
            comment_query_url:     Comment listing endpoint.
            request_delay_seconds: Pause inserted before each request.
            max_retries:           Attempts before abandoning a request.
        """
        self._client_key = client_key
        self._client_secret = client_secret
        self._oauth_url = oauth_url
        self._video_query_url = video_query_url
        self._comment_query_url = comment_query_url
        self._delay = request_delay_seconds
        self._max_retries = max_retries

        # TikTok's research tier permits roughly 1000 requests per 24 hours,
        # resetting at 00:00 UTC. Exceeding it does not merely fail - repeated
        # abuse can put institutional access at risk. Counting requests here
        # lets the client warn as the budget runs low and stop cleanly before
        # it is exhausted, rather than discovering the limit as a wall of
        # HTTP 429s mid-run.
        self._daily_request_quota = daily_request_quota
        self._requests_made = 0

        # A `Session` reuses the underlying TCP connection across requests.
        # Over a multi-thousand-request collection run this removes a full
        # TLS handshake per call - a large saving in both time and load on
        # TikTok's servers.
        self._session = requests.Session()

        # Access tokens are short-lived (about two hours). We cache the token
        # and its expiry, and refresh only when needed.
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # -----------------------------------------------------------------
    # Authentication
    # -----------------------------------------------------------------

    def _get_access_token(self) -> str:
        """Return a valid bearer token, refreshing it if necessary.

        Uses the OAuth 2.0 *client credentials* grant: the application
        authenticates as itself, with no end user involved. That is the correct
        grant type for server-to-server research data collection.

        Returns:
            The bearer token string.

        Raises:
            TikTokClientError: If authentication fails.
        """
        # Refresh 60 seconds BEFORE the stated expiry. Without that margin, a
        # token can expire between the check and the request arriving at
        # TikTok's servers, producing a spurious 401 mid-run.
        if self._access_token and time.time() < (self._token_expires_at - 60):
            return self._access_token

        logger.info("Requesting a new TikTok Research API access token.")

        try:
            response = self._session.post(
                self._oauth_url,
                headers={
                    # This endpoint requires form encoding, NOT JSON - a
                    # common and confusing source of 400 errors.
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Cache-Control": "no-cache",
                },
                data={
                    "client_key": self._client_key,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise TikTokClientError(
                f"Network error while requesting an access token: {exc}"
            ) from exc

        if response.status_code != 200:
            # The response body is deliberately NOT logged in full: on some
            # failures TikTok echoes part of the submitted credentials back.
            raise TikTokClientError(
                f"Authentication failed with HTTP {response.status_code}. "
                "Check TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in .env, and "
                "confirm your research application is still approved."
            )

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise TikTokClientError(
                "Authentication response contained no access_token field."
            )

        # `expires_in` is a duration in seconds; convert to an absolute time so
        # the comparison above is a simple clock check.
        expires_in = int(payload.get("expires_in", 7200))
        self._access_token = token
        self._token_expires_at = time.time() + expires_in

        logger.info("Access token obtained; valid for %d seconds.", expires_in)
        return token

    # -----------------------------------------------------------------
    # Generic request helper - rate limiting, retries, error translation
    # -----------------------------------------------------------------

    def _request(self, url: str, params: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
        """Perform one authenticated POST, with retries, and return the JSON.

        Every API call in this class routes through here, so rate limiting,
        retry policy and error handling are implemented exactly once.

        Args:
            url:    Full endpoint URL.
            params: Query-string parameters (the `fields` list lives here).
            body:   JSON request body.

        Returns:
            The parsed `data` section of the response.

        Raises:
            TikTokClientError: After all retries are exhausted, or on an error
                               that retrying cannot fix (such as a 400).
        """
        # Stop cleanly at the daily quota rather than hammering the API into
        # returning 429s. Raising here means the pipeline records everything
        # collected so far and exits tidily; the run can resume tomorrow after
        # the quota resets at 00:00 UTC, skipping what is already on disk.
        if self._requests_made >= self._daily_request_quota:
            raise TikTokClientError(
                f"Daily API request quota of {self._daily_request_quota} has "
                "been reached. Data collected so far is saved. The quota "
                "resets at 00:00 UTC; re-run then and collection will resume "
                "from where it stopped."
            )

        self._requests_made += 1

        # Warn once at 80% so a long run gives advance notice rather than
        # stopping without warning.
        if self._requests_made == int(self._daily_request_quota * 0.8):
            logger.warning(
                "80%% of the daily API quota used (%d of %d requests).",
                self._requests_made, self._daily_request_quota,
            )

        last_error = "unknown error"

        for attempt in range(1, self._max_retries + 1):
            # Politeness delay before EVERY request, including the first.
            # Being a well-behaved API consumer is both a courtesy and a
            # practical necessity: institutional access can be revoked for
            # aggressive querying.
            time.sleep(self._delay)

            token = self._get_access_token()

            try:
                response = self._session.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    params=params,
                    json=body,
                    timeout=60,
                )
            except requests.RequestException as exc:
                # Transient network problems (DNS blips, dropped connections)
                # are worth retrying; record and continue.
                last_error = f"network error: {exc}"
                logger.warning("Attempt %d/%d failed: %s", attempt, self._max_retries, last_error)
                self._backoff(attempt)
                continue

            # --- 200 OK: the normal path --------------------------------
            if response.status_code == 200:
                payload = response.json()
                # TikTok returns HTTP 200 even for some application-level
                # errors, signalling them in an `error` object instead. Missing
                # that would mean silently recording zero results as success.
                error = payload.get("error", {})
                if error and error.get("code") not in (None, "ok"):
                    raise TikTokClientError(
                        f"API returned an error: {error.get('code')} - "
                        f"{error.get('message')}"
                    )
                return payload.get("data", {})

            # --- 429 Too Many Requests: back off and retry ---------------
            if response.status_code == 429:
                last_error = "rate limited (HTTP 429)"
                logger.warning(
                    "Rate limited on attempt %d/%d; backing off.",
                    attempt, self._max_retries,
                )
                self._backoff(attempt)
                continue

            # --- 401 Unauthorized: token may have been revoked early ------
            if response.status_code == 401:
                logger.warning("Token rejected (HTTP 401); forcing a refresh.")
                self._access_token = None      # discard the cached token
                last_error = "unauthorized (HTTP 401)"
                self._backoff(attempt)
                continue

            # --- 5xx: TikTok-side problem, worth retrying -----------------
            if 500 <= response.status_code < 600:
                last_error = f"server error (HTTP {response.status_code})"
                logger.warning(
                    "Server error on attempt %d/%d: %s",
                    attempt, self._max_retries, last_error,
                )
                self._backoff(attempt)
                continue

            # --- Anything else (400, 403): retrying will not help ---------
            raise TikTokClientError(
                f"Request to {url} failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        raise TikTokClientError(
            f"Request to {url} failed after {self._max_retries} attempts. "
            f"Last error: {last_error}"
        )

    def _backoff(self, attempt: int) -> None:
        """Sleep for an exponentially increasing interval between retries.

        Waits 2, 4, 8, 16... seconds. Exponential backoff is the standard
        response to rate limiting: retrying immediately would add load to a
        service that has just told us it is overloaded, and would burn through
        the retry budget in under a second.
        """
        wait_seconds = 2 ** attempt
        logger.info("Backing off for %d seconds before retrying.", wait_seconds)
        time.sleep(wait_seconds)

    # -----------------------------------------------------------------
    # Interface method 1: video search
    # -----------------------------------------------------------------

    def search_posts(
        self,
        hashtags: List[str],
        start_date: date,
        end_date: date,
        language: str,
        max_posts: int,
    ) -> Iterator[Post]:
        """Search videos by hashtag, date window and language.

        Automatically splits the requested window into 30-day chunks (an API
        limit) and paginates within each chunk until `max_posts` posts have
        been yielded or the data runs out.
        """
        logger.info(
            "Searching TikTok Research API: tags=%s window=%s..%s lang=%s target=%d",
            hashtags, start_date, end_date, language, max_posts,
        )

        yielded = 0

        for chunk_start, chunk_end in self._split_date_window(start_date, end_date):
            if yielded >= max_posts:
                break

            logger.info("Querying window %s .. %s", chunk_start, chunk_end)

            # Cursor-based pagination state, reset for each new date window.
            cursor: Optional[int] = None
            search_id: Optional[str] = None

            while yielded < max_posts:
                body = self._build_search_body(
                    hashtags=hashtags,
                    language=language,
                    start_date=chunk_start,
                    end_date=chunk_end,
                    # Never request more than we still need, so the final page
                    # is not needlessly large.
                    page_size=min(_MAX_PAGE_SIZE, max_posts - yielded),
                    cursor=cursor,
                    search_id=search_id,
                )

                data = self._request(
                    url=self._video_query_url,
                    params={"fields": ",".join(_VIDEO_FIELDS)},
                    body=body,
                )

                videos = data.get("videos", [])
                if not videos:
                    logger.info("No further results in this window.")
                    break

                for raw_video in videos:
                    if yielded >= max_posts:
                        break
                    post = self._parse_post(raw_video)
                    if post is not None:
                        yielded += 1
                        yield post

                # `has_more` is the authoritative end-of-results signal.
                if not data.get("has_more", False):
                    break

                # Carry BOTH values forward: TikTok's pagination is stateful
                # and rejects a cursor presented without its search_id.
                cursor = data.get("cursor")
                search_id = data.get("search_id")

        logger.info("Search complete: %d posts retrieved.", yielded)

    def _split_date_window(self, start: date, end: date) -> List[tuple]:
        """Split a date range into consecutive chunks of at most 30 days.

        The API rejects any query spanning more than 30 days, but the study
        window is four months. Splitting here means the rest of the codebase
        can reason about the full study period and stay unaware of this limit.

        Returns:
            A list of (chunk_start, chunk_end) date pairs covering [start, end]
            exactly once, with no gaps and no overlaps. Overlaps would matter:
            they would produce duplicate posts and inflate the sample.
        """
        chunks: List[tuple] = []
        cursor = start

        while cursor <= end:
            # Subtract one day because both endpoints are inclusive: a window
            # from the 1st to the 30th is 30 days, not 31.
            chunk_end = min(cursor + timedelta(days=_MAX_QUERY_WINDOW_DAYS - 1), end)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end + timedelta(days=1)

        logger.debug("Split %s..%s into %d query window(s).", start, end, len(chunks))
        return chunks

    def _build_search_body(
        self,
        hashtags: List[str],
        language: str,
        start_date: date,
        end_date: date,
        page_size: int,
        cursor: Optional[int],
        search_id: Optional[str],
    ) -> Dict[str, Any]:
        """Assemble the JSON body for one video-query request.

        The API's query language is a boolean tree of conditions. We build:

            (hashtag IN [our tags])  AND  (language IN [en])

        Within a single condition the list of values is an OR, so this asks
        for: a post carrying ANY of the study hashtags.

        WHY LANGUAGE IS NOT FILTERED HERE
        ---------------------------------
        The `language` argument is accepted (to satisfy the shared client
        interface) but deliberately NOT turned into a query condition.

        The video-query endpoint accepts only the field names listed in
        `_QUERYABLE_FIELDS`, and none of them describes language. Sending a
        `video_language` condition - which an earlier version of this file did -
        produces an opaque HTTP 400 on every single request.

        Two alternatives were rejected:

          * Filtering by `region_code` to English-speaking countries would be
            a proxy, not a language filter, and a badly biased one for THIS
            study: it would systematically exclude English-language posts from
            Iran, Israel and the wider region, which are among the most
            important cases in the sample.

          * Dropping the language criterion entirely would change the study
            design.

        Instead, language is determined locally from the caption text after
        retrieval (see `src/language.py`), which the pipeline applies as an
        inclusion criterion. This costs API quota - non-English posts are
        fetched and then discarded - which is accounted for in the quota
        guidance in the README.
        """
        conditions: List[Dict[str, Any]] = [
            {
                "operation": "IN",
                "field_name": "hashtag_name",
                # The API expects bare tag names, without '#'. `config.py`
                # already strips any hash the researcher typed.
                "field_values": hashtags,
            }
        ]

        # Defensive guard: if a future edit adds a condition on a field the
        # endpoint does not accept, fail here with a clear message rather than
        # sending it and receiving an unexplained 400 from TikTok.
        for condition in conditions:
            field_name = condition["field_name"]
            if field_name not in _QUERYABLE_FIELDS:
                raise TikTokClientError(
                    f"'{field_name}' is not a queryable field on the video "
                    f"endpoint. Valid fields: {sorted(_QUERYABLE_FIELDS)}"
                )

        body: Dict[str, Any] = {
            "query": {"and": conditions},
            # The API wants compact dates: "20260401", not "2026-04-01".
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
            "max_count": page_size,
        }

        # Pagination fields are included only on follow-up requests. Sending a
        # null cursor on the first request causes a 400.
        if cursor is not None:
            body["cursor"] = cursor
        if search_id is not None:
            body["search_id"] = search_id

        return body

    def _parse_post(self, raw: Dict[str, Any]) -> Optional[Post]:
        """Translate one raw API video object into a `Post`.

        Returns None (rather than raising) when the record is unusable, so a
        single malformed row cannot abort a multi-hour collection run. The
        skip is logged so it remains visible and countable.
        """
        post_id = str(raw.get("id", "")).strip()
        if not post_id:
            logger.warning("Skipping a video record with no id field.")
            return None

        username = str(raw.get("username", "")).strip()

        # `hashtag_names` is absent (not empty) when a post has no hashtags,
        # so a plain `raw["hashtag_names"]` would raise KeyError.
        hashtags = raw.get("hashtag_names") or []

        has_ai_label, ai_label_kind = self._extract_ai_label(raw)

        return Post(
            post_id=post_id,
            username=username,
            author_region=str(raw.get("region_code", "") or ""),
            create_time=unix_to_iso(raw.get("create_time")),
            caption=str(raw.get("video_description", "") or ""),
            hashtags=[str(tag).lstrip("#") for tag in hashtags],
            # The Research API does not return a canonical URL, so we build the
            # standard public permalink from the two components it does return.
            video_url=f"https://www.tiktok.com/@{username}/video/{post_id}",
            has_ai_label=has_ai_label,
            ai_label_kind=ai_label_kind,
            view_count=int(raw.get("view_count", 0) or 0),
            like_count=int(raw.get("like_count", 0) or 0),
            comment_count=int(raw.get("comment_count", 0) or 0),
            share_count=int(raw.get("share_count", 0) or 0),
            favorites_count=int(raw.get("favorites_count", 0) or 0),
        )

    @staticmethod
    def _extract_ai_label(raw: Dict[str, Any]) -> tuple:
        """Determine whether, and how, TikTok labelled this post as AI content.

        This method produces the study's key dependent variable, so it is worth
        understanding precisely.

        THE SOURCE FIELD
        ----------------
        The Research API exposes content labels through `video_tag`, a struct
        (or list of structs) of the form:

            {"type": "AIGC",    "number": 1}   Creator Labelled as AI-Generated
            {"type": "AIGC",    "number": 2}   AI-Generated
            {"type": "Branded", "number": 1}   Paid Partnership
            {"type": "Branded", "number": 7}   Creator Earns Commission

        Only the AIGC type concerns this study; Branded tags are commercial
        disclosures and are ignored.

        WHY THE TWO AIGC VALUES ARE KEPT SEPARATE
        -----------------------------------------
        number=1 is the creator's own voluntary disclosure - a measure of
        creator behaviour. number=2 is applied by TikTok, typically from C2PA
        content credentials embedded by the generating tool - a measure of
        provenance metadata. They answer different research questions, and
        collapsing them into one boolean at collection time discards the
        distinction permanently. Both are returned; `has_ai_label` is True for
        either.

        The implementation tolerates `video_tag` arriving as a single object, a
        list of objects, or being absent entirely, because the API is
        documented loosely on this point and the shape has changed before.

        Returns:
            A tuple of (has_ai_label, ai_label_kind), where ai_label_kind is
            "creator_labelled", "ai_generated" or "".
        """
        video_tag = raw.get("video_tag")
        if not video_tag:
            # No tags at all. Note the ambiguity this leaves: the absence of a
            # label is not evidence that the content is authentic.
            return False, ""

        # Normalise to a list so a single struct and a list of structs are
        # handled by exactly the same code below.
        tags = video_tag if isinstance(video_tag, list) else [video_tag]

        found_creator_labelled = False
        found_ai_generated = False

        for tag in tags:
            if not isinstance(tag, dict):
                continue

            # Compare case-insensitively: the documentation writes "AIGC", but
            # relying on exact casing for the study's key variable would be
            # fragile.
            if str(tag.get("type", "")).strip().upper() != "AIGC":
                continue  # a Branded tag or something unrecognised

            try:
                number = int(tag.get("number", 0))
            except (TypeError, ValueError):
                logger.warning(
                    "Unparseable video_tag number %r on post %s; treating the "
                    "post as AI-labelled but of unknown kind.",
                    tag.get("number"), raw.get("id"),
                )
                found_ai_generated = True
                continue

            if number == 1:
                found_creator_labelled = True
            elif number == 2:
                found_ai_generated = True
            else:
                # An AIGC tag with an unexpected number still means an AI label
                # is present. Log it so a new value TikTok introduces is
                # noticed rather than silently mapped to "no label".
                logger.warning(
                    "Unrecognised AIGC video_tag number %d on post %s. "
                    "Recording it as AI-labelled; check the API docs for a "
                    "newly added label type.", number, raw.get("id"),
                )
                found_ai_generated = True

        if found_ai_generated:
            # Platform-applied detection takes precedence when both are present,
            # because it is the stronger evidence of provenance.
            return True, "ai_generated"
        if found_creator_labelled:
            return True, "creator_labelled"
        return False, ""

    # -----------------------------------------------------------------
    # Interface method 2: comments
    # -----------------------------------------------------------------

    def fetch_comments(self, post_id: str, max_comments: int) -> Iterator[Comment]:
        """Yield up to `max_comments` comments for one video.

        Pagination here is simple offset-based cursoring: each response reports
        how many records remain, and the next request supplies the running
        offset.
        """
        logger.debug("Fetching up to %d comments for post %s.", max_comments, post_id)

        cursor = 0
        yielded = 0

        while yielded < max_comments:
            body = {
                "video_id": post_id,
                "max_count": min(_MAX_PAGE_SIZE, max_comments - yielded),
                "cursor": cursor,
            }

            try:
                data = self._request(
                    url=self._comment_query_url,
                    params={"fields": ",".join(_COMMENT_FIELDS)},
                    body=body,
                )
            except TikTokClientError as exc:
                # A post with comments disabled, or one deleted between the
                # search and this call, is a NORMAL observation - not a reason
                # to abort the run. Log it and move on with zero comments.
                logger.warning(
                    "Could not fetch comments for post %s (%s). "
                    "Recording it with zero comments.",
                    post_id, exc,
                )
                return

            comments = data.get("comments", [])
            if not comments:
                break

            for raw_comment in comments:
                if yielded >= max_comments:
                    break
                parsed = self._parse_comment(raw_comment, post_id)
                if parsed is not None:
                    yielded += 1
                    yield parsed

            if not data.get("has_more", False):
                break

            cursor = data.get("cursor", cursor + len(comments))

        logger.debug("Retrieved %d comments for post %s.", yielded, post_id)

    def _parse_comment(self, raw: Dict[str, Any], post_id: str) -> Optional[Comment]:
        """Translate one raw API comment object into a `Comment`."""
        comment_id = str(raw.get("id", "")).strip()
        if not comment_id:
            logger.warning("Skipping a comment record with no id on post %s.", post_id)
            return None

        return Comment(
            comment_id=comment_id,
            # Prefer the API's own video_id, but fall back to the post we
            # asked about, so the foreign key is never left empty.
            post_id=str(raw.get("video_id", "") or post_id),
            comment_text=str(raw.get("text", "") or ""),
            create_time=unix_to_iso(raw.get("create_time")),
            like_count=int(raw.get("like_count", 0) or 0),
            reply_count=int(raw.get("reply_count", 0) or 0),
            parent_comment_id=str(raw.get("parent_comment_id", "") or ""),
        )

    # -----------------------------------------------------------------
    # Interface method 3 and cleanup
    # -----------------------------------------------------------------

    def resolve_video_download_url(self, post: Post) -> Optional[str]:
        """Return None: the Research API does not supply direct media URLs.

        See `base.TikTokClient.resolve_video_download_url` for the full
        explanation. In short: the Research API returns metadata only, and
        fetching from TikTok's media CDN is restricted by the Terms of Service.
        Confirm which media-access route your institution's agreement permits,
        then implement that route here.
        """
        logger.debug(
            "No media URL available for post %s - the Research API returns "
            "metadata only.", post.post_id,
        )
        return None

    def close(self) -> None:
        """Close the HTTP session and release its connection pool."""
        self._session.close()
        logger.debug("HTTP session closed.")
