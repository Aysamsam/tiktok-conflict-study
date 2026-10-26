"""
Tests for src/urls.py - parsing video ids out of researcher-supplied URLs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.urls import extract_video_id, extract_video_ids  # noqa: E402


class TestExtractVideoId:

    def test_standard_permalink(self):
        url = "https://www.tiktok.com/@someuser/video/7623343101167422741"
        assert extract_video_id(url) == "7623343101167422741"

    def test_permalink_with_query_string(self):
        url = "https://www.tiktok.com/@someuser/video/7623343101167422741?is_from_webapp=1"
        assert extract_video_id(url) == "7623343101167422741"

    def test_bare_id(self):
        assert extract_video_id("7623343101167422741") == "7623343101167422741"

    def test_bare_id_with_surrounding_whitespace(self):
        assert extract_video_id("  7623343101167422741  ") == "7623343101167422741"

    def test_short_link_is_unparseable(self):
        """vm.tiktok.com/vt.tiktok.com links don't contain the id - out of scope."""
        assert extract_video_id("https://vm.tiktok.com/ZMabcdefg/") is None

    def test_unrelated_url_is_unparseable(self):
        assert extract_video_id("https://example.com/not-tiktok") is None

    def test_empty_string_is_unparseable(self):
        assert extract_video_id("") is None
        assert extract_video_id("   ") is None


class TestExtractVideoIds:

    def test_mixed_valid_and_invalid(self):
        urls = [
            "https://www.tiktok.com/@a/video/111",
            "not a url",
            "https://www.tiktok.com/@b/video/222",
        ]
        ids, unparseable = extract_video_ids(urls)
        assert ids == ["111", "222"]
        assert unparseable == ["not a url"]

    def test_duplicates_collapsed_preserving_first_seen_order(self):
        urls = [
            "https://www.tiktok.com/@a/video/111",
            "https://www.tiktok.com/@b/video/222",
            "https://www.tiktok.com/@c/video/111",
        ]
        ids, unparseable = extract_video_ids(urls)
        assert ids == ["111", "222"]
        assert unparseable == []

    def test_empty_input(self):
        assert extract_video_ids([]) == ([], [])
