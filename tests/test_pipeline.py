"""
Automated tests for the collection pipeline.

Verifies the properties the study's validity depends on: the CSV schema
matches spec, no post or comment is ever written twice, every comment maps
to an existing post, an interrupted run resumes without duplicating,
inclusion/exclusion criteria are applied correctly, and text containing
commas, quotes, newlines and emoji survives the round trip.

Run with:
    pip install pytest
    python -m pytest tests/ -v

All tests run offline against the mock client, so they need no credentials
and finish in about a second.
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
from src.models import COMMENT_CSV_COLUMNS, POST_CSV_COLUMNS, Post  # noqa: E402
from src.pipeline import CollectionPipeline                   # noqa: E402
from src.storage import open_comments_writer, open_posts_writer  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Build a Settings object pointing at a temporary directory.

    Constructs `Settings` directly rather than via `load_settings()`, so
    tests are independent of whatever is in the developer's real .env file.
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
        keywords=[],
        languages=["en"],
        random_order=False,
        # Off by default so the mock's synthetic captions aren't filtered
        # out; language detection has its own tests in TestLanguageFilter.
        language_mode="off",
        max_posts=5,
        search_overfetch_factor=3,
        max_comments_per_post=10,
        min_comments=0,
        max_video_duration_seconds=0,
        require_ai_label=False,
        require_regions=[],
        exclude_regions=[],
        require_hashtag_contains="",
        sort_by="none",
        boost_hashtags=[],
        boost_multiplier=1.2,
        max_posts_per_account=0,
        min_posts_per_hashtag=0,
        max_posts_per_hashtag=0,
        daily_request_quota=1000,
        output_root=tmp_path,
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
        with pytest.raises(ConfigError, match="after"):
            make_settings(tmp_path, start_date=date(2026, 7, 31), end_date=date(2026, 4, 1))

    def test_empty_hashtags_and_keywords_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="empty"):
            make_settings(tmp_path, hashtags=[], keywords=[])

    def test_empty_hashtags_with_keywords_set_is_allowed(self, tmp_path):
        """Keyword-only search (no hashtags at all) must be a valid config."""
        s = make_settings(tmp_path, hashtags=[], keywords=["iran war"])
        assert s.hashtags == []
        assert s.keywords == ["iran war"]

    def test_unknown_client_type_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="mock"):
            make_settings(tmp_path, client_type="tiktok_scraper")

    def test_research_api_requires_real_credentials(self, tmp_path):
        with pytest.raises(ConfigError, match="placeholder"):
            make_settings(
                tmp_path,
                client_type="research_api",
                client_key="REPLACE_ME_WITH_REAL_CLIENT_KEY",
                client_secret="something",
            )

    def test_directory_layout_is_derived_from_output_root(self, tmp_path):
        """Changing OUTPUT_ROOT must relocate the entire dataset."""
        s = make_settings(tmp_path)
        assert s.posts_csv == tmp_path / "data" / "raw_posts.csv"
        assert s.comments_csv == tmp_path / "data" / "raw_comments.csv"


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
        """A numeric flag, not the text True/False."""
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        for row in read_csv(settings.posts_csv):
            assert row["has_ai_label"] in {"0", "1"}

    def test_post_ids_are_preserved_exactly(self, settings):
        """19-digit IDs must survive as strings, with no rounding."""
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
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        ids = [row["post_id"] for row in read_csv(settings.posts_csv)]
        assert len(ids) == len(set(ids))

    def test_no_duplicate_comments(self, settings):
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        ids = [row["comment_id"] for row in read_csv(settings.comments_csv)]
        assert len(ids) == len(set(ids))

    def test_every_comment_maps_to_a_collected_post(self, settings):
        """No orphan comments: every post_id must exist in raw_posts.csv."""
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        post_ids = {row["post_id"] for row in read_csv(settings.posts_csv)}
        for comment in read_csv(settings.comments_csv):
            assert comment["post_id"] in post_ids

    def test_comments_never_predate_their_post(self, settings):
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        post_times = {r["post_id"]: r["create_time"] for r in read_csv(settings.posts_csv)}
        for comment in read_csv(settings.comments_csv):
            # ISO 8601 strings compare correctly as plain text.
            assert comment["create_time"] >= post_times[comment["post_id"]]

    def test_comment_cap_is_respected(self, settings):
        settings = make_settings(settings.output_root, max_comments_per_post=4)
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()

        counts = {}
        for comment in read_csv(settings.comments_csv):
            counts[comment["post_id"]] = counts.get(comment["post_id"], 0) + 1

        assert all(count <= 4 for count in counts.values())

    def test_sample_size_is_respected(self, settings):
        settings = make_settings(settings.output_root, max_posts=3)
        stats = CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        assert stats.posts_saved == 3
        assert len(read_csv(settings.posts_csv)) == 3

    def test_target_sample_size_is_reached_despite_language_filtering(self, tmp_path):
        """Local filtering must not silently shrink the sample below target."""
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
        """Sample size is measured against the total dataset, not just this run's additions."""
        first = CollectionPipeline(settings, client=MockTikTokClient()).run()
        assert first.posts_saved == 5

        second = CollectionPipeline(settings, client=MockTikTokClient()).run()
        assert second.posts_saved == 0
        assert len(read_csv(settings.posts_csv)) == 5

    def test_completed_sample_does_not_contact_the_api(self, settings):
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
        """The same configuration must produce the same synthetic post IDs."""
        def collect_ids():
            client = MockTikTokClient()
            return [
                p.post_id for p in client.search_posts(
                    hashtags=settings.hashtags,
                    start_date=settings.start_date,
                    end_date=settings.end_date,
                    language=", ".join(settings.languages),
                    max_posts=5,
                )
            ]

        assert collect_ids() == collect_ids()

    def test_partial_file_is_extended_not_overwritten(self, settings):
        """Existing rows must survive a subsequent run."""
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
        assert self._pipeline(settings)._is_eligible(self._post(hashtags=["ISRAELIRAN"]))
        assert self._pipeline(settings)._is_eligible(self._post(hashtags=["israeliran"]))

    def test_post_without_any_study_hashtag_is_excluded(self, settings):
        assert not self._pipeline(settings)._is_eligible(
            self._post(hashtags=["cooking", "cats"])
        )

    def test_post_outside_the_date_window_is_excluded(self, settings):
        assert not self._pipeline(settings)._is_eligible(
            self._post(create_time="2026-01-01T12:00:00+00:00")
        )

    def test_post_on_the_window_boundary_is_included(self, settings):
        """Both endpoints are inclusive - a classic off-by-one to guard."""
        pipeline = self._pipeline(settings)
        assert pipeline._is_eligible(self._post(create_time="2026-04-01T00:00:00+00:00"))
        assert pipeline._is_eligible(self._post(create_time="2026-07-31T23:59:59+00:00"))

    def test_require_ai_label_excludes_unlabelled_posts(self, tmp_path):
        settings = make_settings(tmp_path, require_ai_label=True)
        pipeline = self._pipeline(settings)
        assert not pipeline._is_eligible(self._post(has_ai_label=False))
        assert pipeline._is_eligible(self._post(has_ai_label=True))

    def test_ai_label_not_required_by_default(self, settings):
        assert self._pipeline(settings)._is_eligible(self._post(has_ai_label=False))

    def test_require_regions_excludes_other_regions(self, tmp_path):
        settings = make_settings(tmp_path, require_regions=["IR"])
        pipeline = self._pipeline(settings)
        assert not pipeline._is_eligible(self._post(author_region="US"))
        assert pipeline._is_eligible(self._post(author_region="IR"))

    def test_require_regions_matches_case_insensitively(self, tmp_path):
        """TikTok returns region codes in inconsistent case ("US" vs "us")."""
        settings = make_settings(tmp_path, require_regions=["IR"])
        pipeline = self._pipeline(settings)
        assert pipeline._is_eligible(self._post(author_region="ir"))

    def test_region_not_required_by_default(self, settings):
        assert self._pipeline(settings)._is_eligible(self._post(author_region="US"))

    def test_exclude_regions_excludes_the_named_region(self, tmp_path):
        settings = make_settings(tmp_path, exclude_regions=["PK"])
        pipeline = self._pipeline(settings)
        assert not pipeline._is_eligible(self._post(author_region="PK"))
        assert pipeline._is_eligible(self._post(author_region="US"))

    def test_exclude_regions_matches_case_insensitively(self, tmp_path):
        settings = make_settings(tmp_path, exclude_regions=["PK"])
        pipeline = self._pipeline(settings)
        assert not pipeline._is_eligible(self._post(author_region="pk"))

    def test_exclude_regions_not_enforced_by_default(self, settings):
        assert self._pipeline(settings)._is_eligible(self._post(author_region="PK"))

    def test_require_hashtag_contains_excludes_posts_with_no_matching_tag(self, tmp_path):
        """Primary match can come from a keyword, independent of the hashtags."""
        settings = make_settings(
            tmp_path, keywords=["breaking news"], require_hashtag_contains="iran",
        )
        pipeline = self._pipeline(settings)
        post = self._post(caption="breaking news today", hashtags=["fyp", "worldnews"])
        assert not pipeline._is_eligible(post)

    def test_require_hashtag_contains_includes_posts_with_a_matching_tag(self, tmp_path):
        settings = make_settings(
            tmp_path, keywords=["breaking news"], require_hashtag_contains="iran",
        )
        pipeline = self._pipeline(settings)
        post = self._post(caption="breaking news today", hashtags=["fyp", "standwithiran"])
        assert pipeline._is_eligible(post)

    def test_require_hashtag_contains_matches_case_insensitively(self, tmp_path):
        settings = make_settings(
            tmp_path, keywords=["breaking news"], require_hashtag_contains="iran",
        )
        pipeline = self._pipeline(settings)
        post = self._post(caption="breaking news today", hashtags=["fyp", "STANDWITHIRAN"])
        assert pipeline._is_eligible(post)

    def test_hashtag_contains_not_required_by_default(self, settings):
        assert self._pipeline(settings)._is_eligible(self._post(hashtags=["Iran"]))

    def test_max_duration_excludes_longer_posts(self, tmp_path):
        settings = make_settings(tmp_path, max_video_duration_seconds=30)
        pipeline = self._pipeline(settings)
        assert pipeline._is_eligible(self._post(video_duration=30))
        assert not pipeline._is_eligible(self._post(video_duration=31))

    def test_max_duration_not_enforced_by_default(self, settings):
        assert self._pipeline(settings)._is_eligible(self._post(video_duration=600))

    def test_unreported_duration_is_kept_not_excluded(self, tmp_path):
        """0 means the API did not report a duration, not a zero-length video."""
        settings = make_settings(tmp_path, max_video_duration_seconds=30)
        assert self._pipeline(settings)._is_eligible(self._post(video_duration=0))


# ---------------------------------------------------------------------------
# Per-account cap
# ---------------------------------------------------------------------------

class TestAccountCap:
    """STUDY_MAX_POSTS_PER_ACCOUNT must stop one account from filling the sample."""

    def _post(self, post_id, username, view_count=100) -> Post:
        return Post(
            post_id=post_id,
            username=username,
            author_region="US",
            create_time="2026-05-15T12:00:00+00:00",
            caption="a caption long enough to not be treated as undetermined",
            hashtags=["Iran"],
            video_url="https://example.invalid",
            has_ai_label=False,
            view_count=view_count, like_count=0, comment_count=0, share_count=0,
        )

    def _run(self, settings, posts):
        pipeline = CollectionPipeline(settings, client=MockTikTokClient(seed=1))
        settings.ensure_directories()
        if settings.max_posts_per_account > 0:
            pipeline._account_counts = pipeline._load_account_counts(settings.posts_csv)
        with open_posts_writer(settings.posts_csv) as posts_writer, \
             open_comments_writer(settings.comments_csv) as comments_writer:
            if settings.sort_by == "view_count":
                pipeline._run_ranked(iter(posts), posts_writer, comments_writer)
            else:
                pipeline._run_streaming(iter(posts), posts_writer, comments_writer)
        return pipeline

    def test_streaming_mode_respects_the_cap(self, tmp_path):
        settings = make_settings(
            tmp_path, max_posts=10, max_posts_per_account=1, sort_by="none",
        )
        posts = [self._post(str(7_000_000_000_000_000_000 + i), "same_user") for i in range(5)]
        pipeline = self._run(settings, posts)

        rows = read_csv(settings.posts_csv)
        assert len(rows) == 1
        assert pipeline.stats.posts_skipped_account_cap == 4

    def test_ranked_mode_keeps_the_best_post_per_account_and_backfills(self, tmp_path):
        """A capped account must not block a lower-ranked post from someone else."""
        settings = make_settings(
            tmp_path, max_posts=2, max_posts_per_account=1, sort_by="view_count",
        )
        posts = [
            self._post("7000000000000000001", "user_a", view_count=500),
            self._post("7000000000000000002", "user_a", view_count=900),
            self._post("7000000000000000003", "user_b", view_count=300),
        ]
        pipeline = self._run(settings, posts)

        rows = read_csv(settings.posts_csv)
        ids = {r["post_id"] for r in rows}
        assert len(rows) == 2
        assert "7000000000000000002" in ids, "user_a's higher-view post should win"
        assert "7000000000000000001" not in ids, "user_a is capped at one post"
        assert "7000000000000000003" in ids, "user_b should backfill the freed slot"
        assert pipeline.stats.posts_skipped_account_cap == 1

    def test_cap_holds_across_a_resumed_run(self, tmp_path):
        """An account's posts from a prior run must still count toward the cap."""
        settings = make_settings(
            tmp_path, max_posts=10, max_posts_per_account=1, sort_by="none",
        )
        self._run(settings, [self._post("7000000000000000001", "same_user")])
        assert len(read_csv(settings.posts_csv)) == 1

        pipeline = self._run(
            settings, [self._post("7000000000000000002", "same_user")],
        )
        assert len(read_csv(settings.posts_csv)) == 1
        assert pipeline.stats.posts_skipped_account_cap == 1


# ---------------------------------------------------------------------------
# Per-hashtag cap
# ---------------------------------------------------------------------------

class TestHashtagCap:
    """STUDY_MAX_POSTS_PER_HASHTAG must stop one hashtag from dominating the sample."""

    def _post(self, post_id, hashtags, username=None, view_count=100) -> Post:
        return Post(
            post_id=post_id,
            username=username or f"user_{post_id}",
            author_region="US",
            create_time="2026-05-15T12:00:00+00:00",
            caption="a caption long enough to not be treated as undetermined",
            hashtags=hashtags,
            video_url="https://example.invalid",
            has_ai_label=False,
            view_count=view_count, like_count=0, comment_count=0, share_count=0,
        )

    def _run(self, settings, posts):
        pipeline = CollectionPipeline(settings, client=MockTikTokClient(seed=1))
        settings.ensure_directories()
        if settings.max_posts_per_account > 0:
            pipeline._account_counts = pipeline._load_account_counts(settings.posts_csv)
        if settings.max_posts_per_hashtag > 0:
            pipeline._hashtag_counts = pipeline._load_hashtag_counts(settings.posts_csv)
        with open_posts_writer(settings.posts_csv) as posts_writer, \
             open_comments_writer(settings.comments_csv) as comments_writer:
            if settings.sort_by == "view_count":
                pipeline._run_ranked(iter(posts), posts_writer, comments_writer)
            else:
                pipeline._run_streaming(iter(posts), posts_writer, comments_writer)
        return pipeline

    def test_streaming_mode_respects_the_cap(self, tmp_path):
        settings = make_settings(
            tmp_path, max_posts=10, max_posts_per_hashtag=1, sort_by="none",
        )
        posts = [
            self._post(str(7_000_000_000_000_000_000 + i), ["Iran"]) for i in range(5)
        ]
        pipeline = self._run(settings, posts)

        rows = read_csv(settings.posts_csv)
        assert len(rows) == 1
        assert pipeline.stats.posts_skipped_hashtag_cap == 4

    def test_a_post_is_excluded_if_any_carried_tag_is_at_cap(self, tmp_path):
        """A post with several study hashtags is rejected wholesale, not partially."""
        settings = make_settings(
            tmp_path, max_posts=10, max_posts_per_hashtag=1,
            hashtags=["Iran", "IranWar"], sort_by="none",
        )
        posts = [
            self._post("7000000000000000001", ["Iran"]),
            # Carries the still-open "IranWar" too, but "Iran" is already full.
            self._post("7000000000000000002", ["Iran", "IranWar"]),
            self._post("7000000000000000003", ["IranWar"]),
        ]
        pipeline = self._run(settings, posts)

        rows = read_csv(settings.posts_csv)
        ids = {r["post_id"] for r in rows}
        assert ids == {"7000000000000000001", "7000000000000000003"}
        assert pipeline.stats.posts_skipped_hashtag_cap == 1

    def test_ranked_mode_backfills_from_a_hashtag_with_room(self, tmp_path):
        settings = make_settings(
            tmp_path, max_posts=2, max_posts_per_hashtag=1,
            hashtags=["Iran", "IranWar"], sort_by="view_count",
        )
        posts = [
            self._post("7000000000000000001", ["Iran"], view_count=900),
            self._post("7000000000000000002", ["Iran"], view_count=500),
            self._post("7000000000000000003", ["IranWar"], view_count=300),
        ]
        pipeline = self._run(settings, posts)

        rows = read_csv(settings.posts_csv)
        ids = {r["post_id"] for r in rows}
        assert len(rows) == 2
        assert "7000000000000000001" in ids, "the higher-view #Iran post should win"
        assert "7000000000000000002" not in ids, "#Iran is capped at one post"
        assert "7000000000000000003" in ids, "#IranWar should backfill the freed slot"
        assert pipeline.stats.posts_skipped_hashtag_cap == 1

    def test_cap_holds_across_a_resumed_run(self, tmp_path):
        """A hashtag's posts from a prior run must still count toward the cap."""
        settings = make_settings(
            tmp_path, max_posts=10, max_posts_per_hashtag=1, sort_by="none",
        )
        self._run(settings, [self._post("7000000000000000001", ["Iran"])])
        assert len(read_csv(settings.posts_csv)) == 1

        pipeline = self._run(
            settings, [self._post("7000000000000000002", ["Iran"])],
        )
        assert len(read_csv(settings.posts_csv)) == 1
        assert pipeline.stats.posts_skipped_hashtag_cap == 1


# ---------------------------------------------------------------------------
# Text handling
# ---------------------------------------------------------------------------

class TestTextHandling:
    """Awkward characters must survive the write/read round trip intact."""

    def test_difficult_text_round_trips_exactly(self, settings):
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
        CollectionPipeline(settings, client=MockTikTokClient(seed=42)).run()
        for row in read_csv(settings.posts_csv):
            assert row["author_region"] != "None"


# ---------------------------------------------------------------------------
# Search budget distribution across date-window chunks
# ---------------------------------------------------------------------------

class TestSearchBudgetDistribution:
    """Guards the bug where one busy chunk consumed the entire search budget.

    A multi-month window is split into 30-day chunks, but TikTok returns
    results within a chunk newest-first. Without per-chunk budgeting, a
    chunk with enough volume on its own satisfies the whole `max_posts`
    target, and the pipeline never queries the rest of the window at all -
    silently turning "search six months" into "search whichever 30-day
    chunk happened to have enough recent posts."
    """

    def _make_client(self):
        from src.clients.research_client import ResearchApiClient
        return ResearchApiClient(
            client_key="k", client_secret="s",
            oauth_url="https://example.invalid",
            video_query_url="https://example.invalid",
            comment_query_url="https://example.invalid",
        )

    def test_budget_is_spread_across_chunks_not_consumed_by_the_first(self):
        client = self._make_client()

        # Every simulated chunk has effectively unlimited supply (50 videos
        # per page, has_more always True), so the pre-fix behaviour would
        # let the first chunk alone satisfy the entire target.
        def fake_request(url, params, body):
            start = body["start_date"]
            videos = [
                {
                    "id": f"{start}_{i}", "username": "u", "region_code": "US",
                    "create_time": 1775001600, "video_description": "x",
                    "hashtag_names": ["Iran"], "view_count": 1, "like_count": 0,
                    "comment_count": 0, "share_count": 0, "favorites_count": 0,
                    "video_tag": None,
                }
                for i in range(50)
            ]
            return {"videos": videos, "has_more": True, "cursor": 1, "search_id": "s"}

        client._request = fake_request

        start_date, end_date = date(2026, 4, 1), date(2026, 6, 29)
        expected_chunks = len(client._split_date_window(start_date, end_date))
        assert expected_chunks > 1, "test needs a window spanning multiple chunks"

        posts = list(client.search_posts(
            hashtags=["Iran"], start_date=start_date, end_date=end_date,
            language="en", max_posts=30,
        ))

        assert len(posts) == 30

        from collections import Counter
        counts = Counter(p.post_id.split("_")[0] for p in posts)
        assert len(counts) == expected_chunks, (
            f"expected all {expected_chunks} chunks represented, got {dict(counts)}"
        )
        # Roughly even (10 each for a 30/3 split); no single chunk should
        # dominate the way the pre-fix bug let it.
        assert max(counts.values()) <= 12, f"one chunk dominated: {dict(counts)}"


class TestHashtagStratification:
    """Guards the bug where one common hashtag drowned out rarer ones.

    Searching several hashtags at once used to send TikTok one combined
    `hashtag_name IN [...]` condition; `is_random` then drew a uniform
    sample over the UNION of all matching posts. A hashtag with far more
    matching posts than the others dominated the sample almost entirely -
    a live run of 6 hashtags returned 19/30 posts for the broadest tag and
    0/30 for three narrower ones. The fix searches each term separately and
    interleaves the results, round-robin, so every term gets a fair share
    regardless of its relative volume.
    """

    def _make_client(self):
        from src.clients.research_client import ResearchApiClient
        return ResearchApiClient(
            client_key="k", client_secret="s",
            oauth_url="https://example.invalid",
            video_query_url="https://example.invalid",
            comment_query_url="https://example.invalid",
        )

    def test_rare_hashtag_is_not_drowned_out_by_a_common_one(self):
        client = self._make_client()

        # "common" has effectively unlimited supply; "rare" has only 2
        # matching posts total. Pre-fix, a single combined query would let
        # "common" fill the entire sample before "rare" ever appeared.
        def fake_request(url, params, body):
            field_values = body["query"]["and"][0]["field_values"]
            assert len(field_values) == 1, "expected one term per stratified request"
            tag = field_values[0]

            if tag == "common":
                videos = [
                    {
                        "id": f"common_{i}", "username": "u", "region_code": "US",
                        "create_time": 1775001600, "video_description": "x",
                        "hashtag_names": ["common"], "view_count": 1, "like_count": 0,
                        "comment_count": 0, "share_count": 0, "favorites_count": 0,
                        "video_tag": None,
                    }
                    for i in range(50)
                ]
                return {"videos": videos, "has_more": True, "cursor": 1, "search_id": "s"}

            # "rare": only 2 posts ever, regardless of how much budget is offered.
            cursor = body.get("cursor")
            if cursor is not None:
                return {"videos": [], "has_more": False, "cursor": None, "search_id": None}
            videos = [
                {
                    "id": f"rare_{i}", "username": "u", "region_code": "US",
                    "create_time": 1775001600, "video_description": "x",
                    "hashtag_names": ["rare"], "view_count": 1, "like_count": 0,
                    "comment_count": 0, "share_count": 0, "favorites_count": 0,
                    "video_tag": None,
                }
                for i in range(2)
            ]
            return {"videos": videos, "has_more": False, "cursor": None, "search_id": None}

        client._request = fake_request

        posts = list(client.search_posts(
            hashtags=["common", "rare"], start_date=date(2026, 4, 1), end_date=date(2026, 4, 20),
            language="en", max_posts=30,
        ))

        from collections import Counter
        counts = Counter(p.hashtags[0] for p in posts)

        # Both terms must be represented - the whole point of the fix.
        assert counts["rare"] == 2, f"rare hashtag was drowned out: {dict(counts)}"
        assert counts["common"] > 0

    def test_min_posts_per_term_reserves_a_floor_before_round_robin_fill(self):
        """Round-robin alone still lets a high-supply term end up with far
        more posts than a low-supply one, since it simply keeps having a
        next result available for longer. min_posts_per_term reserves each
        term's floor first, before the remaining budget is filled by
        availability.
        """
        client = self._make_client()

        def fake_request(url, params, body):
            field_values = body["query"]["and"][0]["field_values"]
            tag = field_values[0]

            if tag == "common":
                videos = [
                    {
                        "id": f"common_{i}", "username": "u", "region_code": "US",
                        "create_time": 1775001600, "video_description": "x",
                        "hashtag_names": ["common"], "view_count": 1, "like_count": 0,
                        "comment_count": 0, "share_count": 0, "favorites_count": 0,
                        "video_tag": None,
                    }
                    for i in range(50)
                ]
                return {"videos": videos, "has_more": True, "cursor": 1, "search_id": "s"}

            # "rare": only 5 posts ever, regardless of how much budget is offered.
            cursor = body.get("cursor")
            if cursor is not None:
                return {"videos": [], "has_more": False, "cursor": None, "search_id": None}
            videos = [
                {
                    "id": f"rare_{i}", "username": "u", "region_code": "US",
                    "create_time": 1775001600, "video_description": "x",
                    "hashtag_names": ["rare"], "view_count": 1, "like_count": 0,
                    "comment_count": 0, "share_count": 0, "favorites_count": 0,
                    "video_tag": None,
                }
                for i in range(5)
            ]
            return {"videos": videos, "has_more": False, "cursor": None, "search_id": None}

        client._request = fake_request

        posts = list(client.search_posts(
            hashtags=["common", "rare"], start_date=date(2026, 4, 1), end_date=date(2026, 4, 20),
            language="en", max_posts=30, min_posts_per_term=5,
        ))

        from collections import Counter
        counts = Counter(p.hashtags[0] for p in posts)

        # "rare" gets its full available supply reserved up front, not just
        # whatever round-robin luck would have given it.
        assert counts["rare"] == 5, f"rare hashtag's floor was not reserved: {dict(counts)}"
        assert len(posts) == 30


# ---------------------------------------------------------------------------
# AI-label extraction
# ---------------------------------------------------------------------------

class TestAiLabelExtraction:
    """The study's key variable, parsed from the API's `video_tag` field.

        {"type": "AIGC",    "number": 1} -> Creator Labelled as AI-Generated
        {"type": "AIGC",    "number": 2} -> AI-Generated
        {"type": "Branded", "number": 1} -> Paid Partnership (not an AI label)
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
        """A Paid Partnership disclosure must not inflate the study's headline number."""
        raw = {"id": "1", "video_tag": [{"type": "Branded", "number": 1}]}
        assert self._extract(raw) == (False, "")

    def test_a_single_struct_is_handled_like_a_list(self):
        raw = {"id": "1", "video_tag": {"type": "AIGC", "number": 2}}
        assert self._extract(raw) == (True, "ai_generated")

    def test_mixed_tags_find_the_aigc_one(self):
        raw = {"id": "1", "video_tag": [
            {"type": "Branded", "number": 1},
            {"type": "AIGC", "number": 1},
        ]}
        assert self._extract(raw) == (True, "creator_labelled")

    def test_platform_detection_wins_over_self_disclosure(self):
        raw = {"id": "1", "video_tag": [
            {"type": "AIGC", "number": 1},
            {"type": "AIGC", "number": 2},
        ]}
        assert self._extract(raw) == (True, "ai_generated")

    def test_type_matching_is_case_insensitive(self):
        raw = {"id": "1", "video_tag": [{"type": "aigc", "number": 2}]}
        assert self._extract(raw) == (True, "ai_generated")

    def test_live_api_sends_aigc_type_suffix_not_bare_aigc(self):
        """Real API responses use 'AIGC Type', not the bare 'AIGC' the docs show.

        Found via a live test collection: every post came back with a
        video_tag, but has_ai_label was False for all of them because the
        exact-match check rejected "AIGC Type". Silent and invisible in the
        finished dataset - nothing looked broken.
        """
        raw = {"id": "1", "video_tag": [{"type": "AIGC Type", "number": 1}]}
        assert self._extract(raw) == (True, "creator_labelled")

    def test_unknown_aigc_number_still_counts_as_labelled(self):
        raw = {"id": "1", "video_tag": [{"type": "AIGC", "number": 99}]}
        has_label, _ = self._extract(raw)
        assert has_label is True

    def test_malformed_tag_does_not_crash(self):
        raw = {"id": "1", "video_tag": ["not a dict", None, 42]}
        assert self._extract(raw) == (False, "")


class TestQueryableFields:
    """Guards the bug where an unsupported filter field 400s every request."""

    def test_language_is_not_a_queryable_field(self):
        from src.clients.research_client import _QUERYABLE_FIELDS
        assert "video_language" not in _QUERYABLE_FIELDS
        assert "language" not in _QUERYABLE_FIELDS

    def test_hashtag_name_is_queryable(self):
        from src.clients.research_client import _QUERYABLE_FIELDS
        assert "hashtag_name" in _QUERYABLE_FIELDS

    def test_search_body_only_uses_queryable_fields(self):
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

    def test_keyword_only_search_omits_the_hashtag_condition(self):
        """Empty hashtags with keywords set must search by keyword alone."""
        from src.clients.research_client import ResearchApiClient
        client = ResearchApiClient(
            client_key="k", client_secret="s",
            oauth_url="https://example.invalid",
            video_query_url="https://example.invalid",
            comment_query_url="https://example.invalid",
        )
        body = client._build_search_body(
            hashtags=[], keywords=["iran war"], language="en",
            start_date=date(2026, 4, 1), end_date=date(2026, 4, 30),
            page_size=100, cursor=None, search_id=None,
        )
        conditions = body["query"]["and"]
        assert len(conditions) == 1
        assert conditions[0]["field_name"] == "keyword"
        assert conditions[0]["field_values"] == ["iran war"]

    def test_random_order_is_omitted_by_default(self):
        """Default behaviour must be identical to before is_random existed."""
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
        assert "is_random" not in body

    def test_random_order_true_sets_is_random(self):
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
            page_size=100, cursor=None, search_id=None, random_order=True,
        )
        assert body["is_random"] is True

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
        f = LanguageFilter(target_languages=["en"], mode="off")
        keep, _ = f.matches("این یک متن فارسی است که باید پذیرفته شود")
        assert keep is True

    def test_short_captions_are_kept_by_default(self):
        from src.language import LanguageFilter
        f = LanguageFilter(target_languages=["en"], mode="detect")
        keep, reason = f.matches("ok")
        assert keep is True
        assert "too short" in reason

    def test_short_captions_can_be_dropped_when_strict(self):
        from src.language import LanguageFilter
        f = LanguageFilter(target_languages=["en"], mode="detect", keep_undetermined=False)
        keep, _ = f.matches("ok")
        assert keep is False

    def test_summary_counts_every_decision(self):
        from src.language import LanguageFilter
        f = LanguageFilter(target_languages=["en"], mode="off")
        for _ in range(3):
            f.matches("some caption text here")
        assert f.checked == 3
        assert "3" in f.summary()

    def test_multiple_target_languages_are_all_accepted(self):
        """A caption in any one of several configured languages is kept."""
        from src.language import LanguageFilter
        f = LanguageFilter(target_languages=["en", "fa", "he"], mode="detect")
        keep_fa, reason_fa = f.matches(
            "این یک ویدیوی خبری در مورد وضعیت کنونی منطقه است و جزئیات بیشتری ارائه می دهد"
        )
        assert keep_fa is True, reason_fa
        keep_en, reason_en = f.matches(
            "Breaking news from the region tonight, more details as they come in"
        )
        assert keep_en is True, reason_en

    def test_language_outside_the_configured_set_is_rejected(self):
        from src.language import LanguageFilter
        f = LanguageFilter(target_languages=["en", "fa", "he"], mode="detect")
        keep, reason = f.matches(
            "Bonsoir à tous, voici les dernières nouvelles de la région ce soir"
        )
        assert keep is False, reason


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
        chunks = self._client()._split_date_window(date(2026, 4, 1), date(2026, 7, 31))

        assert chunks[0][0] == date(2026, 4, 1), "the window's first day was skipped"
        assert chunks[-1][1] == date(2026, 7, 31), "the window's last day was skipped"

        for (_, previous_end), (next_start, _) in zip(chunks, chunks[1:]):
            assert (next_start - previous_end).days == 1, "gap or overlap between chunks"

    def test_a_short_window_is_not_split(self):
        chunks = self._client()._split_date_window(date(2026, 4, 1), date(2026, 4, 10))
        assert chunks == [(date(2026, 4, 1), date(2026, 4, 10))]
