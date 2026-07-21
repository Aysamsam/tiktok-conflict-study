"""
Automated tests for the collection pipeline.

WHY A RESEARCH SCRIPT NEEDS TESTS
---------------------------------
A bug in analysis code produces a wrong number, which a reviewer might catch.
A bug in COLLECTION code produces a wrong dataset, and by the time anyone
notices, the posts have been deleted, the engagement counts have changed, and
the data cannot be re-collected. Collection code has one chance to be correct.

These tests verify the properties the study's validity depends on:

  * the CSV columns match the documented schema exactly;
  * no post or comment is ever written twice (which would inflate every count);
  * every comment maps to a post that exists (referential integrity);
  * an interrupted run leaves valid data and resumes without duplicating;
  * posts outside the study's criteria are excluded, and posts inside are not;
  * text containing commas, quotes, newlines and emoji survives the round trip.

RUNNING THEM
------------
    pip install pytest
    python -m pytest tests/ -v

Every test runs offline against the mock client in a temporary directory, so
they need no credentials, touch no real data, and finish in about a second.
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import pytest

# Make the project root importable when pytest is run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.clients.mock_client import MockTikTokClient          # noqa: E402
from src.config import ConfigError, Settings                  # noqa: E402
from src.models import COMMENT_CSV_COLUMNS, POST_CSV_COLUMNS, Comment, Post  # noqa: E402
from src.pipeline import CollectionPipeline                   # noqa: E402
from src.storage import open_posts_writer                     # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Build a Settings object pointing at a temporary directory.

    Constructing `Settings` directly - rather than through `load_settings()` -
    keeps the tests completely independent of whatever is in the developer's
    real .env file. A test that passes on one machine therefore passes on all
    of them.
    """
    defaults = dict(
        client_type="mock",
        client_key="",
        client_secret="",
        oauth_url="https://example.invalid/oauth",
        video_query_url="https://example.invalid/video",
        comment_query_url="https://example.invalid/comment",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 7, 31),
        hashtags=["IsraelIran", "IsraelIranWar", "Iranwar", "Iran"],
        language="en",
        # Language detection is switched off in most tests so that the mock's
        # synthetic captions are not filtered out. The filter has its own
        # dedicated tests in TestLanguageFilter below.
        language_mode="off",
        max_posts=5,
        search_overfetch_factor=3,
        max_comments_per_post=10,
        daily_request_quota=1000,
        output_root=tmp_path,
        download_videos=False,      # no network in tests
        transfer_mode="none",
        server_dest_path="",
        server_host="",
        server_port=22,
        server_user="",
        server_ssh_key_path="",
        request_delay_seconds=0.0,  # no artificial delay in tests
        max_retries=1,
        log_level="WARNING",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """A ready-to-use configuration in a per-test temporary directory."""
    return make_settings(tmp_path)


def read_csv(path: Path) -> list:
    """Read a CSV into a list of dictionaries."""
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------

class TestConfiguration:
    """The configuration layer must reject bad input at start-up, not mid-run."""

    def test_reversed_date_range_is_rejected(self, tmp_path):
        """An end date before the start date must fail immediately."""
        with pytest.raises(ConfigError, match="after"):
            make_settings(tmp_path, start_date=date(2026, 7, 31), end_date=date(2026, 4, 1))

    def test_empty_hashtag_list_is_rejected(self, tmp_path):
        """Searching for nothing is always a configuration mistake."""
        with pytest.raises(ConfigError, match="empty"):
            make_settings(tmp_path, hashtags=[])

    def test_unknown_client_type_is_rejected(self, tmp_path):
        """A typo in TIKTOK_CLIENT must be caught, not silently defaulted."""
        with pytest.raises(ConfigError, match="mock"):
            make_settings(tmp_path, client_type="tiktok_scraper")

    def test_research_api_requires_real_credentials(self, tmp_path):
        """Selecting the live API with placeholder keys must fail loudly."""
        with pytest.raises(ConfigError, match="placeholder"):
            make_settings(
                tmp_path,
                client_type="research_api",
                client_key="REPLACE_ME_WITH_REAL_CLIENT_KEY",
                client_secret="something",
            )

    def test_directory_layout_is_derived_from_output_root(self, tmp_path):
        """Changing OUTPUT_ROOT must relocate the ENTIRE dataset.

        This is the property that makes the local-to-server migration a
        one-line change, so it is worth asserting explicitly.
        """
        s = make_settings(tmp_path)
        assert s.posts_csv == tmp_path / "data" / "raw_posts.csv"
        assert s.comments_csv == tmp_path / "data" / "raw_comments.csv"
        assert s.videos_dir == tmp_path / "downloads" / "videos"


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class TestSchema:
    """The CSV files must match the schema documented in the study protocol."""

    def test_posts_csv_has_the_documented_columns(self, settings):
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        with open(settings.posts_csv, newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        assert header == POST_CSV_COLUMNS

    def test_comments_csv_has_the_documented_columns(self, settings):
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        with open(settings.comments_csv, newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        assert header == COMMENT_CSV_COLUMNS

    def test_ai_label_is_written_as_1_or_0(self, settings):
        """The schema specifies a numeric flag, not the text True/False.

        Numeric flags import cleanly into R and SPSS; the strings "True" and
        "False" are read as text and silently break numeric summaries.
        """
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        for row in read_csv(settings.posts_csv):
            assert row["has_ai_label"] in {"0", "1"}

    def test_post_ids_are_preserved_exactly(self, settings):
        """19-digit IDs must survive as strings, with no rounding.

        Treating these as floats anywhere in the chain would corrupt the key
        that links posts, comments and video files together.
        """
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        for row in read_csv(settings.posts_csv):
            assert row["post_id"].isdigit()
            assert len(row["post_id"]) == 19


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------

class TestDataIntegrity:
    """Properties the study's statistical validity depends upon."""

    def test_no_duplicate_posts(self, settings):
        """Every post_id must appear exactly once."""
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        ids = [row["post_id"] for row in read_csv(settings.posts_csv)]
        assert len(ids) == len(set(ids))

    def test_no_duplicate_comments(self, settings):
        """Every comment_id must appear exactly once."""
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        ids = [row["comment_id"] for row in read_csv(settings.comments_csv)]
        assert len(ids) == len(set(ids))

    def test_every_comment_maps_to_a_collected_post(self, settings):
        """Referential integrity: no orphan comments.

        A comment whose post_id is absent from raw_posts.csv cannot be
        analysed, because none of the post-level variables are available.
        """
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        post_ids = {row["post_id"] for row in read_csv(settings.posts_csv)}
        for comment in read_csv(settings.comments_csv):
            assert comment["post_id"] in post_ids

    def test_comments_never_predate_their_post(self, settings):
        """A comment cannot exist before the video it replies to.

        This guards a real bug found during development, where the mock
        generated comment timestamps independently of the post's own.
        """
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        post_times = {r["post_id"]: r["create_time"] for r in read_csv(settings.posts_csv)}
        for comment in read_csv(settings.comments_csv):
            # ISO 8601 strings compare correctly as plain text.
            assert comment["create_time"] >= post_times[comment["post_id"]]

    def test_comment_cap_is_respected(self, settings):
        """No post may exceed STUDY_MAX_COMMENTS_PER_POST comments."""
        settings = make_settings(settings.output_root, max_comments_per_post=4)
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()

        counts = {}
        for comment in read_csv(settings.comments_csv):
            counts[comment["post_id"]] = counts.get(comment["post_id"], 0) + 1

        assert all(count <= 4 for count in counts.values())

    def test_sample_size_is_respected(self, settings):
        """The run must stop at exactly STUDY_MAX_POSTS posts."""
        settings = make_settings(settings.output_root, max_posts=3)
        stats = CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        assert stats.posts_saved == 3
        assert len(read_csv(settings.posts_csv)) == 3

    def test_target_sample_size_is_reached_despite_language_filtering(self, tmp_path):
        """Local filtering must not silently shrink the sample.

        The language criterion is applied AFTER retrieval, so some fetched
        posts are discarded. Without over-fetching, asking for 20 posts and
        rejecting 3 as non-English would quietly yield 17 - undershooting the
        study design in a way nothing in the output would flag.
        """
        settings = make_settings(
            tmp_path,
            max_posts=10,
            language_mode="detect",     # the real, filtering configuration
            search_overfetch_factor=3,
        )
        stats = CollectionPipeline(settings, client=MockTikTokClient(seed=7)).run()

        assert stats.posts_saved == 10, (
            f"asked for 10 posts, got {stats.posts_saved}; "
            f"{stats.posts_skipped_ineligible} were filtered out and the "
            "over-fetch did not compensate"
        )


# ---------------------------------------------------------------------------
# Resumability
# ---------------------------------------------------------------------------

class TestResumability:
    """An interrupted run must resume cleanly without duplicating data."""

    def test_second_run_adds_nothing(self, settings):
        """Re-running a completed collection must be a no-op.

        This is what makes the collector safe to re-launch after a crash, a
        rate-limit pause, or an overnight server reboot.

        Note that the sample size is measured against the TOTAL dataset, not
        against what one run happened to add. Were it otherwise, every re-run
        would append another full sample and silently inflate the study's n.
        """
        first = CollectionPipeline(settings, client=MockTikTokClient()).run()
        assert first.posts_saved == 5

        second = CollectionPipeline(settings, client=MockTikTokClient()).run()
        assert second.posts_saved == 0
        assert len(read_csv(settings.posts_csv)) == 5

    def test_completed_sample_does_not_contact_the_api(self, settings):
        """A finished collection must not spend API quota re-searching.

        The daily quota is 1000 requests; wasting it re-fetching posts that are
        already on disk, only to discard them as duplicates, is a real cost.
        """
        CollectionPipeline(settings, client=MockTikTokClient()).run()

        class ExplodingClient(MockTikTokClient):
            """Fails loudly if the pipeline tries to search."""
            def search_posts(self, *args, **kwargs):
                raise AssertionError("the API was contacted for a complete sample")

        second = CollectionPipeline(settings, client=ExplodingClient()).run()
        assert second.posts_saved == 0

    def test_raising_the_target_extends_an_existing_sample(self, tmp_path):
        """Increasing STUDY_MAX_POSTS must top the sample up, not restart it."""
        first_settings = make_settings(tmp_path, max_posts=5)
        CollectionPipeline(first_settings, client=MockTikTokClient()).run()
        assert len(read_csv(first_settings.posts_csv)) == 5

        second_settings = make_settings(tmp_path, max_posts=12)
        CollectionPipeline(second_settings, client=MockTikTokClient()).run()

        rows = read_csv(second_settings.posts_csv)
        assert len(rows) == 12
        assert len({r["post_id"] for r in rows}) == 12, "top-up introduced duplicates"

    def test_mock_client_is_deterministic(self, settings):
        """The same configuration must produce the same synthetic post IDs.

        This guards a real bug found during development: the seed was derived
        from Python's built-in hash(), which is randomised per process, so
        every run produced different IDs and deduplication could never engage.
        """
        def collect_ids():
            client = MockTikTokClient()
            return [
                p.post_id for p in client.search_posts(
                    hashtags=settings.hashtags,
                    start_date=settings.start_date,
                    end_date=settings.end_date,
                    language=settings.language,
                    max_posts=5,
                )
            ]

        assert collect_ids() == collect_ids()

    def test_partial_file_is_extended_not_overwritten(self, settings):
        """Existing rows must survive a subsequent run."""
        # Simulate a previous run that captured one post.
        settings.ensure_directories()
        with open_posts_writer(settings.posts_csv) as writer:
            writer.write_row(Post(
                post_id="1234567890123456789",
                username="earlier_run",
                author_region="US",
                create_time="2026-05-01T00:00:00+00:00",
                caption="from an earlier run",
                hashtags=["Iran"],
                video_url="https://www.tiktok.com/@earlier_run/video/1234567890123456789",
                has_ai_label=False,
                view_count=1, like_count=1, comment_count=0, share_count=0,
            ).to_csv_row())

        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()

        usernames = [row["username"] for row in read_csv(settings.posts_csv)]
        assert "earlier_run" in usernames, "the earlier run's data was destroyed"


# ---------------------------------------------------------------------------
# Inclusion criteria
# ---------------------------------------------------------------------------

class TestEligibilityFiltering:
    """Posts must be included or excluded strictly per the study criteria."""

    def _pipeline(self, settings):
        return CollectionPipeline(settings, client=MockTikTokClient(seed=1))

    def _post(self, **overrides) -> Post:
        defaults = dict(
            post_id="7000000000000000001",
            username="tester",
            author_region="US",
            create_time="2026-05-15T12:00:00+00:00",
            caption="test",
            hashtags=["Iran"],
            video_url="https://example.invalid",
            has_ai_label=False,
            view_count=0, like_count=0, comment_count=0, share_count=0,
        )
        defaults.update(overrides)
        return Post(**defaults)

    def test_post_with_a_study_hashtag_is_included(self, settings):
        assert self._pipeline(settings)._is_eligible(self._post(hashtags=["Iran"]))

    def test_hashtag_matching_ignores_case(self, settings):
        """TikTok preserves the author's capitalisation; the study must not."""
        assert self._pipeline(settings)._is_eligible(self._post(hashtags=["ISRAELIRAN"]))
        assert self._pipeline(settings)._is_eligible(self._post(hashtags=["israeliran"]))

    def test_post_without_any_study_hashtag_is_excluded(self, settings):
        assert not self._pipeline(settings)._is_eligible(
            self._post(hashtags=["cooking", "cats"])
        )

    def test_post_outside_the_date_window_is_excluded(self, settings):
        """A post from before the study window must not enter the sample."""
        assert not self._pipeline(settings)._is_eligible(
            self._post(create_time="2026-01-01T12:00:00+00:00")
        )

    def test_post_on_the_window_boundary_is_included(self, settings):
        """Both endpoints are inclusive - a classic off-by-one to guard."""
        pipeline = self._pipeline(settings)
        assert pipeline._is_eligible(self._post(create_time="2026-04-01T00:00:00+00:00"))
        assert pipeline._is_eligible(self._post(create_time="2026-07-31T23:59:59+00:00"))


# ---------------------------------------------------------------------------
# Text handling
# ---------------------------------------------------------------------------

class TestTextHandling:
    """Awkward characters must survive the write/read round trip intact."""

    def test_difficult_text_round_trips_exactly(self, settings):
        """Commas, quotes, newlines, emoji and non-Latin script must survive.

        These are precisely the characters that naive CSV writing destroys, and
        precisely the characters this study's data is full of.
        """
        nasty = 'Caption with, a comma; "quotes"; a\nnewline; emoji \U0001F4A5; عربي; עברית; فارسی'

        settings.ensure_directories()
        with open_posts_writer(settings.posts_csv) as writer:
            writer.write_row(Post(
                post_id="7000000000000000009",
                username="tester",
                author_region="",
                create_time="2026-05-01T00:00:00+00:00",
                caption=nasty,
                hashtags=["Iran", "news"],
                video_url="https://example.invalid",
                has_ai_label=True,
                view_count=1, like_count=1, comment_count=1, share_count=1,
            ).to_csv_row())

        rows = read_csv(settings.posts_csv)
        assert len(rows) == 1, "the newline in the caption split one row into several"
        assert rows[0]["caption"] == nasty

    def test_hashtag_list_is_flattened_to_a_comma_separated_cell(self):
        post = Post(
            post_id="1", username="u", author_region="", create_time="",
            caption="", hashtags=["IsraelIran", "news", "osint"],
            video_url="", has_ai_label=False,
            view_count=0, like_count=0, comment_count=0, share_count=0,
        )
        assert post.to_csv_row()["hashtags"] == "IsraelIran,news,osint"

    def test_missing_region_becomes_an_empty_cell(self, settings):
        """An absent optional field must read as blank, not as the word 'None'."""
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        for row in read_csv(settings.posts_csv):
            assert row["author_region"] != "None"


# ---------------------------------------------------------------------------
# Date-window splitting (a Research API constraint)
# ---------------------------------------------------------------------------

class TestAiLabelExtraction:
    """The study's key variable, parsed from the API's `video_tag` field.

    An error here would be near-invisible in the finished dataset: every post
    would simply read as not-AI-labelled, and nothing would look broken. These
    tests pin the mapping documented by TikTok:

        {"type": "AIGC",    "number": 1} -> Creator Labelled as AI-Generated
        {"type": "AIGC",    "number": 2} -> AI-Generated
        {"type": "Branded", "number": 1} -> Paid Partnership (NOT an AI label)
    """

    def _extract(self, raw):
        from src.clients.research_client import ResearchApiClient
        return ResearchApiClient._extract_ai_label(raw)

    def test_no_video_tag_means_no_label(self):
        assert self._extract({"id": "1"}) == (False, "")

    def test_empty_video_tag_means_no_label(self):
        assert self._extract({"id": "1", "video_tag": []}) == (False, "")

    def test_aigc_number_1_is_creator_labelled(self):
        raw = {"id": "1", "video_tag": [{"type": "AIGC", "number": 1}]}
        assert self._extract(raw) == (True, "creator_labelled")

    def test_aigc_number_2_is_platform_detected(self):
        raw = {"id": "1", "video_tag": [{"type": "AIGC", "number": 2}]}
        assert self._extract(raw) == (True, "ai_generated")

    def test_branded_tag_is_not_an_ai_label(self):
        """A Paid Partnership disclosure must not be counted as AI content.

        This is the mistake that would inflate the study's headline number.
        """
        raw = {"id": "1", "video_tag": [{"type": "Branded", "number": 1}]}
        assert self._extract(raw) == (False, "")

    def test_a_single_struct_is_handled_like_a_list(self):
        """The API is loosely documented on whether video_tag is a list."""
        raw = {"id": "1", "video_tag": {"type": "AIGC", "number": 2}}
        assert self._extract(raw) == (True, "ai_generated")

    def test_mixed_tags_find_the_aigc_one(self):
        raw = {"id": "1", "video_tag": [
            {"type": "Branded", "number": 1},
            {"type": "AIGC", "number": 1},
        ]}
        assert self._extract(raw) == (True, "creator_labelled")

    def test_platform_detection_wins_over_self_disclosure(self):
        """When both are present, the stronger provenance evidence is recorded."""
        raw = {"id": "1", "video_tag": [
            {"type": "AIGC", "number": 1},
            {"type": "AIGC", "number": 2},
        ]}
        assert self._extract(raw) == (True, "ai_generated")

    def test_type_matching_is_case_insensitive(self):
        raw = {"id": "1", "video_tag": [{"type": "aigc", "number": 2}]}
        assert self._extract(raw) == (True, "ai_generated")

    def test_unknown_aigc_number_still_counts_as_labelled(self):
        """If TikTok adds a third AIGC value, it must not read as 'no label'."""
        raw = {"id": "1", "video_tag": [{"type": "AIGC", "number": 99}]}
        has_label, _ = self._extract(raw)
        assert has_label is True

    def test_malformed_tag_does_not_crash(self):
        raw = {"id": "1", "video_tag": ["not a dict", None, 42]}
        assert self._extract(raw) == (False, "")


class TestQueryableFields:
    """Guards the bug where an unsupported filter field 400s every request."""

    def test_language_is_not_a_queryable_field(self):
        """Documents WHY language filtering happens locally.

        An earlier version sent a `video_language` condition, which the video
        endpoint does not accept - every request would have failed with an
        opaque HTTP 400.
        """
        from src.clients.research_client import _QUERYABLE_FIELDS
        assert "video_language" not in _QUERYABLE_FIELDS
        assert "language" not in _QUERYABLE_FIELDS

    def test_hashtag_name_is_queryable(self):
        from src.clients.research_client import _QUERYABLE_FIELDS
        assert "hashtag_name" in _QUERYABLE_FIELDS

    def test_search_body_only_uses_queryable_fields(self):
        """Every condition the client builds must use an accepted field."""
        from src.clients.research_client import ResearchApiClient, _QUERYABLE_FIELDS
        client = ResearchApiClient(
            client_key="k", client_secret="s",
            oauth_url="https://example.invalid",
            video_query_url="https://example.invalid",
            comment_query_url="https://example.invalid",
        )
        body = client._build_search_body(
            hashtags=["Iran"], language="en",
            start_date=date(2026, 4, 1), end_date=date(2026, 4, 30),
            page_size=100, cursor=None, search_id=None,
        )
        for condition in body["query"]["and"]:
            assert condition["field_name"] in _QUERYABLE_FIELDS

    def test_dates_are_formatted_compactly(self):
        """The API wants '20260401', not '2026-04-01'."""
        from src.clients.research_client import ResearchApiClient
        client = ResearchApiClient(
            client_key="k", client_secret="s",
            oauth_url="https://example.invalid",
            video_query_url="https://example.invalid",
            comment_query_url="https://example.invalid",
        )
        body = client._build_search_body(
            hashtags=["Iran"], language="en",
            start_date=date(2026, 4, 1), end_date=date(2026, 4, 30),
            page_size=100, cursor=None, search_id=None,
        )
        assert body["start_date"] == "20260401"
        assert body["end_date"] == "20260430"

    def test_first_request_omits_pagination_fields(self):
        """Sending a null cursor on the first request causes a 400."""
        from src.clients.research_client import ResearchApiClient
        client = ResearchApiClient(
            client_key="k", client_secret="s",
            oauth_url="https://example.invalid",
            video_query_url="https://example.invalid",
            comment_query_url="https://example.invalid",
        )
        body = client._build_search_body(
            hashtags=["Iran"], language="en",
            start_date=date(2026, 4, 1), end_date=date(2026, 4, 30),
            page_size=100, cursor=None, search_id=None,
        )
        assert "cursor" not in body
        assert "search_id" not in body


class TestLanguageFilter:
    """Local language detection, used because the API offers no such filter."""

    def test_cleaning_removes_language_neutral_noise(self):
        """Hashtags, mentions, URLs and emoji mislead the detector."""
        from src.language import clean_for_detection
        cleaned = clean_for_detection(
            "Breaking news from the region \U0001F525 #Iran #IsraelIran "
            "@someuser https://example.com/article"
        )
        assert "#" not in cleaned
        assert "@" not in cleaned
        assert "http" not in cleaned
        assert "Breaking news from the region" in cleaned

    def test_mode_off_accepts_everything(self):
        from src.language import LanguageFilter
        f = LanguageFilter(target_language="en", mode="off")
        keep, _ = f.matches("این یک متن فارسی است که باید پذیرفته شود")
        assert keep is True

    def test_short_captions_are_kept_by_default(self):
        """Conservative default: over-collect rather than discard permanently.

        A post excluded at collection time cannot be recovered later; a post
        collected in error can always be filtered during analysis.
        """
        from src.language import LanguageFilter
        f = LanguageFilter(target_language="en", mode="detect")
        keep, reason = f.matches("ok")
        assert keep is True
        assert "too short" in reason

    def test_short_captions_can_be_dropped_when_strict(self):
        from src.language import LanguageFilter
        f = LanguageFilter(target_language="en", mode="detect", keep_undetermined=False)
        keep, _ = f.matches("ok")
        assert keep is False

    def test_summary_counts_every_decision(self):
        """Exclusion counts are numbers a methods section must report."""
        from src.language import LanguageFilter
        f = LanguageFilter(target_language="en", mode="off")
        for _ in range(3):
            f.matches("some caption text here")
        assert f.checked == 3
        assert "3" in f.summary()


class TestDateWindowSplitting:
    """The 4-month study window must be split into legal <=30-day queries."""

    def _client(self):
        from src.clients.research_client import ResearchApiClient
        return ResearchApiClient(
            client_key="k", client_secret="s",
            oauth_url="https://example.invalid",
            video_query_url="https://example.invalid",
            comment_query_url="https://example.invalid",
        )

    def test_full_study_window_is_split_into_legal_chunks(self):
        chunks = self._client()._split_date_window(date(2026, 4, 1), date(2026, 7, 31))
        for start, end in chunks:
            assert (end - start).days < 30, f"chunk {start}..{end} exceeds the API limit"

    def test_chunks_cover_the_window_with_no_gaps_or_overlaps(self):
        """Gaps would lose posts; overlaps would duplicate them and inflate n."""
        chunks = self._client()._split_date_window(date(2026, 4, 1), date(2026, 7, 31))

        assert chunks[0][0] == date(2026, 4, 1), "the window's first day was skipped"
        assert chunks[-1][1] == date(2026, 7, 31), "the window's last day was skipped"

        for (_, previous_end), (next_start, _) in zip(chunks, chunks[1:]):
            assert (next_start - previous_end).days == 1, "gap or overlap between chunks"

    def test_a_short_window_is_not_split(self):
        chunks = self._client()._split_date_window(date(2026, 4, 1), date(2026, 4, 10))
        assert chunks == [(date(2026, 4, 1), date(2026, 4, 10))]
