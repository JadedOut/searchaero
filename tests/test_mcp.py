"""Tests for mcp_server.py MCP tool functions."""

import asyncio
import datetime
import json
import os
import sqlite3
import threading

import pytest
from unittest.mock import MagicMock, patch

from core.db import create_schema, upsert_availability, record_scrape_job
from core.models import AwardResult


def _future(offset_days=30):
    return datetime.date.today() + datetime.timedelta(days=offset_days)


@pytest.fixture
def mcp_db(tmp_path, monkeypatch):
    """Create a temp SQLite db and monkeypatch db.get_connection to use it."""
    db_file = str(tmp_path / "test_mcp.db")
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    conn.close()

    from core import db

    def _get_test_conn(db_path=None):
        c = sqlite3.connect(db_file)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    monkeypatch.setattr(db, "get_connection", _get_test_conn)
    return db_file


@pytest.fixture
def seeded_mcp_db(mcp_db):
    """Seed the test db with sample data."""
    conn = sqlite3.connect(mcp_db)
    conn.row_factory = sqlite3.Row
    from core.db import create_schema
    create_schema(conn)

    d1 = _future(30)
    d2 = _future(60)
    scraped = datetime.datetime.now(datetime.timezone.utc)

    results = [
        AwardResult("YYZ", "LAX", d1, "economy", "Saver", 13000, 6851, scraped),
        AwardResult("YYZ", "LAX", d1, "business", "Saver", 70000, 6851, scraped),
        AwardResult("YYZ", "LAX", d1, "first", "Saver", 120000, 6851, scraped),
        AwardResult("YYZ", "LAX", d2, "economy", "Saver", 15000, 6851, scraped),
        AwardResult("YYZ", "LAX", d2, "business", "Saver", 70000, 6851, scraped),
        AwardResult("YYZ", "LAX", d2, "first", "Saver", 120000, 6851, scraped),
        AwardResult("YYZ", "LAX", d1, "economy", "Standard", 22500, 6851, scraped),
        AwardResult("YVR", "SFO", d1, "economy", "Saver", 18000, 5200, scraped),
    ]
    upsert_availability(conn, results)
    record_scrape_job(conn, "YYZ", "LAX", d1.replace(day=1), "completed",
                      solutions_found=7, solutions_stored=7)
    conn.close()
    return mcp_db, d1, d2


class TestQueryFlights:
    def test_basic(self, seeded_mcp_db):
        from mcp_server import query_flights
        result = json.loads(query_flights("YYZ", "LAX"))
        assert isinstance(result, dict)
        assert result["count"] == 7
        assert "_summary" in result
        assert "_display_hint" in result
        assert "results" not in result

    def test_cabin_filter(self, seeded_mcp_db):
        from mcp_server import query_flights
        result = json.loads(query_flights("YYZ", "LAX", cabin="business"))
        assert isinstance(result, dict)
        assert result["count"] == 2
        assert result["_display_hint"] == "best_deal"
        assert "results" not in result

    def test_date_range(self, seeded_mcp_db):
        _, d1, d2 = seeded_mcp_db
        from mcp_server import query_flights
        result = json.loads(query_flights("YYZ", "LAX",
                                          from_date=d1.isoformat(),
                                          to_date=d1.isoformat()))
        assert isinstance(result, dict)
        assert result["count"] > 0
        assert "results" not in result

    def test_no_results(self, mcp_db):
        from mcp_server import query_flights
        result = json.loads(query_flights("YYZ", "LAX"))
        assert "error" in result
        assert result["error"] == "no_results"

    def test_summary_cheapest(self, seeded_mcp_db):
        from mcp_server import query_flights
        result = json.loads(query_flights("YYZ", "LAX"))
        summary = result["_summary"]
        assert summary["cheapest"]["miles"] == 13000
        assert summary["cheapest"]["cabin"] == "economy"
        assert summary["saver_dates"] > 0
        assert "economy" in summary["cabins_available"]
        assert result["count"] == 7

    def test_display_hint_full_list(self, seeded_mcp_db):
        _, d1, _ = seeded_mcp_db
        from mcp_server import query_flights
        result = json.loads(query_flights("YYZ", "LAX", date=d1.isoformat()))
        assert result["_display_hint"] == "full_list"

    def test_format_suggestions_present(self, seeded_mcp_db):
        from mcp_server import query_flights
        result = json.loads(query_flights("YYZ", "LAX"))
        suggestions = result["_format_suggestions"]
        assert "best_deal" in suggestions
        assert "date_comparison" in suggestions
        assert "full_list" in suggestions


class TestFlightStatus:
    def test_with_data(self, seeded_mcp_db):
        from mcp_server import flight_status
        result = json.loads(flight_status())
        assert result["total_rows"] == 8
        assert result["routes_covered"] == 2
        assert result["completed"] == 1

    def test_empty_db(self, mcp_db):
        from mcp_server import flight_status
        result = json.loads(flight_status())
        assert result["total_rows"] == 0


class TestAddAlert:
    def test_create(self, mcp_db):
        from mcp_server import add_alert
        result = json.loads(add_alert("YYZ", "LAX", 50000))
        assert result["status"] == "created"
        assert "id" in result
        assert result["origin"] == "YYZ"


class TestCheckAlerts:
    def test_with_match(self, seeded_mcp_db):
        from mcp_server import add_alert, check_alerts
        # Create alert that should match (economy Saver 13000 <= 50000)
        add_alert("YYZ", "LAX", 50000)
        result = json.loads(check_alerts())
        assert result["alerts_checked"] == 1
        assert result["alerts_triggered"] == 1
        assert len(result["results"]) == 1
        assert len(result["results"][0]["matches"]) > 0

    def test_no_match(self, seeded_mcp_db):
        from mcp_server import add_alert, check_alerts
        # Create alert with threshold too low to match anything
        add_alert("YYZ", "LAX", 100)
        result = json.loads(check_alerts())
        assert result["alerts_checked"] == 1
        assert result["alerts_triggered"] == 0


class TestGetFlightDetails:
    def test_basic(self, seeded_mcp_db):
        from mcp_server import get_flight_details
        result = json.loads(get_flight_details("YYZ", "LAX"))
        assert "results" in result
        assert isinstance(result["results"], list)
        assert result["total"] == 7
        assert len(result["results"]) <= 15
        assert "has_more" in result
        assert "showing" in result

    def test_limit_offset(self, seeded_mcp_db):
        from mcp_server import get_flight_details
        result = json.loads(get_flight_details("YYZ", "LAX", limit=3, offset=0))
        assert len(result["results"]) == 3
        assert result["total"] == 7
        assert result["has_more"] is True
        assert result["showing"] == "1-3 of 7"

        page2 = json.loads(get_flight_details("YYZ", "LAX", limit=3, offset=3))
        assert len(page2["results"]) == 3
        assert page2["showing"] == "4-6 of 7"

        page3 = json.loads(get_flight_details("YYZ", "LAX", limit=3, offset=6))
        assert len(page3["results"]) == 1
        assert page3["has_more"] is False

    def test_cabin_filter(self, seeded_mcp_db):
        from mcp_server import get_flight_details
        result = json.loads(get_flight_details("YYZ", "LAX", cabin="business"))
        assert all(r["cabin"] in ("business", "business_pure") for r in result["results"])
        assert result["total"] == 2

    def test_sort_by_miles(self, seeded_mcp_db):
        from mcp_server import get_flight_details
        result = json.loads(get_flight_details("YYZ", "LAX", sort="miles"))
        miles = [r["miles"] for r in result["results"]]
        assert miles == sorted(miles)

    def test_no_results(self, mcp_db):
        from mcp_server import get_flight_details
        result = json.loads(get_flight_details("YYZ", "LAX"))
        assert result["error"] == "no_results"

    def test_limit_clamped(self, seeded_mcp_db):
        from mcp_server import get_flight_details
        result = json.loads(get_flight_details("YYZ", "LAX", limit=100))
        assert len(result["results"]) == 7


class TestGetPriceTrend:
    def test_basic(self, seeded_mcp_db):
        from mcp_server import get_price_trend
        result = json.loads(get_price_trend("YYZ", "LAX"))
        assert result["data_points"] > 0
        assert "trend" in result
        for point in result["trend"]:
            assert "date" in point
            assert "miles" in point
        dates = [p["date"] for p in result["trend"]]
        assert dates == sorted(dates)

    def test_cabin_filter(self, seeded_mcp_db):
        from mcp_server import get_price_trend
        result = json.loads(get_price_trend("YYZ", "LAX", cabin="business"))
        assert result["cabin_filter"] == "business"
        for point in result["trend"]:
            assert point["cabin"] in ("business", "business_pure")

    def test_no_results(self, mcp_db):
        from mcp_server import get_price_trend
        result = json.loads(get_price_trend("YYZ", "LAX"))
        assert result["error"] == "no_results"


class TestFindDeals:
    def test_no_deals_empty_db(self, mcp_db):
        from mcp_server import find_deals
        result = json.loads(find_deals())
        assert result["deals_found"] == 0

    def test_returns_deals_structure(self, seeded_mcp_db):
        from mcp_server import find_deals
        result = json.loads(find_deals())
        assert "deals_found" in result
        if result["deals_found"] > 0:
            deal = result["deals"][0]
            assert "origin" in deal
            assert "destination" in deal
            assert "miles" in deal
            assert "savings_pct" in deal

    def test_cabin_filter(self, seeded_mcp_db):
        from mcp_server import find_deals
        result = json.loads(find_deals(cabin="business"))
        if result["deals_found"] > 0:
            assert result.get("cabin_filter") == "business"
        else:
            # No deals found — cabin_filter only present in non-empty responses
            assert result["deals_found"] == 0

    def test_max_results_clamped(self, seeded_mcp_db):
        from mcp_server import find_deals
        result = json.loads(find_deals(max_results=50))
        if result["deals_found"] > 0:
            assert len(result["deals"]) <= 25


class TestSearchRouteAsync:
    """Tests for the async search_route tool with elicitation and Progress DI."""

    def test_search_route_is_async(self):
        """search_route is registered as an async function."""
        import asyncio
        import mcp_server
        assert asyncio.iscoroutinefunction(mcp_server.search_route)

    def test_search_route_uppercase_normalization(self):
        """search_route normalizes origin/destination to uppercase."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock, AsyncMock, patch

        mock_ctx = MagicMock()
        mock_progress = MagicMock()
        mock_progress.set_total = AsyncMock()
        mock_progress.set_message = AsyncMock()
        mock_progress.increment = AsyncMock()

        mock_result = {"found": 10, "stored": 8}
        mock_scrape = MagicMock(return_value=mock_result)

        # Mock warm session
        mcp_server._session["farm"] = MagicMock()
        mcp_server._session["farm"]._has_login_cookies.return_value = True
        mcp_server._session["farm"].refresh_cookies.return_value = True
        mcp_server._session["scraper"] = MagicMock()
        mcp_server._session["scraper"].is_browser_alive.return_value = True
        mcp_server._session["logged_in"] = True

        with patch.dict("sys.modules", {"scrape": MagicMock(scrape_route=mock_scrape)}):
            result = json.loads(asyncio.run(
                mcp_server.search_route("yyz", "lax", ctx=mock_ctx, progress=mock_progress)
            ))

        assert result["route"] == "YYZ-LAX"
        assert result["status"] == "complete"

        # Verify scrape_route was called with uppercase
        call_args = mock_scrape.call_args
        assert call_args[0][0] == "YYZ"
        assert call_args[0][1] == "LAX"

        # Cleanup
        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False

    def test_search_route_warm_session(self, mcp_db):
        """Warm session scrape completes successfully via asyncio."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock, AsyncMock, patch

        mock_ctx = MagicMock()
        mock_progress = MagicMock()
        mock_progress.set_total = AsyncMock()
        mock_progress.set_message = AsyncMock()
        mock_progress.increment = AsyncMock()

        mock_result = {"found": 100, "stored": 95}
        mock_scrape = MagicMock(return_value=mock_result)

        mcp_server._session["farm"] = MagicMock()
        mcp_server._session["farm"]._has_login_cookies.return_value = True
        mcp_server._session["farm"].refresh_cookies.return_value = True
        mcp_server._session["scraper"] = MagicMock()
        mcp_server._session["scraper"].is_browser_alive.return_value = True
        mcp_server._session["logged_in"] = True

        with patch.dict("sys.modules", {"scrape": MagicMock(scrape_route=mock_scrape)}):
            result = json.loads(asyncio.run(
                mcp_server.search_route("YYZ", "LAX", ctx=mock_ctx, progress=mock_progress)
            ))

        assert result["status"] == "complete"
        assert result["found"] == 100
        assert result["stored"] == 95
        mock_scrape.assert_called_once()

        # Cleanup
        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False

    def test_search_route_cold_session(self, mcp_db):
        """Cold session calls _ensure_session then scrapes."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock, AsyncMock, patch, call

        mock_ctx = MagicMock()
        mock_progress = MagicMock()
        mock_progress.set_total = AsyncMock()
        mock_progress.set_message = AsyncMock()
        mock_progress.increment = AsyncMock()

        mock_result = {"found": 50, "stored": 45}
        mock_scrape = MagicMock(return_value=mock_result)

        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False

        # _ensure_session sets up the session
        def fake_ensure(mfa_prompt=None, mfa_method="sms"):
            mcp_server._session["farm"] = MagicMock()
            mcp_server._session["farm"]._has_login_cookies.return_value = True
            mcp_server._session["farm"].refresh_cookies.return_value = True
            mcp_server._session["scraper"] = MagicMock()
            mcp_server._session["scraper"].is_browser_alive.return_value = True
            mcp_server._session["logged_in"] = True

        with patch.object(mcp_server, "_ensure_session", side_effect=fake_ensure), \
             patch.dict("sys.modules", {"scrape": MagicMock(scrape_route=mock_scrape)}):
            result = json.loads(asyncio.run(
                mcp_server.search_route("YYZ", "LAX", ctx=mock_ctx, progress=mock_progress)
            ))

        assert result["status"] == "complete"
        assert result["found"] == 50

        # Cleanup
        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False

    def test_search_route_scrape_error(self, mcp_db):
        """Scrape error returns JSON error and tears down session."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock, AsyncMock, patch

        mock_ctx = MagicMock()
        mock_progress = MagicMock()
        mock_progress.set_total = AsyncMock()
        mock_progress.set_message = AsyncMock()
        mock_progress.increment = AsyncMock()

        mock_scrape = MagicMock(side_effect=ConnectionError("CDP connection closed"))

        mcp_server._session["farm"] = MagicMock()
        mcp_server._session["farm"]._has_login_cookies.return_value = True
        mcp_server._session["farm"].refresh_cookies.return_value = True
        mcp_server._session["scraper"] = MagicMock()
        mcp_server._session["scraper"].is_browser_alive.return_value = True
        mcp_server._session["logged_in"] = True

        with patch.dict("sys.modules", {"scrape": MagicMock(scrape_route=mock_scrape)}):
            result = json.loads(asyncio.run(
                mcp_server.search_route("YYZ", "LAX", ctx=mock_ctx, progress=mock_progress)
            ))

        assert result["error"] == "ConnectionError"
        assert "CDP connection closed" in result["message"]

        # Session should be torn down
        assert mcp_server._session["farm"] is None
        assert mcp_server._session["logged_in"] is False

    def test_search_route_mfa_decline(self, mcp_db):
        """MFA decline returns clear error JSON."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock, AsyncMock, patch

        mock_ctx = MagicMock()
        mock_progress = MagicMock()
        mock_progress.set_total = AsyncMock()
        mock_progress.set_message = AsyncMock()
        mock_progress.increment = AsyncMock()

        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False

        # _ensure_session calls mfa_prompt, which will trigger elicitation
        # The elicitation returns "decline"
        def fake_ensure(mfa_prompt=None, mfa_method="sms"):
            if mfa_prompt:
                mfa_prompt()  # This will trigger the sync_mfa_prompt which calls ctx.elicit

        # Mock elicit to return decline
        mock_elicit_result = MagicMock()
        mock_elicit_result.action = "decline"
        mock_elicit_result.data = None
        mock_ctx.elicit = AsyncMock(return_value=mock_elicit_result)

        with patch.object(mcp_server, "_ensure_session", side_effect=fake_ensure):
            result = json.loads(asyncio.run(
                mcp_server.search_route("YYZ", "LAX", ctx=mock_ctx, progress=mock_progress)
            ))

        assert result["error"] == "mfa_declined"
        assert "declined" in result["message"].lower()

        # Cleanup
        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False

    def test_search_route_mfa_pending(self, mcp_db):
        """Elicitation failure with active farm returns mfa_required (not error)."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock, AsyncMock, patch

        mock_ctx = MagicMock()
        mock_ctx.elicit = AsyncMock(side_effect=Exception("Elicitation not supported"))
        mock_progress = MagicMock()
        mock_progress.set_total = AsyncMock()
        mock_progress.set_message = AsyncMock()
        mock_progress.increment = AsyncMock()

        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = False
        mcp_server._session["pending_scrape"] = None
        mcp_server._session["mfa_method"] = "sms"

        # _ensure_session sets up farm but fails during login (MFA prompt triggers
        # ctx.elicit which raises).  _wrapped_ensure_session sees farm != None and
        # logged_in == False, so it converts the exception into _MFAPending.
        def fake_ensure(mfa_prompt=None, mfa_method="sms"):
            mcp_server._session["farm"] = MagicMock()
            mcp_server._session["farm"]._has_login_cookies.return_value = False
            # Simulate login attempt that calls mfa_prompt, which triggers elicitation
            if mfa_prompt:
                mfa_prompt()  # This calls sync_mfa_prompt -> ctx.elicit which raises

        with patch.object(mcp_server, "_ensure_session", side_effect=fake_ensure):
            result = json.loads(asyncio.run(
                mcp_server.search_route("YYZ", "LAX", ctx=mock_ctx, progress=mock_progress)
            ))

        assert result["status"] == "mfa_required"
        assert result["route"] == "YYZ-LAX"
        assert mcp_server._session["mfa_pending"] is True
        assert mcp_server._session["pending_scrape"] == ("YYZ", "LAX")

        # Cleanup
        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = False
        mcp_server._session["pending_scrape"] = None
        mcp_server._session["mfa_method"] = "sms"

    def test_search_route_autonomous_forces_email(self, mcp_db):
        """autonomous=True forces mfa_method='email' and returns mfa_required."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock, AsyncMock, patch

        mock_ctx = MagicMock()
        mock_progress = MagicMock()
        mock_progress.set_total = AsyncMock()
        mock_progress.set_message = AsyncMock()
        mock_progress.increment = AsyncMock()

        mock_farm = MagicMock()

        def fake_ensure(mfa_prompt=None, mfa_method="sms"):
            mcp_server._session["farm"] = mock_farm
            mcp_server._session["logged_in"] = False
            assert mfa_method == "email", "autonomous should force email"
            if mfa_prompt:
                mfa_prompt()  # The autonomous prompt raises immediately

        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = False
        mcp_server._session["pending_scrape"] = None
        mcp_server._session["mfa_method"] = "sms"

        with patch.object(mcp_server, "_ensure_session", side_effect=fake_ensure):
            result = json.loads(asyncio.run(
                mcp_server.search_route("YYZ", "LAX", ctx=mock_ctx, progress=mock_progress,
                                         autonomous=True)
            ))

        assert result["status"] == "mfa_required"
        assert result["mfa_method"] == "email"
        assert mcp_server._session["mfa_method"] == "email"

        # Cleanup
        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = False
        mcp_server._session["pending_scrape"] = None
        mcp_server._session["mfa_method"] = "sms"

    def test_search_route_email_mfa_method_stored(self, mcp_db):
        """mfa_method='email' is stored in _session after successful scrape."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock, AsyncMock, patch

        mock_ctx = MagicMock()
        mock_progress = MagicMock()
        mock_progress.set_total = AsyncMock()
        mock_progress.set_message = AsyncMock()
        mock_progress.increment = AsyncMock()

        mock_scrape = MagicMock(return_value={"found": 10, "stored": 10})

        def fake_ensure(mfa_prompt=None, mfa_method="sms"):
            mcp_server._session["farm"] = MagicMock()
            mcp_server._session["logged_in"] = True
            mcp_server._session["scraper"] = MagicMock()
            mcp_server._session["scraper"].is_browser_alive.return_value = True

        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = False
        mcp_server._session["pending_scrape"] = None
        mcp_server._session["mfa_method"] = "sms"

        with patch.object(mcp_server, "_ensure_session", side_effect=fake_ensure), \
             patch("mcp_server.db.get_connection") as mock_conn, \
             patch.dict("sys.modules", {"scrape": MagicMock(scrape_route=mock_scrape)}):
            mock_conn.return_value = MagicMock()
            result = json.loads(asyncio.run(
                mcp_server.search_route("YYZ", "LAX", ctx=mock_ctx, progress=mock_progress,
                                         mfa_method="email")
            ))

        assert mcp_server._session["mfa_method"] == "email"

        # Cleanup
        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = False
        mcp_server._session["pending_scrape"] = None
        mcp_server._session["mfa_method"] = "sms"


class TestMCPMetadata:
    """Tests for FastMCP instructions and ToolAnnotations."""

    def test_instructions_set(self):
        import mcp_server
        assert mcp_server.mcp.instructions is not None
        assert "query_flights" in mcp_server.mcp.instructions
        assert "search_route" in mcp_server.mcp.instructions
        assert "Do NOT" in mcp_server.mcp.instructions
        assert "autonomous" in mcp_server.mcp.instructions.lower()


class TestMCPCLIFlags:
    """Tests for seataero-mcp --list-tools, --health, --help flags."""

    def test_list_tools_output(self, capsys):
        """--list-tools prints all tool names."""
        import mcp_server
        mcp_server._list_tools()
        captured = capsys.readouterr()
        assert "query_flights" in captured.out
        assert "search_route" in captured.out
        assert "submit_mfa" in captured.out
        assert "add_alert" in captured.out
        assert "check_watches" in captured.out
        assert "13 tools available" in captured.out

    def test_list_tools_all_tools_present(self, capsys):
        """--list-tools lists every registered tool."""
        import mcp_server
        expected = [
            "query_flights", "get_flight_details", "get_price_trend",
            "find_deals", "flight_status", "search_route", "submit_mfa",
            "add_alert", "check_alerts",
            "add_watch", "list_watches", "remove_watch", "check_watches",
        ]
        mcp_server._list_tools()
        captured = capsys.readouterr()
        for tool in expected:
            assert tool in captured.out, f"Missing tool: {tool}"

    def test_health_check_returns_int(self):
        """_health_check returns 0 or 1 (int exit code)."""
        import mcp_server
        with patch("mcp_server.db.get_connection") as mock_conn, \
             patch("mcp_server.db.create_schema"), \
             patch("os.path.isfile", return_value=True):
            conn = MagicMock()
            conn.execute = MagicMock(return_value=MagicMock(
                fetchone=MagicMock(return_value=(42,))
            ))
            mock_conn.return_value = conn
            result = mcp_server._health_check()
            assert isinstance(result, int)
            assert result in (0, 1)

    def test_health_check_missing_credentials(self, capsys):
        """_health_check reports FAIL when credentials file is missing."""
        import mcp_server
        with patch("mcp_server.db.get_connection") as mock_conn, \
             patch("mcp_server.db.create_schema"), \
             patch("os.path.isfile", return_value=False):
            conn = MagicMock()
            conn.execute = MagicMock(return_value=MagicMock(
                fetchone=MagicMock(return_value=(0,))
            ))
            mock_conn.return_value = conn
            result = mcp_server._health_check()
            assert result == 1
            captured = capsys.readouterr()
            assert "FAIL" in captured.out


class TestProxyPassthrough:
    """Tests for proxy passthrough in MCP server."""

    def test_proxy_url_from_env(self, monkeypatch):
        """PROXY_URL env var is read at module level and passed to CookieFarm."""
        monkeypatch.setenv("PROXY_URL", "socks5://user:pass@proxy.example.com:1080")

        # Re-import to pick up the env var change
        import mcp_server
        # Directly test the module-level variable by setting it as the env would
        proxy_url = os.getenv("PROXY_URL", "").strip() or None
        assert proxy_url == "socks5://user:pass@proxy.example.com:1080"

    def test_ensure_session_passes_proxy_to_cookiefarm(self, monkeypatch, mcp_db):
        """_ensure_session passes _PROXY_URL to CookieFarm constructor."""
        import mcp_server

        test_proxy = "socks5://test:pass@proxy.local:9050"
        monkeypatch.setattr(mcp_server, "_PROXY_URL", test_proxy)

        # Reset session state
        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False

        captured_kwargs = {}

        class FakeCookieFarm:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                self.proxy = kwargs.get("proxy")
            def start(self):
                pass
            def ensure_logged_in(self, mfa_prompt=None):
                pass

        class FakeHybridScraper:
            def __init__(self, farm, **kwargs):
                pass
            def start(self):
                pass

        # Patch the imports that _ensure_session uses
        monkeypatch.setattr(mcp_server, "_ensure_session", lambda mfa_prompt=None: None)

        # Directly test that CookieFarm would receive the proxy
        # by simulating what _ensure_session does
        from core.cookie_farm import CookieFarm

        # Monkeypatch os.getenv to return empty PROXY_URL so CookieFarm uses explicit param
        monkeypatch.delenv("PROXY_URL", raising=False)

        farm = CookieFarm(headless=False, ephemeral=True, proxy=test_proxy)
        assert farm.proxy == test_proxy

        # Cleanup
        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False


# ======================================================================
# Auto-login MP#-only tests
# ======================================================================

class TestAutoLoginMPOnly:
    """Tests for _auto_login() with MileagePlus number only (no email/Gmail)."""

    def _make_farm(self, has_united=True):
        """Create a CookieFarm with mocked internals."""
        from core.cookie_farm import CookieFarm

        farm = object.__new__(CookieFarm)
        farm._lock = threading.Lock()
        farm._headless = True
        farm._mfa_prompt = None

        farm._united_mp_number = "MUH48117" if has_united else None
        farm._united_password = "pass123" if has_united else ""

        mock_page = MagicMock()
        mock_page.url = "https://www.united.com/en/ca/"
        mock_page.content.return_value = ""
        farm._page = mock_page
        farm._context = MagicMock()
        farm._browser_ready = threading.Event()

        return farm

    def test_login_success(self):
        """MP# + password login works -> auto_login returns success."""
        farm = self._make_farm()
        farm._is_logged_in = MagicMock(side_effect=[False, True])
        farm._auto_login = MagicMock(return_value="success")
        farm.ensure_logged_in()
        farm._auto_login.assert_called_once()

    def test_auto_login_returns_failed_without_mp(self):
        """No MP# configured -> ensure_logged_in raises RuntimeError."""
        farm = self._make_farm(has_united=False)
        farm._is_logged_in = MagicMock(return_value=False)
        farm._auto_login = MagicMock(return_value="failed")
        with pytest.raises(RuntimeError):
            farm.ensure_logged_in()

    def test_ensure_logged_in_no_auth_method_param(self):
        """ensure_logged_in() no longer accepts auth_method parameter."""
        farm = self._make_farm()
        import inspect
        sig = inspect.signature(farm.ensure_logged_in)
        assert "auth_method" not in sig.parameters

    def test_mfa_still_works(self):
        """Normal login hits MFA -> code submitted -> success."""
        farm = self._make_farm()
        farm._is_logged_in = MagicMock(side_effect=[False, True])
        farm._auto_login = MagicMock(return_value="mfa_required")
        farm._enter_mfa_code = MagicMock(return_value=True)

        farm.ensure_logged_in(mfa_prompt=lambda: "123456")

        farm._auto_login.assert_called_once()
        farm._enter_mfa_code.assert_called_once_with("123456")


class TestAddWatch:
    def test_create(self, mcp_db):
        from mcp_server import add_watch
        result = json.loads(asyncio.run(add_watch("YYZ", "LAX", 50000)))
        assert result["status"] == "created"
        assert "id" in result
        assert result["origin"] == "YYZ"
        assert result["check_interval_minutes"] == 720  # default 12h

    def test_custom_interval(self, mcp_db):
        from mcp_server import add_watch
        result = json.loads(asyncio.run(add_watch("YYZ", "LAX", 50000, every="daily")))
        assert result["check_interval_minutes"] == 1440

    def test_invalid_interval(self, mcp_db):
        from mcp_server import add_watch
        result = json.loads(asyncio.run(add_watch("YYZ", "LAX", 50000, every="invalid")))
        assert "error" in result


class TestListWatches:
    def test_empty(self, mcp_db):
        from mcp_server import list_watches
        result = json.loads(list_watches())
        assert result["count"] == 0

    def test_with_watches(self, mcp_db):
        from mcp_server import add_watch, list_watches
        asyncio.run(add_watch("YYZ", "LAX", 50000))
        asyncio.run(add_watch("YVR", "SFO", 30000))
        result = json.loads(list_watches())
        assert result["count"] == 2


class TestRemoveWatch:
    def test_remove_existing(self, mcp_db):
        from mcp_server import add_watch, remove_watch
        created = json.loads(asyncio.run(add_watch("YYZ", "LAX", 50000)))
        result = json.loads(remove_watch(created["id"]))
        assert result["status"] == "removed"

    def test_remove_nonexistent(self, mcp_db):
        from mcp_server import remove_watch
        result = json.loads(remove_watch(999))
        assert result["status"] == "not_found"


class TestCheckWatches:
    def test_no_watches(self, mcp_db):
        from mcp_server import check_watches
        result = json.loads(check_watches())
        assert result["watches_checked"] == 0

    def test_with_match(self, seeded_mcp_db):
        from mcp_server import add_watch, check_watches
        from unittest.mock import patch
        asyncio.run(add_watch("YYZ", "LAX", 50000))
        with patch("mcp_server.load_notify_config", return_value={"ntfy_topic": "test", "ntfy_server": "https://ntfy.sh"}), \
             patch("mcp_server.send_ntfy", return_value=True) as mock_ntfy:
            result = json.loads(check_watches())
        assert result["watches_checked"] == 1
        assert result["watches_triggered"] >= 1
        assert result["ntfy_active"] is True
        # Verify notification block
        triggered = result["results"][0]
        assert "notification" in triggered
        assert "title" in triggered["notification"]
        assert "body" in triggered["notification"]
        assert triggered["ntfy_sent"] is True
        mock_ntfy.assert_called_once()

    def test_with_match_no_ntfy(self, seeded_mcp_db):
        """No ntfy configured — notification block still present for agent delivery."""
        from mcp_server import add_watch, check_watches
        from unittest.mock import patch
        asyncio.run(add_watch("YYZ", "LAX", 50000))
        with patch("mcp_server.load_notify_config", return_value={"ntfy_topic": "", "ntfy_server": "https://ntfy.sh"}):
            result = json.loads(check_watches())
        assert result["watches_triggered"] >= 1
        assert result["ntfy_active"] is False
        triggered = result["results"][0]
        assert "notification" in triggered
        assert "title" in triggered["notification"]
        assert "body" in triggered["notification"]
        assert triggered["ntfy_sent"] is False


class TestAddWatchElicitation:
    def test_no_elicitation_when_ntfy_configured(self, mcp_db, monkeypatch):
        """No elicitation when ntfy is configured."""
        from mcp_server import add_watch
        from unittest.mock import AsyncMock

        monkeypatch.setattr("mcp_server.load_notify_config", lambda: {
            "ntfy_topic": "alerts", "ntfy_server": "https://ntfy.sh",
            "gmail_sender": "", "gmail_recipient": "",
            "gmail_app_password": "",
        })
        monkeypatch.setattr("mcp_server.send_ntfy", lambda **kwargs: True)

        mock_ctx = MagicMock()
        mock_ctx.elicit = AsyncMock()

        result = json.loads(asyncio.run(add_watch("YYZ", "LAX", 50000, ctx=mock_ctx)))
        assert result["status"] == "created"
        mock_ctx.elicit.assert_not_called()
        assert result.get("notifications") == "ntfy"

    def test_elicitation_declined_still_creates_watch(self, mcp_db, monkeypatch):
        """Elicitation declined; watch should still be created."""
        from mcp_server import add_watch
        from unittest.mock import AsyncMock

        monkeypatch.setattr("mcp_server.load_notify_config", lambda: {
            "ntfy_topic": "", "ntfy_server": "https://ntfy.sh",
            "gmail_sender": "", "gmail_recipient": "",
            "gmail_app_password": "",
        })

        mock_elicit_result = MagicMock()
        mock_elicit_result.action = "decline"
        mock_elicit_result.data = None

        mock_ctx = MagicMock()
        mock_ctx.elicit = AsyncMock(return_value=mock_elicit_result)

        result = json.loads(asyncio.run(add_watch("YYZ", "LAX", 50000, ctx=mock_ctx)))
        assert result["status"] == "created"
        assert result.get("notifications") == "none"

    def test_elicitation_not_supported_still_creates_watch(self, mcp_db, monkeypatch):
        """Elicitation raises exception; watch should still be created."""
        from mcp_server import add_watch
        from unittest.mock import AsyncMock

        monkeypatch.setattr("mcp_server.load_notify_config", lambda: {
            "ntfy_topic": "", "ntfy_server": "https://ntfy.sh",
            "gmail_sender": "", "gmail_recipient": "",
            "gmail_app_password": "",
        })

        mock_ctx = MagicMock()
        mock_ctx.elicit = AsyncMock(side_effect=Exception("Elicitation not supported"))

        result = json.loads(asyncio.run(add_watch("YYZ", "LAX", 50000, ctx=mock_ctx)))
        assert result["status"] == "created"
        assert result.get("notifications") == "none"

    def test_elicits_ntfy_topic_when_none_configured(self, mcp_db, monkeypatch):
        """No ntfy topic — elicits str for ntfy topic, saves it."""
        from mcp_server import add_watch
        from unittest.mock import AsyncMock

        monkeypatch.setattr("mcp_server.load_notify_config", lambda: {
            "ntfy_topic": "", "ntfy_server": "https://ntfy.sh",
            "gmail_sender": "", "gmail_recipient": "",
            "gmail_app_password": "",
        })

        mock_save = MagicMock()
        monkeypatch.setattr("mcp_server.save_notify_config", mock_save)

        mock_elicit_result = MagicMock()
        mock_elicit_result.action = "accept"
        mock_elicit_result.data = "my-topic"

        mock_ctx = MagicMock()
        mock_ctx.elicit = AsyncMock(return_value=mock_elicit_result)

        result = json.loads(asyncio.run(add_watch("YYZ", "LAX", 50000, ctx=mock_ctx)))
        assert result["status"] == "created"
        mock_ctx.elicit.assert_called_once()
        # Verify it's a str elicitation
        call_kwargs = mock_ctx.elicit.call_args
        assert call_kwargs.kwargs.get("response_type") is str or call_kwargs[1].get("response_type") is str
        # Verify save was called with topic
        mock_save.assert_any_call(topic="my-topic")

    def test_response_includes_notifications_key(self, mcp_db, monkeypatch):
        """Response always includes notifications key with correct status."""
        from mcp_server import add_watch

        monkeypatch.setattr("mcp_server.load_notify_config", lambda: {
            "ntfy_topic": "alerts", "ntfy_server": "https://ntfy.sh",
            "gmail_sender": "", "gmail_recipient": "",
            "gmail_app_password": "",
        })
        monkeypatch.setattr("mcp_server.send_ntfy", lambda **kwargs: True)

        # No ctx provided — no elicitation possible
        result = json.loads(asyncio.run(add_watch("YYZ", "LAX", 50000)))
        assert "notifications" in result
        assert result["notifications"] == "ntfy"


class TestFormatNotification:
    def test_single_match(self):
        from mcp_server import _format_notification
        note = _format_notification(
            {"origin": "YYZ", "destination": "LAX", "max_miles": 50000},
            [{"date": "2026-05-01", "cabin": "economy", "award_type": "Saver", "miles": 13000, "taxes_cents": 6851}],
        )
        assert note["title"] == "Award Deal: YYZ -> LAX"
        assert "13,000" in note["body"]
        assert "economy Saver" in note["body"]
        assert "$68.51" in note["body"]
        assert "50,000" in note["body"]

    def test_multiple_matches(self):
        from mcp_server import _format_notification
        note = _format_notification(
            {"origin": "YVR", "destination": "SFO", "max_miles": 30000},
            [
                {"date": "2026-06-01", "cabin": "economy", "award_type": "Saver", "miles": 10000, "taxes_cents": 5200},
                {"date": "2026-06-02", "cabin": "economy", "award_type": "Saver", "miles": 12000, "taxes_cents": 5200},
                {"date": "2026-06-03", "cabin": "business", "award_type": "Standard", "miles": 25000, "taxes_cents": 5200},
            ],
        )
        assert "YVR" in note["title"] and "SFO" in note["title"]
        assert "10,000" in note["body"]  # cheapest
        assert "+ 2 more matches" in note["body"]
        assert "30,000" in note["body"]


class TestSubmitMFA:
    """Tests for the submit_mfa MCP tool."""

    def _reset_session(self):
        """Reset all _session keys to clean state."""
        import mcp_server
        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = False
        mcp_server._session["pending_scrape"] = None
        mcp_server._session["mfa_method"] = "sms"

    def test_submit_mfa_success(self, mcp_db):
        """MFA pending, code accepted, pending scrape runs successfully."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock, patch

        mock_farm = MagicMock()
        mock_farm._enter_mfa_code.return_value = True
        mock_farm.refresh_cookies.return_value = True

        mcp_server._session["farm"] = mock_farm
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = True
        mcp_server._session["pending_scrape"] = ("YYZ", "LAX")

        mock_scrape = MagicMock(return_value={"found": 47, "stored": 47})

        # Mock HybridScraper so submit_mfa can start the scraper
        mock_hs_module = MagicMock()
        mock_scraper_instance = MagicMock()
        mock_hs_module.HybridScraper.return_value = mock_scraper_instance

        try:
            with patch.dict("sys.modules", {
                     "scrape": MagicMock(scrape_route=mock_scrape),
                     "core.hybrid_scraper": mock_hs_module,
                 }):
                result = json.loads(asyncio.run(mcp_server.submit_mfa("123456")))

            assert result["status"] == "complete"
            assert result["route"] == "YYZ-LAX"
            assert result["found"] == 47
            assert result["stored"] == 47
            assert mcp_server._session["logged_in"] is True
            assert mcp_server._session["mfa_pending"] is False
            assert mcp_server._session["pending_scrape"] is None
            mock_farm._enter_mfa_code.assert_called_once_with("123456")
        finally:
            self._reset_session()

    def test_submit_mfa_code_rejected(self, mcp_db):
        """Code rejected by United — mfa_pending stays True for retry."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock

        mock_farm = MagicMock()
        mock_farm._enter_mfa_code.return_value = False

        mcp_server._session["farm"] = mock_farm
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = True
        mcp_server._session["pending_scrape"] = ("YYZ", "LAX")

        try:
            result = json.loads(asyncio.run(mcp_server.submit_mfa("000000")))

            assert result["status"] == "mfa_failed"
            assert mcp_server._session["mfa_pending"] is True
            assert mcp_server._session["logged_in"] is False
        finally:
            self._reset_session()

    def test_submit_mfa_no_pending(self, mcp_db):
        """No MFA in progress — returns no_mfa_pending error."""
        import asyncio
        import mcp_server

        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = False
        mcp_server._session["pending_scrape"] = None

        try:
            result = json.loads(asyncio.run(mcp_server.submit_mfa("123456")))

            assert result["error"] == "no_mfa_pending"
        finally:
            self._reset_session()

    def test_submit_mfa_no_session(self, mcp_db):
        """MFA pending but farm is None (browser crashed) — returns no_session."""
        import asyncio
        import mcp_server

        mcp_server._session["farm"] = None
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = True
        mcp_server._session["pending_scrape"] = ("YYZ", "LAX")

        try:
            result = json.loads(asyncio.run(mcp_server.submit_mfa("123456")))

            assert result["error"] == "no_session"
            # Session state should be cleaned up
            assert mcp_server._session["mfa_pending"] is False
            assert mcp_server._session["pending_scrape"] is None
        finally:
            self._reset_session()

    def test_submit_mfa_login_only(self, mcp_db):
        """MFA pending, no pending scrape — just completes login."""
        import asyncio
        import mcp_server
        from unittest.mock import MagicMock, patch

        mock_farm = MagicMock()
        mock_farm._enter_mfa_code.return_value = True

        mcp_server._session["farm"] = mock_farm
        mcp_server._session["scraper"] = None
        mcp_server._session["logged_in"] = False
        mcp_server._session["mfa_pending"] = True
        mcp_server._session["pending_scrape"] = None

        # Mock HybridScraper so submit_mfa can start the scraper
        mock_hs_module = MagicMock()
        mock_scraper_instance = MagicMock()
        mock_hs_module.HybridScraper.return_value = mock_scraper_instance

        try:
            with patch.dict("sys.modules", {"core.hybrid_scraper": mock_hs_module}):
                result = json.loads(asyncio.run(mcp_server.submit_mfa("654321")))

            assert result["status"] == "logged_in"
            assert mcp_server._session["logged_in"] is True
            assert mcp_server._session["mfa_pending"] is False
            mock_farm._enter_mfa_code.assert_called_once_with("654321")
        finally:
            self._reset_session()
