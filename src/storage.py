"""
CSV persistence: writing posts and comments to disk safely and resumably.

Each row is flushed as soon as it is written, so an interrupted run leaves a
valid, usable CSV of everything collected so far. IDs already present in the
file are loaded at start-up, so re-running the collector never writes a
duplicate row. `encoding="utf-8"` and `newline=""` keep the Arabic, Hebrew,
Persian and emoji text in this dataset intact and correctly escaped.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Iterable, List, Set

from .models import COMMENT_CSV_COLUMNS, POST_CSV_COLUMNS, Comment, Post

logger = logging.getLogger(__name__)


class CsvWriter:
    """Append-only CSV writer with a header guarantee and duplicate rejection.

    One instance manages one file, used as a context manager:

        with CsvWriter(path, COLUMNS, "post_id") as writer:
            writer.write_row(post.to_csv_row())
    """

    def __init__(self, path: Path, columns: List[str], id_column: str) -> None:
        """Prepare the writer and learn which records already exist.

        Args:
            path:      The .csv file to append to. Created if absent.
            columns:   The authoritative column list from `models.py`. This
                       becomes the header row and fixes the column order.
            id_column: The column holding the unique key ("post_id" or
                       "comment_id"). Used for duplicate detection.
        """
        self.path = path
        self.columns = columns
        self.id_column = id_column

        self._seen_ids: Set[str] = set()

        self._file = None    # the open file handle, set in __enter__
        self._writer = None  # the csv.DictWriter, set in __enter__
        self._rows_written = 0

        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "CsvWriter":
        """Open the file for appending and load existing IDs."""
        self._load_existing_ids()

        needs_header = (not self.path.exists()) or self.path.stat().st_size == 0

        # Append mode ("a") is what makes the file resumable: reopening never
        # truncates data from a previous run. newline="" is required on
        # Windows to avoid the csv module's line endings being doubled.
        self._file = open(self.path, mode="a", newline="", encoding="utf-8")

        # extrasaction="ignore": a dict with extra keys is fine. A MISSING
        # key still raises - forgetting a required field must be loud.
        self._writer = csv.DictWriter(
            self._file,
            fieldnames=self.columns,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )

        if needs_header:
            self._writer.writeheader()
            self._file.flush()
            logger.debug("Wrote header row to %s", self.path.name)

        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Flush and close the file, reporting how much was written."""
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None
        logger.info("%s: %d new row(s) written.", self.path.name, self._rows_written)

    def _load_existing_ids(self) -> None:
        """Populate `_seen_ids` from the file's current contents."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            logger.debug("%s does not exist yet - starting fresh.", self.path.name)
            return

        try:
            with open(self.path, mode="r", newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    existing_id = row.get(self.id_column, "").strip()
                    if existing_id:
                        self._seen_ids.add(existing_id)
        except (OSError, csv.Error) as exc:
            raise RuntimeError(
                f"Could not read existing data from {self.path}: {exc}. "
                "Inspect or move the file before re-running."
            ) from exc

        logger.info(
            "%s: found %d existing record(s); these will not be re-collected.",
            self.path.name, len(self._seen_ids),
        )

    def already_have(self, record_id: str) -> bool:
        """Return True if `record_id` is already stored in this file.

        Lets the pipeline skip expensive work (downloading a video, fetching
        a comment thread) for a post it has already collected.
        """
        return record_id in self._seen_ids

    def write_row(self, row: dict) -> bool:
        """Append one row, unless its ID has already been written.

        Args:
            row: A dictionary produced by `Post.to_csv_row()` or
                 `Comment.to_csv_row()`.

        Returns:
            True if the row was written; False if it was a duplicate.
        """
        if self._writer is None:
            raise RuntimeError(
                "CsvWriter used outside a 'with' block - the file is not open."
            )

        record_id = str(row.get(self.id_column, "")).strip()
        if not record_id:
            logger.warning("Refusing to write a row with an empty %s.", self.id_column)
            return False

        if record_id in self._seen_ids:
            logger.debug("Skipping duplicate %s=%s", self.id_column, record_id)
            return False

        self._writer.writerow(row)
        self._seen_ids.add(record_id)
        self._rows_written += 1

        # Flush after every row so a crash loses at most the write in flight.
        self._file.flush()
        return True

    @property
    def rows_written(self) -> int:
        """How many new rows this writer has appended during this run."""
        return self._rows_written

    @property
    def total_records(self) -> int:
        """How many records exist in the file in total (prior runs plus this one)."""
        return len(self._seen_ids)


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def open_posts_writer(path: Path) -> CsvWriter:
    """Open `raw_posts.csv` with the post schema and post_id deduplication."""
    return CsvWriter(path=path, columns=POST_CSV_COLUMNS, id_column="post_id")


def open_comments_writer(path: Path) -> CsvWriter:
    """Open `raw_comments.csv` with the comment schema and comment_id dedup."""
    return CsvWriter(path=path, columns=COMMENT_CSV_COLUMNS, id_column="comment_id")


def write_posts(writer: CsvWriter, posts: Iterable[Post]) -> int:
    """Write many posts at once, returning how many were newly added."""
    return sum(1 for post in posts if writer.write_row(post.to_csv_row()))


def write_comments(writer: CsvWriter, comments: Iterable[Comment]) -> int:
    """Write many comments at once, returning how many were newly added."""
    return sum(1 for comment in comments if writer.write_row(comment.to_csv_row()))
