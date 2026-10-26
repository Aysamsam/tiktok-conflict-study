"""
Extracting TikTok video ids from URLs a researcher pastes in.

Only the standard permalink form is supported:
    https://www.tiktok.com/@username/video/7123456789012345678

A short link (vm.tiktok.com/XXXX, vt.tiktok.com/XXXX) redirects to that form
but does not contain the id itself, so it cannot be resolved without
following the redirect - out of scope here, and callers should ask the
researcher for the full link instead.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

_VIDEO_URL_PATTERN = re.compile(r"/video/(\d+)")
_BARE_ID_PATTERN = re.compile(r"^\d{5,25}$")


def extract_video_id(url: str) -> Optional[str]:
    """Return the numeric video id in `url`, or None if it cannot be found.

    Accepts a full TikTok permalink, or a bare numeric id typed directly
    (e.g. copied from a spreadsheet without the surrounding URL).
    """
    candidate = url.strip()
    if not candidate:
        return None

    if _BARE_ID_PATTERN.match(candidate):
        return candidate

    match = _VIDEO_URL_PATTERN.search(candidate)
    if match:
        return match.group(1)

    return None


def extract_video_ids(urls: List[str]) -> Tuple[List[str], List[str]]:
    """Split `urls` into (video_ids, unparseable_urls), preserving order.

    Duplicate ids are removed - each id appears once, in first-seen order.
    """
    ids: List[str] = []
    seen = set()
    unparseable: List[str] = []

    for url in urls:
        video_id = extract_video_id(url)
        if video_id is None:
            unparseable.append(url)
            continue
        if video_id not in seen:
            seen.add(video_id)
            ids.append(video_id)

    return ids, unparseable
