"""Route scraping logic for seataero award availability.

Provides scrape_route() for fetching United award calendar data across
date windows and storing validated results in the database.
"""

import random
import time
from datetime import date, timedelta

from core import db, models
from core import united_api


# ---------------------------------------------------------------------------
# Route scraping
# ---------------------------------------------------------------------------


def scrape_route(origin: str, destination: str, conn, scraper, delay: float = 7.0, verbose: bool = True, start_window: int = 1, max_windows: int = 12, progress_cb=None) -> dict:
    """Scrape calendar windows for a single route and store results.

    Generates departure dates spaced 30 days apart (today, today+30, ...,
    today+330) and fetches award calendar data for each window.

    Args:
        origin: 3-letter IATA origin code.
        destination: 3-letter IATA destination code.
        conn: SQLite database connection.
        scraper: HybridScraper instance (must already be started).
        delay: Seconds to wait between API calls.
        verbose: If True, print progress to stdout. Default True for
            backwards compatibility.
        start_window: 1-indexed window to start from (default: 1).
        max_windows: Maximum number of windows to scrape (default: 12).

    Returns:
        Dict with totals: found, stored, rejected, errors, total_windows.
    """
    today = date.today()
    depart_dates = [(today + timedelta(days=30 * i)).strftime("%Y-%m-%d") for i in range(12)]
    depart_dates = depart_dates[start_window - 1 : start_window - 1 + max_windows]

    total_found = 0
    total_stored = 0
    total_rejected = 0
    total_errors = 0
    error_messages = []

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

                if progress_cb:
                    progress_cb(window=start_window + i, total=12,
                                found=total_found, stored=total_stored)

                if verbose:
                    print(f"  Window {start_window + i}/12 ({depart_date}): {found} solutions, {stored} stored, {rejected} rejected")
            else:
                total_errors += 1
                error_msg = result.get("error", "Unknown error")
                error_messages.append(error_msg)

                db.record_scrape_job(
                    conn, origin, destination, depart_date,
                    "failed", error=error_msg,
                )

                if progress_cb:
                    progress_cb(window=start_window + i, total=12,
                                found=total_found, stored=total_stored)

                if verbose:
                    print(f"  Window {start_window + i}/12 ({depart_date}): FAILED — {error_msg}")

        except Exception as exc:
            total_errors += 1
            error_messages.append(str(exc))
            if verbose:
                print(f"  Window {start_window + i}/12 ({depart_date}): ERROR — {exc}")

            try:
                db.record_scrape_job(
                    conn, origin, destination, depart_date,
                    "failed", error=str(exc),
                )
            except Exception:
                pass

            if progress_cb:
                progress_cb(window=start_window + i, total=12,
                            found=total_found, stored=total_stored)

        # Circuit breaker: abort route if scraper is consistently blocked
        if scraper.consecutive_burns >= 3:
            if verbose:
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
        "total_windows": len(depart_dates),
        "circuit_break": scraper.consecutive_burns >= 3,
        "error_messages": error_messages,
    }


# ---------------------------------------------------------------------------
# Crash detection wrapper
# ---------------------------------------------------------------------------

# Keywords that indicate a browser-level crash (vs. normal API errors)
_BROWSER_CRASH_KEYWORDS = [
    "browser has been closed",
    "browser has been disconnected",
    "target closed",
    "target crashed",
    "disposed",
]


def detect_browser_crash(totals: dict) -> bool:
    """Check if scrape_route() results indicate a browser-level crash.

    Returns True when all windows errored AND any error message
    contains a browser crash keyword (e.g. 'browser has been closed').
    """
    if totals.get("errors") != totals.get("total_windows", 12):
        return False
    error_msgs = totals.get("error_messages", [])
    if not error_msgs:
        return False
    all_text = " ".join(error_msgs).lower()
    return any(kw in all_text for kw in _BROWSER_CRASH_KEYWORDS)


def _scrape_with_crash_detection(origin, destination, conn, scraper, delay=7.0, verbose=True, start_window=1, max_windows=12):
    """Run scrape_route() and detect browser crashes from structured error data.

    Returns:
        (totals_dict, browser_crashed_bool)
    """
    totals = scrape_route(origin, destination, conn, scraper, delay=delay, verbose=verbose, start_window=start_window, max_windows=max_windows)
    return totals, detect_browser_crash(totals)
