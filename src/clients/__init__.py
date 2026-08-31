"""
Client package - and the factory that chooses between implementations.

`build_client` below is the single place where the choice between the mock
and the live API is made, driven by configuration. Nothing else in the
project imports a concrete client class.
"""

from __future__ import annotations

import logging

from ..config import Settings
from .base import TikTokClient, TikTokClientError
from .mock_client import MockTikTokClient
from .research_client import ResearchApiClient

logger = logging.getLogger(__name__)

# Re-exported so other modules can write `from .clients import TikTokClient`
# without needing to know which submodule each name lives in.
__all__ = [
    "TikTokClient",
    "TikTokClientError",
    "MockTikTokClient",
    "ResearchApiClient",
    "build_client",
]


def build_client(settings: Settings) -> TikTokClient:
    """Construct the data-source client selected by the configuration.

    Args:
        settings: The validated configuration. `settings.client_type` decides
                  which implementation is returned.

    Returns:
        A ready-to-use object satisfying the `TikTokClient` interface.

    Raises:
        ValueError: Unreachable in practice - `Settings.__post_init__`
                    already rejects any unknown client type. Kept so that
                    adding a third client type without updating this
                    function fails loudly rather than silently.
    """
    if settings.client_type == "mock":
        logger.warning(
            "Using the MOCK client. All output from this run is SYNTHETIC "
            "test data. Set TIKTOK_CLIENT=research_api in .env for real data."
        )
        return MockTikTokClient()

    if settings.client_type == "research_api":
        logger.info("Using the live TikTok Research API client.")
        return ResearchApiClient(
            client_key=settings.client_key,
            client_secret=settings.client_secret,
            oauth_url=settings.oauth_url,
            video_query_url=settings.video_query_url,
            comment_query_url=settings.comment_query_url,
            request_delay_seconds=settings.request_delay_seconds,
            max_retries=settings.max_retries,
            daily_request_quota=settings.daily_request_quota,
        )

    raise ValueError(f"Unsupported TIKTOK_CLIENT value: {settings.client_type!r}")
