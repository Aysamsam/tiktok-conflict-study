"""
Data models: the exact shape of one post and one comment.

`Post` and `Comment` are dataclasses rather than raw dictionaries, so that a
typo in a field name fails immediately instead of producing a silently wrong
CSV column. Each client implementation translates its own response format
into these classes, so the rest of the pipeline works with one stable
vocabulary regardless of data source.

`POST_CSV_COLUMNS` and `COMMENT_CSV_COLUMNS` below are the authoritative
schema: the CSV writer's header and column order are read from them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def unix_to_iso(unix_seconds: Optional[int]) -> str:
    """Convert a Unix timestamp into an ISO 8601 UTC string.

    Args:
        unix_seconds: Seconds since the Unix epoch, or None when the API
                      omitted the field.

    Returns:
        An ISO 8601 string, e.g. "2026-06-14T09:31:07+00:00", or "" when the
        input was None.
    """
    if unix_seconds is None:
        return ""
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Post
# ---------------------------------------------------------------------------

@dataclass
class Post:
    """One TikTok video and all metadata the study records about it."""

    # --- Identity ---------------------------------------------------------
    post_id: str
    """TikTok's unique video identifier; also the key linking to the
    comments table and to `<post_id>.mp4`. Stored as a string, never an
    integer - these IDs are 19 digits long and spreadsheet software silently
    rounds integers that large."""

    username: str
    """The author's public @handle, without the leading '@'."""

    author_region: str
    """Two-letter country code of the author's registered region (API field
    `region_code`), when available. Empty string otherwise."""

    create_time: str
    """Publication timestamp, ISO 8601 UTC. See `unix_to_iso` above."""

    # --- Content ----------------------------------------------------------
    caption: str
    """The full caption text as written by the author, stored verbatim."""

    hashtags: List[str]
    """Hashtag names attached to the post, without '#'."""

    video_url: str
    """Canonical public permalink:
    https://www.tiktok.com/@<username>/video/<post_id>"""

    has_ai_label: bool
    """True when TikTok displayed an AI-generated-content label on the post
    at the time of collection. Derived from the Research API's `video_tag`
    field, where an entry of type "AIGC" indicates an AI label - see
    `ai_label_kind` for which of the two AIGC labels was present.

    This is a measure of disclosure, not ground truth about whether the
    video is synthetic, and the label can be added or removed after
    publication - hence `collected_at` on every row."""

    # --- Engagement metrics ----------------------------------------------
    view_count: int
    like_count: int
    comment_count: int
    share_count: int
    """Engagement counters as reported by the API at collection time -
    point-in-time snapshots, not stable properties of the post."""

    # --- Fields with defaults ---------------------------------------------

    ai_label_kind: str = ""
    """Which AI label TikTok applied, one of:

        ""                  no AI label present
        "creator_labelled"  video_tag AIGC number=1 - the creator voluntarily
                             disclosed AI generation (self-reported)
        "ai_generated"      video_tag AIGC number=2 - applied by TikTok
                             itself, typically from embedded C2PA content
                             credentials (platform-detected)

    These measure different things (creator behaviour vs. provenance
    metadata) and are kept separate rather than collapsed into one boolean."""

    favorites_count: int = 0
    """Times the video was saved to a user's favourites (API field
    `favorites_count`)."""

    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    """When this row was written, ISO 8601 UTC."""

    def hashtags_as_string(self) -> str:
        """Flatten the hashtag list into one comma-separated CSV cell."""
        return ",".join(self.hashtags)

    def to_csv_row(self) -> Dict[str, Any]:
        """Convert this post into a dictionary ready for `csv.DictWriter`."""
        return {
            "post_id": self.post_id,
            "username": self.username,
            "author_region": self.author_region,
            "create_time": self.create_time,
            "caption": self.caption,
            "hashtags": self.hashtags_as_string(),
            "has_ai_label": 1 if self.has_ai_label else 0,
            "ai_label_kind": self.ai_label_kind,
            "video_url": self.video_url,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "share_count": self.share_count,
            "favorites_count": self.favorites_count,
            "collected_at": self.collected_at,
        }


# ---------------------------------------------------------------------------
# Comment
# ---------------------------------------------------------------------------

@dataclass
class Comment:
    """One comment posted underneath one video."""

    comment_id: str
    """TikTok's unique identifier for this comment. Stored as a string, for
    the same reason as `Post.post_id`."""

    post_id: str
    """Foreign key into `raw_posts.csv`."""

    comment_text: str
    """The comment body, verbatim."""

    create_time: str
    """When the comment was posted, ISO 8601 UTC."""

    like_count: int
    """Likes on this comment at collection time."""

    reply_count: int = 0
    """How many replies this comment received (API field `reply_count`)."""

    parent_comment_id: str = ""
    """The comment this one replies to, or "" for a top-level comment.
    Lets reply threads be reconstructed during analysis."""

    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    """When this row was written, ISO 8601 UTC."""

    def to_csv_row(self) -> Dict[str, Any]:
        """Convert this comment into a dictionary for `csv.DictWriter`."""
        return {
            "comment_id": self.comment_id,
            "post_id": self.post_id,
            "comment_text": self.comment_text,
            "like_count": self.like_count,
            "reply_count": self.reply_count,
            "create_time": self.create_time,
            "parent_comment_id": self.parent_comment_id,
            "collected_at": self.collected_at,
        }


# ---------------------------------------------------------------------------
# Authoritative CSV schemas
# ---------------------------------------------------------------------------
# The header row and column order of the output files. `csv.DictWriter` is
# constructed from these, so a field added to a dataclass but forgotten here
# raises an error immediately instead of being silently dropped.

POST_CSV_COLUMNS: List[str] = [
    "post_id",          # String  - unique TikTok post identifier
    "username",         # String  - poster's handle
    "author_region",    # String  - ISO country code, blank if unavailable
    "create_time",      # String  - ISO 8601 UTC publication time
    "caption",          # Text    - full video caption
    "hashtags",         # String  - comma-separated list, no '#'
    "has_ai_label",     # Integer - 1 if TikTok labelled it AI-generated, else 0
    "ai_label_kind",    # String  - "creator_labelled" | "ai_generated" | ""
    "video_url",        # String  - canonical public permalink
    "view_count",       # Integer - total views at collection time
    "like_count",       # Integer - total likes at collection time
    "comment_count",    # Integer - total comments reported by TikTok
    "share_count",      # Integer - total shares at collection time
    "favorites_count",  # Integer - times saved to favourites
    "collected_at",     # String  - ISO 8601 UTC time this row was written
]

COMMENT_CSV_COLUMNS: List[str] = [
    "comment_id",         # String  - unique comment identifier
    "post_id",            # String  - foreign key into raw_posts.csv
    "comment_text",       # Text    - the comment body
    "like_count",         # Integer - likes on this comment
    "reply_count",        # Integer - replies this comment received
    "create_time",        # String  - ISO 8601 UTC time the comment was posted
    "parent_comment_id",  # String  - replied-to comment, blank if top-level
    "collected_at",       # String  - ISO 8601 UTC time this row was written
]
