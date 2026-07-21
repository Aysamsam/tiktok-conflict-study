"""
Data models: the exact shape of one post and one comment.

WHY DEFINE EXPLICIT CLASSES INSTEAD OF PASSING DICTIONARIES AROUND?
-------------------------------------------------------------------
A raw API response is a nested dictionary whose keys differ between providers,
change between API versions, and are silently absent when a field is empty.
Passing those dictionaries straight into the CSV writer would mean:

  * a typo like `post["view_cont"]` fails at 3am, half-way through a run;
  * the CSV column order depends on whatever order the API happened to use;
  * switching from the mock client to the real API changes the column names.

Defining `Post` and `Comment` as dataclasses fixes all three. Each client
implementation is responsible for translating its own response format INTO
these classes, so the rest of the pipeline - deduplication, CSV writing, video
downloading - works with one stable vocabulary regardless of data source.

The `CSV_COLUMNS` lists below are the authoritative schema. The CSV writer
reads its header from them, so the column order in the output file can never
drift out of sync with the code.
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

    TikTok returns `create_time` as an integer count of seconds since
    1970-01-01 UTC. That is compact but unreadable in a spreadsheet, and it
    invites timezone mistakes during analysis.

    We normalise to ISO 8601 with an explicit UTC offset, e.g.
    "2026-06-14T09:31:07+00:00", because:
      * Excel, R, Python and SPSS all parse it without configuration;
      * the timezone is stated in the value itself, so a collaborator in a
        different country cannot misread it;
      * ISO strings sort chronologically as plain text.

    Args:
        unix_seconds: Seconds since the Unix epoch, or None when the API
                      omitted the field.

    Returns:
        An ISO 8601 string, or "" when the input was None. An empty string is
        used rather than the word "None" so that the CSV cell reads as a true
        blank in analysis software.
    """
    if unix_seconds is None:
        return ""
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Post
# ---------------------------------------------------------------------------

@dataclass
class Post:
    """One TikTok video and all metadata the study records about it.

    Field order here intentionally mirrors the order of `POST_CSV_COLUMNS`
    below, so that reading this class tells you what the spreadsheet looks
    like.
    """

    # --- Identity ---------------------------------------------------------
    post_id: str
    """TikTok's unique video identifier. Also the primary key linking this row
    to the comments table and to the downloaded `<post_id>.mp4` file. Stored as
    a STRING, never an integer: these IDs are 19 digits long, and spreadsheet
    software silently rounds large integers, which would corrupt the key."""

    username: str
    """The author's public @handle, without the leading '@'."""

    author_region: str
    """Two-letter country code of the author's registered region, when the API
    exposes it (the Research API field is `region_code`). Empty string when
    unavailable - the study protocol marks this field as optional."""

    create_time: str
    """Publication timestamp, ISO 8601 UTC. See `unix_to_iso` above."""

    # --- Content ----------------------------------------------------------
    caption: str
    """The full caption text as written by the author, including any emoji and
    inline hashtags. Stored verbatim; no cleaning is applied at collection
    time so that the raw data remains faithful to the source."""

    hashtags: List[str]
    """Hashtag names attached to the post, without '#'. Kept as a Python list
    in memory for convenient filtering, and flattened to a comma-separated
    string only at the moment of CSV writing."""

    video_url: str
    """Canonical public web address of the post, i.e.
    https://www.tiktok.com/@<username>/video/<post_id>. This is the citable
    permalink, not a direct media link."""

    has_ai_label: bool
    """True when TikTok displayed an AI-generated-content label on the post AT
    THE TIME OF COLLECTION.

    Derived from the Research API's `video_tag` field, where an entry of type
    "AIGC" indicates an AI label. See `ai_label_kind` for WHICH of the two
    distinct AIGC labels was present - a distinction that matters a great deal
    for this study and is easy to lose by collapsing both into one boolean.

    IMPORTANT METHODOLOGICAL CAVEAT: this is a measure of *disclosure*, not a
    ground-truth measure of whether the video is synthetic. It can also be
    added or removed after publication, which is why the collection timestamp
    is recorded alongside it in `collected_at`."""

    # --- Engagement metrics ----------------------------------------------
    view_count: int
    like_count: int
    comment_count: int
    share_count: int
    """Engagement counters as reported by the API at collection time. These are
    snapshots: a post collected on day 1 and re-collected on day 30 will show
    different values, which is why `collected_at` is stored on every row."""

    # --- Fields with defaults ---------------------------------------------
    # Python requires every field with a default value to follow every field
    # without one, so these are grouped here rather than beside the fields they
    # relate to conceptually.

    ai_label_kind: str = ""
    """WHICH AI label TikTok applied. One of:

        ""                 no AI label present
        "creator_labelled" video_tag AIGC number=1 - "Creator Labelled as
                           AI-Generated". The creator voluntarily flipped the
                           disclosure toggle. This is SELF-REPORTED.
        "ai_generated"     video_tag AIGC number=2 - "AI-Generated". Applied by
                           TikTok itself, typically from C2PA content
                           credentials embedded by the generating tool. This is
                           PLATFORM-DETECTED.

    These two are not interchangeable. Self-disclosure measures creator
    behaviour and honesty; platform detection measures provenance metadata that
    survived upload. A study of AI content in conflict reporting will almost
    certainly want to analyse them separately, and collapsing them into a
    single boolean at collection time would destroy that ability permanently -
    the distinction cannot be recovered later."""

    favorites_count: int = 0
    """Times the video was saved to a user's favourites. Offered by the
    Research API's `favorites_count` field and captured because it is a
    distinct engagement signal from a like: saving indicates an intent to
    return to or re-share the content, which is of particular interest when
    studying how conflict material propagates."""

    # --- Local bookkeeping -----------------------------------------------
    video_path: str = ""
    """Path to the downloaded .mp4 file, RELATIVE to the project root (for
    example `downloads/videos/7382910473829104738.mp4`).

    A relative path is stored deliberately: an absolute Windows path like
    `D:\\Aysam\\...` would be meaningless once the dataset is moved to the
    Linux college server. Relative paths keep the dataset portable."""

    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    """When this row was written, ISO 8601 UTC. Essential for interpreting the
    engagement metrics and the AI label, both of which are point-in-time
    observations rather than stable properties of the post."""

    def hashtags_as_string(self) -> str:
        """Flatten the hashtag list into one comma-separated CSV cell.

        Example: ["IsraelIran", "news"] -> "IsraelIran,news"

        Comma separation is used because the study schema specifies it. The CSV
        writer quotes the whole cell, so embedded commas cannot break the
        column structure.
        """
        return ",".join(self.hashtags)

    def to_csv_row(self) -> Dict[str, Any]:
        """Convert this post into one dictionary ready for `csv.DictWriter`.

        Two type conversions happen here and nowhere else:
          * the hashtag list becomes a comma-separated string;
          * the boolean AI label becomes 1 or 0, as the schema requires
            (numeric flags import cleanly into R and SPSS, whereas the strings
            "True"/"False" are read as text and need recoding).
        """
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
            "video_path": self.video_path,
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
    """TikTok's unique identifier for this comment. Stored as a string for the
    same large-integer-rounding reason as `Post.post_id`."""

    post_id: str
    """The video this comment belongs to. This is the foreign key that joins
    `raw_comments.csv` back to `raw_posts.csv`."""

    comment_text: str
    """The comment body, verbatim and uncleaned."""

    create_time: str
    """When the comment was posted, ISO 8601 UTC."""

    like_count: int
    """Likes on this specific comment at collection time."""

    reply_count: int = 0
    """How many replies this comment received. Offered by the Research API's
    `reply_count` field. Together with `parent_comment_id` it allows reply
    threads to be reconstructed and their depth measured - useful when the
    object of study is argumentative exchange rather than isolated reactions."""

    parent_comment_id: str = ""
    """The comment this one replies to, or "" for a top-level comment.

    Not required by the original schema, but captured because it costs nothing
    at collection time and is impossible to recover afterwards. It allows the
    analysis to reconstruct reply threads, which matters when studying
    argumentative exchanges in a conflict context."""

    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    """When this row was written, ISO 8601 UTC."""

    def to_csv_row(self) -> Dict[str, Any]:
        """Convert this comment into one dictionary for `csv.DictWriter`."""
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
# These lists define both the header row AND the column order of the output
# files. `csv.DictWriter` is constructed from them, which means a field added
# to a dataclass but forgotten here raises an error immediately rather than
# being silently dropped from the dataset.

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
    "video_path",       # String  - project-relative path to the .mp4
    "view_count",       # Integer - total views at collection time
    "like_count",       # Integer - total likes at collection time
    "comment_count",    # Integer - total comments reported by TikTok
    "share_count",      # Integer - total shares at collection time
    "favorites_count",  # Integer - times saved to favourites
    "collected_at",     # String  - ISO 8601 UTC time this row was written
]

COMMENT_CSV_COLUMNS: List[str] = [
    "comment_id",        # String  - unique comment identifier
    "post_id",           # String  - foreign key into raw_posts.csv
    "comment_text",      # Text    - the comment body
    "like_count",        # Integer - likes on this comment
    "reply_count",       # Integer - replies this comment received
    "create_time",       # String  - ISO 8601 UTC time the comment was posted
    "parent_comment_id", # String  - replied-to comment, blank if top-level
    "collected_at",      # String  - ISO 8601 UTC time this row was written
]
