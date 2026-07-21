"""
Video file downloading.

RESPONSIBILITY
--------------
Given a `Post` and a media URL, fetch the .mp4 and save it as
`downloads/videos/<post_id>.mp4`, then report the project-relative path so it
can be recorded in `raw_posts.csv`.

Naming files by `post_id` (and nothing else) is deliberate: it makes the link
between a video file and its metadata row exact and mechanical. A researcher
holding row `7382910473829104738` can find its video without a lookup table,
and a script can join the two with a single string operation.

PLEASE READ - WHY DOWNLOADS MAY BE SKIPPED
------------------------------------------
The TikTok Research API returns metadata only; it does not provide a direct
.mp4 link, and retrieving files from TikTok's media CDN is restricted by their
Terms of Service. `TikTokClient.resolve_video_download_url()` therefore returns
None by default, and this module records the post with an empty `video_path`
rather than attempting an unauthorised fetch.

That is a deliberate ethical default, not an unfinished feature. Once your
institution confirms which media-access route its agreement permits, implement
`resolve_video_download_url` in the relevant client class; everything in this
module then works unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import requests

from .models import Post

logger = logging.getLogger(__name__)

# Read the response in 1 MiB pieces rather than all at once. `iter_content`
# with a chunk size keeps memory usage flat regardless of file size, so a
# 500 MB video cannot exhaust RAM on a shared server.
_CHUNK_SIZE_BYTES = 1024 * 1024

# Abandon a download that stalls. The tuple is (connect timeout, read timeout):
# 30 seconds to establish the connection, then 120 seconds of silence before
# giving up. Without a read timeout, one stalled connection hangs the run
# forever.
_TIMEOUT = (30, 120)


class VideoDownloader:
    """Downloads and stores post videos, skipping ones already on disk."""

    def __init__(self, videos_dir: Path, project_root: Path, max_retries: int = 3) -> None:
        """Prepare the downloader.

        Args:
            videos_dir:   Directory that will hold the .mp4 files.
            project_root: Used to compute the RELATIVE path stored in the CSV,
                          so the dataset stays portable between the Windows
                          development machine and the Linux college server.
            max_retries:  Attempts per file before giving up on it.
        """
        self.videos_dir = videos_dir
        self.project_root = project_root
        self.max_retries = max_retries

        self.videos_dir.mkdir(parents=True, exist_ok=True)

        # One session for all downloads, reusing connections across files.
        self._session = requests.Session()

        # Running totals, reported in the end-of-run summary.
        self.downloaded_count = 0
        self.skipped_count = 0
        self.failed_count = 0

    def target_path(self, post_id: str) -> Path:
        """Return where `post_id`'s video belongs on disk.

        A single function owns this naming rule, so the download logic and the
        "already have it?" check can never disagree about the filename.
        """
        return self.videos_dir / f"{post_id}.mp4"

    def relative_path(self, absolute_path: Path) -> str:
        """Express `absolute_path` relative to the project root, POSIX-style.

        Two conversions happen here:
          * absolute -> relative, so the CSV does not embed `D:\\Aysam\\...`,
            which would be meaningless after the move to the college server;
          * Windows backslashes -> forward slashes, so the same CSV reads
            correctly on Windows, Linux and macOS.
        """
        try:
            relative = absolute_path.relative_to(self.project_root)
        except ValueError:
            # `relative_to` raises when the path lies outside the project root,
            # which happens if OUTPUT_ROOT points at a network share. Storing
            # the absolute path is the correct fallback there.
            return absolute_path.as_posix()
        return relative.as_posix()

    def download(self, post: Post, media_url: Optional[str]) -> str:
        """Download one post's video and return its project-relative path.

        Args:
            post:      The post being collected; supplies the filename.
            media_url: A directly fetchable media URL, or None when the data
                       source cannot provide one.

        Returns:
            The relative path to the saved file, or "" when no file was saved.
            The caller writes this straight into `Post.video_path`, so an empty
            string correctly reads as "no video stored for this post".
        """
        destination = self.target_path(post.post_id)

        # --- Case 1: already downloaded by an earlier run -----------------
        # Check the file size too: a zero-byte file is the residue of an
        # interrupted download and must be re-fetched, not trusted.
        if destination.exists() and destination.stat().st_size > 0:
            logger.debug("Video for post %s already on disk; skipping.", post.post_id)
            self.skipped_count += 1
            return self.relative_path(destination)

        # --- Case 2: no media URL available -------------------------------
        if not media_url:
            logger.info(
                "No downloadable media URL for post %s - metadata saved, "
                "video not stored. (See the note at the top of downloader.py.)",
                post.post_id,
            )
            self.skipped_count += 1
            return ""

        # --- Case 3: attempt the download ---------------------------------
        for attempt in range(1, self.max_retries + 1):
            try:
                if self._download_once(media_url, destination):
                    self.downloaded_count += 1
                    size_mb = destination.stat().st_size / (1024 * 1024)
                    logger.info(
                        "Downloaded %s.mp4 (%.1f MB)", post.post_id, size_mb,
                    )
                    return self.relative_path(destination)
            except requests.RequestException as exc:
                logger.warning(
                    "Download attempt %d/%d for post %s failed: %s",
                    attempt, self.max_retries, post.post_id, exc,
                )

        # Every attempt failed. This is logged as a warning rather than raised,
        # because one unavailable video must not end the whole collection run -
        # the metadata for that post is still valid and worth keeping.
        logger.warning(
            "Giving up on the video for post %s after %d attempts. "
            "Metadata is retained with an empty video_path.",
            post.post_id, self.max_retries,
        )
        self.failed_count += 1
        return ""

    def _download_once(self, media_url: str, destination: Path) -> bool:
        """Perform one streaming download attempt. Returns True on success.

        Writes to a temporary `.part` file and renames it only after the whole
        body has been received. This guarantees that a file named
        `<post_id>.mp4` in the dataset is always complete: a download
        interrupted by a crash leaves a `.part` file, which the "already on
        disk" check above correctly ignores.
        """
        temp_path = destination.with_suffix(".mp4.part")

        # `stream=True` returns as soon as the headers arrive, before the body
        # is read, which is what allows chunked writing to disk.
        with self._session.get(media_url, stream=True, timeout=_TIMEOUT) as response:
            # Raise for 4xx/5xx so the retry loop above can catch and count it.
            response.raise_for_status()

            with open(temp_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE_BYTES):
                    if chunk:  # filter out keep-alive chunks, which are empty
                        handle.write(chunk)

        # A zero-byte result means the server returned an empty body. Treat it
        # as a failure so the retry loop tries again.
        if temp_path.stat().st_size == 0:
            temp_path.unlink()
            logger.warning("Download produced an empty file; discarding it.")
            return False

        # `replace` is atomic on both Windows and POSIX: at no instant does a
        # partially written file exist under the final name.
        temp_path.replace(destination)
        return True

    def close(self) -> None:
        """Close the HTTP session."""
        self._session.close()

    def summary(self) -> str:
        """Return a one-line report of this run's download outcomes."""
        return (
            f"Videos: {self.downloaded_count} downloaded, "
            f"{self.skipped_count} skipped (already present or unavailable), "
            f"{self.failed_count} failed."
        )

    def __enter__(self) -> "VideoDownloader":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
