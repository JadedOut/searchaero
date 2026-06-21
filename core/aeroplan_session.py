"""Reusable, headed Aeroplan login/session manager (Phase-1).

Mirrors core/cookie_farm.py's role for United: a long-lived, persistent-profile
browser session that knows how to (re)authenticate the Air Canada / Aeroplan
Gigya login on demand. The future Phase-2 scraper calls `ensure_logged_in()` on
session expiry; this module owns LOGIN + SESSION LIFECYCLE ONLY — it contains no
scraper, search, or DB logic.

Reuse strategy — IMPORT, not copy:
    All selector ladders and the PROVEN login/2FA helpers are imported directly
    from `scripts.experiments.aeroplan_login_drive` (the Phase-1 driver). That
    module is importable with ZERO import-time side effects (its heavy logic
    lives inside functions; `main()` is guarded by `if __name__ == "__main__"`),
    so importing it never launches a browser. Importing keeps the selector
    strings + flow as a single source of truth instead of letting two copies
    drift. We deliberately do NOT copy-paste the helpers.

Account safety (inherited from the driver's discipline):
    - HEADED-ONLY. Constructing with headless=True RAISES (Akamai Bot Manager +
      Arkose FunCaptcha make headless non-viable).
    - Credentials load from ~/.searchaero/.env ONLY; secrets are never logged
      (member# / password are masked via the driver's masking helpers).
    - NEVER auto-solves Arkose: detect-and-bail, returning a typed result.
    - Email 2FA is resolved unattended via the mfa_request/mfa_response file
      protocol that scripts/mfa_responder.py answers (Gmail IMAP).

Usage (the future scraper):
    from core.aeroplan_session import AeroplanSession

    session = AeroplanSession()          # headed, persistent profile
    session.start()
    try:
        result = session.ensure_logged_in(mfa_method="email")
        if result.status != "logged_in":
            ...  # handle arkose / tfa_failed / creds_rejected
        # ... Phase-2 scraper uses session.page / session.context ...
    finally:
        session.stop()
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from core import humanize

# Reuse the PROVEN Gigya flow: selector ladders, helpers, masking, MFA-file
# protocol, and process hygiene — imported (no copy) from the Phase-1 driver.
# This module-level import has NO side effects (verified: no browser launches).
from scripts.experiments.aeroplan_login_drive import (
    AEROPLAN_MFA_SENDER,
    AIRCANADA_HOME,
    CONTINUE_SELECTORS,
    LOGIN_FORM_URL_MARKER,
    LOGIN_HOST,
    LOGIN_ID_SELECTORS,
    LOGIN_PASSWORD_SELECTORS,
    SETTLE_SECONDS,
    SIGN_IN_ENTRY_SELECTORS,
    SUBMIT_SELECTORS,
    TFA_CODE_SELECTORS,
    complete_email_2fa,
    complete_sms_2fa,
    detect_arkose,
    first_visible_locator,
    is_logged_in_dom,
    kill_orphaned_chrome,
    load_aeroplan_credentials,
    mask_secret,
    redact_member_number,
    scrape_tfa_delivery_method,
)

log = logging.getLogger(__name__)

# Default persistent profile for the Aeroplan session (distinct from the
# experiment driver's profile and from cookie_farm's united profile). The
# warmed profile persists cookies across runs so re-auth is rarely needed.
DEFAULT_PROFILE_DIR = Path.home() / ".searchaero" / ".aeroplan-profile"

# How long to wait for a logged-in / app-redirect signal after the login flow.
_LOGIN_SETTLE_DEADLINE_SECONDS = 30


def _settle() -> None:
    """Human-shaped post-navigate/click settle pause.

    Replaces the fixed ``time.sleep(SETTLE_SECONDS)`` clockwork cadence on the
    login path with a mean-preserving LOG-NORMAL sleep (same ~3 s feel, human
    shape). A flat/fixed settle is itself a behavioral-biometrics tell; this is
    sampled via ``core.humanize`` (NOT ``random.uniform``).
    """
    humanize.human_sleep(mean=3.0, lo=1.5, hi=5.5)


@dataclass
class LoginResult:
    """Typed outcome of a login / ensure_logged_in attempt.

    status is exactly one of:
        "logged_in"      — authenticated (already, or via a completed flow)
        "arkose"         — an interactive Arkose/FunCaptcha wall blocked login
                           (NEVER auto-solved by design)
        "tfa_failed"     — a 2FA code was submitted but login did not complete
        "tfa_required"   — reached the 2FA wall but it was not completed
                           (e.g. email-code poll timed out, no code dropped)
        "creds_rejected" — the login form stayed up after submit (bad creds /
                           submit did not advance)
        "no_credentials" — AEROPLAN_NUMBER / AEROPLAN_PASSWORD not set in .env
        "error"          — an unexpected error; see `detail`
    """

    status: str
    tfa_method: str = "none"
    detail: str = ""
    url: str = ""
    arkose_where: str = ""
    extras: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "logged_in"


class HeadlessNotSupported(RuntimeError):
    """Raised when headless mode is requested — Aeroplan login is headed-only."""


class AeroplanSession:
    """Headed, persistent-profile Aeroplan login/session manager.

    Lifecycle mirrors core/cookie_farm.py::CookieFarm: `start()` launches a
    persistent Chrome context (with the exact proven anti-detection kwargs),
    `ensure_logged_in()` (re)authenticates as needed, and `stop()` tears the
    browser down best-effort. LOGIN + SESSION ONLY — no scraper/DB code.
    """

    def __init__(self, profile_dir=None, out_dir=None, headless=False):
        # HEADED-ONLY: refuse headless up front (Akamai/Arkose non-viability).
        if headless:
            raise HeadlessNotSupported(
                "AeroplanSession is HEADED-ONLY. Air Canada's stack (Akamai Bot "
                "Manager + Arkose FunCaptcha) detects and blocks headless "
                "browsers via TLS/HTTP2 fingerprinting. Construct with "
                "headless=False (the default)."
            )
        self._headless = False
        self._profile_dir = Path(profile_dir) if profile_dir else DEFAULT_PROFILE_DIR
        self._out_dir = Path(out_dir) if out_dir else None

        self._playwright = None
        self._context = None
        self._page = None

        # Load creds from ~/.searchaero/.env (driver helper). Never logged raw.
        self._number, self._password = load_aeroplan_credentials()
        log.info(
            "AeroplanSession: creds AEROPLAN_NUMBER=%s AEROPLAN_PASSWORD=%s",
            redact_member_number(self._number),
            mask_secret(self._password),
        )

    # ------------------------------------------------------------------
    # Accessors (read-only handles for the future Phase-2 scraper)
    # ------------------------------------------------------------------

    @property
    def page(self):
        return self._page

    @property
    def context(self):
        return self._context

    @property
    def profile_dir(self) -> Path:
        return self._profile_dir

    def _has_credentials(self) -> bool:
        return bool(self._number and self._password)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Launch the persistent Chrome context with proven anti-detection kwargs.

        Kills any orphaned Chrome holding this profile lock first (mirrors
        cookie_farm.start() / the driver's run_live), then launches headed with
        the EXACT kwargs proven against Air Canada's Akamai/Arkose stack.
        """
        from playwright.sync_api import sync_playwright

        self._profile_dir.mkdir(parents=True, exist_ok=True)
        if self._out_dir is not None:
            self._out_dir.mkdir(parents=True, exist_ok=True)

        # Process hygiene: prevent "Opening in existing browser session".
        kill_orphaned_chrome(self._profile_dir)
        time.sleep(1)  # let the OS release file locks (cookie_farm discipline)

        self._playwright = sync_playwright().start()
        # Persistent-context launch kwargs reused VERBATIM from
        # cookie_farm.start() / aeroplan_login_drive.run_live (proven against
        # Air Canada's Akamai/Arkose stack). Headed-only by construction.
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_dir),
            headless=False,
            channel="chrome",
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else self._context.new_page()
        )
        log.info("AeroplanSession started (persistent profile: %s)", self._profile_dir)

    def stop(self):
        """Close the context + stop Playwright, best-effort (never raises)."""
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                log.debug("aeroplan context close failed", exc_info=True)
            self._context = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                log.debug("aeroplan playwright stop failed", exc_info=True)
            self._playwright = None
        self._page = None
        log.info("AeroplanSession stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    # ------------------------------------------------------------------
    # Login state
    # ------------------------------------------------------------------

    def is_logged_in(self) -> bool:
        """Return True iff the current page shows logged-in DOM markers.

        Lifts the driver's is_logged_in_dom logic verbatim: a visible Sign-in
        entry => NOT logged in; otherwise positive DOM markers. Best-effort.
        """
        if self._page is None:
            return False
        return is_logged_in_dom(self._page)

    def ensure_logged_in(self, mfa_method="email") -> LoginResult:
        """Cheap-check the session and re-auth only if needed.

        Navigates to the aircanada.com home (the driver's cheap check URL); if
        `is_logged_in()` is already True, returns a logged_in result WITHOUT a
        login attempt. Otherwise runs `login(mfa_method)`. Returns the resulting
        LoginResult. Never raises for ordinary flow outcomes (Arkose/2FA/creds);
        a browser/Playwright error surfaces as status="error".
        """
        if self._page is None:
            return LoginResult(
                status="error",
                detail="start() not called — no page; call start() first.",
            )

        # Cheap check: navigate home (cheap, cached) and read login state.
        try:
            cur = self._safe_url()
            if "aircanada.com" not in cur:
                self._page.goto(
                    AIRCANADA_HOME, wait_until="domcontentloaded", timeout=60000
                )
                _settle()  # let the SPA settle before reading DOM
        except Exception as exc:
            log.warning("ensure_logged_in: navigation failed", exc_info=True)
            return LoginResult(
                status="error",
                detail=f"navigation to aircanada home failed: {exc}",
                url=self._safe_url(),
            )

        if self.is_logged_in():
            log.info("AeroplanSession: already logged in")
            return LoginResult(status="logged_in", url=self._safe_url())

        log.info("AeroplanSession: not logged in — driving login (mfa_method=%s)",
                 mfa_method)
        result = self.login(mfa_method=mfa_method)

        # Cold-start guard: a FIRST-attempt creds_rejected is, in practice,
        # usually a transient form-render race — the Gigya login widget hadn't
        # painted its fields inside login()'s wait window, so nothing was
        # submitted and the classifier fell through to creds_rejected ("login
        # form still present after submit — credentials likely rejected OR
        # submit did not advance"). The classifier cannot tell a real rejection
        # from a non-advancing submit apart. creds_rejected means we never
        # reached the 2FA wall, so a clean re-navigate + one retry is safe (no
        # double 2FA, no extra code email). If the creds are genuinely bad, the
        # retry also returns creds_rejected and we surface it. Observed live
        # 2026-06-06: first attempt creds_rejected, immediate retry logged in.
        if result.status == "creds_rejected":
            log.warning("ensure_logged_in: first login attempt returned "
                        "creds_rejected — re-navigating and retrying once "
                        "(cold-start guard)")
            try:
                self._page.goto(
                    AIRCANADA_HOME, wait_until="domcontentloaded", timeout=60000
                )
            except Exception as exc:
                log.warning("ensure_logged_in: retry navigation failed", exc_info=True)
                return LoginResult(
                    status="error",
                    detail=f"cold-start retry navigation failed: {exc}",
                    url=self._safe_url(),
                )
            # Short log-normal backoff before re-trying the rejected cold start
            # (this ALSO serves as the post-navigate settle here — no DOM is read
            # between the goto above and the retry login() below, so a separate
            # fixed SETTLE_SECONDS settle would only stack into a double-sleep)
            # (human-shaped pause, NOT a fixed/uniform delay) — paces the retry
            # so a back-to-back re-submit doesn't read like credential-stuffing.
            humanize.human_sleep(mean=10, lo=5, hi=25)
            result = self.login(mfa_method=mfa_method)

        return result

    # ------------------------------------------------------------------
    # Login flow (Gigya A -> D, reusing the proven driver helpers)
    # ------------------------------------------------------------------

    def login(self, mfa_method="email") -> LoginResult:
        """Drive the Gigya login flow A->D and return a typed LoginResult.

        A. open login : click a Sign-in entry, wait for the Gigya form.
        B. member #   : fill the member-number field, click Continue/Next.
        C. password   : fill the password, submit via Enter (+ submit ladder),
                        wait for a post-submit transition.
        D. classify   : detect Arkose first (bail, never solve), then a 2FA code
                        field (complete via email/sms), then a logged-in signal,
                        else creds_rejected.

        NEVER auto-solves Arkose: if an interactive Arkose wall is detected at
        any decision point we return status="arkose". Returns a structured
        LoginResult; does not sys.exit. Browser errors => status="error".
        """
        if self._page is None:
            return LoginResult(
                status="error",
                detail="start() not called — no page; call start() first.",
            )
        if not self._has_credentials():
            log.error("AeroplanSession: AEROPLAN_NUMBER / AEROPLAN_PASSWORD not set")
            return LoginResult(
                status="no_credentials",
                detail="AEROPLAN_NUMBER / AEROPLAN_PASSWORD not set in "
                       "~/.searchaero/.env",
            )

        page = self._page
        runlog = self._runlog  # driver helpers take a runlog(msg) callable

        try:
            # ---------- Step A — open login ----------
            entry, entry_sel = first_visible_locator(page, SIGN_IN_ENTRY_SELECTORS)
            if entry is not None:
                try:
                    entry.click(timeout=5000)
                    runlog(f"login[A]: clicked Sign-in entry via {entry_sel!r}")
                except Exception as exc:
                    runlog(f"login[A]: Sign-in entry click raised ({exc}); continuing")
            else:
                runlog("login[A]: no Sign-in entry visible; maybe already on form")
            _settle()

            # Wait for the Gigya form: on login host OR an id/password field.
            deadline = time.time() + 20
            while time.time() < deadline:
                on_host = LOGIN_HOST in self._safe_url()
                id_vis, _ = first_visible_locator(page, LOGIN_ID_SELECTORS)
                pw_vis, _ = first_visible_locator(page, LOGIN_PASSWORD_SELECTORS)
                if on_host or id_vis is not None or pw_vis is not None:
                    break
                time.sleep(1)

            # Arkose can wall the login form itself — bail before typing creds.
            ark = detect_arkose(page)
            if ark.get("present"):
                return self._arkose_result(ark)

            # ---------- Step B — member number ----------
            id_field, id_sel = first_visible_locator(page, LOGIN_ID_SELECTORS)
            if id_field is not None:
                try:
                    # Type key-by-key with per-key log-normal timing (NOT
                    # .fill() "paste", NOT a single uniform delay) so the entry
                    # looks human to behavioral biometrics.
                    id_field.fill("", timeout=5000)
                    for ch in self._number:
                        id_field.press(ch, timeout=5000)
                        humanize.human_sleep(mean=0.11, lo=0.04, hi=0.30)
                    runlog(f"login[B]: typed member# via {id_sel!r} "
                           f"(value {redact_member_number(self._number)})")
                except Exception as exc:
                    runlog(f"login[B]: member# typing raised ({exc}); continuing")
            else:
                runlog("login[B]: no member-number field found; continuing")

            cont, cont_sel = first_visible_locator(page, CONTINUE_SELECTORS)
            if cont is not None:
                try:
                    cont.click(timeout=5000)
                    runlog(f"login[B]: clicked Continue/Next via {cont_sel!r}")
                except Exception as exc:
                    runlog(f"login[B]: continue click raised ({exc}); continuing")
            _settle()

            ark = detect_arkose(page)
            if ark.get("present"):
                return self._arkose_result(ark)

            # ---------- Step C — password ----------
            pw_field, pw_sel = first_visible_locator(page, LOGIN_PASSWORD_SELECTORS)
            if pw_field is not None:
                try:
                    # Type key-by-key with per-key log-normal timing (NOT
                    # .fill() "paste") — see member# entry above.
                    pw_field.fill("", timeout=5000)
                    for ch in self._password:
                        pw_field.press(ch, timeout=5000)
                        humanize.human_sleep(mean=0.11, lo=0.04, hi=0.30)
                    runlog(f"login[C]: typed password via {pw_sel!r} "
                           f"(value {mask_secret(self._password)})")
                except Exception as exc:
                    runlog(f"login[C]: password typing raised ({exc}); continuing")

                # Human dwell between the final field and submit — a person
                # pauses to read/verify before hitting Enter.
                humanize.human_sleep(mean=0.8, lo=0.3, hi=2.0)

                # Submit: Enter on the password field (most reliable for Gigya's
                # native form), then a visible submit-ladder click as backup.
                try:
                    page.locator('input[type="password"]:visible').first.press(
                        "Enter", timeout=5000)
                    runlog("login[C]: submitted via Enter on password field")
                except Exception as exc:
                    runlog(f"login[C]: Enter-on-password raised ({exc}); "
                           "trying submit ladder")
                submit, submit_sel = first_visible_locator(page, SUBMIT_SELECTORS)
                if submit is not None:
                    try:
                        submit.click(timeout=5000)
                        runlog(f"login[C]: clicked submit via {submit_sel!r}")
                    except Exception as exc:
                        runlog(f"login[C]: submit click raised ({exc}); continuing")

                # Wait ~25s for a post-submit state change (off form / 2FA /
                # Arkose), mirroring the driver's Step-C poll.
                self._wait_post_submit(page, runlog)
                _settle()
            else:
                runlog("login[C]: no password field appeared after step B "
                       "(selector-blocked or Arkose-gated); continuing to classify")

            # ---------- Step D — classify ----------
            _settle()
            return self._classify_and_complete(page, runlog, mfa_method)

        except Exception as exc:
            log.error("AeroplanSession.login: unexpected error", exc_info=True)
            return LoginResult(
                status="error",
                detail=f"login raised: {exc}",
                url=self._safe_url(),
            )

    # ------------------------------------------------------------------
    # Step-D classification + 2FA completion
    # ------------------------------------------------------------------

    def _classify_and_complete(self, page, runlog, mfa_method) -> LoginResult:
        """Step-D decision: Arkose (bail) > 2FA (complete) > logged-in > creds."""
        cur_url = self._safe_url()

        # Arkose takes priority over everything except an already-completed 2FA
        # path — detect first and NEVER auto-solve.
        ark = detect_arkose(page)
        if ark.get("present"):
            return self._arkose_result(ark)

        # 2FA code field present? Gigya advances IN PLACE (URL stays on the
        # login path), so a visible code field => 2FA, regardless of URL.
        tfa_field, tfa_sel = first_visible_locator(page, TFA_CODE_SELECTORS)
        if tfa_field is not None:
            tfa_method = scrape_tfa_delivery_method(page, tfa_field)
            runlog(f"login[D]: 2FA wall reached via {tfa_sel!r}; method={tfa_method}; "
                   f"completing via {mfa_method}")
            if mfa_method == "email":
                comp = complete_email_2fa(page, runlog, tfa_field, tfa_sel,
                                          self._out_dir)
            else:
                comp = complete_sms_2fa(page, runlog, tfa_field, tfa_sel,
                                        self._out_dir)

            # Re-check Arkose AFTER 2FA (it can escalate post-2FA).
            ark = detect_arkose(page)
            if ark.get("present"):
                return self._arkose_result(ark)

            comp_class = comp.get("classification")
            comp_method = comp.get("tfa_method") or tfa_method
            if comp_class == "logged_in_success":
                return LoginResult(
                    status="logged_in",
                    tfa_method=comp_method,
                    url=self._safe_url(),
                    detail="2FA completed; login succeeded",
                    extras=comp,
                )
            if comp_class == "tfa_failed":
                return LoginResult(
                    status="tfa_failed",
                    tfa_method=comp_method,
                    url=self._safe_url(),
                    detail=comp.get("error_text") or "2FA submitted but login "
                           "did not complete",
                    extras=comp,
                )
            # tfa_required (timeout / no code / email-selection failed)
            return LoginResult(
                status="tfa_required",
                tfa_method=comp_method,
                url=self._safe_url(),
                detail=comp.get("error_text") or "2FA reached but not completed",
                extras=comp,
            )

        # No 2FA field — did we leave the login form with a logged-in signal?
        still_on_login_form = LOGIN_FORM_URL_MARKER in cur_url
        logged_in_signal = self.is_logged_in()
        on_app_pages = (
            ("aircanada.com" in cur_url and not still_on_login_form)
            or "consent" in cur_url.lower()
            or "afterlogin" in cur_url.lower()
        )
        if (not still_on_login_form) and (logged_in_signal or on_app_pages):
            runlog(f"login[D]: logged in (url={cur_url}, "
                   f"signal={logged_in_signal}, app={on_app_pages})")
            return LoginResult(status="logged_in", url=cur_url)

        # Otherwise the form is still up: submit failed / credentials rejected.
        runlog(f"login[D]: login form still present (url={cur_url}); "
               "submit failed or credentials rejected")
        return LoginResult(
            status="creds_rejected",
            url=cur_url,
            detail="login form still present after submit — credentials likely "
                   "rejected or submit did not advance",
        )

    def _arkose_result(self, ark) -> LoginResult:
        """Build the typed Arkose-bail result (NEVER auto-solve)."""
        where = ark.get("where") or ""
        log.warning("AeroplanSession: Arkose detected (%s) — NOT auto-solving", where)
        return LoginResult(
            status="arkose",
            url=self._safe_url(),
            arkose_where=where,
            detail=f"interactive Arkose/FunCaptcha detected ({where}); "
                   "not auto-solved by design",
        )

    # ------------------------------------------------------------------
    # Small internal helpers
    # ------------------------------------------------------------------

    def _wait_post_submit(self, page, runlog):
        """Poll ~25s for a post-submit transition (off login form / 2FA / Arkose).

        Mirrors the driver's Step-C wait. Best-effort; never raises.
        """
        started_on_form = LOGIN_FORM_URL_MARKER in self._safe_url()
        deadline = time.time() + 25
        while time.time() < deadline:
            cur = self._safe_url()
            if started_on_form and LOGIN_FORM_URL_MARKER not in cur:
                runlog(f"login[C]: redirected off login form -> {cur}")
                return
            tf, _ = first_visible_locator(page, TFA_CODE_SELECTORS)
            if tf is not None:
                runlog("login[C]: 2FA code field appeared")
                return
            if detect_arkose(page).get("present"):
                runlog("login[C]: Arkose challenge appeared")
                return
            time.sleep(1.5)
        runlog("login[C]: no post-submit transition observed within ~25s")

    def _safe_url(self) -> str:
        """Return page.url or '' — never raises."""
        try:
            return (self._page.url if self._page else "") or ""
        except Exception:
            return ""

    @staticmethod
    def _runlog(msg: str) -> None:
        """Adapter so the driver's helpers (which expect a runlog(msg) callable)
        route their progress lines through this module's logger."""
        log.info(msg)
