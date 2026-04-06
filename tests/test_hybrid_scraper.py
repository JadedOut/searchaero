"""Tests for HybridScraper reset behaviors and session budget defaults."""

import sys
import os
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "experiments"))

from hybrid_scraper import HybridScraper


@pytest.fixture
def mock_farm():
    """Create a mock CookieFarm that returns dummy values."""
    farm = MagicMock()
    farm.get_cookies.return_value = "a=b"
    farm.get_bearer_token.return_value = "token123"
    farm.refresh_cookies.return_value = None
    return farm


class TestStartResetsState:
    def test_start_resets_backoff_state(self, mock_farm):
        scraper = HybridScraper(mock_farm)
        # Simulate escalated backoff from prior burns
        scraper._consecutive_burns = 5
        scraper._backoff_seconds = 300.0

        scraper.start()

        assert scraper._consecutive_burns == 0
        assert scraper._backoff_seconds == HybridScraper._BASE_BACKOFF

    def test_start_resets_request_counters(self, mock_farm):
        scraper = HybridScraper(mock_farm)
        # Simulate mid-session state
        scraper._calls_since_refresh = 10
        scraper._requests_this_session = 50

        scraper.start()

        assert scraper._calls_since_refresh == 0
        assert scraper._requests_this_session == 0


class TestResetBackoff:
    def test_reset_backoff_clears_counters(self, mock_farm):
        scraper = HybridScraper(mock_farm)
        scraper._consecutive_burns = 3
        scraper._backoff_seconds = 120.0

        scraper.reset_backoff()

        assert scraper._consecutive_burns == 0
        assert scraper._backoff_seconds == HybridScraper._BASE_BACKOFF

    def test_reset_backoff_does_not_affect_session_counters(self, mock_farm):
        scraper = HybridScraper(mock_farm)
        scraper._calls_since_refresh = 5
        scraper._requests_this_session = 20

        scraper.reset_backoff()

        # These should NOT be reset by reset_backoff()
        assert scraper._calls_since_refresh == 5
        assert scraper._requests_this_session == 20


class TestSessionBudgetDefault:
    def test_default_session_budget_is_30(self, mock_farm):
        scraper = HybridScraper(mock_farm)
        assert scraper._session_budget == 30

    def test_custom_session_budget(self, mock_farm):
        scraper = HybridScraper(mock_farm, session_budget=50)
        assert scraper._session_budget == 50
