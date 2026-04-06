"""Continuous burn-in runner for the seataero scraper.

Loops through a route list, scrapes all 12 calendar windows per route,
logs every result as JSONL, handles session expiry/recovery, and supports
Ctrl+C graceful shutdown.

Usage:
    python scripts/burn_in.py --routes-file routes/canada_test.txt --duration 60
    python scripts/burn_in.py --routes-file routes/canada_test.txt --duration 120 --headless
    python scripts/burn_in.py --routes-file routes/canada_test.txt --duration 10 --create-schema
"""

import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# Path setup — allow imports from scripts/experiments and project root
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "experiments"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import db, models  # noqa: E402
from cookie_farm import CookieFarm  # noqa: E402
from hybrid_scraper import HybridScraper, load_routes_file  # noqa: E402
from scrape import scrape_route  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Pattern to capture per-window FAILED/ERROR lines printed by scrape_route().
# Examples:
#   "  Window 3/12 (2026-05-02): FAILED — timeout"
#   "  Window 7/12 (2026-07-31): ERROR — cookie_burn"
_WINDOW_ERROR_RE = re.compile(
    r"Window\s+(\d+)/12\s+\([^)]+\):\s+(?:FAILED|ERROR)\s*[—-]\s*(.*)",
)


def _write_status_file(worker_id, status_data, log_dir="logs"):
    """Atomically write worker status to a JSON file."""
    if not worker_id:
        return
    status_path = os.path.join(log_dir, f"worker_{worker_id}_status.json")
    tmp_path = status_path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(status_data, f, indent=2)
        os.replace(tmp_path, status_path)
    except OSError:
        pass


def _capture_scrape_route(origin, dest, conn, scraper, delay):
    """Run scrape_route() while capturing its stdout to extract error details.

    scrape_route() prints per-window results to stdout. We tee the output so
    it still appears on the console, but also parse it for FAILED/ERROR lines
    to populate the JSONL errors list.

    Returns:
        (totals_dict, error_strings, cookie_refreshed_flag)
    """
    old_stdout = sys.stdout
    capture = io.StringIO()

    class Tee:
        """Write to both the real stdout and an in-memory buffer."""
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
        totals = scrape_route(origin, dest, conn, scraper, delay=delay)
    finally:
        sys.stdout = old_stdout

    captured = capture.getvalue()

    # Extract per-window error messages
    errors = []
    for match in _WINDOW_ERROR_RE.finditer(captured):
        window_num = match.group(1)
        error_msg = match.group(2).strip()
        errors.append(f"Window {window_num}: {error_msg}")

    # Detect if a cookie refresh happened (HybridScraper prints this)
    cookie_refreshed = "cookie refresh" in captured.lower() or "Cookies refreshed" in captured

    return totals, errors, cookie_refreshed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Burn-in test runner for seataero. Continuously scrapes a list of "
            "routes, logging results as JSONL for later analysis."
        ),
    )
    parser.add_argument(
        "--routes-file",
        type=str,
        required=True,
        help="Path to routes file (one 'ORIG DEST' per line)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Maximum run duration in minutes (default: 60)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Delay in seconds between API calls (default: 3.0)",
    )
    parser.add_argument(
        "--cycle-delay",
        type=int,
        default=120,
        help="Delay in seconds between full cycles (default: 120)",
    )
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=2,
        help="Refresh cookies every N calls (default: 2)",
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
        "--log-dir",
        type=str,
        default="logs/",
        help="Directory for JSONL log files (default: logs/)",
    )
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="Create/update database schema before starting",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="PostgreSQL connection string (overrides DATABASE_URL env var)",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Path to .env file with United credentials (default: scripts/experiments/.env)",
    )
    parser.add_argument(
        "--session-budget",
        type=int,
        default=30,
        help="Proactively pause and reset session after N requests (default: 30)",
    )
    parser.add_argument(
        "--session-pause",
        type=int,
        default=60,
        help="Seconds to pause on session budget reset (default: 60)",
    )
    parser.add_argument(
        "--route-delay",
        type=int,
        default=90,
        help="Seconds to pause between routes (default: 90)",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default=None,
        help="Worker ID for parallel runs — sets a unique browser profile",
    )
    parser.add_argument(
        "--one-shot",
        action="store_true",
        help="Exit after completing one pass through all routes (no cycling)",
    )
    parser.add_argument(
        "--burn-limit",
        type=int,
        default=10,
        help="Exit if total circuit breaks reach this limit (default: 10)",
    )
    return parser


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------


def _print_summary(total_cycles, total_routes_scraped, total_windows_ok,
                   total_windows_failed, total_found, total_stored,
                   total_rejected, total_errors, run_duration, log_filename):
    """Print the end-of-run summary table."""
    total_windows = total_windows_ok + total_windows_failed
    if total_windows > 0:
        success_rate = total_windows_ok / total_windows * 100
    else:
        success_rate = 0.0

    print()
    print("=" * 60)
    print("Burn-In Complete")
    print("=" * 60)
    print(f"  Total cycles:          {total_cycles}")
    print(f"  Total routes scraped:  {total_routes_scraped}")
    print(f"  Success rate:          {total_windows_ok}/{total_windows} windows "
          f"({success_rate:.1f}%)")
    print(f"  Solutions found:       {total_found}")
    print(f"  Solutions stored:      {total_stored}")
    print(f"  Solutions rejected:    {total_rejected}")
    print(f"  Total errors:          {total_errors}")
    print(f"  Run duration:          {run_duration / 60:.1f} minutes")
    print(f"  Log file:              {log_filename}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Load routes
    routes = load_routes_file(args.routes_file)
    if not routes:
        print(f"ERROR: No routes found in {args.routes_file}")
        sys.exit(1)

    duration_seconds = args.duration * 60

    # Banner
    print("=" * 60)
    print("Seataero Burn-In Runner")
    if args.worker_id:
        print(f"Worker ID:         {args.worker_id}")
    print(f"Time:              {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Routes file:       {args.routes_file}")
    print(f"Routes:            {len(routes)}")
    for orig, dest in routes:
        print(f"                   {orig} -> {dest}")
    print(f"Duration:          {args.duration} minutes")
    print(f"Delay:             {args.delay}s between calls")
    print(f"Cycle delay:       {args.cycle_delay}s between cycles")
    print(f"Refresh interval:  every {args.refresh_interval} calls")
    print(f"Session budget:    every {args.session_budget} requests")
    print(f"Session pause:     {args.session_pause}s on budget reset")
    print(f"Route delay:       {args.route_delay}s between routes")
    print(f"Headless:          {args.headless}")
    print(f"Profile:           {'persistent' if args.persist_profile else 'ephemeral (fresh)'}")
    if args.env_file:
        print(f"Credentials:       {args.env_file}")
    print(f"Log dir:           {args.log_dir}")
    print(f"Create schema:     {args.create_schema}")
    print("=" * 60)

    # Create log directory
    os.makedirs(args.log_dir, exist_ok=True)
    worker_tag = f"_w{args.worker_id}" if args.worker_id else ""
    log_filename = os.path.join(
        args.log_dir,
        f"burn_in{worker_tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl",
    )
    print(f"\nLog file: {log_filename}")

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
            # Compute worker-specific browser profile if --worker-id is set
            profile_dir = None
            if args.worker_id:
                profile_dir = os.path.join(os.path.dirname(__file__), "experiments", f".browser-profile-{args.worker_id}")
            farm = CookieFarm(user_data_dir=profile_dir, headless=args.headless, ephemeral=not args.persist_profile, env_file=args.env_file)
            farm.start()
        except Exception as exc:
            print(f"Failed to start cookie farm: {exc}")
            sys.exit(1)

        try:
            farm.ensure_logged_in()

            # Start hybrid scraper
            print("\nStarting hybrid scraper...")
            scraper = HybridScraper(farm, refresh_interval=args.refresh_interval, session_budget=args.session_budget, session_pause=args.session_pause)
            scraper.start()

            try:
                _run_burn_in(
                    args, routes, conn, farm, scraper,
                    log_filename, duration_seconds,
                )
            finally:
                scraper.stop()

        finally:
            farm.stop()

    finally:
        conn.close()


def _run_burn_in(args, routes, conn, farm, scraper, log_filename, duration_seconds):
    """Execute the main burn-in loop. Separated for clean try/finally nesting."""

    run_start = time.time()

    # Aggregate counters
    total_cycles = 0
    total_routes_scraped = 0
    total_windows_ok = 0
    total_windows_failed = 0
    total_found = 0
    total_stored = 0
    total_rejected = 0
    total_errors = 0
    total_burns = 0

    print(f"\nBurn-in started. Will run for up to {args.duration} minutes.\n")

    try:
        with open(log_filename, "a") as log_file:
            while True:
                elapsed = time.time() - run_start
                if elapsed >= duration_seconds:
                    print("\nDuration limit reached.")
                    break

                cycle_num = total_cycles + 1
                cycle_start = time.time()
                remaining = duration_seconds - elapsed
                remaining_min = remaining / 60

                print("-" * 60)
                print(f"Cycle {cycle_num}  |  "
                      f"Elapsed: {elapsed / 60:.1f} min  |  "
                      f"Remaining: {remaining_min:.1f} min")
                print("-" * 60)

                cycle_routes_scraped = 0
                cycle_windows_ok = 0
                cycle_windows_failed = 0
                cycle_found = 0
                cycle_stored = 0
                cycle_rejected = 0
                cycle_errors = 0
                session_expired_this_cycle = False
                consecutive_circuit_breaks = 0

                for orig, dest in routes:
                    # Check duration before starting a route
                    elapsed = time.time() - run_start
                    if elapsed >= duration_seconds:
                        print("\nDuration limit reached mid-cycle.")
                        break

                    route_label = f"{orig}-{dest}"
                    print(f"\n  Scraping {route_label} (12 windows)...")

                    route_start = time.time()
                    totals, error_strings, cookie_refreshed = _capture_scrape_route(
                        orig, dest, conn, scraper, delay=args.delay,
                    )
                    route_duration = time.time() - route_start

                    windows_ok = 12 - totals["errors"]
                    windows_failed = totals["errors"]

                    # Build JSONL record
                    record = {
                        "timestamp": datetime.now().isoformat(),
                        "cycle": cycle_num,
                        "route": route_label,
                        "origin": orig,
                        "destination": dest,
                        "windows_ok": windows_ok,
                        "windows_failed": windows_failed,
                        "solutions_found": totals["found"],
                        "solutions_stored": totals["stored"],
                        "solutions_rejected": totals["rejected"],
                        "duration_seconds": round(route_duration, 1),
                        "errors": error_strings,
                        "session_expired": session_expired_this_cycle,
                        "cookie_refreshed": cookie_refreshed,
                        "circuit_break": totals.get("circuit_break", False),
                        "requests_this_session": scraper.requests_this_session,
                        "total_burns_cumulative": total_burns,
                    }

                    # Write and flush
                    log_file.write(json.dumps(record) + "\n")
                    log_file.flush()

                    _write_status_file(args.worker_id, {
                        "worker_id": args.worker_id,
                        "status": "running",
                        "routes_total": len(routes),
                        "routes_completed": total_routes_scraped + cycle_routes_scraped,
                        "current_route": route_label,
                        "windows_ok": total_windows_ok + cycle_windows_ok,
                        "windows_failed": total_windows_failed + cycle_windows_failed,
                        "solutions_found": total_found + cycle_found,
                        "solutions_stored": total_stored + cycle_stored,
                        "total_burns": total_burns,
                        "updated_at": datetime.now().isoformat(),
                    }, log_dir=args.log_dir)

                    # Update cycle counters
                    cycle_routes_scraped += 1
                    cycle_windows_ok += windows_ok
                    cycle_windows_failed += windows_failed
                    cycle_found += totals["found"]
                    cycle_stored += totals["stored"]
                    cycle_rejected += totals["rejected"]
                    cycle_errors += totals["errors"]

                    print(f"  {route_label}: "
                          f"{windows_ok}/12 OK, "
                          f"{totals['found']} found, "
                          f"{totals['stored']} stored, "
                          f"{totals['rejected']} rejected, "
                          f"{totals['errors']} errors  "
                          f"({route_duration:.1f}s)")

                    # Browser crash detection — all 12 windows failed with
                    # Playwright "browser has been closed" errors
                    browser_crashed = (
                        windows_failed == 12
                        and any("browser has been closed" in e for e in error_strings)
                    )
                    if browser_crashed:
                        print("\n  BROWSER CRASH detected — restarting browser...")
                        scraper.stop()
                        farm.restart()
                        farm.ensure_logged_in()
                        scraper.start()
                        scraper.reset_backoff()
                        print("  Browser restarted, session recovered")
                        # Brief pause before next route
                        time.sleep(10)

                    # Circuit breaker handling
                    if totals.get("circuit_break"):
                        total_burns += 1
                        if total_burns >= args.burn_limit:
                            print(f"\n  BURN LIMIT REACHED ({total_burns}/{args.burn_limit}) — shutting down worker")
                            break  # breaks the inner for loop
                        consecutive_circuit_breaks += 1
                        if consecutive_circuit_breaks >= 2:
                            print("\n  2 consecutive circuit breaks — aborting cycle, waiting for next cycle...")
                            break
                        print("\n  Circuit breaker: scraper blocked, pausing 5 minutes for full reset...")
                        time.sleep(300)
                        scraper.stop()
                        farm.refresh_cookies()
                        farm.ensure_logged_in()
                        scraper.start()
                        scraper.reset_backoff()
                        print("  Session fully reset, backoff state cleared (consecutive_burns=0, backoff=30s)")
                    else:
                        consecutive_circuit_breaks = 0

                    # Inter-route pause (skip after last route)
                    if (orig, dest) != routes[-1]:
                        elapsed = time.time() - run_start
                        if elapsed < duration_seconds:
                            print(f"  Pausing {args.route_delay}s between routes...")
                            time.sleep(args.route_delay)

                # Exit outer loop if burn limit reached
                if total_burns >= args.burn_limit:
                    # Still update aggregate counters before breaking
                    total_cycles += 1
                    total_routes_scraped += cycle_routes_scraped
                    total_windows_ok += cycle_windows_ok
                    total_windows_failed += cycle_windows_failed
                    total_found += cycle_found
                    total_stored += cycle_stored
                    total_rejected += cycle_rejected
                    total_errors += cycle_errors
                    break

                # Update aggregate counters
                total_cycles += 1
                total_routes_scraped += cycle_routes_scraped
                total_windows_ok += cycle_windows_ok
                total_windows_failed += cycle_windows_failed
                total_found += cycle_found
                total_stored += cycle_stored
                total_rejected += cycle_rejected
                total_errors += cycle_errors

                # In one-shot mode, exit after completing all routes once
                if args.one_shot:
                    print("\nOne-shot mode: all routes completed.")
                    break

                # Check duration before session check / next cycle
                elapsed = time.time() - run_start
                if elapsed >= duration_seconds:
                    print("\nDuration limit reached after cycle.")
                    break

                # Check session between cycles
                print(f"\nChecking session...")
                try:
                    session_ok = farm.check_session()
                except Exception as session_exc:
                    print(f"WARNING: Session check failed ({session_exc})")
                    print("  Attempting full browser restart...")
                    scraper.stop()
                    farm.restart()
                    farm.ensure_logged_in()
                    scraper.start()
                    scraper.reset_backoff()
                    session_ok = True  # Just recovered
                    print("  Browser restarted, session recovered")

                if not session_ok:
                    print("WARNING: Session expired! Re-authenticating...")
                    session_expired_this_cycle = True
                    farm.ensure_logged_in()
                    # Restart scraper with fresh cookies
                    scraper.stop()
                    scraper.start()
                    print("Session recovered, scraper restarted (backoff state cleared)")

                # Cycle summary
                cycle_elapsed = time.time() - cycle_start
                print(f"\nCycle {cycle_num} complete: "
                      f"{cycle_routes_scraped} routes, "
                      f"{cycle_windows_ok} OK / {cycle_windows_failed} failed, "
                      f"{cycle_found} found, "
                      f"{cycle_stored} stored, "
                      f"{cycle_errors} errors  "
                      f"({cycle_elapsed:.1f}s)")

                # Inter-cycle delay
                elapsed = time.time() - run_start
                if elapsed >= duration_seconds:
                    break

                scraper.reset_backoff()
                print(f"\nSleeping {args.cycle_delay}s before next cycle...")
                time.sleep(args.cycle_delay)

    except KeyboardInterrupt:
        print("\n\nInterrupted by Ctrl+C!")

    # End-of-run summary
    run_duration = time.time() - run_start
    _print_summary(
        total_cycles, total_routes_scraped, total_windows_ok,
        total_windows_failed, total_found, total_stored,
        total_rejected, total_errors, run_duration, log_filename,
    )

    exit_reason = "completed"
    if total_burns >= args.burn_limit:
        exit_reason = "burn_limit"
    _write_status_file(args.worker_id, {
        "worker_id": args.worker_id,
        "status": exit_reason,
        "routes_total": len(routes),
        "routes_completed": total_routes_scraped,
        "current_route": None,
        "windows_ok": total_windows_ok,
        "windows_failed": total_windows_failed,
        "solutions_found": total_found,
        "solutions_stored": total_stored,
        "total_burns": total_burns,
        "updated_at": datetime.now().isoformat(),
    }, log_dir=args.log_dir)


if __name__ == "__main__":
    main()
