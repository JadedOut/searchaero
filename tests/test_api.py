"""Tests for the FastAPI web API endpoints.

These tests mock the database layer so they run without PostgreSQL.
"""

import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, date, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from web.api import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_connection_ctx(mock_cursor=None):
    """Build a MagicMock that behaves like `with get_connection() as conn`.

    If *mock_cursor* is provided it will be returned by conn.cursor().__enter__.
    """
    mock_conn = MagicMock()
    if mock_cursor is not None:
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    return mock_ctx


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health(self):
        """GET /api/health returns 200 with status ok."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/stats
# ---------------------------------------------------------------------------


class TestStats:
    @patch("web.api.get_scrape_stats")
    @patch("web.api.get_connection")
    def test_stats_with_data(self, mock_get_conn, mock_get_stats):
        """Stats endpoint returns all five fields with correct types."""
        mock_get_conn.return_value = _mock_connection_ctx()

        mock_get_stats.return_value = {
            "total_rows": 500,
            "routes_covered": 10,
            "latest_scrape": datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
            "date_range_start": date(2026, 5, 1),
            "date_range_end": date(2026, 12, 31),
        }

        resp = client.get("/api/stats")
        assert resp.status_code == 200

        data = resp.json()
        assert data["total_rows"] == 500
        assert data["routes_covered"] == 10
        assert data["latest_scrape"] == "2026-04-01T12:00:00+00:00"
        assert data["date_range_start"] == "2026-05-01"
        assert data["date_range_end"] == "2026-12-31"

    @patch("web.api.get_scrape_stats")
    @patch("web.api.get_connection")
    def test_stats_empty_db(self, mock_get_conn, mock_get_stats):
        """Stats endpoint returns zeros and nulls for an empty database."""
        mock_get_conn.return_value = _mock_connection_ctx()

        mock_get_stats.return_value = {
            "total_rows": 0,
            "routes_covered": 0,
            "latest_scrape": None,
            "date_range_start": None,
            "date_range_end": None,
        }

        resp = client.get("/api/stats")
        assert resp.status_code == 200

        data = resp.json()
        assert data["total_rows"] == 0
        assert data["routes_covered"] == 0
        assert data["latest_scrape"] is None
        assert data["date_range_start"] is None
        assert data["date_range_end"] is None


# ---------------------------------------------------------------------------
# /api/search
# ---------------------------------------------------------------------------


class TestSearch:
    @patch("web.api.get_connection")
    def test_search_returns_direct_field(self, mock_get_conn):
        """Search results include the 'direct' field in each cabin object."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {
                "date": date(2026, 6, 15),
                "last_seen": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                "economy_miles": 12000,
                "economy_taxes": 5600,
                "economy_direct": True,
                "premium_miles": None,
                "premium_taxes": None,
                "premium_direct": None,
                "business_miles": 35000,
                "business_taxes": 5600,
                "business_direct": False,
                "first_miles": None,
                "first_taxes": None,
                "first_direct": None,
            },
        ]

        mock_get_conn.return_value = _mock_connection_ctx(mock_cursor)

        resp = client.get("/api/search", params={"origin": "YYZ", "destination": "SFO"})
        assert resp.status_code == 200

        data = resp.json()
        assert data["origin"] == "YYZ"
        assert data["destination"] == "SFO"
        assert len(data["results"]) == 1

        row = data["results"][0]
        assert row["date"] == "2026-06-15"
        assert row["last_seen"] == "2026-04-01T10:00:00+00:00"
        assert row["program"] == "United"

        # Economy cabin present with direct field
        econ = row["economy"]
        assert econ is not None
        assert econ["miles"] == 12000
        assert econ["taxes_cents"] == 5600
        assert econ["direct"] is True

        # Business cabin present with direct field
        biz = row["business"]
        assert biz is not None
        assert biz["miles"] == 35000
        assert biz["taxes_cents"] == 5600
        assert biz["direct"] is False

        # Premium and first are null when no availability
        assert row["premium"] is None
        assert row["first"] is None

    @patch("web.api.get_connection")
    def test_search_empty_results(self, mock_get_conn):
        """Search returns empty results list when no rows match."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        mock_get_conn.return_value = _mock_connection_ctx(mock_cursor)

        resp = client.get("/api/search", params={"origin": "YYZ", "destination": "SFO"})
        assert resp.status_code == 200

        data = resp.json()
        assert data["results"] == []

    @patch("web.api.get_connection")
    def test_search_multiple_dates(self, mock_get_conn):
        """Search returns multiple date rows in order."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {
                "date": date(2026, 6, 15),
                "last_seen": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                "economy_miles": 12000,
                "economy_taxes": 5600,
                "economy_direct": True,
                "premium_miles": None,
                "premium_taxes": None,
                "premium_direct": None,
                "business_miles": None,
                "business_taxes": None,
                "business_direct": None,
                "first_miles": None,
                "first_taxes": None,
                "first_direct": None,
            },
            {
                "date": date(2026, 6, 16),
                "last_seen": datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc),
                "economy_miles": 15000,
                "economy_taxes": 5600,
                "economy_direct": False,
                "premium_miles": None,
                "premium_taxes": None,
                "premium_direct": None,
                "business_miles": None,
                "business_taxes": None,
                "business_direct": None,
                "first_miles": None,
                "first_taxes": None,
                "first_direct": None,
            },
        ]

        mock_get_conn.return_value = _mock_connection_ctx(mock_cursor)

        resp = client.get("/api/search", params={"origin": "YVR", "destination": "LAX"})
        assert resp.status_code == 200

        data = resp.json()
        assert len(data["results"]) == 2
        assert data["results"][0]["date"] == "2026-06-15"
        assert data["results"][1]["date"] == "2026-06-16"

    def test_search_invalid_origin(self):
        """Search rejects non-IATA origin codes with 400."""
        resp = client.get("/api/search", params={"origin": "invalid", "destination": "JFK"})
        assert resp.status_code == 400
        assert "origin" in resp.json()["detail"].lower()

    def test_search_invalid_destination(self):
        """Search rejects non-IATA destination codes with 400."""
        resp = client.get("/api/search", params={"origin": "YYZ", "destination": "xx"})
        assert resp.status_code == 400
        assert "destination" in resp.json()["detail"].lower()

    def test_search_lowercase_rejected(self):
        """Search rejects lowercase IATA codes with 400."""
        resp = client.get("/api/search", params={"origin": "yyz", "destination": "sfo"})
        assert resp.status_code == 400

    def test_search_numeric_rejected(self):
        """Search rejects numeric codes with 400."""
        resp = client.get("/api/search", params={"origin": "123", "destination": "SFO"})
        assert resp.status_code == 400

    def test_search_missing_params(self):
        """Search returns 422 when required params are missing."""
        resp = client.get("/api/search")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /api/search/detail
# ---------------------------------------------------------------------------


class TestSearchDetail:
    @patch("web.api.get_connection")
    def test_search_detail_returns_scraped_at_and_direct(self, mock_get_conn):
        """Detail offerings include scraped_at and direct fields."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {
                "cabin": "economy",
                "award_type": "Saver",
                "miles": 12000,
                "taxes_cents": 5600,
                "scraped_at": datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
                "direct": True,
            },
            {
                "cabin": "economy",
                "award_type": "Standard",
                "miles": 22500,
                "taxes_cents": 5600,
                "scraped_at": datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
                "direct": False,
            },
            {
                "cabin": "business",
                "award_type": "Saver",
                "miles": 35000,
                "taxes_cents": 5600,
                "scraped_at": datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
                "direct": True,
            },
        ]

        mock_get_conn.return_value = _mock_connection_ctx(mock_cursor)

        resp = client.get(
            "/api/search/detail",
            params={"origin": "YYZ", "destination": "SFO", "date": "2026-06-15"},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["date"] == "2026-06-15"
        assert data["origin"] == "YYZ"
        assert data["destination"] == "SFO"
        assert len(data["offerings"]) == 3

        # First offering: economy saver
        o1 = data["offerings"][0]
        assert o1["cabin"] == "Economy"
        assert o1["award_type"] == "Saver"
        assert o1["miles"] == 12000
        assert o1["taxes_cents"] == 5600
        assert o1["scraped_at"] == "2026-04-01T10:30:00+00:00"
        assert o1["direct"] is True

        # Second offering: economy standard
        o2 = data["offerings"][1]
        assert o2["cabin"] == "Economy"
        assert o2["award_type"] == "Standard"
        assert o2["direct"] is False

        # Third offering: business saver
        o3 = data["offerings"][2]
        assert o3["cabin"] == "Business"
        assert o3["miles"] == 35000
        assert o3["direct"] is True

    @patch("web.api.get_connection")
    def test_search_detail_cabin_display_names(self, mock_get_conn):
        """Detail maps internal cabin keys to display names."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {
                "cabin": "premium_economy",
                "award_type": "Saver",
                "miles": 20000,
                "taxes_cents": 5600,
                "scraped_at": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                "direct": True,
            },
            {
                "cabin": "business_pure",
                "award_type": "Saver",
                "miles": 70000,
                "taxes_cents": 5600,
                "scraped_at": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                "direct": False,
            },
            {
                "cabin": "first_pure",
                "award_type": "Saver",
                "miles": 120000,
                "taxes_cents": 5600,
                "scraped_at": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                "direct": True,
            },
        ]

        mock_get_conn.return_value = _mock_connection_ctx(mock_cursor)

        resp = client.get(
            "/api/search/detail",
            params={"origin": "YYZ", "destination": "NRT", "date": "2026-08-01"},
        )
        assert resp.status_code == 200

        offerings = resp.json()["offerings"]
        assert offerings[0]["cabin"] == "Premium Economy"
        assert offerings[1]["cabin"] == "Business (pure)"
        assert offerings[2]["cabin"] == "First (pure)"

    @patch("web.api.get_connection")
    def test_search_detail_empty(self, mock_get_conn):
        """Detail returns empty offerings for a date with no data."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        mock_get_conn.return_value = _mock_connection_ctx(mock_cursor)

        resp = client.get(
            "/api/search/detail",
            params={"origin": "YYZ", "destination": "SFO", "date": "2026-06-15"},
        )
        assert resp.status_code == 200
        assert resp.json()["offerings"] == []

    @patch("web.api.get_connection")
    def test_search_detail_null_scraped_at(self, mock_get_conn):
        """Detail handles null scraped_at gracefully."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {
                "cabin": "economy",
                "award_type": "Saver",
                "miles": 12000,
                "taxes_cents": 5600,
                "scraped_at": None,
                "direct": True,
            },
        ]

        mock_get_conn.return_value = _mock_connection_ctx(mock_cursor)

        resp = client.get(
            "/api/search/detail",
            params={"origin": "YYZ", "destination": "SFO", "date": "2026-06-15"},
        )
        assert resp.status_code == 200
        assert resp.json()["offerings"][0]["scraped_at"] is None

    def test_search_detail_invalid_codes(self):
        """Detail rejects invalid IATA codes with 400."""
        # Invalid origin
        resp = client.get(
            "/api/search/detail",
            params={"origin": "bad", "destination": "SFO", "date": "2026-06-15"},
        )
        assert resp.status_code == 400
        assert "origin" in resp.json()["detail"].lower()

        # Invalid destination
        resp = client.get(
            "/api/search/detail",
            params={"origin": "YYZ", "destination": "1X", "date": "2026-06-15"},
        )
        assert resp.status_code == 400
        assert "destination" in resp.json()["detail"].lower()

    def test_search_detail_missing_params(self):
        """Detail returns 422 when required params are missing."""
        resp = client.get("/api/search/detail", params={"origin": "YYZ"})
        assert resp.status_code == 422
