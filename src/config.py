"""
Configuration loading and validation for the TikTok Conflict Study collector.

DESIGN RATIONALE
----------------
Every tunable value in this project - credentials, date range, hashtags, output
locations, rate limits - is read from environment variables (populated from a
`.env` file) and funnelled through the single `Settings` object defined below.

This gives the project three properties that matter for reviewed research code:

  1. NO SECRETS IN SOURCE. The API key exists only in `.env`, which is
     git-ignored. The code that a reviewer reads contains no credentials.

  2. ONE PLACE TO CHANGE THE STUDY DESIGN. Widening the date window or adding a
     hashtag is a configuration edit, not a code edit, so the analysis logic
     that was reviewed stays untouched and auditable.

  3. TRIVIAL LOCAL -> SERVER MIGRATION. Every filesystem path used anywhere in
     the codebase is derived from `Settings.output_root`. Pointing the project
     at the college server means changing that one variable.

Validation happens eagerly at start-up ("fail fast"): a misspelled date or a
missing API key raises immediately with a clear message, rather than causing a
confusing crash forty minutes into a collection run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv


class ConfigError(Exception):
    """
    Raised when the configuration is missing or malformed.

    A dedicated exception type (rather than a bare `ValueError`) lets the
    top-level entry point catch *configuration* problems specifically and print
    a friendly "check your .env file" message, while letting genuine bugs
    propagate with a full traceback.
    """


# ---------------------------------------------------------------------------
# Low-level helpers for reading a single environment variable.
#
# Each helper does exactly one job: fetch one variable, coerce it to one type,
# and raise a descriptive ConfigError if that is impossible. Keeping them
# separate means the error message can name the exact offending variable.
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
    # os.environ.get returns None when unset; we also treat an empty string as
    # unset, because a line like "FOO=" in a .env file is almost always an
    # oversight rather than a deliberate empty value.
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
        # Re-raise with context: the original message ("invalid literal for
        # int()") does not tell the user WHICH setting is wrong.
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
    """Read `name` as a boolean.

    Environment variables are always strings, so we accept the common written
    forms a researcher might type rather than insisting on one spelling.
    """
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(
        f"Setting '{name}' must be true or false, got '{raw}'."
    )


def _get_date(name: str, default: str) -> date:
    """Read `name` as an ISO-format calendar date (YYYY-MM-DD)."""
    raw = os.environ.get(name, "").strip() or default
    try:
        # `strptime` with an explicit format rejects ambiguous inputs such as
        # "01/04/2026", which could mean 1 April or 4 January depending on
        # locale. Forcing ISO 8601 removes that ambiguity entirely.
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ConfigError(
            f"Setting '{name}' must be a date in YYYY-MM-DD format, got '{raw}'."
        ) from exc


def _get_csv_list(name: str, default: str) -> List[str]:
    """Read `name` as a comma-separated list of strings.

    Leading '#' characters are stripped so that a researcher can paste either
    "IsraelIran" or "#IsraelIran" into the .env file and get the same result -
    the API expects the bare tag name without the hash.
    """
    raw = os.environ.get(name, "").strip() or default
    items = []
    for chunk in raw.split(","):
        cleaned = chunk.strip().lstrip("#")
        if cleaned:                       # skip blanks from a trailing comma
            items.append(cleaned)
    return items


# ---------------------------------------------------------------------------
# The Settings object: one immutable snapshot of the whole configuration.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    """A validated, read-only snapshot of every configurable value.

    `frozen=True` makes instances immutable. This is deliberate: it guarantees
    that no part of the pipeline can quietly mutate the study parameters
    half-way through a run, which would make the resulting dataset impossible
    to describe accurately in a methods section.
    """

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
    language: str                       # ISO 639-1 code, e.g. "en"
    language_mode: str                  # "detect" or "off" - see src/language.py
    max_posts: int                      # Total post target for this run
    search_overfetch_factor: int        # Retrieve N x max_posts before filtering
    max_comments_per_post: int          # Per-post comment cap
    daily_request_quota: int            # API requests permitted per 24h

    # --- Output -----------------------------------------------------------
    output_root: Path                   # Everything is written under here
    download_videos: bool               # Whether to fetch the .mp4 files

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
    # These are computed in __post_init__ rather than stored in .env, because
    # the study protocol fixes the internal folder layout. Only the ROOT is
    # configurable; the structure beneath it must stay constant so that the
    # dataset is laid out identically on every machine.
    data_dir: Path = field(init=False)
    videos_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    posts_csv: Path = field(init=False)
    comments_csv: Path = field(init=False)

    def __post_init__(self) -> None:
        """Derive the fixed directory layout and validate cross-field rules.

        Runs automatically right after the dataclass is constructed. Because
        the dataclass is frozen, normal attribute assignment is blocked, so we
        use `object.__setattr__` - the standard escape hatch for computing
        derived fields on a frozen dataclass.
        """
        root = self.output_root

        # The exact tree required by the study's data-architecture spec:
        #   <root>/data/raw_posts.csv
        #   <root>/data/raw_comments.csv
        #   <root>/downloads/videos/<post_id>.mp4
        object.__setattr__(self, "data_dir", root / "data")
        object.__setattr__(self, "videos_dir", root / "downloads" / "videos")
        object.__setattr__(self, "logs_dir", root / "logs")
        object.__setattr__(self, "posts_csv", root / "data" / "raw_posts.csv")
        object.__setattr__(self, "comments_csv", root / "data" / "raw_comments.csv")

        # --- Cross-field validation ---------------------------------------
        # These checks cannot live in the per-variable helpers above because
        # each one depends on more than one setting at a time.

        if self.start_date > self.end_date:
            raise ConfigError(
                f"STUDY_START_DATE ({self.start_date}) is after "
                f"STUDY_END_DATE ({self.end_date}). Check the .env file."
            )

        if not self.hashtags:
            raise ConfigError(
                "STUDY_HASHTAGS is empty - there is nothing to search for."
            )

        if self.max_posts < 1:
            raise ConfigError("STUDY_MAX_POSTS must be at least 1.")

        if self.search_overfetch_factor < 1:
            raise ConfigError(
                "SEARCH_OVERFETCH_FACTOR must be at least 1 (1 means no "
                "over-fetching, so local filtering will shrink the sample)."
            )

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

        # Credentials are only mandatory for the real API. Requiring them in
        # mock mode would defeat the purpose of having an offline mode.
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
        """Create the output directory tree if it does not already exist.

        Called once at start-up. `parents=True` creates intermediate folders
        (e.g. `downloads/` on the way to `downloads/videos/`), and
        `exist_ok=True` makes the call safe to repeat on every run.
        """
        for directory in (self.data_dir, self.videos_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def describe(self) -> str:
        """Return a human-readable summary of the active configuration.

        Printed to the log at the start of every run so that each log file is
        self-documenting: months later it is possible to read a log and know
        exactly which parameters produced the accompanying dataset.

        SECURITY NOTE: the client secret is never included, and the client key
        is truncated. Log files are frequently shared during troubleshooting.
        """
        key_preview = (self.client_key[:6] + "...") if self.client_key else "(none)"
        return (
            "Active configuration:\n"
            f"  Data source      : {self.client_type} (key: {key_preview})\n"
            f"  Date range       : {self.start_date} to {self.end_date}\n"
            f"  Hashtags         : {', '.join('#' + h for h in self.hashtags)}\n"
            f"  Language filter  : {self.language} (mode: {self.language_mode})\n"
            f"  Target posts     : {self.max_posts}\n"
            f"  Comments per post: up to {self.max_comments_per_post}\n"
            f"  Download videos  : {self.download_videos}\n"
            f"  Output root      : {self.output_root.resolve()}\n"
            f"  Server transfer  : {self.transfer_mode}"
        )


def load_settings(env_file: Optional[Path] = None) -> Settings:
    """Build a validated `Settings` object from the environment.

    This is the ONLY function in the project that reads `os.environ`. Every
    other module receives a `Settings` instance as an argument. That single
    choke point is what makes the pipeline testable: a unit test can construct
    a `Settings` object by hand and never touch the real environment.

    Args:
        env_file: Path to the .env file. Defaults to `.env` next to the
                  project root. A missing file is NOT an error - the variables
                  may already be exported by the shell, which is how the
                  college server's job scheduler will typically supply them.

    Returns:
        A fully validated, immutable `Settings` instance.

    Raises:
        ConfigError: If any setting is missing, malformed or contradictory.
    """
    # Resolve the default .env location relative to THIS file rather than to
    # the current working directory, so the script behaves identically whether
    # it is launched from the project folder or from a cron job elsewhere.
    if env_file is None:
        env_file = Path(__file__).resolve().parent.parent / ".env"

    # `override=False` means a variable already set in the real environment
    # wins over the .env file. That is the behaviour you want on a shared
    # server, where the scheduler injects credentials as real env vars.
    load_dotenv(dotenv_path=env_file, override=False)

    # OUTPUT_ROOT may be relative (".") - resolve it against the project
    # directory so that the data always lands in a predictable place.
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
        language=_get_str("STUDY_LANGUAGE", default="en").lower(),
        language_mode=_get_str("STUDY_LANGUAGE_MODE", default="detect").lower(),
        max_posts=_get_int("STUDY_MAX_POSTS", default=20),
        search_overfetch_factor=_get_int("SEARCH_OVERFETCH_FACTOR", default=3),
        max_comments_per_post=_get_int("STUDY_MAX_COMMENTS_PER_POST", default=200),
        daily_request_quota=_get_int("DAILY_REQUEST_QUOTA", default=1000),

        # --- Output -------------------------------------------------------
        output_root=output_root,
        download_videos=_get_bool("DOWNLOAD_VIDEOS", default=True),

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
