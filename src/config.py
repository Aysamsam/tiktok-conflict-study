"""
Configuration loading and validation for the TikTok Conflict Study collector.

Every tunable value - credentials, date range, hashtags, output locations,
rate limits - is read from environment variables (populated from a `.env`
file) into the single `Settings` object defined below. Validation happens
eagerly at start-up: a misspelled date or a missing API key raises
immediately with a clear message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when the configuration is missing or malformed."""


# ---------------------------------------------------------------------------
# Low-level helpers for reading a single environment variable.
# ---------------------------------------------------------------------------

def _get_str(name: str, default: Optional[str] = None, *, required: bool = False) -> str:
    """Read `name` from the environment as a string.

    Args:
        name:     The environment variable name, e.g. "TIKTOK_CLIENT_KEY".
        default:  Value to use when the variable is absent or empty.
        required: When True, an absent/empty value is a fatal error.

    Returns:
        The variable's value, or `default` when unset.

    Raises:
        ConfigError: If `required` is True and no value is available.
    """
    raw = os.environ.get(name, "").strip()

    if raw:
        return raw
    if default is not None:
        return default
    if required:
        raise ConfigError(
            f"Required setting '{name}' is missing. "
            f"Copy .env.example to .env and fill in a value for {name}."
        )
    return ""


def _get_int(name: str, default: int) -> int:
    """Read `name` as an integer, falling back to `default` when unset."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Setting '{name}' must be a whole number, got '{raw}'."
        ) from exc


def _get_float(name: str, default: float) -> float:
    """Read `name` as a floating-point number, falling back to `default`."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Setting '{name}' must be a number, got '{raw}'."
        ) from exc


def _get_bool(name: str, default: bool) -> bool:
    """Read `name` as a boolean. Accepts true/false/1/0/yes/no, case-insensitive."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ConfigError(
        f"Setting '{name}' must be true/false (or 1/0, yes/no), got '{raw}'."
    )


def _get_date(name: str, default: str) -> date:
    """Read `name` as an ISO-format calendar date (YYYY-MM-DD)."""
    raw = os.environ.get(name, "").strip() or default
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ConfigError(
            f"Setting '{name}' must be a date in YYYY-MM-DD format, got '{raw}'."
        ) from exc


def _get_csv_list(name: str, default: str) -> List[str]:
    """Read `name` as a comma-separated list of strings, stripping any leading '#'.

    Unlike the other `_get_*` helpers, an explicitly empty value (e.g.
    `STUDY_HASHTAGS=` with nothing after the `=`) is a meaningful, valid
    state here - an empty list - not "unset". So this checks presence in
    the environment, not truthiness, before falling back to `default`;
    `"".strip() or default` would wrongly treat "explicitly emptied" the
    same as "never set".
    """
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    raw = raw.strip()
    items = []
    for chunk in raw.split(","):
        cleaned = chunk.strip().lstrip("#")
        if cleaned:
            items.append(cleaned)
    return items


# ---------------------------------------------------------------------------
# The Settings object: one immutable snapshot of the whole configuration.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    """A validated, read-only snapshot of every configurable value."""

    # --- Data source ------------------------------------------------------
    client_type: str                    # "mock" or "research_api"
    client_key: str                     # TikTok OAuth client key
    client_secret: str                  # TikTok OAuth client secret
    oauth_url: str                      # Token endpoint
    video_query_url: str                # Video search endpoint
    comment_query_url: str              # Comment listing endpoint

    # --- Study scope ------------------------------------------------------
    start_date: date                    # First day of collection (inclusive)
    end_date: date                      # Last day of collection (inclusive)
    hashtags: List[str]                 # Tag names without '#'
    keywords: List[str]                 # Free-text terms; matched against the caption
    languages: List[str]                 # ISO 639-1 codes, e.g. ["en", "fa", "he"]
    random_order: bool                  # Ask the API for a random sample instead of decreasing-video-ID order
    language_mode: str                  # "detect" or "off" - see src/language.py
    max_posts: int                      # Total post target for this run
    search_overfetch_factor: int        # Retrieve N x max_posts before filtering
    max_comments_per_post: int          # Per-post comment cap
    min_comments: int                   # Posts below this comment_count are excluded
    max_video_duration_seconds: int     # Posts longer than this are excluded. 0 = no limit
    require_ai_label: bool              # Keep only posts with an AI content label (any kind)
    require_regions: List[str]          # Keep only posts whose author_region is one of these (empty = no restriction)
    exclude_regions: List[str]          # Drop posts whose author_region is one of these (empty = no restriction)
    require_hashtag_contains: str       # Keep only posts with a hashtag containing this substring (empty = no restriction)
    sort_by: str                        # "none" (first match) or "view_count" (most viral first)
    boost_hashtags: List[str]           # Tags that get a ranking multiplier (sort_by=view_count only)
    boost_multiplier: float             # Score multiplier applied when a candidate carries a boost tag
    max_posts_per_account: int          # Cap on posts from one username in the dataset. 0 = unlimited
    min_posts_per_hashtag: int          # Reserve at least this many posts per hashtag/keyword before round-robin fill. 0 = no reservation
    max_posts_per_hashtag: int          # Cap on saved posts carrying any one study hashtag. 0 = unlimited
    daily_request_quota: int            # API requests permitted per 24h

    # --- Output -------------------------------------------------------
    output_root: Path                   # Everything is written under here

    # --- Server transfer --------------------------------------------------
    transfer_mode: str                  # "none" | "copy" | "sftp"
    server_dest_path: str               # Target directory on the server
    server_host: str
    server_port: int
    server_user: str
    server_ssh_key_path: str

    # --- Runtime ----------------------------------------------------------
    request_delay_seconds: float        # Politeness pause between API calls
    max_retries: int                    # Retry attempts per failed request
    log_level: str                      # DEBUG / INFO / WARNING / ERROR

    # -- Derived paths -----------------------------------------------------
    # Fixed by the study's data-architecture spec; only the root is configurable.
    data_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    posts_csv: Path = field(init=False)
    comments_csv: Path = field(init=False)

    def __post_init__(self) -> None:
        """Derive the fixed directory layout and validate cross-field rules."""
        root = self.output_root

        # <root>/data/raw_posts.csv, raw_comments.csv
        object.__setattr__(self, "data_dir", root / "data")
        object.__setattr__(self, "logs_dir", root / "logs")
        object.__setattr__(self, "posts_csv", root / "data" / "raw_posts.csv")
        object.__setattr__(self, "comments_csv", root / "data" / "raw_comments.csv")

        if self.start_date > self.end_date:
            raise ConfigError(
                f"STUDY_START_DATE ({self.start_date}) is after "
                f"STUDY_END_DATE ({self.end_date}). Check the .env file."
            )

        if not self.hashtags and not self.keywords:
            raise ConfigError(
                "STUDY_HASHTAGS and STUDY_KEYWORDS are both empty - "
                "there is nothing to search for."
            )

        if not self.languages:
            raise ConfigError(
                "STUDY_LANGUAGE is empty - there is nothing to filter for."
            )

        if self.max_posts < 1:
            raise ConfigError("STUDY_MAX_POSTS must be at least 1.")

        if self.search_overfetch_factor < 1:
            raise ConfigError(
                "SEARCH_OVERFETCH_FACTOR must be at least 1 (1 means no "
                "over-fetching, so local filtering will shrink the sample)."
            )

        if self.min_comments < 0:
            raise ConfigError("STUDY_MIN_COMMENTS must be 0 or greater.")

        if self.max_video_duration_seconds < 0:
            raise ConfigError("STUDY_MAX_VIDEO_DURATION_SECONDS must be 0 or greater.")

        if self.sort_by not in {"none", "view_count"}:
            raise ConfigError(
                f"STUDY_SORT_BY must be 'none' or 'view_count', got '{self.sort_by}'."
            )

        if self.boost_multiplier <= 0:
            raise ConfigError("STUDY_BOOST_MULTIPLIER must be greater than 0.")

        if self.max_posts_per_account < 0:
            raise ConfigError("STUDY_MAX_POSTS_PER_ACCOUNT must be 0 or greater.")

        if self.min_posts_per_hashtag < 0:
            raise ConfigError("STUDY_MIN_POSTS_PER_HASHTAG must be 0 or greater.")

        if self.max_posts_per_hashtag < 0:
            raise ConfigError("STUDY_MAX_POSTS_PER_HASHTAG must be 0 or greater.")

        if self.client_type not in {"mock", "research_api"}:
            raise ConfigError(
                f"TIKTOK_CLIENT must be 'mock' or 'research_api', "
                f"got '{self.client_type}'."
            )

        if self.language_mode not in {"detect", "off"}:
            raise ConfigError(
                f"STUDY_LANGUAGE_MODE must be 'detect' or 'off', "
                f"got '{self.language_mode}'."
            )

        if self.transfer_mode not in {"none", "copy", "sftp"}:
            raise ConfigError(
                f"SERVER_TRANSFER_MODE must be 'none', 'copy' or 'sftp', "
                f"got '{self.transfer_mode}'."
            )

        # Credentials are only mandatory for the real API.
        if self.client_type == "research_api":
            placeholder = "REPLACE_ME"
            if not self.client_key or placeholder in self.client_key:
                raise ConfigError(
                    "TIKTOK_CLIENT=research_api but TIKTOK_CLIENT_KEY is still "
                    "the placeholder value. Paste your real client key into .env."
                )
            if not self.client_secret or placeholder in self.client_secret:
                raise ConfigError(
                    "TIKTOK_CLIENT=research_api but TIKTOK_CLIENT_SECRET is still "
                    "the placeholder value. Paste your real secret into .env."
                )

    def ensure_directories(self) -> None:
        """Create the output directory tree if it does not already exist."""
        for directory in (self.data_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def describe(self) -> str:
        """Return a human-readable summary of the active configuration.

        Logged at the start of every run. The client secret is never
        included, and the client key is truncated.
        """
        key_preview = (self.client_key[:6] + "...") if self.client_key else "(none)"
        return (
            "Active configuration:\n"
            f"  Data source      : {self.client_type} (key: {key_preview})\n"
            f"  Date range       : {self.start_date} to {self.end_date}\n"
            f"  Hashtags         : {', '.join('#' + h for h in self.hashtags)}\n"
            f"  Keywords         : {', '.join(self.keywords) if self.keywords else '(none)'}\n"
            f"  Language filter  : {', '.join(self.languages)} (mode: {self.language_mode})\n"
            f"  Random order     : {self.random_order}\n"
            f"  Target posts     : {self.max_posts}\n"
            f"  Comments per post: up to {self.max_comments_per_post}\n"
            f"  Min comments     : {self.min_comments}\n"
            f"  Max duration     : "
            f"{str(self.max_video_duration_seconds) + 's' if self.max_video_duration_seconds else 'unlimited'}\n"
            f"  AI label only    : {self.require_ai_label}\n"
            f"  Region filter    : "
            f"{', '.join(self.require_regions) if self.require_regions else '(none)'}\n"
            f"  Region exclusion : "
            f"{', '.join(self.exclude_regions) if self.exclude_regions else '(none)'}\n"
            f"  Hashtag contains : {self.require_hashtag_contains or '(none)'}\n"
            f"  Selection order  : {self.sort_by}\n"
            f"  Rank boost tags  : {', '.join(self.boost_hashtags) if self.boost_hashtags else '(none)'} "
            f"(x{self.boost_multiplier})\n"
            f"  Max per account  : "
            f"{self.max_posts_per_account if self.max_posts_per_account else 'unlimited'}\n"
            f"  Min per hashtag  : "
            f"{self.min_posts_per_hashtag if self.min_posts_per_hashtag else 'none (pure round-robin)'}\n"
            f"  Max per hashtag  : "
            f"{self.max_posts_per_hashtag if self.max_posts_per_hashtag else 'unlimited'}\n"
            f"  Output root      : {self.output_root.resolve()}\n"
            f"  Server transfer  : {self.transfer_mode}"
        )


def load_settings(env_file: Optional[Path] = None) -> Settings:
    """Build a validated `Settings` object from the environment.

    This is the only function in the project that reads `os.environ`. Every
    other module receives a `Settings` instance as an argument.

    Args:
        env_file: Path to the .env file. Defaults to `.env` next to the
                  project root. A missing file is not an error - the
                  variables may already be exported by the shell.

    Returns:
        A fully validated, immutable `Settings` instance.

    Raises:
        ConfigError: If any setting is missing, malformed or contradictory.
    """
    if env_file is None:
        env_file = Path(__file__).resolve().parent.parent / ".env"

    # override=False: a variable already set in the real environment wins
    # over the .env file (relevant on a shared server / job scheduler).
    load_dotenv(dotenv_path=env_file, override=False)

    # OUTPUT_ROOT may be relative; resolve it against the project directory.
    project_dir = Path(__file__).resolve().parent.parent
    output_root_raw = _get_str("OUTPUT_ROOT", default=".")
    output_root = Path(output_root_raw)
    if not output_root.is_absolute():
        output_root = (project_dir / output_root).resolve()

    return Settings(
        # --- Data source --------------------------------------------------
        client_type=_get_str("TIKTOK_CLIENT", default="mock").lower(),
        client_key=_get_str("TIKTOK_CLIENT_KEY", default=""),
        client_secret=_get_str("TIKTOK_CLIENT_SECRET", default=""),
        oauth_url=_get_str(
            "TIKTOK_OAUTH_URL",
            default="https://open.tiktokapis.com/v2/oauth/token/",
        ),
        video_query_url=_get_str(
            "TIKTOK_VIDEO_QUERY_URL",
            default="https://open.tiktokapis.com/v2/research/video/query/",
        ),
        comment_query_url=_get_str(
            "TIKTOK_COMMENT_QUERY_URL",
            default="https://open.tiktokapis.com/v2/research/video/comment/list/",
        ),

        # --- Study scope --------------------------------------------------
        start_date=_get_date("STUDY_START_DATE", default="2026-04-01"),
        end_date=_get_date("STUDY_END_DATE", default="2026-07-31"),
        hashtags=_get_csv_list(
            "STUDY_HASHTAGS",
            default="IsraelIran,IsraelIranWar,Iranwar,Iran",
        ),
        keywords=_get_csv_list("STUDY_KEYWORDS", default=""),
        languages=[lang.lower() for lang in _get_csv_list("STUDY_LANGUAGE", default="en")],
        random_order=_get_bool("STUDY_RANDOM_ORDER", default=False),
        language_mode=_get_str("STUDY_LANGUAGE_MODE", default="detect").lower(),
        max_posts=_get_int("STUDY_MAX_POSTS", default=20),
        search_overfetch_factor=_get_int("SEARCH_OVERFETCH_FACTOR", default=3),
        max_comments_per_post=_get_int("STUDY_MAX_COMMENTS_PER_POST", default=200),
        min_comments=_get_int("STUDY_MIN_COMMENTS", default=0),
        max_video_duration_seconds=_get_int("STUDY_MAX_VIDEO_DURATION_SECONDS", default=0),
        require_ai_label=_get_bool("STUDY_REQUIRE_AI_LABEL", default=False),
        require_regions=[
            r.upper() for r in _get_csv_list("STUDY_REQUIRE_REGIONS", default="")
        ],
        exclude_regions=[
            r.upper() for r in _get_csv_list("STUDY_EXCLUDE_REGIONS", default="")
        ],
        require_hashtag_contains=_get_str("STUDY_REQUIRE_HASHTAG_CONTAINS", default=""),
        sort_by=_get_str("STUDY_SORT_BY", default="none").lower(),
        boost_hashtags=_get_csv_list("STUDY_BOOST_HASHTAGS", default=""),
        boost_multiplier=_get_float("STUDY_BOOST_MULTIPLIER", default=1.2),
        max_posts_per_account=_get_int("STUDY_MAX_POSTS_PER_ACCOUNT", default=0),
        min_posts_per_hashtag=_get_int("STUDY_MIN_POSTS_PER_HASHTAG", default=0),
        max_posts_per_hashtag=_get_int("STUDY_MAX_POSTS_PER_HASHTAG", default=0),
        daily_request_quota=_get_int("DAILY_REQUEST_QUOTA", default=1000),

        # --- Output ---------------------------------------------------
        output_root=output_root,

        # --- Server transfer ----------------------------------------------
        transfer_mode=_get_str("SERVER_TRANSFER_MODE", default="none").lower(),
        server_dest_path=_get_str("SERVER_DEST_PATH", default=""),
        server_host=_get_str("SERVER_HOST", default=""),
        server_port=_get_int("SERVER_PORT", default=22),
        server_user=_get_str("SERVER_USER", default=""),
        server_ssh_key_path=_get_str("SERVER_SSH_KEY_PATH", default=""),

        # --- Runtime ------------------------------------------------------
        request_delay_seconds=_get_float("REQUEST_DELAY_SECONDS", default=1.0),
        max_retries=_get_int("MAX_RETRIES", default=3),
        log_level=_get_str("LOG_LEVEL", default="INFO").upper(),
    )
