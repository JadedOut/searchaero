"""Fully-offline unit tests for core/aeroplan_runner.py (Phase 3, Task 1).

NO real browser, NO network, NO real Playwright, NO real 2FA. The Aeroplan
session is a small fake exposing only `ensure_logged_in()`, and the scraper is
a stub callable whose per-call return is scripted by each test. The wall-clock
deadline is exercised with an INJECTED fake monotonic clock so nothing sleeps.

These tests pin the bounded re-auth-and-resume contract:

  1. expired-then-complete → exactly ONE re-auth (ensure_logged_in called > once)
     and the warm-up hook re-runs.
  2. The resume `from_date` of the 2nd scrape call is PAST the first batch's last
     completed window (it is the window the first batch expired on).
  3. An always-expired scraper terminates after `max_reauths` and reports the
     span as NOT fully covered (no infinite loop).
  4. A first result with expired=False → ZERO re-auths (ensure_logged_in called
     exactly once, by the live wrapper's initial login — here the runner itself
     never re-auths).
  5. found/stored/etc. totals sum across batches.
"""

import pytest

from core import aeroplan_api, aeroplan_runner


# A real future span so window_dates() produces multiple windows we can resume
# across. (window_dates tiles forward from today; a far-future from_date with a
# generous max_windows yields a stable ordered window list.)
SPAN_FROM = None  # let window_dates tile from today
SPAN_MONTHS = None
STEP_DAYS = 5
MAX_WINDOWS = 12


def _windows():
    return aeroplan_api.window_dates(
        from_date=SPAN_FROM, to_date=None, months=SPAN_MONTHS,
        step_days=STEP_DAYS, max_windows=MAX_WINDOWS,
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeLoginResult:
    def __init__(self, status="logged_in", detail=None):
        self.status = status
        self.detail = detail

    @property
    def ok(self):
        return self.status == "logged_in"


class FakeSession:
    """Records ensure_logged_in calls; returns a logged_in result by default."""

    def __init__(self, results=None):
        self.page = object()
        self.context = object()
        self.ensure_calls = []
        # Optional scripted re-auth outcomes (list of FakeLoginResult).
        self._results = list(results) if results else None

    def ensure_logged_in(self, mfa_method="email"):
        self.ensure_calls.append(mfa_method)
        if self._results:
            return self._results.pop(0)
        return FakeLoginResult("logged_in")


class StubScraper:
    """A scripted stand-in for scrape_route_aeroplan.

    `script` is a list of dicts merged onto a default batch result, consumed one
    per call. If `always` is set, every call returns `always` (merged on the
    default). Records every call's kwargs for assertions.
    """

    def __init__(self, script=None, always=None):
        self._script = list(script) if script else []
        self._always = always
        self.calls = []

    def __call__(self, origin, dest, session, conn, **kwargs):
        self.calls.append(kwargs)
        base = {
            "found": 0, "stored": 0, "rejected": 0, "errors": 0,
            "total_windows": 0, "expired": False, "error_messages": [],
        }
        if self._always is not None:
            base.update(self._always)
            return base
        if self._script:
            base.update(self._script.pop(0))
        else:
            base.update({"expired": False})
        return base


def _expired_batch(window, *, found=0, stored=0, rejected=0, errors=0,
                   total_windows=1):
    """Build an `expired=True` batch result that names `window` as the stop
    point (matching the scraper's real error_messages convention)."""
    return {
        "found": found, "stored": stored, "rejected": rejected,
        "errors": errors, "total_windows": total_windows, "expired": True,
        "error_messages": [
            f"session expired at window {window}: redirected to login host"
        ],
    }


def _ok_batch(*, found=0, stored=0, rejected=0, errors=0, total_windows=1):
    return {
        "found": found, "stored": stored, "rejected": rejected,
        "errors": errors, "total_windows": total_windows, "expired": False,
        "error_messages": [],
    }


# ---------------------------------------------------------------------------
# 1. expired-then-complete → exactly one re-auth + warm-up re-runs
# ---------------------------------------------------------------------------

def test_reauth_fires_once_on_expiry_then_complete():
    windows = _windows()
    assert len(windows) >= 2, "test setup needs >=2 windows"

    session = FakeSession()
    warmups = []
    scraper = StubScraper(script=[
        _expired_batch(windows[0], found=3, stored=3),
        _ok_batch(found=5, stored=4),
    ])

    result = aeroplan_runner.run_aeroplan_route_with_reauth(
        "YYZ", "LAX", session, conn=None,
        scrape_fn=scraper,
        warmup=lambda s: warmups.append(s),
        from_date=SPAN_FROM, months=SPAN_MONTHS,
        step_days=STEP_DAYS, max_windows=MAX_WINDOWS,
        verbose=False,
    )

    # Re-auth fired exactly once inside the runner.
    assert len(session.ensure_calls) == 1
    assert result["reauths"] == 1
    assert result["batches"] == 2
    # Warm-up re-ran after the re-auth.
    assert warmups == [session]
    # Span completed (second pass had expired=False).
    assert result["span_complete"] is True


# ---------------------------------------------------------------------------
# 2. resume from_date is PAST the first batch's last completed window
# ---------------------------------------------------------------------------

def test_resume_from_next_unscraped_window():
    windows = _windows()
    assert len(windows) >= 3, "test setup needs >=3 windows"
    expired_at = windows[1]  # first batch expires on the 2nd window

    session = FakeSession()
    scraper = StubScraper(script=[
        _expired_batch(expired_at, found=2, stored=2),
        _ok_batch(found=1, stored=1),
    ])

    aeroplan_runner.run_aeroplan_route_with_reauth(
        "YYZ", "LAX", session, conn=None,
        scrape_fn=scraper, warmup=None,
        from_date=SPAN_FROM, months=SPAN_MONTHS,
        step_days=STEP_DAYS, max_windows=MAX_WINDOWS,
        verbose=False,
    )

    assert len(scraper.calls) == 2
    first_from = scraper.calls[0]["from_date"]
    second_from = scraper.calls[1]["from_date"]

    # Second pass resumes at the expired window itself (the first UNSCRAPED
    # window — the expired window was never stored).
    assert second_from == expired_at
    # And it is strictly PAST the first batch's last completed window
    # (windows[0]); i.e. past where the first batch actually started.
    assert second_from in windows
    assert windows.index(second_from) > windows.index(windows[0])
    # Sanity: the first call started at the span start (None == default).
    assert first_from == SPAN_FROM


# ---------------------------------------------------------------------------
# 3. always-expired → stops after max_reauths, not fully covered
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("max_reauths", [1, 3])
def test_always_expired_terminates_at_max_reauths(max_reauths):
    windows = _windows()
    assert len(windows) >= 3, "test setup needs >=3 windows"
    # Always expire on a NON-final window so a resume point always exists
    # (otherwise the runner would (correctly) treat a final-window expiry as
    # done and stop early).
    expired_at = windows[0]

    session = FakeSession()
    scraper = StubScraper(always=_expired_batch(expired_at, found=1, stored=1))

    result = aeroplan_runner.run_aeroplan_route_with_reauth(
        "YYZ", "LAX", session, conn=None,
        scrape_fn=scraper, warmup=None,
        from_date=SPAN_FROM, months=SPAN_MONTHS,
        step_days=STEP_DAYS, max_windows=MAX_WINDOWS,
        max_reauths=max_reauths,
        verbose=False,
    )

    # Exactly max_reauths re-auths, then it stops (no infinite loop).
    # With max_reauths=1 the loop stops after exactly ONE re-authentication.
    assert len(session.ensure_calls) == max_reauths
    assert result["reauths"] == max_reauths
    assert result["reauths"] <= max_reauths
    # batches = initial pass + one pass after each re-auth.
    assert result["batches"] == max_reauths + 1
    # The cap stopped the span before full coverage.
    assert result["span_complete"] is False


# ---------------------------------------------------------------------------
# 4. first result expired=False → ZERO re-auths inside the runner
# ---------------------------------------------------------------------------

def test_single_session_span_triggers_zero_reauths():
    session = FakeSession()
    scraper = StubScraper(script=[_ok_batch(found=7, stored=6)])

    result = aeroplan_runner.run_aeroplan_route_with_reauth(
        "YYZ", "LAX", session, conn=None,
        scrape_fn=scraper, warmup=None,
        from_date=SPAN_FROM, months=SPAN_MONTHS,
        step_days=STEP_DAYS, max_windows=MAX_WINDOWS,
        verbose=False,
    )

    # The runner did NOT re-auth at all when the first pass fit one session.
    assert session.ensure_calls == []
    assert result["reauths"] == 0
    assert result["batches"] == 1
    assert result["span_complete"] is True


# ---------------------------------------------------------------------------
# 4b. via the live wrapper path: ensure_logged_in called EXACTLY once when the
#     first result has expired=False (the initial login; zero re-auths).
# ---------------------------------------------------------------------------

def test_live_wrapper_logs_in_exactly_once_when_not_expired(monkeypatch):
    import cli

    session = FakeSession()
    session.started = False
    session.stopped = False
    session.start = lambda: setattr(session, "started", True)
    session.stop = lambda: setattr(session, "stopped", True)

    scraper = StubScraper(script=[_ok_batch(found=4, stored=4)])

    # Patch the session class + scraper used inside the live wrapper. The
    # wrapper imports these lazily inside the function, so patch the source
    # modules.
    import core.aeroplan_session as aps
    import core.aeroplan_scraper as apscr
    monkeypatch.setattr(aps, "AeroplanSession", lambda *a, **k: session)
    monkeypatch.setattr(apscr, "scrape_route_aeroplan", scraper)
    # Make the warm-up a no-op (no real browser page).
    monkeypatch.setattr(cli, "_aeroplan_warmup", lambda s: None)

    totals = cli._scrape_route_aeroplan_live(
        "YYZ", "LAX", conn=None,
        delay=0, json_mode=True, mfa_method="email",
        months=None, from_date=None, to_date=None,
    )

    # Exactly one ensure_logged_in (the initial login); zero re-auths.
    assert len(session.ensure_calls) == 1
    assert totals["reauths"] == 0
    assert totals["span_complete"] is True
    assert session.started and session.stopped


# ---------------------------------------------------------------------------
# 5. totals aggregate across batches
# ---------------------------------------------------------------------------

def test_totals_aggregate_across_batches():
    windows = _windows()
    assert len(windows) >= 2

    session = FakeSession()
    scraper = StubScraper(script=[
        _expired_batch(windows[0], found=3, stored=2, rejected=1, errors=1,
                       total_windows=1),
        _ok_batch(found=5, stored=4, rejected=2, errors=0, total_windows=3),
    ])

    result = aeroplan_runner.run_aeroplan_route_with_reauth(
        "YYZ", "LAX", session, conn=None,
        scrape_fn=scraper, warmup=None,
        from_date=SPAN_FROM, months=SPAN_MONTHS,
        step_days=STEP_DAYS, max_windows=MAX_WINDOWS,
        verbose=False,
    )

    assert result["found"] == 8
    assert result["stored"] == 6
    assert result["rejected"] == 3
    assert result["errors"] == 1
    # total_windows is the TRUE full-span window count computed once up front,
    # NOT the sum of per-batch window counts (which would double-count the
    # overlapping resumed window). The span here is the default 12-window span.
    assert result["total_windows"] == len(windows) == 12
    # Error messages from BOTH batches accumulate.
    assert any("session expired" in m for m in result["error_messages"])


# ---------------------------------------------------------------------------
# 6. deadline cap (test-safe via injected monotonic clock — no real sleeping)
# ---------------------------------------------------------------------------

def test_deadline_cap_stops_span_without_sleeping():
    windows = _windows()
    assert len(windows) >= 3

    session = FakeSession()
    scraper = StubScraper(always=_expired_batch(windows[0], found=1, stored=1))

    # Fake monotonic: each read advances 100s. With deadline_seconds=150 the
    # budget is exceeded after the first re-auth's post-check, so the loop stops
    # well before max_reauths — proving the deadline path works with NO sleep.
    ticks = {"t": 0.0}

    def fake_monotonic():
        ticks["t"] += 100.0
        return ticks["t"]

    result = aeroplan_runner.run_aeroplan_route_with_reauth(
        "YYZ", "LAX", session, conn=None,
        scrape_fn=scraper, warmup=None,
        from_date=SPAN_FROM, months=SPAN_MONTHS,
        step_days=STEP_DAYS, max_windows=MAX_WINDOWS,
        max_reauths=99, deadline_seconds=150.0,
        monotonic=fake_monotonic,
        verbose=False,
    )

    assert result["span_complete"] is False
    # Deadline tripped before the (very high) re-auth cap.
    assert result["reauths"] < 99
