"""
Moving the finished dataset to the college server.

Collection always writes to `OUTPUT_ROOT`; this module is solely responsible
for getting data from there to the server. Three strategies, chosen by
`SERVER_TRANSFER_MODE`:

  none  No transfer. Correct when OUTPUT_ROOT already points at the server
        (a mounted network drive or NFS share) - the simplest setup.
  copy  Mirror the output tree to SERVER_DEST_PATH with a filesystem copy.
  sftp  Upload over SSH. Requires `pip install paramiko`.

In every case the collection logic itself is untouched.
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

    Called once, at the end of a successful collection run, so a failed
    transfer never corrupts a completed dataset - the local copy stays
    intact and the upload can simply be retried.

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

    Enumerating files explicitly, rather than copying the whole folder,
    keeps transient artefacts (e.g. `__pycache__`) off the server.
    """
    files: List[Path] = []

    for csv_path in (settings.posts_csv, settings.comments_csv):
        if csv_path.exists():
            files.append(csv_path)

    files.extend(sorted(settings.logs_dir.glob("*.log")))

    return files


def _transfer_by_copy(settings: Settings) -> None:
    """Mirror the dataset to `SERVER_DEST_PATH` using a filesystem copy."""
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
        relative = source.relative_to(settings.output_root)
        target = destination_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(source, target)
            copied += 1
        except OSError as exc:
            # One failed file should not abandon the rest.
            logger.error("Failed to copy %s: %s", relative, exc)

    logger.info("Copy complete: %d of %d file(s) transferred.", copied, len(files))

    if copied < len(files):
        raise TransferError(
            f"Only {copied} of {len(files)} files reached the server. "
            "See the log above for the individual failures."
        )


def _transfer_by_sftp(settings: Settings) -> None:
    """Upload the dataset to the server over SSH.

    Requires `paramiko`, which is not a hard dependency of the project.
    """
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

    # Host-key verification stays at its strict default: an unknown host key
    # raises rather than being auto-accepted. Connect once manually with
    # `ssh user@host` to record the key first.
    ssh_client.load_system_host_keys()

    try:
        ssh_client.connect(
            hostname=settings.server_host,
            port=settings.server_port,
            username=settings.server_user,
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
        ssh_client.close()
        logger.debug("SSH connection closed.")


def _ensure_remote_dir(sftp, remote_dir: str) -> None:
    """Create `remote_dir` on the server, including any missing parents.

    SFTP has no `mkdir -p`; each level is created and an "already exists"
    error is ignored.
    """
    parts = [part for part in remote_dir.split("/") if part]
    path_so_far = "/" if remote_dir.startswith("/") else ""

    for part in parts:
        path_so_far = f"{path_so_far.rstrip('/')}/{part}"
        try:
            sftp.mkdir(path_so_far)
        except OSError:
            pass
