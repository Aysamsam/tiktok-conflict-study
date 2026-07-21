"""
Moving the finished dataset to the college server.

WHY THIS IS A SEPARATE, PLUGGABLE MODULE
----------------------------------------
The study's stated plan is: develop locally now, store on an institutional
server later. The risk in that plan is that "later" turns into rewriting the
collection code once the server exists.

This module removes that risk. Collection ALWAYS writes to `OUTPUT_ROOT`, and
this module - and nothing else - is responsible for getting data from there to
the server. Three strategies are supported, chosen by one .env variable:

  none  No transfer. Correct when OUTPUT_ROOT already points AT the server
        (a mounted network drive or NFS share). This is the simplest and
        recommended setup, and the one to aim for: set OUTPUT_ROOT to the
        mounted research share and the data is written to the server directly,
        with no copy step at all.

  copy  Mirror the output tree to SERVER_DEST_PATH with a filesystem copy.
        Correct when the server is reachable as a path but you want a local
        working copy as well.

  sftp  Upload over SSH. Correct when the server is only reachable over the
        network. Requires `pip install paramiko`.

In every case the collection logic is identical and untouched.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import List

from .config import Settings

logger = logging.getLogger(__name__)


class TransferError(Exception):
    """Raised when the dataset could not be delivered to the server."""


def transfer_dataset(settings: Settings) -> None:
    """Deliver the collected dataset according to `SERVER_TRANSFER_MODE`.

    Called once, at the very end of a successful collection run. Transferring
    afterwards rather than during collection means a failed transfer never
    corrupts a completed dataset - the local copy stays intact and the upload
    can simply be retried.

    Args:
        settings: The validated configuration.

    Raises:
        TransferError: If the configured transfer could not be completed.
    """
    mode = settings.transfer_mode

    if mode == "none":
        logger.info(
            "SERVER_TRANSFER_MODE=none - data remains at %s. "
            "(If that path is already the server share, nothing more is needed.)",
            settings.output_root,
        )
        return

    if mode == "copy":
        _transfer_by_copy(settings)
        return

    if mode == "sftp":
        _transfer_by_sftp(settings)
        return

    # Unreachable: Settings validation rejects unknown modes at start-up.
    raise TransferError(f"Unknown transfer mode: {mode!r}")


def _files_to_transfer(settings: Settings) -> List[Path]:
    """List every file that forms part of the dataset.

    Explicitly enumerating the files - rather than copying the whole folder -
    means transient artefacts never reach the server: `.part` files from an
    interrupted download, `__pycache__` directories, editor swap files.
    """
    files: List[Path] = []

    # The two master spreadsheets.
    for csv_path in (settings.posts_csv, settings.comments_csv):
        if csv_path.exists():
            files.append(csv_path)

    # Every complete video. The `.part` suffix is excluded by the glob, so
    # partially downloaded files are left behind by construction.
    files.extend(sorted(settings.videos_dir.glob("*.mp4")))

    # Run logs travel with the data: they are the dataset's provenance record.
    files.extend(sorted(settings.logs_dir.glob("*.log")))

    return files


def _transfer_by_copy(settings: Settings) -> None:
    """Mirror the dataset to `SERVER_DEST_PATH` using a filesystem copy.

    Directory structure is preserved: a file at `<root>/data/raw_posts.csv`
    lands at `<dest>/data/raw_posts.csv`, so the layout on the server matches
    the layout described in the study's data-architecture specification.
    """
    if not settings.server_dest_path:
        raise TransferError(
            "SERVER_TRANSFER_MODE=copy but SERVER_DEST_PATH is empty."
        )

    destination_root = Path(settings.server_dest_path)

    try:
        destination_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TransferError(
            f"Cannot create or access {destination_root}: {exc}. "
            "Check that the network share is mounted and writable."
        ) from exc

    files = _files_to_transfer(settings)
    logger.info("Copying %d file(s) to %s", len(files), destination_root)

    copied = 0
    for source in files:
        # Reproduce the source's position within the project tree at the
        # destination, so the two trees are structurally identical.
        relative = source.relative_to(settings.output_root)
        target = destination_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            # `copy2` preserves modification times, which keeps the server copy
            # honest about when each file was actually produced.
            shutil.copy2(source, target)
            copied += 1
        except OSError as exc:
            # One failed file should not abandon the other 999. Log it and
            # continue; the count in the summary will reveal the shortfall.
            logger.error("Failed to copy %s: %s", relative, exc)

    logger.info("Copy complete: %d of %d file(s) transferred.", copied, len(files))

    if copied < len(files):
        raise TransferError(
            f"Only {copied} of {len(files)} files reached the server. "
            "See the log above for the individual failures."
        )


def _transfer_by_sftp(settings: Settings) -> None:
    """Upload the dataset to the server over SSH.

    Requires `paramiko`, which is deliberately NOT a hard dependency - a
    researcher running the pipeline locally, or writing straight to a mounted
    share, should not have to install an SSH library they will never use.
    """
    # Imported here rather than at module top so that the absence of paramiko
    # only matters when SFTP is actually requested.
    try:
        import paramiko  # type: ignore
    except ImportError as exc:
        raise TransferError(
            "SERVER_TRANSFER_MODE=sftp requires the paramiko package. "
            "Install it with:  pip install paramiko"
        ) from exc

    for field_name, value in (
        ("SERVER_HOST", settings.server_host),
        ("SERVER_USER", settings.server_user),
        ("SERVER_DEST_PATH", settings.server_dest_path),
    ):
        if not value:
            raise TransferError(f"SERVER_TRANSFER_MODE=sftp but {field_name} is empty.")

    logger.info(
        "Connecting to %s@%s:%d over SFTP.",
        settings.server_user, settings.server_host, settings.server_port,
    )

    ssh_client = paramiko.SSHClient()

    # Load the machine's known_hosts file so that previously verified servers
    # are recognised. Host-key verification is left at its strict default: an
    # unknown host key raises rather than being auto-accepted, because silently
    # trusting any key would expose credentials to a man-in-the-middle. Connect
    # once manually with `ssh user@host` to record the key first.
    ssh_client.load_system_host_keys()

    try:
        ssh_client.connect(
            hostname=settings.server_host,
            port=settings.server_port,
            username=settings.server_user,
            # Key-based authentication only. No password is read from the
            # config, so no password can be left sitting in a .env file.
            key_filename=settings.server_ssh_key_path or None,
            timeout=30,
        )
    except Exception as exc:
        raise TransferError(
            f"Could not connect to {settings.server_host}: {exc}. "
            "Verify the hostname, your username, and that your SSH public key "
            "is installed on the server."
        ) from exc

    try:
        sftp = ssh_client.open_sftp()
        files = _files_to_transfer(settings)
        logger.info("Uploading %d file(s) to %s", len(files), settings.server_dest_path)

        uploaded = 0
        for source in files:
            relative = source.relative_to(settings.output_root)
            # POSIX separators: the server is Linux even when we are on Windows.
            remote_path = f"{settings.server_dest_path.rstrip('/')}/{relative.as_posix()}"

            _ensure_remote_dir(sftp, remote_path.rsplit("/", 1)[0])

            try:
                sftp.put(str(source), remote_path)
                uploaded += 1
                logger.debug("Uploaded %s", relative)
            except OSError as exc:
                logger.error("Failed to upload %s: %s", relative, exc)

        logger.info("Upload complete: %d of %d file(s).", uploaded, len(files))

        if uploaded < len(files):
            raise TransferError(
                f"Only {uploaded} of {len(files)} files were uploaded successfully."
            )
    finally:
        # `finally` guarantees the SSH connection is closed even if an upload
        # raised, so no session is left dangling on the server.
        ssh_client.close()
        logger.debug("SSH connection closed.")


def _ensure_remote_dir(sftp, remote_dir: str) -> None:
    """Create `remote_dir` on the server, including any missing parents.

    SFTP has no equivalent of `mkdir -p`, so directories must be created one
    level at a time. Each level is attempted and an "already exists" error is
    ignored, which is both simpler and race-safe compared with checking first
    and then creating.
    """
    parts = [part for part in remote_dir.split("/") if part]
    # Preserve a leading slash for absolute server paths.
    path_so_far = "/" if remote_dir.startswith("/") else ""

    for part in parts:
        path_so_far = f"{path_so_far.rstrip('/')}/{part}"
        try:
            sftp.mkdir(path_so_far)
        except OSError:
            # Already exists (the common case) - nothing to do.
            pass
