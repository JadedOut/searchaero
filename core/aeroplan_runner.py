"""Bounded re-auth-and-resume orchestration for Aeroplan route scraping.

An Aeroplan login session lasts only ~30-40 minutes, but a wide date span can
take longer than one session to tile. `core/aeroplan_scraper.scrape_route_aeroplan`
deliberately STOPS at the first window where it detects session expiry and
surfaces `expired=True` to its caller — re-auth is the caller's job.

This module IS that caller's orchestration: it drives `scrape_route_aeroplan`
in a bounded loop, and on `expired=True` (with windows still remaining) it
re-authenticates the session (`session.ensure_logged_in()` + a warm-up hook) and
RESUMES from the next unscraped window, accumulating totals across batches.

It is PURE orchestration:
    - It owns NO browser logic (it only calls `session.ensure_logged_in()` and a
      caller-supplied `warmup` hook) and NO DB logic (it forwards `conn` to the
      scraper untouched).
    - It imports with ZERO side effects and never imports Playwright.
    - The wall-clock deadline uses an INJECTABLE monotonic clock (default
      `time.monotonic`) so tests control elapsed time without real sleeping.

Bounds (both enforced — whichever trips first stops the loop cleanly):
    - `max_reauths` — maximum number of re-authentications (default 4).
    - `deadline_seconds` — overall wall-clock budget (None = unbounded).

The returned dict is the per-batch scraper dict aggregated across batches, plus:
    - `reauths`        — how many re-auths fired.
    - `batches`        — how many scrape passes ran.
    - `span_complete`  — True iff the whole requested span was covered (no
                         expiry left the span unfinished and no cap was hit).
"""

import logging
import time

from core import aeroplan_api

log = logging.getLogger(__name__)

# Default cap on re-authentications for one span (a wide span across several
# ~30-40 min sessions). Generous enough for a multi-hour span, small enough that
# a pathological always-expiring session can't spin forever.
DEFAULT_MAX_REAUTHS = 4

# Prefix the scraper writes into error_messages when it stops on expiry, e.g.
# "session expired at window 2026-08-15: ...". Used to recover the exact window
# the scraper bailed on so we resume from precisely there.
_EXPIRED_MSG_PREFIX = "session expired at window "


def _expired_window_date(totals) -> str | None:
    """Best-effort recover the window-start date the scraper stopped on.

    The scraper appends `"session expired at window {date}: {why}"` to
    `error_messages` when it bails on expiry. Parse the LAST such message (the
    most recent batch's stop point). Returns the ISO date string or None.
    """
    for msg in reversed(totals.get("error_messages", []) or []):
        if isinstance(msg, str) and msg.startswith(_EXPIRED_MSG_PREFIX):
            rest = msg[len(_EXPIRED_MSG_PREFIX):]
            # rest looks like "2026-08-15: redirected to login host ..."
            return rest.split(":", 1)[0].strip()
    return None


def _next_from_date(
    *, expired_window, span_from_date, to_date, months, step_days, max_windows
):
    """Compute the resume `from_date`: the expired window itself (the first
    unscraped window), or None if that window is the last/absent in the span.

    The scraper stops AT the expired window without completing it, so resuming
    must re-attempt that window. We therefore resume from `expired_window`
    itself (it is the next UNSCRAPED window). If the expired window is unknown
    or is the last window of the span, returns None (nothing left to do).
    """
    if not expired_window:
        return None

    windows = aeroplan_api.window_dates(
        from_date=span_from_date, to_date=to_date, months=months,
        step_days=step_days, max_windows=max_windows,
    )
    # The expired window is the first UNSCRAPED window, so it is the resume
    # point — UNLESS it is the last window in the span (nothing left after it).
    # If it is absent (span boundaries shifted, e.g. window_dates tiles from
    # today), fall back to resuming from it directly.
    if windows and expired_window == windows[-1]:
        return None  # expired on the final window — nothing left to resume.
    return expired_window


def _accumulate(agg, batch):
    """Sum the numeric scraper totals from `batch` into `agg` (in place).

    `total_windows` is deliberately EXCLUDED here: each batch reports the window
    count for the (shrinking) span it was handed, so summing would over-count
    (and double-count the overlapping resumed window). The aggregate
    `total_windows` is computed once from the full requested span by the caller.
    """
    for key in ("found", "stored", "rejected", "errors"):
        agg[key] = agg.get(key, 0) + int(batch.get(key, 0) or 0)
    msgs = batch.get("error_messages") or []
    agg.setdefault("error_messages", []).extend(msgs)


def run_aeroplan_route_with_reauth(
    origin,
    dest,
    session,
    conn,
    *,
    scrape_fn,
    warmup=None,
    mfa_method="email",
    from_date=None,
    to_date=None,
    months=None,
    step_days=5,
    max_windows=12,
    max_reauths=DEFAULT_MAX_REAUTHS,
    deadline_seconds=None,
    monotonic=time.monotonic,
    verbose=True,
    **scrape_kwargs,
):
    """Drive `scrape_fn` in a bounded re-auth-and-resume loop over one span.

    Runs `scrape_fn(origin, dest, session, conn, ...)` (normally
    `core.aeroplan_scraper.scrape_route_aeroplan`). When a pass returns
    `expired=True` with windows still remaining in the span, re-authenticates
    (`session.ensure_logged_in()` + `warmup(session)`) and resumes from the next
    unscraped window. Totals are summed across passes.

    Args:
        origin / dest: IATA codes forwarded to the scraper.
        session: AeroplanSession (or compatible) — already started by the caller.
        conn: SQLite connection forwarded to the scraper untouched.
        scrape_fn: The scrape callable (injected for testability). Must accept
            `(origin, dest, session, conn, *, from_date, to_date, months,
            step_days, max_windows, verbose, **extra)` and return the
            scrape_route_aeroplan dict.
        warmup: Optional callable(session) run after each re-auth (the post-login
            settle / consent warm-up). Best-effort; never required.
        mfa_method: Passed to `session.ensure_logged_in` on re-auth.
        from_date / to_date / months / step_days / max_windows: Span definition,
            forwarded to the scraper and used to enumerate windows for resume.
        max_reauths: Max re-authentications before stopping (default 4).
        deadline_seconds: Overall wall-clock budget (None = unbounded). Compared
            against `monotonic()` deltas — no real sleeping is introduced here.
        monotonic: Injectable monotonic clock (default time.monotonic) so tests
            can control elapsed time.
        verbose: Forwarded to the scraper and used for progress logging.
        **scrape_kwargs: Extra kwargs forwarded verbatim to `scrape_fn`
            (e.g. delay, capture_timeout, progress_cb).

    Returns:
        Aggregated scraper dict plus `reauths`, `batches`, and
        `span_complete` (bool — True iff the span was fully covered).
        Note: `expired` reflects ONLY the LAST batch's expiry flag, not the
        span as a whole — it is the raw signal from the final pass. The
        authoritative "did we finish the whole span" answer is `span_complete`.
        Both can be True simultaneously (e.g. expiry happened on the final
        window, which still counts as completing the span).
    """
    start = monotonic()
    # Compute the true total window count ONCE from the full requested span.
    # (Per-batch total_windows shrinks as we resume, so it must NOT be summed.)
    span_total_windows = len(aeroplan_api.window_dates(
        from_date=from_date, to_date=to_date, months=months,
        step_days=step_days, max_windows=max_windows,
    ))
    agg = {
        "found": 0,
        "stored": 0,
        "rejected": 0,
        "errors": 0,
        "total_windows": span_total_windows,
        "error_messages": [],
        "expired": False,
        "reauths": 0,
        "batches": 0,
        "span_complete": False,
    }

    cur_from = from_date
    reauths = 0

    while True:
        batch = scrape_fn(
            origin, dest, session, conn,
            from_date=cur_from, to_date=to_date, months=months,
            step_days=step_days, max_windows=max_windows, verbose=verbose,
            **scrape_kwargs,
        )
        agg["batches"] += 1
        _accumulate(agg, batch)
        agg["expired"] = bool(batch.get("expired"))

        if not batch.get("expired"):
            # Pass finished without expiry → the span is fully covered.
            agg["span_complete"] = True
            break

        # Expired before the span finished. Figure out where to resume.
        expired_window = _expired_window_date(batch)
        next_from = _next_from_date(
            expired_window=expired_window,
            span_from_date=cur_from,
            to_date=to_date,
            months=months,
            step_days=step_days,
            max_windows=max_windows,
        )
        if next_from is None:
            # Expired on the final window (or no resume point) → effectively done.
            agg["span_complete"] = True
            break

        # ---- Cap checks BEFORE attempting another re-auth ----
        if reauths >= max_reauths:
            if verbose:
                log.warning(
                    "Aeroplan re-auth cap reached (%d) for %s-%s; span "
                    "NOT fully covered (resume would start at %s).",
                    max_reauths, origin, dest, next_from,
                )
            agg["span_complete"] = False
            break

        if deadline_seconds is not None and (monotonic() - start) >= deadline_seconds:
            if verbose:
                log.warning(
                    "Aeroplan re-auth deadline reached (%.0fs) for %s-%s; span "
                    "NOT fully covered (resume would start at %s).",
                    deadline_seconds, origin, dest, next_from,
                )
            agg["span_complete"] = False
            break

        # ---- Re-authenticate + warm up, then resume ----
        if verbose:
            log.info(
                "Aeroplan session expired mid-span for %s-%s; re-authenticating "
                "(re-auth %d/%d) and resuming from %s.",
                origin, dest, reauths + 1, max_reauths, next_from,
            )
        result = session.ensure_logged_in(mfa_method=mfa_method)
        reauths += 1
        agg["reauths"] = reauths

        if not getattr(result, "ok", False):
            # Re-auth failed — stop cleanly, span not covered.
            detail = getattr(result, "detail", None)
            status = getattr(result, "status", "unknown")
            msg = f"re-auth failed (status={status})"
            if detail:
                msg += f": {detail}"
            agg["error_messages"].append(msg)
            if verbose:
                log.warning("Aeroplan re-auth failed for %s-%s: %s",
                            origin, dest, msg)
            agg["span_complete"] = False
            break

        if warmup is not None:
            try:
                warmup(session)
            except Exception:
                log.debug("warmup hook raised (ignored)", exc_info=True)

        cur_from = next_from

    return agg
