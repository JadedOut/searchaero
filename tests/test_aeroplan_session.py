"""Fully-offline unit tests for core/aeroplan_session.py (Phase-1).

NO real browser, NO network, NO real Playwright. The browser is replaced by a
small `FakePage` / `FakeLocator` pair whose selector behaviour is configured
per-test, mirroring the mocking style of the repo's other unit tests
(tests/test_mfa_responder.py imports the unit under test directly; here we add a
fake-page boundary because the session manager drives a Playwright `page`).

Mocking approach
----------------
* `FakePage` / `FakeLocator`: a configurable stub page. Each test stages a dict
  of selector-substring -> behaviour (visible? count? canned attrs/content), so
  we can present "member# field visible", "password visible", "2FA code field
  visible", "Arkose present", etc. `.fill` / `.click` / `.press` calls are
  recorded on the page for assertions.
* We NEVER call `AeroplanSession.start()` (which would launch real Playwright).
  Where a test needs a live page we set the manager's internal `_page` attribute
  directly to a `FakePage` (the attribute name is `_page`, per
  core/aeroplan_session.py) and skip `start()`.
* `time.sleep` is monkeypatched to a no-op in BOTH the session module and the
  driver module so the flow runs instantly (no real waits).
* The email-2FA file protocol is redirected to `tmp_path` by monkeypatching
  `MFA_REQUEST_FILE` / `MFA_RESPONSE_FILE` on the driver module; the response
  file is pre-created so the poll returns immediately.

Decision for acceptance (b)/(c): we drive `complete_email_2fa(...)` (imported
from the driver, the exact helper `login()` calls) DIRECTLY with a fake page +
tmp MFA files. Wiring the entire `login()` state machine through a fake page to
reach the 2FA wall is brittle (it would have to satisfy ~6 selector ladders and
two ~25s polls in sequence); driving the helper validates the SAME mfa_request
payload + fill/submit contract end-to-end, robustly. (d), (e), (f) and the
no-page guard DO drive `AeroplanSession` directly.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.aeroplan_session as session_mod
import scripts.experiments.aeroplan_login_drive as driver
from core.aeroplan_session import (
    AeroplanSession,
    HeadlessNotSupported,
    LoginResult,
)


# ---------------------------------------------------------------------------
# Fake Playwright page / locator
# ---------------------------------------------------------------------------

class FakeLocator:
    """A stub Playwright locator.

    `behaviour` is a dict that may contain:
        count       -> int returned by .count() (default 1 if visible else 0)
        visible     -> bool returned by .is_visible() (default True)
        attrs       -> dict of get_attribute() returns
        inner_text  -> str returned by inner_text()
    `page` is the owning FakePage so .fill/.click/.press can record on it.
    """

    def __init__(self, page, selector, behaviour):
        self._page = page
        self._selector = selector
        self._b = behaviour or {}

    # `.first` returns the same stub (single-element semantics are fine here).
    @property
    def first(self):
        return self

    def count(self):
        if "count" in self._b:
            return self._b["count"]
        return 1 if self._b.get("visible", True) else 0

    def is_visible(self, timeout=None):
        return bool(self._b.get("visible", True))

    def fill(self, value, timeout=None):
        self._page.fills.append((self._selector, value))

    def click(self, timeout=None):
        self._page.clicks.append(self._selector)

    def press(self, key, timeout=None):
        self._page.presses.append((self._selector, key))

    def get_attribute(self, name, timeout=None):
        return (self._b.get("attrs") or {}).get(name)

    def inner_text(self, timeout=None):
        return self._b.get("inner_text", "")


class FakePage:
    """A configurable stub Playwright page.

    selector_map: dict of selector-substring -> behaviour dict (matched by
    substring against the queried selector). The FIRST substring contained in
    the queried selector wins. Unmatched selectors => an empty (count 0,
    invisible) locator.

    Records: .fills [(sel, value)], .clicks [sel], .presses [(sel, key)],
    .goto_urls [url], .screenshots [path].
    """

    def __init__(self, selector_map=None, url="https://www.aircanada.com/",
                 content="", iframe_srcs=None):
        self.selector_map = selector_map or {}
        self._url = url
        self._content = content
        self._iframe_srcs = iframe_srcs or []
        self.fills = []
        self.clicks = []
        self.presses = []
        self.goto_urls = []
        self.screenshots = []

    @property
    def url(self):
        return self._url

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        self._url = url

    def content(self):
        return self._content

    def locator(self, selector):
        for needle, behaviour in self.selector_map.items():
            if needle in selector:
                return FakeLocator(self, selector, behaviour)
        return FakeLocator(self, selector, {"count": 0, "visible": False})

    def evaluate(self, *args, **kwargs):
        # _DUMP_JS-style call; return an empty dump shape.
        return {"url": self._url, "inputs": [], "buttons": []}

    def eval_on_selector_all(self, selector, script):
        # detect_arkose's iframe-src catch-all.
        return list(self._iframe_srcs)

    def screenshot(self, path=None, **kwargs):
        self.screenshots.append(path)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """No real waits anywhere in the flow under test."""
    monkeypatch.setattr(session_mod.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(driver.time, "sleep", lambda *a, **k: None)


def _make_session(monkeypatch, number="523781508", password="pw-secret"):
    """Build an AeroplanSession with creds stubbed (no .env read, no browser)."""
    monkeypatch.setattr(
        session_mod, "load_aeroplan_credentials",
        lambda *a, **k: (number, password),
    )
    return AeroplanSession()


# ---------------------------------------------------------------------------
# (a) Headless refused
# ---------------------------------------------------------------------------

def test_headless_refused(monkeypatch):
    """AeroplanSession(headless=True) raises HeadlessNotSupported (a RuntimeError
    subclass) before any browser work."""
    monkeypatch.setattr(
        session_mod, "load_aeroplan_credentials", lambda *a, **k: ("n", "p"))
    with pytest.raises(HeadlessNotSupported):
        AeroplanSession(headless=True)
    # And it IS a RuntimeError subclass (documented contract).
    assert issubclass(HeadlessNotSupported, RuntimeError)


# ---------------------------------------------------------------------------
# (b) login() reaches email-selection + writes mfa_request with correct payload
#     (driven through complete_email_2fa — see module docstring decision).
# ---------------------------------------------------------------------------

def test_email_2fa_writes_mfa_request_payload(monkeypatch, tmp_path):
    """complete_email_2fa selects Email, writes mfa_request with mfa_method=email,
    a non-empty sender_filter, and a response_file path."""
    req_file = tmp_path / "mfa_request"
    resp_file = tmp_path / "mfa_response"
    resp_file.write_text("481923", encoding="utf-8")  # pre-drop the code

    monkeypatch.setattr(driver, "MFA_REQUEST_FILE", req_file)
    monkeypatch.setattr(driver, "MFA_RESPONSE_FILE", resp_file)

    # Models the live Gigya screen (verified 2026-06-04): the email method
    # exposes a "Send Code" button (button.gigya-tfa-verification-action-btn
    # inside .tfa-email-method); clicking it reveals the email one-time-code
    # field, and the code is submitted via input.gigya-input-submit.
    page = FakePage(selector_map={
        "gigya-tfa-verification-action-btn": {"visible": True},
        "one-time-code": {"visible": True,
                          "attrs": {"aria-label": "Enter your email code"}},
        "gigya-input-submit": {"visible": True},
    })

    tfa_loc = page.locator('input[autocomplete="one-time-code"]').first
    result = driver.complete_email_2fa(
        page, lambda m: None, tfa_loc,
        'input[autocomplete="one-time-code"]', tmp_path)

    assert req_file.exists(), "mfa_request was not written"
    payload = json.loads(req_file.read_text(encoding="utf-8"))
    assert payload["mfa_method"] == "email"
    assert payload["sender_filter"], "sender_filter must be non-empty"
    assert payload["sender_filter"] == driver.AEROPLAN_MFA_SENDER
    assert payload["response_file"], "response_file must be set"
    assert payload["response_file"] == str(resp_file)
    # The code was consumed -> success path reached.
    assert result["code_received"] is True


# ---------------------------------------------------------------------------
# (c) the code read from mfa_response is filled + submitted
# ---------------------------------------------------------------------------

def test_email_2fa_fills_and_submits_code(monkeypatch, tmp_path):
    """The code in mfa_response is filled into the code field and a submit
    (Verify click) is attempted."""
    req_file = tmp_path / "mfa_request"
    resp_file = tmp_path / "mfa_response"
    resp_file.write_text("778899", encoding="utf-8")

    monkeypatch.setattr(driver, "MFA_REQUEST_FILE", req_file)
    monkeypatch.setattr(driver, "MFA_RESPONSE_FILE", resp_file)
    # Make the success-wait return immediately: page reports logged-in DOM.
    monkeypatch.setattr(driver, "is_logged_in_dom", lambda page: True)

    # Live Gigya screen model: email "Send Code" button -> code field -> submit
    # via input.gigya-input-submit (verified 2026-06-04).
    page = FakePage(selector_map={
        "gigya-tfa-verification-action-btn": {"visible": True},
        "one-time-code": {"visible": True},
        "gigya-input-submit": {"visible": True},
    })

    tfa_loc = page.locator('input[autocomplete="one-time-code"]').first
    result = driver.complete_email_2fa(
        page, lambda m: None, tfa_loc,
        'input[autocomplete="one-time-code"]', tmp_path)

    # The exact code from mfa_response was filled into the code field.
    filled_values = [v for (_sel, v) in page.fills]
    assert "778899" in filled_values, f"code not filled; fills={page.fills}"
    # A submit was attempted (the real Gigya submit control) and recorded.
    assert any("gigya-input-submit" in sel for sel in page.clicks), \
        f"no submit click; clicks={page.clicks}"
    assert result["submitted"] is True
    assert result["classification"] == "logged_in_success"


# ---------------------------------------------------------------------------
# (d) interactive-Arkose detection bails — no auto-solve / code submission
# ---------------------------------------------------------------------------

def test_arkose_detected_bails_without_solving(monkeypatch, tmp_path):
    """When Arkose is present at the login form, login() returns status=arkose
    and never fills a member#/password or submits a 2FA code."""
    session = _make_session(monkeypatch)

    # Page with an Arkose iframe present (detect_arkose matches the selector).
    page = FakePage(selector_map={
        "arkose": {"visible": True, "count": 1},
        # If anything tried to type creds it would record here.
        "loginID": {"visible": True},
        "password": {"visible": True},
        "one-time-code": {"visible": True},
    }, url="https://login.aircanada.com/clogin/pages/login")
    session._page = page

    result = session.login(mfa_method="email")

    assert result.status == "arkose", f"expected arkose, got {result.status}"
    assert result.arkose_where  # records where Arkose was seen
    # NEVER auto-solved: no creds typed, no code submitted, no clicks toward solve.
    assert page.fills == [], f"creds/code were typed despite Arkose: {page.fills}"
    assert page.presses == [], f"a submit press happened despite Arkose: {page.presses}"


# ---------------------------------------------------------------------------
# (e) is_logged_in() true / false branches
# ---------------------------------------------------------------------------

def test_is_logged_in_false_when_sign_in_visible(monkeypatch):
    """A visible Sign-in entry => NOT logged in."""
    session = _make_session(monkeypatch)
    session._page = FakePage(
        selector_map={"Sign in": {"visible": True, "count": 1}},
        content="<html>Sign out your aeroplan</html>",  # markers present but...
    )
    # ...the visible Sign-in entry is the negative signal that wins.
    assert session.is_logged_in() is False


def test_is_logged_in_true_with_logged_in_markers(monkeypatch):
    """No visible Sign-in entry + logged-in DOM markers => logged in."""
    session = _make_session(monkeypatch)
    session._page = FakePage(
        selector_map={"Sign in": {"visible": False, "count": 0}},
        content="<html><a>Sign out</a> Your Aeroplan dashboard</html>",
    )
    assert session.is_logged_in() is True


def test_is_logged_in_false_when_no_page(monkeypatch):
    """No page (start() not called) => is_logged_in() is False, not a raise."""
    session = _make_session(monkeypatch)
    assert session._page is None
    assert session.is_logged_in() is False


# ---------------------------------------------------------------------------
# (f) missing creds aborts cleanly — no browser, no raise
# ---------------------------------------------------------------------------

def test_login_no_credentials(monkeypatch):
    """load_aeroplan_credentials -> ('','') => login() returns no_credentials
    WITHOUT launching a browser or raising."""
    session = _make_session(monkeypatch, number="", password="")
    # Provide a page so we are past the no-page guard and truly hit the creds guard.
    page = FakePage()
    session._page = page

    result = session.login(mfa_method="email")

    assert result.status == "no_credentials"
    assert not result.ok
    # No browser work at all: nothing navigated / typed / clicked.
    assert page.goto_urls == []
    assert page.fills == []
    assert page.clicks == []


# ---------------------------------------------------------------------------
# Guard: login()/ensure_logged_in() before start() => status=error (page None)
# ---------------------------------------------------------------------------

def test_login_before_start_returns_error(monkeypatch):
    session = _make_session(monkeypatch)
    assert session._page is None
    res = session.login(mfa_method="email")
    assert res.status == "error"
    assert not res.ok


def test_ensure_logged_in_before_start_returns_error(monkeypatch):
    session = _make_session(monkeypatch)
    assert session._page is None
    res = session.ensure_logged_in(mfa_method="email")
    assert isinstance(res, LoginResult)
    assert res.status == "error"


# ---------------------------------------------------------------------------
# (g) cold-start guard: a first-attempt creds_rejected is retried once
#     (regression for the 2026-06-06 cold-start form-render flake)
# ---------------------------------------------------------------------------

def _scripted_login(session, monkeypatch, statuses):
    """Replace session.login with a stub returning `statuses` in order, and
    pin is_logged_in() False so ensure_logged_in always proceeds to login().
    Returns a list that records each login() call's mfa_method."""
    calls = []
    seq = list(statuses)

    def fake_login(mfa_method="email"):
        calls.append(mfa_method)
        return LoginResult(status=seq.pop(0))

    monkeypatch.setattr(session, "login", fake_login)
    monkeypatch.setattr(session, "is_logged_in", lambda: False)
    return calls


def test_ensure_logged_in_retries_once_on_cold_start_creds_rejected(monkeypatch):
    """First login() => creds_rejected (cold-start flake), retry => logged_in.
    ensure_logged_in re-navigates home and returns the successful result."""
    session = _make_session(monkeypatch)
    page = FakePage(url="https://www.aircanada.com/")
    session._page = page
    calls = _scripted_login(session, monkeypatch, ["creds_rejected", "logged_in"])

    res = session.ensure_logged_in(mfa_method="email")

    assert res.status == "logged_in"
    assert res.ok
    assert calls == ["email", "email"]          # exactly two login attempts
    # The retry re-navigated to the aircanada home before the second attempt.
    assert any("aircanada.com" in u for u in page.goto_urls)


def test_ensure_logged_in_surfaces_creds_rejected_after_one_retry(monkeypatch):
    """Genuinely bad creds: both attempts creds_rejected. The guard retries
    EXACTLY once (no infinite loop) and surfaces the rejection."""
    session = _make_session(monkeypatch)
    session._page = FakePage(url="https://www.aircanada.com/")
    calls = _scripted_login(
        session, monkeypatch, ["creds_rejected", "creds_rejected"])

    res = session.ensure_logged_in(mfa_method="email")

    assert res.status == "creds_rejected"
    assert not res.ok
    assert calls == ["email", "email"]          # retried once, then stopped


def test_ensure_logged_in_no_retry_when_first_attempt_succeeds(monkeypatch):
    """A clean first login() => no retry, login() called exactly once."""
    session = _make_session(monkeypatch)
    session._page = FakePage(url="https://www.aircanada.com/")
    calls = _scripted_login(session, monkeypatch, ["logged_in"])

    res = session.ensure_logged_in(mfa_method="email")

    assert res.status == "logged_in"
    assert calls == ["email"]                    # no cold-start retry


def test_ensure_logged_in_no_retry_on_arkose(monkeypatch):
    """A non-creds failure (e.g. arkose) is surfaced as-is, NOT retried — the
    cold-start guard only triggers on creds_rejected."""
    session = _make_session(monkeypatch)
    session._page = FakePage(url="https://www.aircanada.com/")
    calls = _scripted_login(session, monkeypatch, ["arkose"])

    res = session.ensure_logged_in(mfa_method="email")

    assert res.status == "arkose"
    assert calls == ["email"]                    # arkose is not retried
