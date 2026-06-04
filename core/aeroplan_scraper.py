"""Aeroplan air-calendars route scraper (Phase 2).

`scrape_route_aeroplan` is the Aeroplan analogue of `scrape.py::scrape_route`
(the United scraper). It tiles a route over short (default 5-day) calendar
windows, captures each window's signed `air-calendars` POST response using the
PROVEN capture discipline from the recon spike, validates the parsed fares, and
upserts them into the DB.

Capture discipline (lifted in spirit from
`scripts/experiments/aeroplan_calendar_recon.py`):

  - A context-level `on("response")` stash reads ONLY sync properties
    (url/status/request.method) inside the handler — it NEVER calls
    `.json()`/`.body()` there (that can deadlock the sync driver). It exists for
    expiry detection (a sticky 401/403 on air-calendars) and a debug signal.
  - Per-window navigation is wrapped in
    `page.expect_response(<air-calendars POST matcher>)` and the body is read
    EAGERLY the instant the response arrives — before the next navigation — to
    avoid Chrome evicting the body.
  - The post-login redirect chain is settled ONCE before the first window.

Session lifecycle is NOT this module's job: the caller owns
`AeroplanSession.ensure_logged_in()` / re-auth. This function only CONSUMES the
already-logged-in `session.page` / `session.context` handles. On a detected
session expiry it stops tiling and surfaces an `expired` flag for the caller.

The module imports with ZERO side effects: no browser launches at import, and
Playwright's `TimeoutError` is imported LAZILY inside the function so the module
imports even where Playwright is unavailable.
"""

import logging
import random
import time

from core import aeroplan_api, db, models
from core.aeroplan_session import LOGIN_HOST

log = logging.getLogger(__name__)

# Substring that marks the signed availability XHR (recon §3 / aeroplan_api).
TARGET_SUBSTRING = "air-calendars"

# Substrings that mark a URL as still being in the login / post-login redirect
# chain (recon §2). While page.url contains any of these, the SPA has not yet
# landed on the availability app and navigating would be interrupted.
LOGIN_REDIRECT_MARKERS = (
    "clogin", "afterLogin", "login.aircanada", "socialize", "idpresponse",
)


# ----------------------------------------------------------------------------
# Context-level response stash (sync reads only — never .json()/.body() here)
# ----------------------------------------------------------------------------

class ResponseStash:
    """Minimal context-level air-calendars stash for expiry detection.

    Inlined (NOT imported from the spike) per the module contract. The handler
    reads ONLY sync properties (url/status/request.method); it NEVER reads the
    body. Its job here is purely a side-channel:

      - `expiry_status`: sticky record of the FIRST 401/403 air-calendars
        response observed (session-expiry signal).
      - `last_status`: the most recent air-calendars status (debug signal).
    """

    def __init__(self):
        # Sticky (url, status) of the first 401/403 seen on air-calendars.
        self.expiry_status = None
        # Debug: most recent air-calendars status observed.
        self.last_status = None

    def handler(self, response):
        # SYNC PROPERTY READS ONLY — never touch the body here.
        try:
            url = response.url
            status = response.status
        except Exception:
            return
        if TARGET_SUBSTRING not in url:
            return
        self.last_status = status
        if status in (401, 403) and self.expiry_status is None:
            self.expiry_status = (url, status)

    def saw_expiry(self) -> bool:
        return self.expiry_status is not None


# ----------------------------------------------------------------------------
# Post-login settle (ported from spike::wait_post_login_settle)
# ----------------------------------------------------------------------------

def _is_login_redirect_url(url: str) -> bool:
    """True if `url` is still on a login / post-login redirect page."""
    if not url:
        return False
    return any(marker in url for marker in LOGIN_REDIRECT_MARKERS)


def _wait_post_login_settle(page) -> None:
    """Settle the post-login redirect chain BEFORE the first window.

    Polls `page.url` (up to ~20s) until it is NO LONGER on a login/redirect URL
    AND has been stable for ~2s, then settles an extra ~3s. Best-effort; never
    raises (a flaky page must not abort the scrape before it starts).
    """
    try:
        deadline = time.time() + 20.0
        stable_since = None
        last_url = None
        while time.time() < deadline:
            try:
                url = page.url or ""
            except Exception:
                url = ""
            if not _is_login_redirect_url(url):
                if url == last_url and stable_since is not None:
                    if time.time() - stable_since >= 2.0:
                        break
                else:
                    stable_since = time.time()
            else:
                stable_since = None
            last_url = url
            time.sleep(0.5)
        # Extra fixed settle once the redirect chain has quiesced.
        time.sleep(3)
    except Exception:
        log.debug("post-login settle failed", exc_info=True)


def _safe_url(page) -> str:
    """Return page.url or '' — never raises."""
    try:
        return (page.url if page else "") or ""
    except Exception:
        return ""


# ----------------------------------------------------------------------------
# Route scraper
# ----------------------------------------------------------------------------

def scrape_route_aeroplan(origin, dest, session, conn, *, delay=5.0,
                          from_date=None, to_date=None, months=None,
                          max_windows=12, step_days=5, verbose=True,
                          progress_cb=None, capture_timeout=35.0) -> dict:
    """Scrape Aeroplan calendar windows for one route and store results.

    Tiles [from_date..to_date] (or the default forward range) into `step_days`
    windows, captures each window's air-calendars POST, validates fares, and
    upserts AwardResults (program='aeroplan').

    Args:
        origin: 3-letter IATA origin code.
        dest: 3-letter IATA destination code.
        session: An AeroplanSession (or compatible) exposing `.page` + `.context`
            after the CALLER has already logged in. This function does NOT log in.
        conn: SQLite connection (schema already ensured by the caller).
        delay: Base seconds to wait between windows (human-paced + jittered).
        from_date / to_date / months: Window filters (see aeroplan_api.window_dates).
        max_windows / step_days: Window stepping (see aeroplan_api.window_dates).
        verbose: Print per-window progress to stdout.
        progress_cb: Optional callable(window=, total=, found=, stored=).
        capture_timeout: Seconds to wait for the air-calendars POST per window.

    Returns:
        Dict: found, stored, rejected, errors, total_windows, expired (bool),
        error_messages.
    """
    # Lazy Playwright import so the module imports without Playwright (spike does
    # the same). If Playwright is absent we fall back to a sentinel so the
    # except-clause below simply never matches (real timeouts still surface as
    # generic exceptions).
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
    except Exception:  # pragma: no cover - exercised only without Playwright
        class PWTimeout(Exception):
            pass

    page = session.page
    context = session.context

    # 1. Generate window start-dates.
    window_starts = aeroplan_api.window_dates(
        from_date=from_date, to_date=to_date, months=months,
        step_days=step_days, max_windows=max_windows,
    )
    total_windows = len(window_starts)

    # 2. Settle the post-login redirect chain ONCE before window 1.
    _wait_post_login_settle(page)

    # 3. Attach a context-level response stash (sync reads only) for expiry
    #    detection + a debug signal.
    stash = ResponseStash()
    if context is not None:
        try:
            context.on("response", stash.handler)
        except Exception:
            log.debug("could not attach response stash", exc_info=True)

    total_found = 0
    total_stored = 0
    total_rejected = 0
    total_errors = 0
    error_messages = []
    expired = False

    for i, win_date in enumerate(window_starts):
        try:
            url = aeroplan_api.build_availability_url(origin, dest, win_date)

            timed_out_on_capture = False
            body = None
            try:
                # EAGER CAPTURE: be listening for the signed air-calendars POST
                # BEFORE the SPA fires it, then read the body the instant it
                # arrives — before the next navigation evicts it.
                with page.expect_response(
                    lambda r: TARGET_SUBSTRING in r.url
                    and r.request.method == "POST",
                    timeout=capture_timeout * 1000,
                ) as info:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                resp = info.value
                # READ IMMEDIATELY — while the body is still resident.
                body = resp.json()
            except PWTimeout:
                # A capture timeout can be benign (slow SPA) OR a symptom of a
                # login redirect that swallowed the XHR. Decide via the URL /
                # stash guard below.
                timed_out_on_capture = True

            # ---- Session-expiry guard (after every navigate) ----
            landed = _safe_url(page)
            if LOGIN_HOST in landed or stash.saw_expiry():
                expired = True
                why = (f"redirected to login host ({LOGIN_HOST})"
                       if LOGIN_HOST in landed
                       else f"air-calendars returned {stash.expiry_status}")
                msg = f"session expired at window {win_date}: {why}"
                if verbose:
                    print(f"  Window {i + 1}/{total_windows} ({win_date}): "
                          f"EXPIRED — {why}")
                error_messages.append(msg)
                try:
                    db.record_scrape_job(
                        conn, origin, dest, win_date, "expired",
                        error=msg,
                    )
                except Exception:
                    log.debug("record expired job failed", exc_info=True)
                if progress_cb:
                    progress_cb(window=i + 1, total=total_windows,
                                found=total_found, stored=total_stored)
                break  # STOP tiling — re-auth is the caller's job.

            if timed_out_on_capture or body is None:
                # Genuine capture timeout (not a login redirect): record failed.
                total_errors += 1
                msg = (f"timeout waiting for air-calendars response "
                       f"(window {win_date})")
                error_messages.append(msg)
                try:
                    db.record_scrape_job(
                        conn, origin, dest, win_date, "failed", error=msg,
                    )
                except Exception:
                    log.debug("record failed job failed", exc_info=True)
                if verbose:
                    print(f"  Window {i + 1}/{total_windows} ({win_date}): "
                          f"FAILED — {msg}")
                if progress_cb:
                    progress_cb(window=i + 1, total=total_windows,
                                found=total_found, stored=total_stored)
            else:
                # ---- Parse + validate + store ----
                parsed = aeroplan_api.parse_calendar_response(body)
                fares = parsed.get("fares", {})

                valid_results = []
                found = 0
                rejected = 0
                for date_iso, fare in fares.items():
                    miles = fare.get("miles")
                    if miles is None:
                        continue
                    found += 1
                    award_result, _reason = models.validate_aeroplan_fare(
                        date_iso, miles, fare.get("taxes_cents"), origin, dest,
                    )
                    if award_result is not None:
                        valid_results.append(award_result)
                    else:
                        rejected += 1

                stored = db.upsert_availability(conn, valid_results)

                total_found += found
                total_stored += stored
                total_rejected += rejected

                db.record_scrape_job(
                    conn, origin, dest, win_date, "completed",
                    found, stored, rejected,
                )

                if progress_cb:
                    progress_cb(window=i + 1, total=total_windows,
                                found=total_found, stored=total_stored)
                if verbose:
                    print(f"  Window {i + 1}/{total_windows} ({win_date}): "
                          f"{found} fares, {stored} stored, {rejected} rejected")

        except Exception as exc:
            # One bad window must not kill the route.
            total_errors += 1
            error_messages.append(str(exc))
            if verbose:
                print(f"  Window {i + 1}/{total_windows} ({win_date}): "
                      f"ERROR — {exc}")
            try:
                db.record_scrape_job(
                    conn, origin, dest, win_date, "failed", error=str(exc),
                )
            except Exception:
                log.debug("record failed job (exc path) failed", exc_info=True)
            if progress_cb:
                progress_cb(window=i + 1, total=total_windows,
                            found=total_found, stored=total_stored)

        # 6. Human-paced jittered delay between windows (skip after the last).
        if i < total_windows - 1:
            jitter_range = max(0.5, delay * 0.3)
            jittered = max(delay * 0.5,
                           delay + random.uniform(-jitter_range, jitter_range))
            time.sleep(jittered)

    return {
        "found": total_found,
        "stored": total_stored,
        "rejected": total_rejected,
        "errors": total_errors,
        "total_windows": total_windows,
        "expired": expired,
        "error_messages": error_messages,
    }
