"""
CSV persistence: writing posts and comments to disk safely and resumably.

THE THREE PROBLEMS THIS MODULE SOLVES
-------------------------------------
1. RESUMABILITY. A collection run can take hours and can be interrupted by a
   dropped connection, an exhausted rate limit, or a server reboot. Writing
   each row the moment it arrives - rather than accumulating everything in
   memory and saving at the end - means an interrupted run leaves a valid,
   usable CSV containing everything collected so far.

2. DEDUPLICATION. Re-running the collector (to resume, or to extend the sample)
   will re-encounter posts already saved. The same hashtag appearing in several
   search chunks also returns overlapping results. Writing those rows twice
   would inflate every count in the analysis. This module loads the IDs already
   present in the file at start-up and refuses to write a duplicate.

3. CORRECT ENCODING AND ESCAPING. Captions and comments in this study contain
   commas, quotation marks, newlines, emoji, and Arabic, Hebrew and Persian
   text. Naive string concatenation into a .csv file corrupts all of these.
   Python's `csv` module handles the escaping; `encoding="utf-8"` and
   `newline=""` handle the rest.
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

    One instance manages one file. It is used as a context manager so that the
    file handle is always flushed and closed, even if the run crashes:

        with CsvWriter(path, COLUMNS, "post_id") as writer:
            writer.write_row(post.to_csv_row())
    """

    def __init__(self, path: Path, columns: List[str], id_column: str) -> None:
        """Prepare the writer and learn which records already exist.

        Args:
            path:      The .csv file to append to. Created if absent.
            columns:   The authoritative column list from `models.py`. This
                       becomes the header row and fixes the column ORDER.
            id_column: The column holding the unique key ("post_id" or
                       "comment_id"). Used for duplicate detection.
        """
        self.path = path
        self.columns = columns
        self.id_column = id_column

        # Set membership is an O(1) operation, so checking a candidate ID
        # against a set of a million already-seen IDs is effectively free. A
        # list would make it O(n) and turn the run quadratic.
        self._seen_ids: Set[str] = set()

        self._file = None    # the open file handle, set in __enter__
        self._writer = None  # the csv.DictWriter, set in __enter__
        self._rows_written = 0

        # Ensure the parent directory exists before trying to open a file in it.
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "CsvWriter":
        """Open the file for appending and load existing IDs."""
        # Read the IDs already in the file BEFORE opening it for writing, so
        # that a resumed run knows exactly what it must not duplicate.
        self._load_existing_ids()

        # A file that does not exist yet, or exists but is empty, needs a
        # header row written first.
        needs_header = (not self.path.exists()) or self.path.stat().st_size == 0

        # Mode "a" (append) rather than "w" (write) is what makes the file
        # resumable: reopening never truncates data from a previous run.
        #
        # newline="" is REQUIRED on Windows. Without it, Python's universal
        # newline translation turns the csv module's "\r\n" into "\r\r\n",
        # producing a blank line between every data row.
        #
        # encoding="utf-8" is required for the non-Latin text in this dataset.
        self._file = open(self.path, mode="a", newline="", encoding="utf-8")

        # `extrasaction="ignore"` means a dictionary containing keys not in
        # `columns` will not raise. A MISSING key still raises, which is the
        # protection we want: forgetting a required field must be loud.
        self._writer = csv.DictWriter(
            self._file,
            fieldnames=self.columns,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,   # quote only cells that need it
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
        """Populate `_seen_ids` from the file's current contents.

        Called once at start-up. On a fresh run the file does not exist and
        this returns immediately; on a resumed run it reads the whole file.
        Reading a large CSV costs a few seconds, which is a small price for
        never producing a duplicated dataset.
        """
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
            # A corrupt or unreadable existing file must not silently become an
            # empty seen-set, because that would cause duplicate rows to be
            # appended to an already-damaged file.
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

        Exposed publicly so the pipeline can skip the expensive work
        (downloading a video, fetching a comment thread) for a post it has
        already collected - not merely skip the CSV write.
        """
        return record_id in self._seen_ids

    def write_row(self, row: dict) -> bool:
        """Append one row, unless its ID has already been written.

        Args:
            row: A dictionary produced by `Post.to_csv_row()` or
                 `Comment.to_csv_row()`.

        Returns:
            True if the row was written; False if it was a duplicate and was
            skipped. The pipeline uses this to keep accurate counts.
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

        # Flush after every row. This is the single most important line for
        # resumability: without it, Python buffers roughly 8 KB of output, and
        # a crash would lose the most recent few dozen records. The performance
        # cost is negligible compared to the network calls that produced them.
        self._file.flush()
        return True

    @property
    def rows_written(self) -> int:
        """How many new rows this writer has appended during this run."""
        return self._rows_written

    @property
    def total_records(self) -> int:
        """How many records exist in the file in total: prior runs plus this one.

        This is the number that matters for a sample-size target. Counting only
        the rows written during the CURRENT run would mean that re-running a
        completed collection appends another full sample, silently growing the
        dataset past the size the study design specifies.
        """
        return len(self._seen_ids)


# ---------------------------------------------------------------------------
# Convenience wrappers - thin, typed front doors for the pipeline.
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
