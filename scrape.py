"""Main CLI entry point for seataero award availability scraping.

Ties together the hybrid scraper pipeline:
    cookie_farm -> hybrid_scraper -> united_api parser -> validation -> database

Usage:
    python scrape.py --route YYZ LAX
    python scrape.py --route YYZ LAX --headless --create-schema
    python scrape.py --route YVR SFO --delay 10.0 --refresh-interval 3
"""

import argparse
import io
import os
import random
import re
import sys
import time
from datetime import datetime, date, timedelta

# ---------------------------------------------------------------------------
# Path setup — allow imports from scripts/experiments
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts", "experiments"))

from core import db, models
from cookie_farm import CookieFarm
from hybrid_scraper import HybridScraper
import united_api


# ---------------------------------------------------------------------------
# Route scraping
# ---------------------------------------------------------------------------


def scrape_route(origin: str, destination: str, conn, scraper, delay: float = 7.0) -> dict:
    """Scrape 12 calendar windows for a single route and store results.

    Generates departure dates spaced 30 days apart (today, today+30, ...,
    today+330) and fetches award calendar data for each window.

    Args:
        origin: 3-letter IATA origin code.
        destination: 3-letter IATA destination code.
        conn: psycopg database connection.
        scraper: HybridScraper instance (must already be started).
        delay: Seconds to wait between API calls.

    Returns:
        Dict with totals: found, stored, rejected, errors.
    """
    today = date.today()
    depart_dates = [(today + timedelta(days=30 * i)).strftime("%Y-%m-%d") for i in range(12)]

    total_found = 0
    total_stored = 0
    total_rejected = 0
    total_errors = 0

    for i, depart_date in enumerate(depart_dates):
        try:
            result = scraper.fetch_calendar(origin, destination, depart_date)

            if result["success"] and result["data"] is not None:
                solutions = united_api.parse_calendar_solutions(result["data"])
                found = len(solutions)
                total_found += found

                valid_results = []
                rejected = 0
                for sol in solutions:
                    award_result, reason = models.validate_solution(sol, origin, destination)
                    if award_result is not None:
                        valid_results.append(award_result)
                    else:
                        rejected += 1

                stored = db.upsert_availability(conn, valid_results)
                total_stored += stored
                total_rejected += rejected

                db.record_scrape_job(
                    conn, origin, destination, depart_date,
                    "completed", found, stored, rejected,
                )

                print(f"  Window {i+1}/12 ({depart_date}): {found} solutions, {stored} stored, {rejected} rejected")
            else:
                total_errors += 1
                error_msg = result.get("error", "Unknown error")

                db.record_scrape_job(
                    conn, origin, destination, depart_date,
                    "failed", error=error_msg,
                )

                print(f"  Window {i+1}/12 ({depart_date}): FAILED — {error_msg}")

        except Exception as exc:
            total_errors += 1
            print(f"  Window {i+1}/12 ({depart_date}): ERROR — {exc}")

            try:
                db.record_scrape_job(
                    conn, origin, destination, depart_date,
                    "failed", error=str(exc),
                )
            except Exception:
                pass

        # Circuit breaker: abort route if scraper is consistently blocked
        if scraper.consecutive_burns >= 3:
            print(f"  Circuit breaker triggered — {scraper.consecutive_burns} consecutive burns, aborting route")
            break

        # Delay between windows (skip after last)
        if i < len(depart_dates) - 1:
            jitter_range = max(0.5, delay * 0.3)
            jittered = max(delay * 0.5, delay + random.uniform(-jitter_range, jitter_range))
            time.sleep(jittered)

    return {
        "found": total_found,
        "stored": total_stored,
        "rejected": total_rejected,
        "errors": total_errors,
        "circuit_break": scraper.consecutive_burns >= 3,
    }


# ---------------------------------------------------------------------------
# Crash detection wrapper
# ---------------------------------------------------------------------------

# Pattern to capture per-window FAILED/ERROR lines printed by scrape_route().
_WINDOW_ERROR_RE = re.compile(
    r"Window\s+(\d+)/12\s+\([^)]+\):\s+(?:FAILED|ERROR)\s*[—-]\s*(.*)",
)

# Keywords that indicate a browser-level crash (vs. normal API errors)
_BROWSER_CRASH_KEYWORDS = [
    "browser has been closed",
    "browser has been disconnected",
    "target closed",
    "target crashed",
    "disposed",
]


def _scrape_with_crash_detection(origin, destination, conn, scraper, delay=7.0):
    """Run scrape_route() while capturing stdout to detect browser crashes.

    Returns:
        (totals_dict, browser_crashed_bool)
    """
    old_stdout = sys.stdout
    capture = io.StringIO()

    class Tee:
        def __init__(self, real, buffer):
            self._real = real
            self._buffer = buffer

        def write(self, data):
            self._real.write(data)
            self._buffer.write(data)

        def flush(self):
            self._real.flush()

    sys.stdout = Tee(old_stdout, capture)
    try:
        totals = scrape_route(origin, destination, conn, scraper, delay=delay)
    finally:
        sys.stdout = old_stdout

    captured = capture.getvalue()

    # Count error windows and check for browser crash keywords
    error_lines = _WINDOW_ERROR_RE.findall(captured)
    if totals["errors"] == 12 and error_lines:
        all_error_text = " ".join(msg for _, msg in error_lines).lower()
        browser_crashed = any(kw in all_error_text for kw in _BROWSER_CRASH_KEYWORDS)
    else:
        browser_crashed = False

    return totals, browser_crashed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the scrape CLI."""
    parser = argparse.ArgumentParser(
        description=(
            "Seataero award availability scraper. "
            "Fetches United award calendar data and stores validated "
            "results in PostgreSQL."
        ),
    )
    parser.add_argument(
        "--route",
        nargs=2,
        metavar=("ORIGIN", "DEST"),
        required=True,
        help="Route to scrape (e.g. --route YYZ LAX)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the cookie farm browser in headless mode",
    )
    parser.add_argument(
        "--persist-profile",
        action="store_true",
        help="Reuse persistent browser profile instead of ephemeral (default: ephemeral)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Delay in seconds between API calls (default: 3.0)",
    )
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=2,
        help="Refresh cookies every N calls (default: 2)",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="PostgreSQL connection string (overrides DATABASE_URL env var)",
    )
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="Create/update database schema before scraping",
    )
    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = build_parser()
    args = parser.parse_args()

    origin = args.route[0].upper()
    destination = args.route[1].upper()

    # Banner
    print("=" * 60)
    print("Seataero Award Scraper")
    print(f"Time:              {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Route:             {origin} -> {destination}")
    print(f"Windows:           12 (today through +330 days)")
    print(f"Delay:             {args.delay}s between calls")
    print(f"Refresh interval:  every {args.refresh_interval} calls")
    print(f"Headless:          {args.headless}")
    print(f"Profile:           {'persistent' if args.persist_profile else 'ephemeral (fresh)'}")
    print(f"Create schema:     {args.create_schema}")
    print("=" * 60)

    # Connect to PostgreSQL
    print("\nConnecting to PostgreSQL...")
    try:
        conn = db.get_connection(args.database_url)
    except Exception as exc:
        print(f"Cannot connect to PostgreSQL. Run `docker compose up -d` first.")
        print(f"  Error: {exc}")
        sys.exit(1)

    try:
        # Optionally create/update schema
        if args.create_schema:
            print("Creating/updating database schema...")
            db.create_schema(conn)
            print("Schema ready.")

        # Start cookie farm
        print("\nStarting cookie farm...")
        try:
            farm = CookieFarm(headless=args.headless, ephemeral=not args.persist_profile)
            farm.start()
        except Exception as exc:
            print(f"Failed to start cookie farm: {exc}")
            sys.exit(1)

        try:
            farm.ensure_logged_in()

            # Start hybrid scraper
            print("\nStarting hybrid scraper...")
            scraper = HybridScraper(farm, refresh_interval=args.refresh_interval)
            scraper.start()

            try:
                # Scrape the route
                print(f"\nScraping {origin} -> {destination} (12 windows)...\n")
                totals, browser_crashed = _scrape_with_crash_detection(
                    origin, destination, conn, scraper, delay=args.delay,
                )

                # If browser crashed, attempt one recovery + retry
                if browser_crashed:
                    print("\nBROWSER CRASH detected — restarting browser and retrying...")
                    scraper.stop()
                    farm.restart()
                    # restart() now calls ensure_logged_in() automatically
                    scraper.start()
                    scraper.reset_backoff()
                    print(f"\nRetrying {origin} -> {destination} (12 windows)...\n")
                    totals, _ = _scrape_with_crash_detection(
                        origin, destination, conn, scraper, delay=args.delay,
                    )

                # Final summary
                print()
                print("=" * 60)
                print("Scrape Complete")
                print(f"  Route:    {origin} -> {destination}")
                print(f"  Found:    {totals['found']} solutions")
                print(f"  Stored:   {totals['stored']} records")
                print(f"  Rejected: {totals['rejected']} (validation failures)")
                print(f"  Errors:   {totals['errors']} windows failed")
                print("=" * 60)

            finally:
                scraper.stop()

        finally:
            farm.stop()

    finally:
        conn.close()


if __name__ == "__main__":
    main()
