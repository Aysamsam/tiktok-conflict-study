"""
Live data source: the official TikTok Research API.

TikTok operates a Research API for academic institutions
(https://developers.tiktok.com/products/research-api/). Access requires an
approved application from a qualifying institution - the only route to
TikTok data that is both permitted by TikTok's terms and defensible to an
ethics board.

Every request here is written against TikTok's published v2 specification.
It cannot be exercised end-to-end without real credentials, so each response
field is read through a helper that tolerates absence, since the API omits
fields rather than returning nulls and has changed field names before.

The three endpoints used:
  1. POST /v2/oauth/token/                          exchange credentials for a bearer token
  2. POST /v2/research/video/query/                 search videos by hashtag, date window, language
  3. POST /v2/research/video/comment/list/          list comments for one video

Three API constraints shape this code:
  * A single video query may span at most 30 days. `search_posts` splits the
    study's 4-month window into consecutive 30-day chunks automatically.
  * Pagination is cursor-based and stateful: the response returns a `cursor`
    and a `search_id`, and both must be echoed back for the next page.
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


# Fields requested from the video query endpoint. The API returns only the
# fields named here, so this list is effectively part of the study's schema.
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
    "video_tag",         # -> Post.has_ai_label / Post.ai_label_kind  (key field)
    "video_duration",    # not stored; used only for sanity-checking results
]

# Fields requested from the comment endpoint.
_COMMENT_FIELDS: List[str] = [
    "id",                  # -> Comment.comment_id
    "video_id",            # -> Comment.post_id
    "text",                # -> Comment.comment_text
    "like_count",          # -> Comment.like_count
    "reply_count",         # -> Comment.reply_count
    "parent_comment_id",   # -> Comment.parent_comment_id
    "create_time",         # -> Comment.create_time
]

# Field names the video-query endpoint accepts inside a query CONDITION.
# This is a different list from the response fields above - conflating the
# two produces an opaque HTTP 400.
#
# There is no language field here. Filtering to English therefore happens
# locally, after retrieval - see src/language.py and `_build_search_body`.
_QUERYABLE_FIELDS = frozenset({
    "create_date", "username", "region_code", "video_id",
    "hashtag_name", "keyword", "music_id", "effect_id",
    "video_length", "view_count", "comment_count",
})

# TikTok caps a single video-query or comment page at 100 results.
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
            daily_request_quota:   Requests permitted per 24h.
        """
        self._client_key = client_key
        self._client_secret = client_secret
        self._oauth_url = oauth_url
        self._video_query_url = video_query_url
        self._comment_query_url = comment_query_url
        self._delay = request_delay_seconds
        self._max_retries = max_retries

        # Counting requests lets the client warn as the budget runs low and
        # stop cleanly, rather than hitting a wall of HTTP 429s mid-run.
        self._daily_request_quota = daily_request_quota
        self._requests_made = 0

        # Reusing a Session avoids a fresh TLS handshake on every request.
        self._session = requests.Session()

        # Access tokens are short-lived (~2 hours); cached and refreshed as needed.
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # -----------------------------------------------------------------
    # Authentication
    # -----------------------------------------------------------------

    def _get_access_token(self) -> str:
        """Return a valid bearer token, refreshing it if necessary.

        Uses the OAuth 2.0 client-credentials grant - the correct grant type
        for server-to-server research data collection.

        Returns:
            The bearer token string.

        Raises:
            TikTokClientError: If authentication fails.
        """
        # Refresh 60 seconds before the stated expiry so a token cannot
        # expire between the check and the request arriving at the server.
        if self._access_token and time.time() < (self._token_expires_at - 60):
            return self._access_token

        logger.info("Requesting a new TikTok Research API access token.")

        try:
            response = self._session.post(
                self._oauth_url,
                headers={
                    # This endpoint requires form encoding, not JSON.
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
            # The response body is deliberately not logged: on some failures
            # TikTok echoes part of the submitted credentials back.
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

        Every API call routes through here, so rate limiting, retry policy
        and error handling are implemented exactly once.

        Args:
            url:    Full endpoint URL.
            params: Query-string parameters (the `fields` list lives here).
            body:   JSON request body.

        Returns:
            The parsed `data` section of the response.

        Raises:
            TikTokClientError: After all retries are exhausted, or on an
                               error that retrying cannot fix (e.g. a 400).
        """
        if self._requests_made >= self._daily_request_quota:
            raise TikTokClientError(
                f"Daily API request quota of {self._daily_request_quota} has "
                "been reached. Data collected so far is saved. The quota "
                "resets at 00:00 UTC; re-run then and collection will resume "
                "from where it stopped."
            )

        self._requests_made += 1

        if self._requests_made == int(self._daily_request_quota * 0.8):
            logger.warning(
                "80%% of the daily API quota used (%d of %d requests).",
                self._requests_made, self._daily_request_quota,
            )

        last_error = "unknown error"

        for attempt in range(1, self._max_retries + 1):
            # Politeness delay before every request, including the first.
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
                last_error = f"network error: {exc}"
                logger.warning("Attempt %d/%d failed: %s", attempt, self._max_retries, last_error)
                self._backoff(attempt)
                continue

            # --- 200 OK: the normal path --------------------------------
            if response.status_code == 200:
                payload = response.json()
                # TikTok returns HTTP 200 even for some application-level
                # errors, signalled in an `error` object instead.
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
                self._access_token = None
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
        """Sleep for an exponentially increasing interval between retries (2, 4, 8, 16... s)."""
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
        keywords: Optional[List[str]] = None,
        random_order: bool = False,
    ) -> Iterator[Post]:
        """Search videos by hashtag (or keyword), date window and language.

        Splits the requested window into 30-day chunks (an API limit) and
        paginates within each chunk - but only up to that chunk's fair
        share of `max_posts`, not the whole budget. Without `random_order`,
        TikTok returns results within a chunk in decreasing video-ID order -
        effectively newest-first, since IDs are roughly chronological - so
        without per-chunk budgeting a single busy chunk (e.g. the most
        recent 30 days of a multi-month window) can consume the entire
        budget before later chunks are ever queried, silently turning a
        "search the whole window" request into "search the first chunk that
        has enough volume." Each chunk's unfilled share rolls forward to the
        remaining chunks, so a quiet chunk doesn't waste budget either.

        `random_order=True` asks the API itself for a random sample instead
        of ID order, which addresses the same clustering problem from a
        different angle - worth verifying live whether it can still be
        paginated with cursor/search_id the normal way, or whether it only
        ever returns a single capped batch; TikTok's docs describe it as
        returning "1-100 videos" without being explicit either way.
        """
        logger.info(
            "Searching TikTok Research API: tags=%s keywords=%s window=%s..%s lang=%s "
            "target=%d random_order=%s",
            hashtags, keywords or [], start_date, end_date, language, max_posts, random_order,
        )

        yielded = 0
        chunks = self._split_date_window(start_date, end_date)

        for chunk_index, (chunk_start, chunk_end) in enumerate(chunks):
            if yielded >= max_posts:
                break

            remaining_budget = max_posts - yielded
            remaining_chunks = len(chunks) - chunk_index
            # Ceiling division: any chunk that comes up short leaves its
            # slack for the chunks after it, rather than being lost.
            chunk_budget = -(-remaining_budget // remaining_chunks)
            chunk_yielded = 0

            logger.info(
                "Querying window %s .. %s (budget: %d of %d remaining)",
                chunk_start, chunk_end, chunk_budget, remaining_budget,
            )

            cursor: Optional[int] = None
            search_id: Optional[str] = None

            while chunk_yielded < chunk_budget and yielded < max_posts:
                body = self._build_search_body(
                    hashtags=hashtags,
                    language=language,
                    start_date=chunk_start,
                    end_date=chunk_end,
                    page_size=min(_MAX_PAGE_SIZE, chunk_budget - chunk_yielded),
                    cursor=cursor,
                    search_id=search_id,
                    keywords=keywords,
                    random_order=random_order,
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
                    if chunk_yielded >= chunk_budget or yielded >= max_posts:
                        break
                    post = self._parse_post(raw_video)
                    if post is not None:
                        yielded += 1
                        chunk_yielded += 1
                        yield post

                # `has_more` is the authoritative end-of-results signal.
                if not data.get("has_more", False):
                    break

                # Both values must be carried forward: TikTok's pagination
                # is stateful and rejects a cursor without its search_id.
                cursor = data.get("cursor")
                search_id = data.get("search_id")

        logger.info("Search complete: %d posts retrieved.", yielded)

    def _split_date_window(self, start: date, end: date) -> List[tuple]:
        """Split a date range into consecutive chunks of at most 30 days.

        Returns:
            A list of (chunk_start, chunk_end) pairs covering [start, end]
            exactly once, with no gaps or overlaps (an overlap would
            duplicate posts and inflate the sample).
        """
        chunks: List[tuple] = []
        cursor = start

        while cursor <= end:
            # Both endpoints are inclusive: 1st to 30th is 30 days, not 31.
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
        keywords: Optional[List[str]] = None,
        random_order: bool = False,
    ) -> Dict[str, Any]:
        """Assemble the JSON body for one video-query request.

        With hashtags but no keywords, builds `hashtag_name IN [our tags]`;
        within that condition the value list is an OR, so this asks for a
        post carrying any of the study hashtags.

        With keywords but no hashtags, builds `keyword IN [our terms]`
        instead - a free-text match against the caption, closer to typing a
        phrase into TikTok's own search bar than matching a formal hashtag.

        When both are given, the query widens to
        `(hashtag_name IN [tags]) OR (keyword IN [terms])`, so a post
        qualifies by carrying a study hashtag OR mentioning a study keyword
        in its caption - this is deliberately a broadening OR, not a
        narrowing AND, so it does not shrink the sample the way stacking two
        AND conditions would. At least one of hashtags or keywords must be
        non-empty; config.py enforces this at start-up.

        The `language` argument is accepted (to satisfy the shared client
        interface) but is not turned into a query condition: the endpoint
        accepts only the fields in `_QUERYABLE_FIELDS`, and none describes
        language. Sending a `video_language` condition produces an opaque
        HTTP 400 on every request. Filtering by `region_code` was rejected
        as a proxy - it would be badly biased for this study, excluding
        English-language posts from Iran, Israel and the wider region.
        Language is instead determined locally from the caption text after
        retrieval; see `src/language.py`.
        """
        conditions: List[Dict[str, Any]] = []

        if hashtags:
            conditions.append({
                "operation": "IN",
                "field_name": "hashtag_name",
                # config.py already strips any '#' the researcher typed.
                "field_values": hashtags,
            })

        if keywords:
            conditions.append({
                "operation": "IN",
                "field_name": "keyword",
                "field_values": keywords,
            })

        if not conditions:
            raise TikTokClientError(
                "Cannot search: both hashtags and keywords are empty."
            )

        # Defensive guard: fail here with a clear message if a future edit
        # adds a condition on a field the endpoint does not accept, rather
        # than sending it and getting an unexplained 400 from TikTok.
        for condition in conditions:
            field_name = condition["field_name"]
            if field_name not in _QUERYABLE_FIELDS:
                raise TikTokClientError(
                    f"'{field_name}' is not a queryable field on the video "
                    f"endpoint. Valid fields: {sorted(_QUERYABLE_FIELDS)}"
                )

        # A single condition is wrapped in "and" (harmless with only one
        # member); two or more become "or" so hashtags and keywords widen
        # the search instead of both being required at once.
        combinator = "and" if len(conditions) == 1 else "or"

        body: Dict[str, Any] = {
            "query": {combinator: conditions},
            # The API wants compact dates: "20260401", not "2026-04-01".
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
            "max_count": page_size,
        }

        # Only sent when true: omitting it entirely (rather than sending
        # false) keeps the request identical to before this option existed
        # for anyone not using it.
        if random_order:
            body["is_random"] = True

        # Pagination fields only on follow-up requests: a null cursor on the
        # first request causes a 400.
        if cursor is not None:
            body["cursor"] = cursor
        if search_id is not None:
            body["search_id"] = search_id

        return body

    def _parse_post(self, raw: Dict[str, Any]) -> Optional[Post]:
        """Translate one raw API video object into a `Post`.

        Returns None (rather than raising) when the record is unusable, so
        a single malformed row cannot abort a multi-hour run. The skip is
        logged so it stays visible and countable.
        """
        post_id = str(raw.get("id", "")).strip()
        if not post_id:
            logger.warning("Skipping a video record with no id field.")
            return None

        username = str(raw.get("username", "")).strip()

        # hashtag_names is absent (not empty) when a post has no hashtags.
        hashtags = raw.get("hashtag_names") or []

        # Logs the raw field exactly as TikTok sent it, before any parsing,
        # so a run with LOG_LEVEL=DEBUG can confirm whether the API is
        # returning video_tag data at all - not just what this code decided
        # to do with it. See the README's note on verifying has_ai_label.
        logger.debug("post %s raw video_tag: %r", post_id, raw.get("video_tag"))

        has_ai_label, ai_label_kind = self._extract_ai_label(raw)

        return Post(
            post_id=post_id,
            username=username,
            author_region=str(raw.get("region_code", "") or ""),
            create_time=unix_to_iso(raw.get("create_time")),
            caption=str(raw.get("video_description", "") or ""),
            hashtags=[str(tag).lstrip("#") for tag in hashtags],
            # The API does not return a canonical URL; build the standard
            # public permalink from the two components it does return.
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

        This produces the study's key dependent variable. The Research API
        exposes content labels through `video_tag`, a struct (or list of
        structs):

            {"type": "AIGC",    "number": 1}   Creator Labelled as AI-Generated
            {"type": "AIGC",    "number": 2}   AI-Generated
            {"type": "Branded", "number": 1}   Paid Partnership
            {"type": "Branded", "number": 7}   Creator Earns Commission

        Only AIGC concerns this study; Branded tags are commercial
        disclosures and are ignored. The two AIGC values are kept separate:
        number=1 is the creator's own voluntary disclosure (creator
        behaviour); number=2 is applied by TikTok itself, typically from
        C2PA content credentials (provenance). Both set `has_ai_label`.

        `video_tag` is tolerated as a single object, a list of objects, or
        absent entirely, since the API's documentation is loose on this
        point and the shape has changed before.

        Returns:
            A tuple of (has_ai_label, ai_label_kind), where ai_label_kind is
            "creator_labelled", "ai_generated" or "".
        """
        video_tag = raw.get("video_tag")
        if not video_tag:
            # Absence of a label is not evidence the content is authentic.
            return False, ""

        tags = video_tag if isinstance(video_tag, list) else [video_tag]

        found_creator_labelled = False
        found_ai_generated = False

        for tag in tags:
            if not isinstance(tag, dict):
                continue

            # Live responses have been observed sending "AIGC Type", not the
            # bare "AIGC" the docs show - match on prefix so both are caught.
            if not str(tag.get("type", "")).strip().upper().startswith("AIGC"):
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
                # A new AIGC number should be noticed, not mapped silently
                # to "no label".
                logger.warning(
                    "Unrecognised AIGC video_tag number %d on post %s. "
                    "Recording it as AI-labelled; check the API docs for a "
                    "newly added label type.", number, raw.get("id"),
                )
                found_ai_generated = True

        if found_ai_generated:
            # Platform-applied detection takes precedence as the stronger
            # evidence of provenance.
            return True, "ai_generated"
        if found_creator_labelled:
            return True, "creator_labelled"
        return False, ""

    # -----------------------------------------------------------------
    # Interface method 2: comments
    # -----------------------------------------------------------------

    def fetch_comments(self, post_id: str, max_comments: int) -> Iterator[Comment]:
        """Yield up to `max_comments` comments for one video.

        Pagination is simple offset-based cursoring: each response reports
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
                # A post with comments disabled, or deleted between the
                # search and this call, is a normal observation.
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
            # Prefer the API's own video_id, falling back to the post asked
            # about so the foreign key is never empty.
            post_id=str(raw.get("video_id", "") or post_id),
            comment_text=str(raw.get("text", "") or ""),
            create_time=unix_to_iso(raw.get("create_time")),
            like_count=int(raw.get("like_count", 0) or 0),
            reply_count=int(raw.get("reply_count", 0) or 0),
            parent_comment_id=str(raw.get("parent_comment_id", "") or ""),
        )

    # -----------------------------------------------------------------
    # -----------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------

    def close(self) -> None:
        """Close the HTTP session and release its connection pool."""
        self._session.close()
        logger.debug("HTTP session closed.")
