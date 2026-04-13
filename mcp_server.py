"""seataero MCP server — exposes award flight tools via JSON-RPC over stdio."""

import asyncio
import glob
import json
import logging
import os
import shutil
import signal
import sys
import atexit
import tempfile
import threading
from contextlib import asynccontextmanager

from typing import Annotated, Literal

from fastmcp import FastMCP, Context
from fastmcp.server.tasks import TaskConfig
from fastmcp.dependencies import Progress
from datetime import timedelta
from mcp.types import ToolAnnotations
from pydantic import Field

from core import db
from core import presentation
from core.matching import CABIN_FILTER_MAP, compute_match_hash as _compute_match_hash, format_notification as _format_notification
from core.watchlist import parse_interval
from core.notify import load_notify_config, save_notify_config, send_ntfy

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("seataero-mcp")

_PROXY_URL = os.getenv("PROXY_URL", "").strip() or None

Airport = Annotated[str, Field(description="3-letter IATA airport code (e.g., YYZ)")]
Cabin = Annotated[Literal["economy", "business", "first", ""], Field(description="Cabin class filter (empty for all)")]
DateStr = Annotated[str, Field(description="Date in YYYY-MM-DD format")]
SortOrder = Annotated[Literal["date", "miles", "cabin"], Field(description="Sort order for results")]
MfaMethod = Annotated[Literal["sms", "email"], Field(description="MFA delivery channel")]


class _MFAPending(Exception):
    """Raised when MFA is required but elicitation is unavailable."""
    pass


mcp = FastMCP("seataero", instructions="""seataero provides United MileagePlus award flight data.

Tool selection:
- query_flights: ALWAYS try this first. Returns cached availability with pre-computed summary (cheapest deal, saver counts, format suggestions). Instant results.
- get_flight_details: Get paginated raw rows (default 15, sorted by cheapest). Use after query_flights when building tables or charts.
- get_price_trend: Per-date cheapest miles for a route. Use for graphing price over time.
- find_deals: Scan all routes for below-average pricing. Server-side analysis.
- search_route: Only if query_flights returns no results or data is stale. Launches a local browser scrape (~2 min). Runs as a background task — returns immediately, reports progress via the protocol.
- submit_mfa: Only after search_route returns {"status": "mfa_required"}. Ask the user for their SMS/email code, then call this. The browser is already on the MFA screen.
- flight_status: Check data freshness and coverage (record count, routes, date range, scrape stats).
- add_alert / check_alerts: Price monitoring. Create alerts with a miles threshold; check_alerts evaluates them against cached data with deduplication.
- add_watch / list_watches / remove_watch / check_watches: Watchlist with push notifications via ntfy. Watches periodically check for deals below a miles threshold. check_watches sends ntfy push if configured and returns pre-formatted notifications for other channels (email, Slack).

Scrape workflow:
1. search_route("YYZ", "LAX") → returns immediately as a background task
2. Progress is reported via the protocol (window X/12, found/stored counts)
3. If MFA is required: search_route returns {"status": "mfa_required"} — ask user for SMS code → submit_mfa(code) → submit_mfa runs the pending scrape automatically
4. When complete: call query_flights or get_flight_details to retrieve the scraped results

MFA may be required on any scrape, not just the first. The browser session is kept alive between routes but United may expire sessions.

IMPORTANT: When query_flights returns no_results, your next action MUST be search_route. Do not return text to the user. Do not ask for confirmation. Just call search_route.

Before calling search_route for any reason (no_results or stale data), tell the user: "Starting a fresh scrape — this takes about 2 minutes." Then immediately call search_route without waiting for a response.

In autonomous/loop mode, skip the query_flights-first rule — call search_route directly since the goal is fresh data each iteration.

Presentation:
After calling a data tool (query_flights, get_flight_details, get_price_trend, find_deals),
call the show_* tool named in the _present_with field to format results for the user.
Display show_* output verbatim — do not reformat, summarize, or add commentary around it.

Presentation tool selection:
- show_summary: Deal card for quick overview (after query_flights)
- show_flights: seats.aero-style table with all dates/cabins (after get_flight_details, or when user wants a table)
- show_graph: ASCII price chart over time (after get_price_trend, or when user wants a graph)
- show_deals: Deals table across routes (after find_deals)
- show_general: Passthrough for freeform text — only when no specific tool fits

You can override _present_with based on user intent:
- "show me flights/prices/table" → show_flights
- "graph/chart/trend" → show_graph
- "what's cheapest?" / quick summary → show_summary

If the user asks for multiple views (e.g., "summary and table", "graph and deals"), call each corresponding show_* tool in sequence before stopping.

Do NOT query the database directly via SQL, import core.db, or run seataero CLI commands via Bash. These tools handle everything.""")

def _notify_status(cfg: dict) -> str:
    """Return 'ntfy' if ntfy topic is configured, else 'none'."""
    return "ntfy" if cfg.get("ntfy_topic") else "none"



# _format_notification and CABIN_FILTER_MAP imported from core.matching

SORT_KEYS = {
    "date": lambda r: (r["date"], r["cabin"], r["miles"]),
    "miles": lambda r: (r["miles"], r["date"], r["cabin"]),
    "cabin": lambda r: (r["cabin"], r["date"], r["miles"]),
}

# Persistent browser session — survives across tool calls
# RLock guards _session against concurrent access from asyncio.to_thread() workers.
# Reentrant because blocking_scrape() → _ensure_session() would deadlock with a plain Lock.
_session_lock = threading.RLock()
_session = {
    "farm": None,        # CookieFarm instance
    "scraper": None,     # HybridScraper instance
    "logged_in": False,  # True after successful login+MFA
    "mfa_pending": False,
    "pending_scrape": None,  # (origin, destination) tuple when scrape interrupted by MFA
    "mfa_method": "sms",
}

def _ensure_session(mfa_prompt=None, mfa_method="sms"):
    """Start CookieFarm + HybridScraper if not already running. Login if needed."""
    with _session_lock:
        if _session["farm"] is not None and _session["logged_in"]:
            return  # Session is warm — reuse

        if _session["farm"] is None:
            from core.cookie_farm import CookieFarm
            from core.hybrid_scraper import HybridScraper  # noqa: F811

            farm = CookieFarm(headless=False, ephemeral=True, proxy=_PROXY_URL)
            farm.start()
            _session["farm"] = farm
            logger.info("Cookie farm started")

        if not _session["logged_in"]:
            _session["farm"].ensure_logged_in(mfa_prompt=mfa_prompt, mfa_method=mfa_method)
            _session["logged_in"] = True
            logger.info("Login confirmed")

        if _session["scraper"] is None:
            from core.hybrid_scraper import HybridScraper
            scraper = HybridScraper(_session["farm"], refresh_interval=2)
            scraper.start()
            _session["scraper"] = scraper
            logger.info("Scraper started")


def _stop_session():
    """Stop CookieFarm, HybridScraper, clean up."""
    with _session_lock:
        if _session["scraper"]:
            try:
                _session["scraper"].stop()
            except Exception:
                pass
            _session["scraper"] = None
        if _session["farm"]:
            try:
                _session["farm"].stop()
            except Exception:
                pass
            _session["farm"] = None
        _session["logged_in"] = False
        _session["mfa_pending"] = False
        _session["pending_scrape"] = None
        _session["mfa_method"] = "sms"
        logger.info("Session stopped")


atexit.register(_stop_session)


def _cleanup_orphans():
    """Kill orphaned Chrome processes and temp profiles from previous crashes."""
    tmp = tempfile.gettempdir()
    dirs = glob.glob(os.path.join(tmp, "seataero-browser-*"))
    if dirs:
        logger.info("Cleaning %d orphaned temp profiles from previous runs", len(dirs))
        for d in dirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass


def _signal_cleanup(signum, frame):
    """Handle SIGTERM/SIGINT — clean up browser before exit."""
    logger.info("Signal %s received, cleaning up...", signum)
    _stop_session()
    _cleanup_orphans()
    sys.exit(0)


# Register signal handlers for graceful shutdown
signal.signal(signal.SIGTERM, _signal_cleanup)
signal.signal(signal.SIGINT, _signal_cleanup)
if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _signal_cleanup)


@asynccontextmanager
async def _lifespan(server):
    """FastMCP lifespan — clean orphans on startup, stop session on shutdown."""
    _cleanup_orphans()
    try:
        yield {}
    finally:
        _stop_session()
        _cleanup_orphans()
        logger.info("Lifespan cleanup complete")

mcp.lifespan = _lifespan


def _compute_summary(rows):
    """Compute summary stats from query results for agent consumption."""
    if not rows:
        return None
    from datetime import datetime, timezone

    cheapest = min(rows, key=lambda r: r["miles"])
    saver_rows = [r for r in rows if r["award_type"] == "Saver"]
    standard_rows = [r for r in rows if r["award_type"] == "Standard"]
    saver_dates = len(set(r["date"] for r in saver_rows))
    standard_dates = len(set(r["date"] for r in standard_rows))
    miles_values = [r["miles"] for r in rows]
    dates = sorted(set(r["date"] for r in rows))
    cabins = sorted(set(r["cabin"] for r in rows))

    # Data age from most recent scraped_at
    latest_scraped = max(r["scraped_at"] for r in rows)
    try:
        scraped_dt = datetime.fromisoformat(latest_scraped.replace("Z", "+00:00"))
        age_hours = round((datetime.now(timezone.utc) - scraped_dt).total_seconds() / 3600, 1)
    except Exception:
        age_hours = None

    return {
        "cheapest": {
            "date": cheapest["date"],
            "cabin": cheapest["cabin"],
            "award_type": cheapest["award_type"],
            "miles": cheapest["miles"],
            "taxes_cents": cheapest.get("taxes_cents"),
        },
        "saver_dates": saver_dates,
        "standard_dates": standard_dates,
        "miles_range": [min(miles_values), max(miles_values)],
        "date_range": [dates[0], dates[-1]] if dates else [],
        "data_age_hours": age_hours,
        "cabins_available": cabins,
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def query_flights(origin: Airport, destination: Airport, cabin: Cabin = "",
                  from_date: DateStr = "", to_date: DateStr = "",
                  date: DateStr = "", sort: SortOrder = "date") -> str:
    """Search cached United MileagePlus award flight availability. Instant — no network calls.

    ALWAYS try this first before search_route. Returns a pre-computed summary: total count,
    cheapest option, Saver/Standard date counts, miles range, data freshness, and format
    suggestions. Does not return individual rows — use get_flight_details for full row data
    when building tables.

    Args:
        origin: 3-letter IATA airport code (e.g., YYZ)
        destination: 3-letter IATA airport code (e.g., LAX)
        cabin: Filter by cabin class — economy, business, or first. Empty for all.
        from_date: Start of date range filter (YYYY-MM-DD)
        to_date: End of date range filter (YYYY-MM-DD)
        date: Exact date filter (YYYY-MM-DD). Overrides from_date/to_date.
        sort: Sort order — date, miles, or cabin"""
    try:
        with db.connection() as conn:
            cabin_filter = CABIN_FILTER_MAP.get(cabin.lower()) if cabin else None
            rows = db.query_availability(
                conn, origin.upper(), destination.upper(),
                date=date or None, date_from=from_date or None,
                date_to=to_date or None, cabin=cabin_filter,
            )

            if not rows:
                return json.dumps({"error": "no_results",
                                   "message": f"No availability found for {origin.upper()}-{destination.upper()}",
                                   "next_step": "AUTOMATICALLY call search_route now to scrape fresh data from United. Do not ask the user."})

            sort_fn = SORT_KEYS.get(sort, SORT_KEYS["date"])
            rows.sort(key=sort_fn)

            summary = _compute_summary(rows)

            return json.dumps({
                "count": len(rows),
                "_summary": summary,
                "_present_with": "show_summary",
            }, indent=2)
    except Exception as e:
        logger.error(f"query_flights failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_flight_details(origin: Airport, destination: Airport, cabin: Cabin = "",
                       from_date: DateStr = "", to_date: DateStr = "",
                       date: DateStr = "", sort: SortOrder = "miles",
                       limit: int = 15, offset: int = 0) -> str:
    """Retrieve individual flight availability rows with pagination.

    Use after query_flights when you need full row data for building tables or charts.
    Returns raw flight data: date, cabin, award_type, miles, taxes.
    Default: 15 rows sorted by cheapest miles. Use offset for pagination.

    Args:
        origin: 3-letter IATA airport code (e.g., YYZ)
        destination: 3-letter IATA airport code (e.g., LAX)
        cabin: Filter by cabin class — economy, business, or first. Empty for all.
        from_date: Start of date range filter (YYYY-MM-DD)
        to_date: End of date range filter (YYYY-MM-DD)
        date: Exact date filter (YYYY-MM-DD)
        sort: Sort order — date, miles, or cabin (default: miles)
        limit: Number of rows to return (1-50, default 15)
        offset: Skip this many rows for pagination"""
    try:
        limit = max(1, min(limit, 50))
        offset = max(0, offset)

        with db.connection() as conn:
            cabin_filter = CABIN_FILTER_MAP.get(cabin.lower()) if cabin else None
            rows = db.query_availability(
                conn, origin.upper(), destination.upper(),
                date=date or None, date_from=from_date or None,
                date_to=to_date or None, cabin=cabin_filter,
            )

            if not rows:
                return json.dumps({"error": "no_results",
                                   "message": f"No availability found for {origin.upper()}-{destination.upper()}"})

            sort_fn = SORT_KEYS.get(sort, SORT_KEYS["miles"])
            rows.sort(key=sort_fn)

            total = len(rows)
            page = rows[offset:offset + limit]

            return json.dumps({
                "results": page,
                "total": total,
                "showing": f"{offset + 1}-{min(offset + limit, total)} of {total}",
                "has_more": offset + limit < total,
                "_present_with": "show_flights",
            }, indent=2)
    except Exception as e:
        logger.error(f"get_flight_details failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_price_trend(origin: Airport, destination: Airport, cabin: Cabin = "",
                    from_date: DateStr = "", to_date: DateStr = "") -> str:
    """Per-date cheapest miles for a route — compact time series for graphing.

    Returns one data point per date: the minimum miles cost across all award types.
    Ideal for plotting price over time (x=date, y=miles). Use this when the user
    asks for a price chart, trend, or graph.

    Args:
        origin: 3-letter IATA airport code (e.g., YYZ)
        destination: 3-letter IATA airport code (e.g., LAX)
        cabin: Filter by cabin class — economy, business, or first. Empty for all.
        from_date: Start of date range filter (YYYY-MM-DD)
        to_date: End of date range filter (YYYY-MM-DD)"""
    try:
        with db.connection() as conn:
            cabin_filter = CABIN_FILTER_MAP.get(cabin.lower()) if cabin else None
            rows = db.query_availability(
                conn, origin.upper(), destination.upper(),
                cabin=cabin_filter,
                date_from=from_date or None, date_to=to_date or None,
            )

            if not rows:
                return json.dumps({"error": "no_results",
                                   "message": f"No availability found for {origin.upper()}-{destination.upper()}"})

            # Aggregate: one point per date, cheapest miles
            by_date = {}
            for r in rows:
                d = r["date"]
                if d not in by_date or r["miles"] < by_date[d]["miles"]:
                    by_date[d] = {"date": d, "miles": r["miles"],
                                  "cabin": r["cabin"], "award_type": r["award_type"]}

            trend = sorted(by_date.values(), key=lambda x: x["date"])

            return json.dumps({
                "route": f"{origin.upper()}-{destination.upper()}",
                "cabin_filter": cabin or "all",
                "data_points": len(trend),
                "trend": trend,
                "_present_with": "show_graph",
            }, indent=2)
    except Exception as e:
        logger.error(f"get_price_trend failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def find_deals(cabin: Cabin = "", max_results: int = 10) -> str:
    """Find the best deals across all cached routes — server-side analysis, no token waste.

    Compares each route's cheapest current price against its historical average.
    Returns routes where current pricing is significantly below average.

    Args:
        cabin: Filter by cabin class — economy, business, or first. Empty for all.
        max_results: Maximum number of deals to return (1-25, default 10)"""
    try:
        max_results = max(1, min(max_results, 25))
        with db.connection() as conn:
            cabin_filter = CABIN_FILTER_MAP.get(cabin.lower()) if cabin else None
            deals = db.find_deals_query(conn, cabin=cabin_filter, max_results=max_results)

            if not deals:
                return json.dumps({"deals_found": 0,
                                   "message": "No deals found. Data may be too fresh for comparison, or all routes are at typical pricing."})

            return json.dumps({
                "deals_found": len(deals),
                "cabin_filter": cabin or "all",
                "deals": deals,
                "_present_with": "show_deals",
            }, indent=2)
    except Exception as e:
        logger.error(f"find_deals failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def show_flights(origin: Airport, destination: Airport, cabin: Cabin = "",
                 from_date: DateStr = "", to_date: DateStr = "",
                 date: DateStr = "", sort: SortOrder = "date",
                 limit: int = 30) -> str:
    """Format flight availability as a seats.aero-style table. Display this output directly to the user without modification.

    Args:
        origin: 3-letter IATA airport code (e.g., YYZ)
        destination: 3-letter IATA airport code (e.g., LAX)
        cabin: Filter by cabin class — economy, business, or first. Empty for all.
        from_date: Start of date range filter (YYYY-MM-DD)
        to_date: End of date range filter (YYYY-MM-DD)
        date: Exact date filter (YYYY-MM-DD)
        sort: Sort order — date, miles, or cabin
        limit: Maximum dates to show (1-50, default 30)"""
    try:
        limit = max(1, min(limit, 50))
        with db.connection() as conn:
            cabin_filter = CABIN_FILTER_MAP.get(cabin.lower()) if cabin else None
            rows = db.query_availability(
                conn, origin.upper(), destination.upper(),
                date=date or None, date_from=from_date or None,
                date_to=to_date or None, cabin=cabin_filter,
            )
            if not rows:
                return f"No data for {origin.upper()}-{destination.upper()}. Call search_route to scrape."
            sort_fn = SORT_KEYS.get(sort, SORT_KEYS["date"])
            rows.sort(key=sort_fn)
            return presentation.format_flights_table(
                rows, origin.upper(), destination.upper(),
                cabin_filter=cabin or None, limit=limit,
            )
    except Exception as e:
        logger.error(f"show_flights failed: {e}", exc_info=True)
        return f"Error: {e}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def show_summary(origin: Airport, destination: Airport, cabin: Cabin = "",
                 from_date: DateStr = "", to_date: DateStr = "",
                 date: DateStr = "") -> str:
    """Format a deal summary card. Display this output directly to the user without modification.

    Args:
        origin: 3-letter IATA airport code (e.g., YYZ)
        destination: 3-letter IATA airport code (e.g., LAX)
        cabin: Filter by cabin class — economy, business, or first. Empty for all.
        from_date: Start of date range filter (YYYY-MM-DD)
        to_date: End of date range filter (YYYY-MM-DD)
        date: Exact date filter (YYYY-MM-DD)"""
    try:
        with db.connection() as conn:
            cabin_filter = CABIN_FILTER_MAP.get(cabin.lower()) if cabin else None
            rows = db.query_availability(
                conn, origin.upper(), destination.upper(),
                date=date or None, date_from=from_date or None,
                date_to=to_date or None, cabin=cabin_filter,
            )
            if not rows:
                return f"No data for {origin.upper()}-{destination.upper()}. Call search_route to scrape."
            summary = _compute_summary(rows)
            return presentation.format_summary_card(
                summary, origin.upper(), destination.upper(), count=len(rows),
            )
    except Exception as e:
        logger.error(f"show_summary failed: {e}", exc_info=True)
        return f"Error: {e}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def show_graph(origin: Airport, destination: Airport, cabin: Cabin = "",
               from_date: DateStr = "", to_date: DateStr = "") -> str:
    """Format a price trend as an ASCII chart. Display this output directly to the user without modification.

    Args:
        origin: 3-letter IATA airport code (e.g., YYZ)
        destination: 3-letter IATA airport code (e.g., LAX)
        cabin: Filter by cabin class — economy, business, or first. Empty for all.
        from_date: Start of date range filter (YYYY-MM-DD)
        to_date: End of date range filter (YYYY-MM-DD)"""
    try:
        with db.connection() as conn:
            cabin_filter = CABIN_FILTER_MAP.get(cabin.lower()) if cabin else None
            rows = db.query_availability(
                conn, origin.upper(), destination.upper(),
                cabin=cabin_filter,
                date_from=from_date or None, date_to=to_date or None,
            )
            if not rows:
                return f"No data for {origin.upper()}-{destination.upper()}. Call search_route to scrape."
            # Aggregate: one point per date, cheapest miles
            by_date = {}
            for r in rows:
                d = r["date"]
                if d not in by_date or r["miles"] < by_date[d]["miles"]:
                    by_date[d] = {"date": d, "miles": r["miles"],
                                  "cabin": r["cabin"], "award_type": r["award_type"]}
            trend = sorted(by_date.values(), key=lambda x: x["date"])
            return presentation.format_price_chart(
                trend, origin.upper(), destination.upper(),
                cabin_filter=cabin or None,
            )
    except Exception as e:
        logger.error(f"show_graph failed: {e}", exc_info=True)
        return f"Error: {e}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def show_deals(cabin: Cabin = "", max_results: int = 10) -> str:
    """Format best deals across all routes as a table. Display this output directly to the user without modification.

    Args:
        cabin: Filter by cabin class — economy, business, or first. Empty for all.
        max_results: Maximum number of deals to return (1-25, default 10)"""
    try:
        max_results = max(1, min(max_results, 25))
        with db.connection() as conn:
            cabin_filter = CABIN_FILTER_MAP.get(cabin.lower()) if cabin else None
            deals = db.find_deals_query(conn, cabin=cabin_filter, max_results=max_results)
            if not deals:
                return "No deals found."
            return presentation.format_deals_table(deals, cabin_filter=cabin or None)
    except Exception as e:
        logger.error(f"show_deals failed: {e}", exc_info=True)
        return f"Error: {e}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def show_general(text: str) -> str:
    """Pass through text for display. Use only when no specific show_* tool applies. Display this output directly to the user without modification.

    Args:
        text: The text to display"""
    return presentation.format_general(text)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def flight_status() -> str:
    """Check seataero database health: record count, route coverage, date range, and data freshness.

    Use this to determine if data exists and how stale it is before deciding whether to scrape.

    Returns JSON with total_rows, routes_covered, latest_scrape, date_range_start/end,
    and scrape job stats (completed/failed/total).
    """
    try:
        with db.connection() as conn:
            avail_stats = db.get_scrape_stats(conn)
            job_stats = db.get_job_stats(conn)

            stats = {**avail_stats, **job_stats}
            return json.dumps(stats, indent=2)
    except Exception as e:
        logger.error(f"flight_status failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def add_alert(origin: Airport, destination: Airport, max_miles: int,
              cabin: Cabin = "", from_date: DateStr = "", to_date: DateStr = "") -> str:
    """Create a price alert for award flights. Triggers when miles cost drops to or below the threshold.

    Args:
        origin: 3-letter IATA airport code (e.g., YYZ)
        destination: 3-letter IATA airport code (e.g., LAX)
        max_miles: Alert when miles cost is at or below this value
        cabin: Filter by cabin class — economy, business, or first. Empty for all.
        from_date: Start of date range filter (YYYY-MM-DD)
        to_date: End of date range filter (YYYY-MM-DD)"""
    try:
        with db.connection() as conn:
            alert_id = db.create_alert(
                conn, origin.upper(), destination.upper(), max_miles,
                cabin=cabin or None, date_from=from_date or None,
                date_to=to_date or None,
            )

            return json.dumps({
                "id": alert_id,
                "status": "created",
                "origin": origin.upper(),
                "destination": destination.upper(),
                "max_miles": max_miles,
            })
    except Exception as e:
        logger.error(f"add_alert failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})



# _compute_match_hash imported from core.matching


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def check_alerts() -> str:
    """Evaluate all active price alerts against current cached data.

    Returns which alerts triggered with matching flights. Deduplicates — won't
    re-notify for identical matches. Side effects: expires past-date alerts,
    updates notification hashes to prevent duplicate triggers.
    No arguments — checks all active alerts."""
    try:
        with db.connection() as conn:
            expired = db.expire_past_alerts(conn)
            alerts = db.list_alerts(conn, active_only=True)

            if not alerts:
                return json.dumps({"alerts_checked": 0, "alerts_triggered": 0, "expired": expired})

            results = []
            for alert in alerts:
                cabin_filter = CABIN_FILTER_MAP.get(alert["cabin"]) if alert.get("cabin") else None
                matches = db.check_alert_matches(
                    conn, alert["origin"], alert["destination"], alert["max_miles"],
                    cabin=cabin_filter, date_from=alert.get("date_from"),
                    date_to=alert.get("date_to"),
                )

                if not matches:
                    continue

                match_hash = _compute_match_hash(matches)
                if match_hash == alert.get("last_notified_hash"):
                    continue

                db.update_alert_notification(conn, alert["id"], match_hash)
                results.append({
                    "alert_id": alert["id"],
                    "origin": alert["origin"],
                    "destination": alert["destination"],
                    "cabin": alert["cabin"],
                    "max_miles": alert["max_miles"],
                    "matches": matches,
                })

            return json.dumps({
                "alerts_checked": len(alerts),
                "alerts_triggered": len(results),
                "expired": expired,
                "results": results,
            }, indent=2)
    except Exception as e:
        logger.error(f"check_alerts failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
async def add_watch(origin: Airport, destination: Airport, max_miles: int,
                    cabin: Cabin = "", from_date: DateStr = "", to_date: DateStr = "",
                    every: str = "12h", ctx: Context = None) -> str:
    """Add a route to your watchlist for automatic monitoring and push notifications.

    Watches periodically check for award availability below a miles threshold
    and send notifications via ntfy when deals are found. If ntfy is not configured,
    prompts for setup. Use check_watches to evaluate all active watches.

    Args:
        origin: 3-letter IATA airport code (e.g., YYZ)
        destination: 3-letter IATA airport code (e.g., LAX)
        max_miles: Alert when miles cost is at or below this value
        cabin: Filter by cabin class — economy, business, or first. Empty for all.
        from_date: Start of date range filter (YYYY-MM-DD)
        to_date: End of date range filter (YYYY-MM-DD)
        every: Check interval — e.g. "12h", "30m", "1d" (default "12h")"""
    try:
        try:
            interval = parse_interval(every)
        except ValueError as e:
            return json.dumps({"error": "invalid_interval", "message": str(e)})

        # --- ntfy setup elicitation ---
        cfg = load_notify_config()
        if not cfg.get("ntfy_topic") and ctx is not None:
            try:
                result = await ctx.elicit(
                    "No ntfy topic configured. Enter an ntfy.sh topic name "
                    "to receive push notifications for watch matches "
                    "(or decline to skip — your agent can deliver via email instead):",
                    response_type=str,
                )
                if result.action == "accept" and result.data:
                    save_notify_config(topic=result.data.strip())
            except Exception:
                pass  # Elicitation not supported — continue without ntfy

        with db.connection() as conn:
            watch_id = db.create_watch(
                conn, origin.upper(), destination.upper(), max_miles,
                cabin=cabin or None, date_from=from_date or None,
                date_to=to_date or None, check_interval_minutes=interval,
            )

        # Send confirmation notification via ntfy
        cfg = load_notify_config()
        if cfg.get("ntfy_topic"):
            cabin_label = f" ({cabin})" if cabin else ""
            send_ntfy(
                topic=cfg["ntfy_topic"],
                title=f"Watch Added: {origin.upper()} -> {destination.upper()}",
                message=f"≤{max_miles:,} miles{cabin_label}. Use check_watches to evaluate.",
                priority=2,
                tags=["eyes"],
                server=cfg.get("ntfy_server", "https://ntfy.sh"),
            )

        # Reload config after potential elicitation changes
        cfg = load_notify_config()

        return json.dumps({
            "id": watch_id,
            "status": "created",
            "origin": origin.upper(),
            "destination": destination.upper(),
            "max_miles": max_miles,
            "check_interval_minutes": interval,
            "notifications": _notify_status(cfg),
        })
    except Exception as e:
        logger.error(f"add_watch failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_watches() -> str:
    """List all active watched routes with check intervals and last-checked times."""
    try:
        with db.connection() as conn:
            watches = db.list_watches(conn, active_only=True)

            return json.dumps({
                "watches": watches,
                "count": len(watches),
            }, indent=2)
    except Exception as e:
        logger.error(f"list_watches failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def remove_watch(watch_id: int) -> str:
    """Remove a route from your watchlist."""
    try:
        with db.connection() as conn:
            removed = db.remove_watch(conn, watch_id)

            if removed:
                return json.dumps({"status": "removed", "watch_id": watch_id})
            else:
                return json.dumps({"status": "not_found", "watch_id": watch_id})
    except Exception as e:
        logger.error(f"remove_watch failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def check_watches() -> str:
    """Evaluate all active watches against cached data. Returns matches with pre-formatted notifications.

    ntfy push notifications are sent directly if configured. For other channels (email, Slack),
    the 'notification' block in each result contains ready-to-use title and body strings.
    Does NOT trigger scraping — only checks existing cached data.
    Side effects: sends ntfy push if configured, updates notification hashes and last-checked timestamps."""
    try:
        with db.connection() as conn:
            watches = db.list_watches(conn, active_only=True)

            if not watches:
                return json.dumps({"watches_checked": 0, "watches_triggered": 0, "results": []})

            results = []
            notify_config = None

            for watch in watches:
                cabin_filter = CABIN_FILTER_MAP.get(watch["cabin"]) if watch.get("cabin") else None
                matches = db.check_alert_matches(
                    conn, watch["origin"], watch["destination"], watch["max_miles"],
                    cabin=cabin_filter, date_from=watch.get("date_from"),
                    date_to=watch.get("date_to"),
                )

                if not matches:
                    db.update_watch_checked(conn, watch["id"])
                    continue

                match_hash = _compute_match_hash(matches)
                if match_hash == watch.get("last_notified_hash"):
                    db.update_watch_checked(conn, watch["id"])
                    continue

                # New matches found
                note = _format_notification(watch, matches)

                # Send ntfy if configured (user's explicit choice)
                ntfy_sent = False
                if notify_config is None:
                    notify_config = load_notify_config()
                if notify_config.get("ntfy_topic"):
                    ntfy_sent = send_ntfy(
                        topic=notify_config["ntfy_topic"],
                        title=note["title"],
                        message=note["body"],
                        priority=4,
                        tags=["airplane", "moneybag"],
                        server=notify_config.get("ntfy_server", "https://ntfy.sh"),
                    )

                # Always update hash to prevent infinite retry when ntfy is down
                db.update_watch_notification(conn, watch["id"], match_hash)
                db.update_watch_checked(conn, watch["id"])

                results.append({
                    "watch_id": watch["id"],
                    "origin": watch["origin"],
                    "destination": watch["destination"],
                    "cabin": watch.get("cabin"),
                    "max_miles": watch["max_miles"],
                    "matches": matches,
                    "notification": note,
                    "ntfy_sent": ntfy_sent,
                })

            return json.dumps({
                "watches_checked": len(watches),
                "watches_triggered": len(results),
                "ntfy_active": bool(notify_config and notify_config.get("ntfy_topic")),
                "results": results,
            }, indent=2)
    except Exception as e:
        logger.error(f"check_watches failed: {e}", exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(
    task=TaskConfig(mode="optional", poll_interval=timedelta(seconds=5)),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def search_route(
    origin: Airport,
    destination: Airport,
    ctx: Context,
    progress: Progress = Progress(),
    mfa_method: MfaMethod = "sms",
    autonomous: Annotated[bool, Field(description="Skip interactive MFA prompts; forces email MFA")] = False,
) -> str:
    """Scrape fresh award flight data from United for a single route. Takes ~2 minutes.

    Only call this if query_flights returns no results or data is stale. Launches a local
    browser scrape. Runs as a background task — returns immediately, reports progress via
    the protocol (window X/12, found/stored counts).

    If MFA is required, returns {status: "mfa_required"} — ask user for SMS/email code,
    then call submit_mfa(code). submit_mfa will automatically run the pending scrape.

    When complete, call query_flights or get_flight_details to retrieve the scraped results.

    Args:
        origin: 3-letter IATA airport code (e.g., YYZ)
        destination: 3-letter IATA airport code (e.g., LAX)
        mfa_method: MFA delivery channel — "sms" (default) or "email"
        autonomous: Skip interactive MFA prompts; forces email MFA for automated/loop workflows"""
    origin, destination = origin.upper(), destination.upper()
    if autonomous:
        mfa_method = "email"
    with _session_lock:
        _session["mfa_method"] = mfa_method
    loop = asyncio.get_running_loop()

    # Sync->async bridge for MFA elicitation
    if autonomous:
        def sync_mfa_prompt(timeout: int = 300) -> str:
            raise RuntimeError("Autonomous mode — MFA code submitted via submit_mfa")
    else:
        if mfa_method == "email":
            _elicit_msg = "United sent a verification code to your email. Enter it:"
        else:
            _elicit_msg = "United sent an SMS code to your phone. Enter it:"

        def sync_mfa_prompt(timeout: int = 300) -> str:
            future = asyncio.run_coroutine_threadsafe(
                ctx.elicit(_elicit_msg, response_type=str),
                loop,
            )
            result = future.result(timeout=timeout)
            if result.action == "accept":
                return result.data
            elif result.action == "decline":
                raise RuntimeError("MFA declined by user")
            else:
                raise RuntimeError("MFA cancelled by user")

    # Sync->async bridge for progress reporting
    window_count = 0
    def sync_progress_cb(window, total, found, stored):
        nonlocal window_count
        asyncio.run_coroutine_threadsafe(
            progress.set_total(total), loop
        )
        asyncio.run_coroutine_threadsafe(
            progress.set_message(f"Window {window}/{total}: {found} found, {stored} stored"),
            loop,
        )
        # Increment by the delta since last callback
        delta = window - window_count
        for _ in range(delta):
            asyncio.run_coroutine_threadsafe(progress.increment(), loop)
        window_count = window

    def _wrapped_ensure_session():
        """Call _ensure_session with MFA-pending detection."""
        try:
            _ensure_session(mfa_prompt=sync_mfa_prompt, mfa_method=mfa_method)
        except RuntimeError as e:
            msg = str(e)
            if "MFA declined" in msg or "MFA cancelled" in msg:
                raise
            if _session.get("farm") is not None and not _session.get("logged_in"):
                raise _MFAPending(msg) from e
            raise
        except Exception as e:
            if _session.get("farm") is not None and not _session.get("logged_in"):
                raise _MFAPending(str(e)) from e
            raise

    def blocking_scrape():
        # Use timeout on lock acquisition — a stuck thread from a previous
        # cancelled call may still hold the lock (e.g., ghost cursor hang).
        if not _session_lock.acquire(timeout=90):
            # Force-tear down the stuck session so the next call works
            logger.error("_session_lock acquisition timed out — previous thread is stuck. Resetting session.")
            try:
                _stop_session()
            except Exception:
                pass
            raise RuntimeError(
                "Login timed out (previous session stuck). The session has been reset. "
                "Please try search_route again."
            )
        try:
            # Ensure session (login if needed, with elicitation for MFA)
            _wrapped_ensure_session()

            # Verify browser health for warm sessions
            if _session.get("scraper") and not _session["scraper"].is_browser_alive():
                logger.warning("Browser is dead — tearing down session, will cold start")
                _stop_session()
                _wrapped_ensure_session()

            # Check auth cookies for warm sessions
            if _session.get("farm") and _session.get("logged_in"):
                try:
                    session_valid = _session["farm"]._has_login_cookies()
                except Exception:
                    session_valid = False
                if not session_valid:
                    logger.warning("Auth cookies missing — tearing down, will cold start")
                    _stop_session()
                    _wrapped_ensure_session()

            with db.connection() as conn:
                from scrape import scrape_route as _scrape
                result = _scrape(origin, destination, conn, _session["scraper"],
                                delay=7.0, verbose=False,
                                progress_cb=sync_progress_cb)
                # Keep session warm for next route
                try:
                    _session["farm"].refresh_cookies()
                except Exception:
                    pass
                return result
        finally:
            _session_lock.release()

    try:
        result = await asyncio.to_thread(blocking_scrape)
        resp = {
            "status": "complete",
            "route": f"{origin}-{destination}",
            "found": result.get("found", 0),
            "stored": result.get("stored", 0),
            "next_step": "Call query_flights or get_flight_details to retrieve the scraped results.",
        }
        if result.get("circuit_break"):
            resp["warning"] = (
                "Akamai rate-limiting detected — scrape was cut short by the circuit breaker. "
                "Stop repeated scraping and wait at least 10 minutes before retrying. "
                "Cached data from previous scrapes is still available via query_flights."
            )
        elif result.get("found", 0) == 0 and result.get("errors", 0) > 0:
            resp["warning"] = (
                "All date windows failed — likely Akamai blocking. "
                "Stop repeated scraping and wait at least 10 minutes before retrying. "
                "Cached data from previous scrapes is still available via query_flights."
            )
        return json.dumps(resp)
    except _MFAPending:
        with _session_lock:
            _session["mfa_pending"] = True
            _session["pending_scrape"] = (origin, destination)
        if mfa_method == "email":
            msg = (
                "United requires a verification code to complete login. "
                "The code was sent to the account's email. "
                "Search Gmail for the most recent email from united@united.com "
                "with subject containing 'verification', "
                "extract the 6-digit code, and call submit_mfa."
            )
        else:
            msg = (
                "United requires a verification code to complete login. "
                "The code was sent via SMS. "
                "Ask the user for the code, then call submit_mfa."
            )
        return json.dumps({
            "status": "mfa_required",
            "route": f"{origin}-{destination}",
            "mfa_method": mfa_method,
            "message": msg,
        })
    except RuntimeError as e:
        if "MFA declined" in str(e) or "MFA cancelled" in str(e):
            return json.dumps({"error": "mfa_declined", "message": str(e)})
        raise
    except Exception as e:
        logger.error(f"search_route failed: {e}", exc_info=True)
        _stop_session()
        return json.dumps({"error": type(e).__name__, "message": str(e)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def submit_mfa(code: Annotated[str, Field(description="6-digit verification code from United")]) -> str:
    """Submit a United verification code to complete login and run the pending scrape.

    Only call after search_route returns {"status": "mfa_required"}. The browser is already
    on the MFA screen — this enters the code, completes login, and automatically runs
    the pending scrape that was interrupted by MFA.

    Args:
        code: 6-digit verification code from United (SMS or email)"""
    with _session_lock:
        if not _session.get("mfa_pending"):
            return json.dumps({"error": "no_mfa_pending",
                               "message": "No MFA in progress. Call search_route first."})

        farm = _session.get("farm")
        if farm is None:
            _session["mfa_pending"] = False
            _session["pending_scrape"] = None
            return json.dumps({"error": "no_session",
                               "message": "Browser session not active. Call search_route to restart."})

    def blocking_submit():
        with _session_lock:
            success = farm._enter_mfa_code(code.strip())
            if not success:
                return {"status": "mfa_failed",
                        "message": "Code rejected by United. Get a fresh code and try again."}

            _session["logged_in"] = True
            _session["mfa_pending"] = False
            logger.info("MFA accepted, login confirmed")

            # Start scraper if not running
            if _session["scraper"] is None:
                from core.hybrid_scraper import HybridScraper
                scraper = HybridScraper(farm, refresh_interval=2)
                scraper.start()
                _session["scraper"] = scraper
                logger.info("Scraper started after MFA")

            # Run pending scrape if any
            pending = _session.get("pending_scrape")
            _session["pending_scrape"] = None
            if pending:
                origin, destination = pending
                with db.connection() as conn:
                    from scrape import scrape_route as _scrape
                    result = _scrape(origin, destination, conn, _session["scraper"],
                                    delay=7.0, verbose=False)
                    try:
                        farm.refresh_cookies()
                    except Exception:
                        pass
                    return {
                        "status": "complete",
                        "route": f"{origin}-{destination}",
                        "found": result.get("found", 0),
                        "stored": result.get("stored", 0),
                        "next_step": "Call query_flights or get_flight_details to retrieve the scraped results.",
                    }

            return {"status": "logged_in",
                    "message": "MFA accepted. Session is warm — call search_route for any route."}

    try:
        result = await asyncio.to_thread(blocking_submit)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"submit_mfa failed: {e}", exc_info=True)
        with _session_lock:
            _session["mfa_pending"] = False
            _session["pending_scrape"] = None
        return json.dumps({"error": type(e).__name__, "message": str(e)})


def _list_tools():
    """Print all available MCP tools and exit."""
    tools = [
        ("query_flights", "Search cached availability (instant summary)"),
        ("get_flight_details", "Paginated raw rows for building tables"),
        ("get_price_trend", "Per-date cheapest miles for graphing"),
        ("find_deals", "Cross-route deal discovery (below-average pricing)"),
        ("flight_status", "Data freshness and coverage"),
        ("search_route", "Scrape fresh data from United (~2 min)"),
        ("submit_mfa", "Submit MFA code to complete login + pending scrape"),
        ("add_alert", "Create a price alert"),
        ("check_alerts", "Evaluate alerts against current data"),
        ("add_watch", "Watch a route with push notifications"),
        ("list_watches", "List active watched routes"),
        ("remove_watch", "Remove a watch"),
        ("check_watches", "Evaluate watches and send notifications"),
        ("show_flights", "Format availability as seats.aero-style table"),
        ("show_summary", "Format deal summary card"),
        ("show_graph", "Format price trend as ASCII chart"),
        ("show_deals", "Format deals table across routes"),
        ("show_general", "Pass through text for display"),
    ]
    print("seataero MCP server — available tools:\n")
    for name, desc in tools:
        print(f"  {name:24s} {desc}")
    print(f"\n{len(tools)} tools available. Connect via: claude mcp add seataero -- seataero-mcp")


def _health_check():
    """Run basic health checks and exit."""
    issues = []

    # Database
    db_path = os.getenv("SEATAERO_DB", db.DEFAULT_DB_PATH)
    try:
        with db.connection() as conn:
            db.create_schema(conn)
            row_count = conn.execute("SELECT COUNT(*) FROM availability").fetchone()[0]
        print(f"  database:    ok ({db_path}, {row_count:,} rows)")
    except Exception as e:
        print(f"  database:    FAIL ({e})")
        issues.append("database")

    # Playwright
    try:
        import importlib.metadata
        pw_version = importlib.metadata.version("playwright")
        print(f"  playwright:  ok ({pw_version})")
    except Exception:
        print(f"  playwright:  FAIL (not installed)")
        issues.append("playwright")

    # Credentials
    env_file = os.path.join(os.path.dirname(__file__), "scripts", "experiments", ".env")
    if os.path.isfile(env_file):
        print(f"  credentials: ok ({env_file})")
    else:
        print(f"  credentials: FAIL ({env_file} not found)")
        issues.append("credentials")

    print()
    if issues:
        print(f"FAIL: {len(issues)} issue(s) — {', '.join(issues)}")
        return 1
    else:
        print("OK: all checks passed")
        return 0


def main():
    if len(sys.argv) > 1:
        flag = sys.argv[1]
        if flag in ("--list-tools", "-l"):
            _list_tools()
            return
        if flag in ("--health", "--check"):
            print("seataero-mcp health check:\n")
            sys.exit(_health_check())
        if flag in ("--help", "-h"):
            print("seataero-mcp — MCP server for United MileagePlus award flight search")
            print()
            print("Usage:")
            print("  seataero-mcp              Start MCP server (stdio transport)")
            print("  seataero-mcp --list-tools  List all available tools")
            print("  seataero-mcp --health      Run health checks")
            print()
            print("Connect to an AI agent:")
            print("  claude mcp add seataero -- seataero-mcp")
            return
    # cookie_farm.py has 46 print() calls for login progress.  In stdio
    # transport, stdout IS the JSON-RPC pipe — those prints corrupt the
    # protocol.  Fix: make print() go to stderr while keeping .buffer on
    # the real stdout (which the MCP SDK reads for protocol I/O).
    _real_buffer = sys.stdout.buffer
    class _ProtocolSafeStdout:
        """print() → stderr, .buffer → real stdout for MCP protocol."""
        buffer = _real_buffer
        encoding = "utf-8"
        def write(self, s):  return sys.stderr.write(s)
        def flush(self):     sys.stderr.flush()
        def fileno(self):    return _real_buffer.fileno()
        def isatty(self):    return False
        def writable(self):  return True
    sys.stdout = _ProtocolSafeStdout()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
