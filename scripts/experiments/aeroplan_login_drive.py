"""Aeroplan scripted-login driver — Phase-1 diagnostic (headed, account-safe).

Drives the Air Canada / Aeroplan Gigya login flow STEP-BY-STEP from a logged-out
but warmed persistent profile, dumping the REAL DOM selectors at each step and
detecting Arkose at every step, then STOPPING at the 2FA wall. It enters ZERO
2FA codes. It answers the Phase-1 question:

    Does Arkose escalate (to an INTERACTIVE FunCaptcha) for a scripted Playwright
    browser, what are the real two-step Gigya form selectors (member# field,
    Continue button, password field), and what 2FA method does the account use?

WHY a dedicated driver (vs the sigv4 probe's best-effort auto-login):
  - The sigv4 probe's auto-login is a SILENT best-effort that degrades to manual
    on any miss — it does NOT report WHICH selectors worked, WHERE Arkose
    appeared, or WHAT 2FA method was offered. This script is the instrument: it
    dumps every visible input/button at each step, screenshots each step, runs
    detect_arkose() at A/B/C/D, and emits a structured login_drive.json + a
    PHASE-1 VERDICT line. It is the recon that the production login path needs.

FLOW (A -> D):
  A. open login : find + click a Sign-in entry, dump candidates, wait for the
                  Gigya form, dump the form, detect_arkose.
  B. member #   : fill the member-number field, click Continue/Next, dump the
                  post-id form (this is where Gigya's TWO-STEP flow reveals the
                  password screen), detect_arkose. Record which selectors worked.
  C. password   : if a password field is now visible, fill it, submit, dump,
                  detect_arkose, log page.url.
  D. classify   : detect ONE of (1) logged-in success, (2) 2FA code field (+
                  delivery-method text), (3) Arkose interactive, (4) error/
                  unknown. Screenshot. DO NOT enter any 2FA code.

ACCOUNT SAFETY (baked in):
  - Headed-only. --headless is REFUSED (Akamai/Arkose make headless non-viable).
  - ONE login attempt only. Reads creds from ~/.searchaero/.env ONLY (never
    hardcoded), masks the password in every log, redacts member#/password in any
    saved file.
  - NEVER auto-solves Arkose. NEVER enters a 2FA code (stops at the wall and
    reports the method). Single account, single attempt — a normal-looking login.
  - Aborts (non-zero) if the warmed profile is STILL logged in (we need a real
    logout to observe the scripted login).

This is a SPIKE, not a scraper. Out of scope: completing 2FA, any award search,
DB writes, Arkose solving.

Usage (live run — MANUAL, human-in-the-loop; the USER runs this, not the agent):
    .venv\\Scripts\\python.exe scripts\\experiments\\aeroplan_login_drive.py

Usage (offline self-test — no browser launched):
    .venv\\Scripts\\python.exe scripts\\experiments\\aeroplan_login_drive.py --dry-run
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from core import humanize

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent

AIRCANADA_HOME = "https://www.aircanada.com/"

# Gigya login host — being ON this host (or seeing an id/password field) means
# the scripted login form is up.
LOGIN_HOST = "login.aircanada.com"

# The Air Canada Gigya login form lives at this URL path. While the URL STILL
# contains this marker the login has NOT progressed (form not submitted, or
# credentials rejected). Leaving this path == the only reliable "login
# progressed" signal we have.
LOGIN_FORM_URL_MARKER = "clogin/pages/login"

# Settle pause after a navigate / click (cookie_farm discipline: let the SPA
# settle before reading the DOM). Kept for reference/other consumers; the login
# path no longer sleeps a FIXED SETTLE_SECONDS (see _settle() below).
SETTLE_SECONDS = 3


def _settle() -> None:
    """Human-shaped post-navigate/click settle pause.

    Replaces the fixed ``time.sleep(SETTLE_SECONDS)`` clockwork cadence on the
    login path with a mean-preserving LOG-NORMAL sleep (same ~3 s feel, human
    shape) sampled via ``core.humanize`` — a flat/fixed settle is itself a
    behavioral-biometrics tell. NOT ``random.uniform`` (the jitter trap).
    """
    humanize.human_sleep(mean=3.0, lo=1.5, hi=5.5)

# .env location (reused from the sigv4 probe / cookie_farm) + credential vars.
ENV_FILE = Path.home() / ".searchaero" / ".env"
AEROPLAN_NUMBER_VAR = "AEROPLAN_NUMBER"
AEROPLAN_PASSWORD_VAR = "AEROPLAN_PASSWORD"

# ---- Resilient selector ladders (tried in order) ----

# Step A — the "Sign in" entry point on aircanada.com that opens Gigya login.
SIGN_IN_ENTRY_SELECTORS = (
    'a:has-text("Sign in")',
    'button:has-text("Sign in")',
    'a:has-text("Sign In")',
    'button:has-text("Sign In")',
    '[aria-label*="sign in" i]',
    '[aria-label*="account" i]',
    '[aria-label*="profile" i]',
    'header [class*="account" i]',
    'header [class*="profile" i]',
)

# Step B — the member-number / login-ID field (Gigya `loginID`).
LOGIN_ID_SELECTORS = (
    'input[name*="loginID" i]',
    'input[name="username"]',
    'input[autocomplete="username"]',
    'input[type="email"]:visible',
    'input[type="text"]:visible',
)

# Step B — the Continue / Next / Sign-in button advancing the two-step form.
CONTINUE_SELECTORS = (
    'button:has-text("Continue")',
    'button:has-text("Next")',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'button[type=submit]',
    'input[type=submit]',
)

# Step C — the password field (revealed on step two of the Gigya flow).
LOGIN_PASSWORD_SELECTORS = (
    'input[type="password"]:visible',
)

# Step C — the password-screen submit button. The Gigya form's real submit
# control is an `input[type=submit]` with value "Sign in" (NOT a <button>), so
# the Gigya-specific input selectors are tried FIRST.
SUBMIT_SELECTORS = (
    'input[type="submit"][value*="sign" i]',
    'input.gigya-input-submit',
    '.gigya-input-submit',
    'input[type="submit"]',
    'button[type=submit]',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'button:has-text("Continue")',
)

# Step D — the 2FA / one-time-code field. Presence => the Gigya 2FA screen is
# up (the transition is IN PLACE — the URL stays on clogin/pages/login), so
# this MUST be detected as tfa_required even while still on the login URL. The
# live DOM uses input[name="phoneCode_0"][autocomplete="one-time-code"]; the
# ladder below covers that plus generic fallbacks (phoneCode*, *code*).
TFA_CODE_SELECTORS = (
    'input[autocomplete="one-time-code"]',
    'input[name*="emailCode" i]',   # Gigya email-2FA code field (live DOM 2026-06-04)
    'input[name*="phoneCode" i]',
    'input.gigya-code-input',
    'input[name*="code" i]',
    'input[type="tel"]',
)

# Step D — request/submit buttons on the SMS 2FA screen (live DOM has
# "Send Code" + "Resend"; the verify ladder covers common submit labels).
TFA_SEND_CODE_SELECTORS = (
    'button:has-text("Send Code")',
    'button:has-text("Send code")',
)
TFA_RESEND_SELECTORS = (
    'button:has-text("Resend")',
)
TFA_VERIFY_SELECTORS = (
    'input.gigya-input-submit',   # Gigya code-submit button (live DOM 2026-06-04)
    'button:has-text("Verify")',
    'button:has-text("Submit")',
    'button:has-text("Continue")',
    'input[type=submit]:visible',
)

# File the user (or scripts/mfa_responder.py) drops the SMS code into. The
# completion stage polls this, reads + strips it, then DELETES it.
SMS_CODE_FILE = Path.home() / ".searchaero" / "aeroplan_sms_code"
SMS_POLL_INTERVAL_SECONDS = 3
SMS_POLL_TIMEOUT_SECONDS = 6 * 60  # 6 minutes
TFA_SUCCESS_WAIT_SECONDS = 30

# ---- Email 2FA (unattended) — routed through the mfa_request/mfa_response file
# protocol that scripts/mfa_responder.py answers (Gmail IMAP). The driver writes
# a request file describing the wanted method + sender filter, then polls the
# response file using the SAME cadence/timeout constants as the SMS poller. ----
MFA_REQUEST_FILE = Path.home() / ".searchaero" / "mfa_request"
MFA_RESPONSE_FILE = Path.home() / ".searchaero" / "mfa_response"
# Aeroplan verification emails come from "Aeroplan <info@communications.aeroplan.com>"
# (confirmed against a real code email, 2026-06-04). The responder matches this
# substring case-insensitively against the From header (mfa_responder.py); note the
# domain is aeroplan.com (NOT aircanada.com — that earlier guess never matched).
AEROPLAN_MFA_SENDER = "aeroplan.com"

# Step D (email 2FA only) — the "switch delivery method" ladder on the Gigya 2FA
# screen, tried IN ORDER before requesting a code when --mfa-method email. The
# Gigya DOM differs from United's OKTA flow (cookie_farm._select_mfa_method is
# REFERENCE ONLY), so the ladders below cover a/button/:text variants broadly.
#
# (1) the "try a different way" / "use a different method" link/button.
TFA_DIFFERENT_WAY_SELECTORS = (
    'a:has-text("try a different way")',
    'button:has-text("try a different way")',
    'a:has-text("different way")',
    'button:has-text("different way")',
    'a:has-text("use a different method")',
    'button:has-text("use a different method")',
    'a:has-text("different method")',
    'button:has-text("different method")',
    'a:has-text("another way")',
    'button:has-text("another way")',
    ':text("try a different way")',
    ':text("different way")',
)
# (2) the Email delivery option (radio / button / label / generic text).
TFA_EMAIL_OPTION_SELECTORS = (
    'label:has-text("Email")',
    'label:has-text("e-mail")',
    'input[value*="email" i]',
    'input[value*="e-mail" i]',
    '[data-testid*="email" i]',
    'button:has-text("Email")',
    'a:has-text("Email")',
    'button:has-text("e-mail")',
    'a:has-text("e-mail")',
    ':text("Email"):not(:has-text("Need help"))',
)
# (3) the Continue / Next / Send button that confirms the email selection.
TFA_EMAIL_CONTINUE_SELECTORS = (
    'button:has-text("Continue")',
    'button:has-text("Next")',
    'button:has-text("Send")',
    'button:has-text("Send Code")',
    'button:has-text("Submit")',
    'input[type="submit"]:visible',
)

# Step D (email 2FA) — VERIFIED against the live Gigya TFA DOM (2026-06-04).
# The "Select authentication method" screen shows Phone AND Email together (NO
# "try a different way" link). The email method is wrapped in
# div.gigya-tfa-verification-method.tfa-email-method; its request button is a
# button.gigya-tfa-verification-action-btn ("Send Code"). Clicking it reveals the
# email one-time-code field input[name^="emailCode"] (class gigya-code-input).
# The phone method auto-sends and shows input[name^="phoneCode"] — which we must
# NOT click or fill, hence the .tfa-email-method scoping below. There are hidden
# duplicate screen instances in the DOM, so first_visible_locator picks the
# rendered one.
TFA_EMAIL_SENDCODE_SELECTORS = (
    '.tfa-email-method button.gigya-tfa-verification-action-btn',
    '.gigya-tfa-verification-method.tfa-email-method button:has-text("Send Code")',
)
TFA_EMAIL_CODE_SELECTORS = (
    '.tfa-email-method input.gigya-code-input',
    'input[name^="emailCode"]',
    '.tfa-email-method input[autocomplete="one-time-code"]',
)

# Arkose / interactive FunCaptcha markers. We NEVER auto-solve these; we only
# detect + report. detect_arkose() also scans ALL iframes for arkoselabs srcs.
ARKOSE_SELECTORS = (
    'iframe[src*="arkose" i]',
    'iframe[src*="funcaptcha" i]',
    '[id*="arkose" i]',
    '[id*="funcaptcha" i]',
    '[class*="funcaptcha" i]',
)

# Robust logged-in DOM signals (the warmed profile must be LOGGED OUT to proceed).
LOGGED_IN_TEXT_MARKERS = (
    "sign out",
    "your aeroplan",
    "jiaming",
    "my account",
)


# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aeroplan_login_drive.py",
        description=(
            "Aeroplan scripted-login driver — headed, account-safe Phase-1 "
            "diagnostic. From a LOGGED-OUT warmed profile, drives the Gigya "
            "login step-by-step (A: open login, B: member#, C: password, D: "
            "classify final state), dumping the real DOM selectors and "
            "detecting Arkose at every step. STOPS at the 2FA wall (reports the "
            "method; enters NO code). Emits login_drive.json + a PHASE-1 "
            "VERDICT. ONE login attempt; never solves Arkose; never enters 2FA."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--out-dir", default=None,
                   help=("Output dir for login_drive.json + run.log + per-step "
                         "screenshots. Defaults to "
                         "logs/aeroplan_login_drive_<ts>."))
    p.add_argument("--profile-dir",
                   default=str(SCRIPT_DIR / ".aeroplan-profile"),
                   help="Persistent browser profile dir (warmed; must be "
                        "LOGGED OUT for this driver to proceed).")
    p.add_argument("--dry-run", action="store_true",
                   help=("Offline self-test: creds load from .env (password "
                         "masked), selector ladders defined + non-empty, out-dir "
                         "creation, redaction masks a member#/password. Prints "
                         "the A->D step plan + selector candidates and exits 0. "
                         "No browser is launched."))
    p.add_argument("--headless", action="store_true",
                   help=("REFUSED. Headless is non-viable against Air Canada's "
                         "Akamai/Arkose stack; passing this prints a refusal "
                         "and exits non-zero."))
    # --complete-2fa / --no-complete-2fa (DEFAULT ON). When ON and a 2FA code
    # field is detected, the driver clicks Send Code (if shown), polls
    # ~/.searchaero/aeroplan_sms_code for up to 6 min, fills + submits the code,
    # then classifies logged_in_success / tfa_failed. With --no-complete-2fa it
    # stops at the 2FA wall (classifies tfa_required, enters NO code).
    p.add_argument("--complete-2fa", dest="complete_2fa", action="store_true",
                   default=True,
                   help=("Complete the SMS 2FA: request the code (click Send "
                         "Code if shown), poll "
                         f"{SMS_CODE_FILE} every {SMS_POLL_INTERVAL_SECONDS}s "
                         f"for up to {SMS_POLL_TIMEOUT_SECONDS // 60} min, fill "
                         "+ submit it, then classify success. DEFAULT ON."))
    p.add_argument("--no-complete-2fa", dest="complete_2fa",
                   action="store_false",
                   help=("Stop at the 2FA wall (classify tfa_required), enter "
                         "NO code — the original Phase-1 behavior."))
    # --mfa-method {sms,email} (DEFAULT email). With email (unattended), the
    # driver first drives the Gigya "switch delivery method" ladder to select
    # email, then routes the code through the mfa_request/mfa_response file
    # protocol that scripts/mfa_responder.py answers (Gmail IMAP). With sms it
    # keeps the original behavior: poll ~/.searchaero/aeroplan_sms_code.
    p.add_argument("--mfa-method", dest="mfa_method",
                   choices=("sms", "email"), default="email",
                   help=("2FA delivery method to complete (when --complete-2fa). "
                         "'email' (default, unattended): select Email on the "
                         "Gigya 2FA screen, then resolve the code via the "
                         f"{MFA_REQUEST_FILE} / {MFA_RESPONSE_FILE} protocol "
                         "(answered by scripts/mfa_responder.py). 'sms': poll "
                         f"{SMS_CODE_FILE} for a manually-dropped code."))
    return p


def resolve_out_dir(arg_out_dir) -> Path:
    if arg_out_dir:
        return Path(arg_out_dir).resolve()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (REPO_ROOT / "logs" / f"aeroplan_login_drive_{ts}").resolve()


# ----------------------------------------------------------------------------
# Run log (observability mirror — prints AND appends to <out-dir>/run.log)
# ----------------------------------------------------------------------------

class RunLog:
    """Print a message AND append a timestamped line to <out-dir>/run.log, so
    background monitoring can tail progress. Best-effort: file-write failures
    never interrupt the run.
    """

    def __init__(self, path: Path = None):
        self.path = path
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                log.debug("could not create run.log parent", exc_info=True)

    def __call__(self, msg: str) -> None:
        print(msg)
        if self.path is None:
            return
        try:
            ts = datetime.now().isoformat(timespec="seconds")
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(f"{ts} {msg}\n")
        except Exception:
            log.debug("run.log write failed", exc_info=True)


# ----------------------------------------------------------------------------
# Credentials + redaction
# ----------------------------------------------------------------------------

def load_aeroplan_credentials(env_file: Path = None):
    """Load (number, password) from ~/.searchaero/.env via python-dotenv.

    Returns a (number, password) tuple of stripped strings (either may be "").
    Never raises; never logs the password.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file or ENV_FILE)
    except Exception:
        log.debug("dotenv load failed", exc_info=True)
    number = os.getenv(AEROPLAN_NUMBER_VAR, "").strip()
    password = os.getenv(AEROPLAN_PASSWORD_VAR, "").strip()
    return number, password


def mask_secret(value: str) -> str:
    """Mask a secret for safe logging. Empty => '<empty>'. Otherwise show only a
    length hint and never any actual characters."""
    if not value:
        return "<empty>"
    return f"***({len(value)} chars)***"


def redact_member_number(value: str) -> str:
    """Mask an Aeroplan member number for safe saving — keep nothing identifying.
    Shows only a length hint. Empty => '<empty>'."""
    if not value:
        return "<empty>"
    return f"***({len(value)} digits)***"


def mask_code(value: str) -> str:
    """Mask an entered 2FA / one-time code for safe logging. Never shows any
    actual digit. Empty => '<empty>'."""
    if not value:
        return "<empty>"
    return f"***({len(value)} digits)***"


# ----------------------------------------------------------------------------
# Browser-process hygiene (reused from cookie_farm._kill_orphaned_chrome)
# ----------------------------------------------------------------------------

def kill_orphaned_chrome(user_data_dir: Path) -> None:
    """Kill Chrome processes holding this profile lock (Windows only).

    Mirrors core/cookie_farm.py::_kill_orphaned_chrome — prevents the
    "Opening in existing browser session" error on relaunch.
    """
    if sys.platform != "win32":
        return
    profile_str = str(user_data_dir).replace("/", "\\")
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='chrome.exe'",
             "get", "ProcessId,CommandLine"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if profile_str in line:
                parts = line.strip().split()
                if parts:
                    pid = parts[-1]
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid, "/T"],
                            capture_output=True, timeout=10,
                        )
                        print(f"  Killed orphaned Chrome process PID {pid}")
                    except Exception:
                        log.debug("taskkill failed for PID %s", pid, exc_info=True)
    except Exception as exc:
        print(f"  Warning: could not scan for orphaned Chrome: {exc}")


# ----------------------------------------------------------------------------
# detect_arkose — runs at EVERY step
# ----------------------------------------------------------------------------

def detect_arkose(page) -> dict:
    """Return {present: bool, where: <first match selector or note> | None}.

    Checks each ARKOSE_SELECTORS entry, then (as a catch-all) scans every iframe
    src for the substring 'arkoselabs'. Best-effort; never raises.
    """
    for sel in ARKOSE_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return {"present": True, "where": sel}
        except Exception:
            log.debug("arkose selector probe failed: %s", sel, exc_info=True)

    # Catch-all: any iframe whose src contains 'arkoselabs'.
    try:
        srcs = page.eval_on_selector_all(
            "iframe",
            "els => els.map(e => e.getAttribute('src') || '')",
        )
        for src in (srcs or []):
            if isinstance(src, str) and "arkoselabs" in src.lower():
                return {"present": True, "where": f"iframe[src*=arkoselabs]: {src[:80]}"}
    except Exception:
        log.debug("arkose iframe src scan failed", exc_info=True)

    return {"present": False, "where": None}


# ----------------------------------------------------------------------------
# dump_form — log every visible input + button, screenshot the step
# ----------------------------------------------------------------------------

# JS that returns every VISIBLE input + button/a[role=button] with diagnostic
# attributes. Visibility = offsetParent not null AND non-zero box.
_DUMP_JS = r"""
() => {
    const isVisible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') return false;
        return el.offsetParent !== null || style.position === 'fixed';
    };
    const inputs = [];
    document.querySelectorAll('input').forEach((el) => {
        if (!isVisible(el)) return;
        inputs.push({
            id: el.id || null,
            name: el.getAttribute('name'),
            type: el.getAttribute('type'),
            placeholder: el.getAttribute('placeholder'),
            autocomplete: el.getAttribute('autocomplete'),
            ariaLabel: el.getAttribute('aria-label'),
        });
    });
    const buttons = [];
    document.querySelectorAll('button, a[role="button"], input[type="submit"]').forEach((el) => {
        if (!isVisible(el)) return;
        buttons.push({
            tag: el.tagName.toLowerCase(),
            text: (el.innerText || el.value || '').trim().slice(0, 60),
            type: el.getAttribute('type'),
            id: el.id || null,
        });
    });
    return {url: window.location.href, inputs, buttons};
}
"""


def dump_form(page, label: str, out_dir: Path, runlog) -> dict:
    """Log every VISIBLE input + button on the page and screenshot to
    <out-dir>/<label>.png. Returns the dump dict {url, inputs, buttons}.

    Never raises — a failure logs the error and returns an empty dump.
    """
    dump = {"url": None, "inputs": [], "buttons": []}
    try:
        dump = page.evaluate(_DUMP_JS)
    except Exception as exc:
        runlog(f"    dump_form[{label}]: evaluate failed ({exc})")

    try:
        runlog(f"  --- DUMP [{label}] url={dump.get('url')}")
        inputs = dump.get("inputs") or []
        buttons = dump.get("buttons") or []
        if not inputs:
            runlog(f"      (no visible inputs)")
        for inp in inputs:
            runlog(
                f"      input id={inp.get('id')!r} name={inp.get('name')!r} "
                f"type={inp.get('type')!r} placeholder={inp.get('placeholder')!r} "
                f"autocomplete={inp.get('autocomplete')!r} "
                f"aria-label={inp.get('ariaLabel')!r}"
            )
        if not buttons:
            runlog(f"      (no visible buttons)")
        for btn in buttons:
            runlog(
                f"      button[{btn.get('tag')}] text={btn.get('text')!r} "
                f"type={btn.get('type')!r} id={btn.get('id')!r}"
            )
    except Exception:
        log.debug("dump_form logging failed", exc_info=True)

    try:
        shot = out_dir / f"{label}.png"
        page.screenshot(path=str(shot))
        runlog(f"      screenshot -> {shot}")
    except Exception as exc:
        runlog(f"    dump_form[{label}]: screenshot failed ({exc})")

    return dump


# ----------------------------------------------------------------------------
# Selector helpers
# ----------------------------------------------------------------------------

def first_visible_locator(page, selectors):
    """Return (locator, selector_string) for the first visible match (in order),
    else (None, None). Best-effort; never raises."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=1500):
                return loc, sel
        except Exception:
            log.debug("selector probe failed: %s", sel, exc_info=True)
            continue
    return None, None


def dump_candidates(page, label: str, selectors, runlog) -> list:
    """Log how many matches each candidate selector has + which are visible.
    Returns the list of selectors with at least one match (diagnostic only)."""
    found = []
    runlog(f"  --- CANDIDATES [{label}]")
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
        except Exception:
            count = -1
        visible = False
        if count and count > 0:
            try:
                visible = loc.first.is_visible(timeout=500)
            except Exception:
                visible = False
        runlog(f"      {sel!r}: count={count} first_visible={visible}")
        if count and count > 0:
            found.append(sel)
    return found


def is_logged_in_dom(page) -> bool:
    """Robust logged-in signal: a visible Sign-in entry => NOT logged in;
    otherwise positive DOM text markers. Best-effort; failures => not logged in.
    """
    try:
        try:
            sign_in = page.locator(
                'a:has-text("Sign in"):visible, button:has-text("Sign in"):visible, '
                'a:has-text("Sign In"):visible, button:has-text("Sign In"):visible'
            )
            if sign_in.count() > 0:
                return False
        except Exception:
            log.debug("sign-in negative-signal check failed", exc_info=True)
        try:
            content = page.content().lower()
        except Exception:
            content = ""
        return any(marker in content for marker in LOGGED_IN_TEXT_MARKERS)
    except Exception:
        log.debug("is_logged_in_dom failed", exc_info=True)
        return False


def _safe_url(page) -> str:
    """Return page.url or '' — never raises."""
    try:
        return page.url or ""
    except Exception:
        return ""


# Gigya / generic visible error + validation markers. We surface the text so
# the classifier can report WHY the login form is still present (e.g. rejected
# credentials).
ERROR_SELECTORS = (
    '.gigya-error-msg-active',
    '.gigya-composite-control-form-error',
    '.gigya-error-msg:visible',
    '[class*="error" i]:visible',
    '[role="alert"]:visible',
)


def _visible_error_text(page) -> str:
    """Best-effort: return the text of the first VISIBLE error/validation
    message (Gigya or generic), trimmed to 200 chars. '' if none. Never raises.
    """
    for sel in ERROR_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                txt = (loc.inner_text(timeout=500) or "").strip()
                txt = " ".join(txt.split())
                if txt:
                    return txt[:200]
        except Exception:
            log.debug("error-text probe failed: %s", sel, exc_info=True)
    return ""


def classify_final_state(*, still_on_login_form, login_form_present,
                         logged_in_signal, on_app_pages, tfa_present,
                         arkose_present) -> str:
    """Pure decision function for Step-D classification (no page access, so it
    is offline-testable). Mirrors the live Step-D ordering EXACTLY.

    Priority: tfa_required > arkose_interactive > logged_in_success >
    login_form_still_present > unknown.

    CRITICAL invariant #1: 2FA detection takes priority over EVERYTHING. A
    visible one-time-code field means Gigya advanced (IN PLACE) to the SMS 2FA
    screen — and that transition keeps the URL on clogin/pages/login. So when
    tfa_present is True we classify tfa_required EVEN WHILE still_on_login_form
    is True; we must NOT fall through to login_form_still_present.

    CRITICAL invariant #2: logged_in_success requires having LEFT the login
    form (still_on_login_form is False) AND a positive signal; being still on
    the form can NEVER be success.
    """
    if tfa_present:
        return "tfa_required"
    if arkose_present:
        return "arkose_interactive"
    if (not still_on_login_form) and (logged_in_signal or on_app_pages):
        return "logged_in_success"
    if still_on_login_form and login_form_present:
        return "login_form_still_present"
    return "unknown"


def scrape_tfa_delivery_method(page, tfa_locator=None) -> str:
    """Scrape the 2FA delivery method, PREFERRING the code field's own
    aria-label / placeholder (the live DOM aria-label is e.g. "Enter code for
    phone ending with 978" -> sms). Falls back to a page-wide text scan.

    Returns a short method string ("sms", "email", "sms/phone", "sms ending
    978", ...) or 'unknown' if nothing is found. Never raises.
    """
    # 1) Prefer the code field's aria-label / placeholder (most specific).
    label = ""
    try:
        loc = tfa_locator
        if loc is None:
            loc, _ = first_visible_locator(page, TFA_CODE_SELECTORS)
        if loc is not None:
            for attr in ("aria-label", "placeholder"):
                try:
                    val = (loc.get_attribute(attr, timeout=500) or "").strip()
                except Exception:
                    val = ""
                if val:
                    label = val
                    break
    except Exception:
        log.debug("tfa aria-label scrape failed", exc_info=True)

    low = label.lower()
    if "phone" in low or "ending" in low or "mobile" in low or "sms" in low \
            or "text" in low:
        # Pull a trailing "ending with NNN" hint if present.
        digits = "".join(ch for ch in low.split("ending")[-1] if ch.isdigit()) \
            if "ending" in low else ""
        return f"sms ending {digits}" if digits else "sms"
    if "email" in low or "e-mail" in low:
        return "email"

    # 2) Fall back to find_2fa_method_text's page-wide scan.
    return find_2fa_method_text(page) or "unknown"


def find_2fa_method_text(page) -> str:
    """Best-effort: scrape the on-page text for the 2FA delivery method
    (sms / phone / email). Returns a short method string or '' if none found."""
    try:
        content = page.content().lower()
    except Exception:
        return ""
    methods = []
    if "text message" in content or "sms" in content or "phone" in content \
            or "mobile" in content:
        methods.append("sms/phone")
    if "email" in content or "e-mail" in content:
        methods.append("email")
    return ", ".join(methods)


# ----------------------------------------------------------------------------
# Step plan / selector printout (dry-run + live banner)
# ----------------------------------------------------------------------------

STEP_PLAN = (
    ("A", "open login",
     "Find + click a Sign-in entry; dump candidates; wait for the Gigya form "
     "(URL on login.aircanada.com OR a password/id field visible); "
     "dump_form('stepA_loginform'); detect_arkose."),
    ("B", "member number",
     "Fill the member-number field; click Continue/Next; dump_form("
     "'stepB_afterid'); detect_arkose. Record which id + continue selectors "
     "worked (reveals Gigya's TWO-STEP password screen)."),
    ("C", "password",
     "If a visible password field now appears, fill it + SUBMIT reliably "
     "(Enter on the password field, then a visible Gigya submit ladder), "
     "then poll ~25s for a state change (left login form / 2FA / Arkose / "
     "error); dump_form('stepC_aftersubmit'); detect_arkose; log page.url."),
    ("D", "classify final state (+ optional 2FA completion: email or sms)",
     "Detect a visible one-time-code field FIRST (Gigya advances IN PLACE — "
     "URL stays on clogin/pages/login — so a code field => tfa_required, NOT "
     "login_form_still_present). Scrape the delivery method from the code "
     "field's aria-label (e.g. 'phone ending with 978' => sms). With "
     "--complete-2fa (default ON), dispatch by --mfa-method: EMAIL (default, "
     "unattended) first drives the 'try a different way' -> Email -> Continue "
     "ladder (select_email_2fa; if email can't be reached, report SMS + STOP — "
     "never send SMS unattended), then writes ~/.searchaero/mfa_request "
     "(mfa_method=email + Aeroplan sender_filter + response_file) and polls "
     "~/.searchaero/mfa_response (every 3s, up to 6 min). SMS clicks Send Code "
     "if shown + polls ~/.searchaero/aeroplan_sms_code. Either way fill + "
     "submit the code, wait ~30s -> logged_in_success / tfa_failed (re-check "
     "Arkose post-2FA). With --no-complete-2fa: stop at the wall (tfa_required, "
     "NO code). Otherwise classify logged_in_success (LEFT the form + signal) / "
     "arkose_interactive / login_form_still_present / unknown. Screenshot "
     "stepD_final."),
)


def print_step_plan():
    print("-" * 72)
    print("STEP PLAN (A -> D, one login attempt; stops at the 2FA wall):")
    print("-" * 72)
    for code, title, desc in STEP_PLAN:
        print(f"  Step {code} — {title}")
        print(f"      {desc}")
    print("-" * 72)


def print_selector_candidates():
    print("SELECTOR CANDIDATES (tried in order at each step):")
    print("-" * 72)
    ladders = (
        ("Step A — sign-in entry", SIGN_IN_ENTRY_SELECTORS),
        ("Step B — member-number field", LOGIN_ID_SELECTORS),
        ("Step B — continue/next button", CONTINUE_SELECTORS),
        ("Step C — password field", LOGIN_PASSWORD_SELECTORS),
        ("Step C — submit button", SUBMIT_SELECTORS),
        ("Step D — 2FA code field", TFA_CODE_SELECTORS),
        ("Step D — email: 'try a different way' link",
         TFA_DIFFERENT_WAY_SELECTORS),
        ("Step D — email: Email delivery option", TFA_EMAIL_OPTION_SELECTORS),
        ("Step D — email: Continue/Next/Send button",
         TFA_EMAIL_CONTINUE_SELECTORS),
        ("Step D — 2FA Send Code button", TFA_SEND_CODE_SELECTORS),
        ("Step D — 2FA Resend button", TFA_RESEND_SELECTORS),
        ("Step D — 2FA verify/submit button", TFA_VERIFY_SELECTORS),
        ("All steps — Arkose markers", ARKOSE_SELECTORS),
    )
    for label, sels in ladders:
        print(f"  {label}:")
        for sel in sels:
            print(f"      {sel}")
    print("-" * 72)


# ----------------------------------------------------------------------------
# Dry-run self-test (offline; no browser)
# ----------------------------------------------------------------------------

def run_dry_run(args) -> int:
    """Exercise everything that does NOT require a browser, then exit 0."""
    print("DRY RUN — no browser will be launched. Validating wiring only.\n")

    # ---- creds load from .env (password masked in output) ----
    number, password = load_aeroplan_credentials()
    print(f"  [ok] creds load from {ENV_FILE}")
    print(f"       AEROPLAN_NUMBER : {redact_member_number(number)}")
    print(f"       AEROPLAN_PASSWORD: {mask_secret(password)}")
    # The masked output must never contain the real password.
    if password:
        assert password not in mask_secret(password), "password leaked in mask!"
    if number:
        assert number not in redact_member_number(number), "member# leaked in mask!"

    # ---- selector ladders defined + non-empty ----
    ladders = {
        "SIGN_IN_ENTRY_SELECTORS": SIGN_IN_ENTRY_SELECTORS,
        "LOGIN_ID_SELECTORS": LOGIN_ID_SELECTORS,
        "CONTINUE_SELECTORS": CONTINUE_SELECTORS,
        "LOGIN_PASSWORD_SELECTORS": LOGIN_PASSWORD_SELECTORS,
        "SUBMIT_SELECTORS": SUBMIT_SELECTORS,
        "TFA_CODE_SELECTORS": TFA_CODE_SELECTORS,
        "TFA_DIFFERENT_WAY_SELECTORS": TFA_DIFFERENT_WAY_SELECTORS,
        "TFA_EMAIL_OPTION_SELECTORS": TFA_EMAIL_OPTION_SELECTORS,
        "TFA_EMAIL_CONTINUE_SELECTORS": TFA_EMAIL_CONTINUE_SELECTORS,
        "ARKOSE_SELECTORS": ARKOSE_SELECTORS,
    }
    for name, sels in ladders.items():
        assert isinstance(sels, tuple) and len(sels) > 0, f"{name} empty/undefined"
        assert all(isinstance(s, str) and s for s in sels), f"{name} has bad entry"
    print(f"  [ok] all {len(ladders)} selector ladders defined + non-empty: "
          f"{', '.join(f'{n}={len(s)}' for n, s in ladders.items())}")

    # ---- out-dir creation ----
    out_dir = resolve_out_dir(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    assert out_dir.exists(), "out dir not created"
    print(f"  [ok] out dir created/exists: {out_dir}")

    profile_dir = Path(args.profile_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [ok] profile dir created/exists: {profile_dir}")

    # ---- redaction masks a password + member number ----
    fake_pw = "Sup3rSecretPassw0rd!"
    fake_num = "523781508"
    masked_pw = mask_secret(fake_pw)
    masked_num = redact_member_number(fake_num)
    assert fake_pw not in masked_pw, "password not masked"
    assert fake_num not in masked_num, "member number not masked"
    assert masked_pw == "***(20 chars)***", f"unexpected pw mask: {masked_pw}"
    assert masked_num == "***(9 digits)***", f"unexpected num mask: {masked_num}"
    assert mask_secret("") == "<empty>"
    assert redact_member_number("") == "<empty>"
    print(f"  [ok] redaction: password -> {masked_pw}; member# -> {masked_num}; "
          "empty -> <empty>")

    # ---- classifier self-check (offline; pure decision function) ----
    # The headline regression: "still on clogin/pages/login with the form
    # present" must NEVER be logged_in_success.
    assert classify_final_state(
        still_on_login_form=True, login_form_present=True,
        logged_in_signal=False, on_app_pages=False,
        tfa_present=False, arkose_present=False,
    ) == "login_form_still_present", "still-on-form misclassified"
    # Even if a stale logged-in marker is somehow detected, staying on the
    # login form can NOT be success.
    still_on_form_result = classify_final_state(
        still_on_login_form=True, login_form_present=True,
        logged_in_signal=True, on_app_pages=True,
        tfa_present=False, arkose_present=False,
    )
    assert still_on_form_result != "logged_in_success", (
        "still-on-form must NEVER be logged_in_success "
        f"(got {still_on_form_result!r})")
    # Real success: left the form AND a logged-in/app signal.
    assert classify_final_state(
        still_on_login_form=False, login_form_present=False,
        logged_in_signal=True, on_app_pages=False,
        tfa_present=False, arkose_present=False,
    ) == "logged_in_success", "genuine success misclassified"
    # 2FA + Arkose take priority over everything.
    assert classify_final_state(
        still_on_login_form=False, login_form_present=False,
        logged_in_signal=True, on_app_pages=True,
        tfa_present=True, arkose_present=False,
    ) == "tfa_required", "2FA priority broken"
    # Guards the IN-PLACE Gigya 2FA transition: when Gigya advances to the 2FA
    # screen the URL STAYS on clogin/pages/login and the login form is still
    # considered present, yet a one-time-code field is visible. That MUST
    # classify tfa_required, NOT login_form_still_present (the old bug).
    assert classify_final_state(
        still_on_login_form=True, login_form_present=True,
        logged_in_signal=False, on_app_pages=False,
        tfa_present=True, arkose_present=False,
    ) == "tfa_required", (
        "in-place Gigya 2FA transition misclassified as "
        "login_form_still_present")
    assert classify_final_state(
        still_on_login_form=True, login_form_present=True,
        logged_in_signal=False, on_app_pages=False,
        tfa_present=False, arkose_present=True,
    ) == "arkose_interactive", "Arkose priority broken"
    print("  [ok] classifier self-check: still-on-form -> "
          "login_form_still_present (NEVER success); in-place Gigya 2FA "
          "transition (still on login form + code field) -> tfa_required "
          "(NOT login_form_still_present); genuine success / 2FA / Arkose "
          "ordering verified")

    # ---- step plan + selector candidates printout ----
    print()
    print_step_plan()
    print()
    print_selector_candidates()

    print("\nDRY RUN OK — wiring validated, no browser launched.")
    return 0


# ----------------------------------------------------------------------------
# Live run — drive the scripted login A -> D
# ----------------------------------------------------------------------------

def _save_drive(out_dir, runlog, steps, classification, tfa_method, verdict,
                worked, reached_2fa=False):
    """Write the structured login_drive.json (redaction-safe — contains no
    secrets; only selectors, URLs, arkose flags, and notes — NOT the 2FA code)."""
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
        "worked_selectors": worked,
        "classification": classification,
        "tfa_method": tfa_method,
        "reached_2fa": reached_2fa,
        "verdict": verdict,
    }
    try:
        (out_dir / "login_drive.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        runlog(f"saved drive results: {out_dir / 'login_drive.json'}")
    except Exception as exc:
        runlog(f"ERROR saving login_drive.json: {exc}")


def _teardown(context, playwright):
    try:
        context.close()
    except Exception:
        log.debug("context close failed", exc_info=True)
    try:
        playwright.stop()
    except Exception:
        log.debug("playwright stop failed", exc_info=True)


def _poll_sms_code_file(runlog, code_file: Path = None,
                        interval: int = SMS_POLL_INTERVAL_SECONDS,
                        timeout: int = SMS_POLL_TIMEOUT_SECONDS):
    """Poll the SMS-code drop file every `interval` seconds for up to `timeout`
    seconds. On appearance: read, .strip(), DELETE the file, return the code.
    Returns '' on timeout. Never raises (a read/delete error logs + retries).
    """
    code_file = code_file or SMS_CODE_FILE
    deadline = time.time() + timeout
    runlog(f"    polling {code_file} every {interval}s for up to "
           f"{timeout // 60} min...")
    while time.time() < deadline:
        try:
            if code_file.exists():
                raw = code_file.read_text(encoding="utf-8")
                code = raw.strip()
                try:
                    code_file.unlink()
                except Exception:
                    log.debug("sms code file unlink failed", exc_info=True)
                if code:
                    runlog(f"    SMS code received ({mask_code(code)}); "
                           "deleted code file.")
                    return code
                runlog("    SMS code file was empty; continuing to poll.")
        except Exception:
            log.debug("sms code file read failed", exc_info=True)
        time.sleep(interval)
    return ""


def _poll_mfa_response_file(runlog, response_file: Path = None,
                            interval: int = SMS_POLL_INTERVAL_SECONDS,
                            timeout: int = SMS_POLL_TIMEOUT_SECONDS):
    """Poll the mfa_response drop file (written by scripts/mfa_responder.py)
    every `interval` seconds for up to `timeout` seconds. On appearance: read,
    .strip(), DELETE the file, return the code. Returns '' on timeout. Never
    raises (a read/delete error logs + retries).

    Mirrors _poll_sms_code_file but reads the email-2FA response path, reusing
    the SAME cadence/timeout constants (every 3s, up to 6 min).
    """
    response_file = response_file or MFA_RESPONSE_FILE
    deadline = time.time() + timeout
    runlog(f"    polling {response_file} every {interval}s for up to "
           f"{timeout // 60} min...")
    while time.time() < deadline:
        try:
            if response_file.exists():
                raw = response_file.read_text(encoding="utf-8")
                code = raw.strip()
                try:
                    response_file.unlink()
                except Exception:
                    log.debug("mfa response file unlink failed", exc_info=True)
                if code:
                    runlog(f"    email 2FA code received ({mask_code(code)}); "
                           "deleted response file.")
                    return code
                runlog("    mfa_response file was empty; continuing to poll.")
        except Exception:
            log.debug("mfa response file read failed", exc_info=True)
        time.sleep(interval)
    return ""


def _write_mfa_request(runlog, sender_filter: str = AEROPLAN_MFA_SENDER,
                       request_file: Path = None, response_file: Path = None):
    """Write ~/.searchaero/mfa_request as the JSON the responder expects:
        {"mfa_method":"email","sender_filter":<sender>,
         "response_file":<path>,"ts":<iso>}
    Best-effort: a write failure logs + returns False (caller decides). Never
    leaks any code (this file requests a code; it never contains one).
    """
    request_file = request_file or MFA_REQUEST_FILE
    response_file = response_file or MFA_RESPONSE_FILE
    payload = {
        "mfa_method": "email",
        "sender_filter": sender_filter,
        "response_file": str(response_file),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        request_file.parent.mkdir(parents=True, exist_ok=True)
        request_file.write_text(json.dumps(payload), encoding="utf-8")
        runlog(f"    wrote mfa_request -> {request_file} "
               f"(mfa_method=email sender_filter={sender_filter!r} "
               f"response_file={response_file}).")
        return True
    except Exception as exc:
        runlog(f"    ERROR writing mfa_request ({exc}); email 2FA cannot be "
               "resolved by the responder.")
        return False


def select_email_2fa(page, runlog) -> bool:
    """Request an EMAIL 2FA code on the Gigya "Select authentication method" screen.

    VERIFIED against the live DOM (2026-06-04): the screen shows Phone AND Email
    together — the phone method auto-sends an SMS and shows its own code field,
    and there is NO "try a different way" link to click. To use email we click
    the Email method's "Send Code" button, scoped to .tfa-email-method so we can
    never click the phone button, then confirm the email one-time-code field
    (input[name^="emailCode"]) appeared.

    Returns True once the email-code state is reached, False otherwise. On
    failure the caller LOGS clearly and does NOT silently fall back to sending an
    SMS unattended (mirrors the detect-and-report discipline).
    """
    # (1) Click the Email method's "Send Code" button (scoped to the email-method
    # wrapper; first_visible_locator skips the hidden duplicate screen instances).
    send, send_sel = first_visible_locator(page, TFA_EMAIL_SENDCODE_SELECTORS)
    if send is None:
        runlog("    EMAIL 'Send Code' button NOT found in .tfa-email-method on the "
               "Gigya 2FA screen — cannot request an email code. NOT falling back "
               "to SMS unattended.")
        return False
    try:
        send.click(timeout=5000)
        runlog(f"    clicked Email 'Send Code' via {send_sel!r}")
    except Exception as exc:
        runlog(f"    Email 'Send Code' click raised ({exc}); cannot confirm the "
               "email request. NOT falling back to SMS unattended.")
        return False
    _settle()

    # (2) Confirm the EMAIL one-time-code field appeared (scoped to
    # .tfa-email-method so the auto-sent phone code field is never mistaken for it).
    code_loc, code_sel = first_visible_locator(page, TFA_EMAIL_CODE_SELECTORS)
    if code_loc is None:
        runlog("    after requesting the Email code, NO email one-time-code field "
               "is visible — email-code state not reached. NOT sending SMS "
               "unattended.")
        return False
    runlog(f"    email code requested; email one-time-code field present via {code_sel!r}.")
    return True


def _fill_submit_and_wait_2fa(page, runlog, tfa_locator, tfa_sel, code,
                              result):
    """Shared 2FA completion tail: fill the code field, submit (Verify ladder
    then Enter), wait ~30s for success, and set result['classification'] /
    ['error_text'] / ['submitted']. Used by BOTH the SMS and email paths so the
    fill/submit/wait logic is not duplicated.
    """
    # Type the code field key-by-key with per-key log-normal timing (NOT
    # .fill() "paste", NOT a single uniform delay) so 2FA entry looks human.
    # Shared by both the email and SMS paths.
    try:
        tfa_locator.fill("", timeout=5000)
        for ch in code:
            tfa_locator.press(ch, timeout=5000)
            humanize.human_sleep(mean=0.11, lo=0.04, hi=0.30)
        runlog(f"    typed 2FA code via {tfa_sel!r} (value {mask_code(code)})")
    except Exception as exc:
        runlog(f"    2FA code typing raised ({exc}); attempting submit anyway.")

    submit_method = None
    verify, verify_sel = first_visible_locator(page, TFA_VERIFY_SELECTORS)
    if verify is not None:
        try:
            verify.click(timeout=5000)
            submit_method = verify_sel
            runlog(f"    submitted 2FA code via {verify_sel!r}")
        except Exception as exc:
            runlog(f"    2FA submit click raised ({exc}); trying Enter.")
    if submit_method is None:
        try:
            tfa_locator.press("Enter", timeout=5000)
            submit_method = "code:press(Enter)"
            runlog("    submitted 2FA code via Enter on the code field")
        except Exception as exc:
            runlog(f"    Enter-on-code raised ({exc}); submit may have failed.")
    result["submitted"] = submit_method is not None

    # Wait ~30s for success.
    runlog(f"    waiting up to ~{TFA_SUCCESS_WAIT_SECONDS}s for 2FA success "
           "(left login form / app/consent/afterLogin redirect / logged-in "
           "DOM)...")
    deadline = time.time() + TFA_SUCCESS_WAIT_SECONDS
    success = False
    while time.time() < deadline:
        cur = _safe_url(page) or ""
        low = cur.lower()
        left_form = LOGIN_FORM_URL_MARKER not in cur
        on_app = (("aircanada.com" in cur and left_form)
                  or "consent" in low or "afterlogin" in low)
        dom_ok = False
        try:
            dom_ok = is_logged_in_dom(page)
        except Exception:
            dom_ok = False
        if (left_form and (on_app or dom_ok)) or on_app:
            success = True
            runlog(f"    2FA SUCCESS — url={cur} (left_form={left_form} "
                   f"on_app={on_app} dom_ok={dom_ok})")
            break
        time.sleep(1.5)

    if success:
        result["classification"] = "logged_in_success"
    else:
        result["classification"] = "tfa_failed"
        err = _visible_error_text(page)
        result["error_text"] = err
        runlog(f"    2FA did NOT complete within ~{TFA_SUCCESS_WAIT_SECONDS}s; "
               f"classifying tfa_failed. {('error: ' + err) if err else 'no visible error.'}")
    return result


def complete_email_2fa(page, runlog, tfa_locator, tfa_sel, out_dir):
    """Drive EMAIL 2FA to completion (unattended). Returns the same dict shape
    as complete_sms_2fa:
        {classification, tfa_method, error_text, submitted, code_received}

    Steps: (1) select Email delivery on the Gigya 2FA screen (select_email_2fa);
    if that fails, REPORT the SMS method and STOP (do NOT send SMS unattended);
    (2) write ~/.searchaero/mfa_request (mfa_method=email + Aeroplan
    sender_filter + response_file); (3) poll ~/.searchaero/mfa_response (every
    3s up to 6 min — same cadence as the SMS poller), read+strip+DELETE it;
    (4) fill + submit the code and wait ~30s for success (shared tail). Always
    re-checks Arkose post-2FA via the caller.
    """
    result = {
        "classification": "tfa_required",
        "tfa_method": scrape_tfa_delivery_method(page, tfa_locator),
        "error_text": "",
        "submitted": False,
        "code_received": False,
    }

    # (1) Select Email delivery BEFORE requesting the code.
    if not select_email_2fa(page, runlog):
        sms_method = result["tfa_method"]
        runlog("    EMAIL 2FA SELECTION FAILED — could not reach the email-code "
               f"state. Reporting the SMS method ({sms_method}) and STOPPING; "
               "NOT sending an SMS unattended (--mfa-method email).")
        result["classification"] = "tfa_required"
        result["error_text"] = "email 2FA selection failed (email option not reached)"
        return result

    # Re-resolve the code field after selection (the DOM may have changed) and
    # refresh the method label. Prefer the email-scoped field so the auto-sent
    # phone code field is never picked for the email code.
    new_loc, new_sel = first_visible_locator(
        page, TFA_EMAIL_CODE_SELECTORS + TFA_CODE_SELECTORS)
    if new_loc is not None:
        tfa_locator, tfa_sel = new_loc, new_sel
    result["tfa_method"] = scrape_tfa_delivery_method(page, tfa_locator)

    # (2) Write the mfa_request the responder answers.
    if not _write_mfa_request(runlog):
        result["classification"] = "tfa_required"
        result["error_text"] = "could not write mfa_request"
        return result

    # (3) NEED-EMAIL-CODE marker + poll the response file.
    runlog("NEED-EMAIL-CODE: requested email 2FA via the mfa_request protocol. "
           f"scripts/mfa_responder.py will write the code to {MFA_RESPONSE_FILE}. "
           f"Method: {result['tfa_method']}.")
    code = _poll_mfa_response_file(runlog)
    if not code:
        runlog(f"    email-code poll TIMED OUT after "
               f"{SMS_POLL_TIMEOUT_SECONDS // 60} min — no mfa_response appeared. "
               "Classifying tfa_required (timeout) and exiting cleanly.")
        result["classification"] = "tfa_required"
        result["error_text"] = "email-code poll timed out (no mfa_response file)"
        return result
    result["code_received"] = True

    # (4) Fill + submit + wait (shared tail).
    return _fill_submit_and_wait_2fa(page, runlog, tfa_locator, tfa_sel, code,
                                     result)


def complete_sms_2fa(page, runlog, tfa_locator, tfa_sel, out_dir):
    """Drive the SMS 2FA to completion. Returns a dict:
        {classification, tfa_method, error_text, submitted, code_received}

    Steps: (1) click Send Code if visible (else skip on Resend/already-sent);
    (2) log NEED-SMS-CODE + poll the code file; (3) fill + submit the code;
    (4) wait ~30s for success (left clogin/pages/login / on app/consent/
    afterLogin / logged-in DOM) -> logged_in_success, else tfa_failed (capture
    error text). Always re-checks Arkose post-2FA via the caller.
    """
    result = {
        "classification": "tfa_required",
        "tfa_method": scrape_tfa_delivery_method(page, tfa_locator),
        "error_text": "",
        "submitted": False,
        "code_received": False,
    }

    # (1) Request the SMS — click "Send Code" if visible; skip if only Resend.
    send, send_sel = first_visible_locator(page, TFA_SEND_CODE_SELECTORS)
    if send is not None:
        try:
            send.click(timeout=5000)
            runlog(f"    requested SMS code (clicked Send Code) via {send_sel!r}")
        except Exception as exc:
            runlog(f"    Send Code click raised ({exc}); continuing.")
    else:
        resend, resend_sel = first_visible_locator(page, TFA_RESEND_SELECTORS)
        if resend is not None:
            runlog("    no Send Code button (only Resend/already-sent); "
                   "skipping the request step.")
        else:
            runlog("    no Send Code / Resend button visible; assuming the code "
                   "was already sent. Continuing.")

    # (2) NEED-SMS-CODE marker (run.log AND stdout via runlog) + poll the file.
    runlog("NEED-SMS-CODE: waiting for the SMS one-time code. Drop it into "
           f"{SMS_CODE_FILE} (raw code, e.g. 123456). Method: "
           f"{result['tfa_method']}.")
    code = _poll_sms_code_file(runlog)
    if not code:
        runlog(f"    SMS-code poll TIMED OUT after "
               f"{SMS_POLL_TIMEOUT_SECONDS // 60} min — no code file appeared. "
               "Classifying tfa_required (timeout) and exiting cleanly.")
        result["classification"] = "tfa_required"
        result["error_text"] = "SMS-code poll timed out (no code file)"
        return result
    result["code_received"] = True

    # (3+4) Fill + submit + wait (shared tail, also used by complete_email_2fa).
    return _fill_submit_and_wait_2fa(page, runlog, tfa_locator, tfa_sel, code,
                                     result)


def run_live(args) -> int:
    from playwright.sync_api import sync_playwright

    profile_dir = Path(args.profile_dir).resolve()
    out_dir = resolve_out_dir(args.out_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    runlog = RunLog(out_dir / "run.log")

    print("=" * 72)
    print("AEROPLAN LOGIN DRIVE — Phase-1 scripted-login diagnostic")
    print("=" * 72)
    print(f"  Profile dir : {profile_dir}")
    print(f"  Out dir     : {out_dir}")
    print(f"  Drive JSON  : {out_dir / 'login_drive.json'}")
    print(f"  Run log     : {out_dir / 'run.log'}")
    print("=" * 72)
    print_step_plan()
    print()

    # Load creds (mask password in any log).
    number, password = load_aeroplan_credentials()
    runlog(f"creds: AEROPLAN_NUMBER={redact_member_number(number)} "
           f"AEROPLAN_PASSWORD={mask_secret(password)}")
    if not (number and password):
        runlog(f"ABORT: AEROPLAN_NUMBER / AEROPLAN_PASSWORD not set in {ENV_FILE}.")
        return 1

    runlog("\nLIVE RUN — headed browser. ONE login attempt; stops at the 2FA "
           "wall; never solves Arkose; enters NO 2FA code.")

    kill_orphaned_chrome(profile_dir)
    time.sleep(1)  # let the OS release file locks
    runlog(f"launch: headed chrome, profile={profile_dir}")

    playwright = sync_playwright().start()
    # Persistent-context launch kwargs reused VERBATIM from cookie_farm.start()
    # / the sigv4 probe (proven against Air Canada's Akamai/Arkose stack).
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        channel="chrome",
        viewport={"width": 1280, "height": 800},
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    page = context.pages[0] if context.pages else context.new_page()

    steps = []            # per-step records
    worked = {}           # which selectors worked, by role
    classification = "unknown"
    tfa_method = "none"
    reached_2fa = False   # set True once a 2FA code field is detected (Step D)
    verdict = "INCONCLUSIVE — see steps."

    def _record_step(code, page_obj, arkose, worked_sel=None, notes=None):
        try:
            url = page_obj.url
        except Exception:
            url = None
        rec = {
            "step": code,
            "url": url,
            "arkose": arkose,
            "worked_selectors": worked_sel or {},
            "notes": notes or "",
        }
        steps.append(rec)
        runlog(f"  [step {code}] url={url} arkose={arkose} "
               f"worked={worked_sel or {}} notes={notes or ''}")
        return rec

    try:
        # ----- 0. Verify logged-OUT -----
        runlog(">>> Navigating to aircanada.com to verify LOGGED-OUT state...")
        try:
            page.goto(AIRCANADA_HOME, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            runlog(f"    initial navigate raised: {exc}")
        _settle()

        if is_logged_in_dom(page):
            runlog("STILL-LOGGED-IN — abort (need real logout). The warmed "
                   "profile is logged in; this driver requires a logged-out "
                   "profile to observe the scripted login. Log out manually and "
                   "re-run.")
            _record_step("0", page, detect_arkose(page),
                         notes="still-logged-in; aborted")
            _save_drive(out_dir, runlog, steps, "still_logged_in", "none",
                        "ABORT: profile still logged in.", worked)
            _teardown(context, playwright)
            return 1
        runlog("    logged-out confirmed (Sign-in entry present / no logged-in "
               "markers). Proceeding.")

        # ===== Step A — open login =====
        runlog("\n>>> STEP A — open login")
        try:
            dump_candidates(page, "stepA_signin_entries",
                            SIGN_IN_ENTRY_SELECTORS, runlog)
            entry, entry_sel = first_visible_locator(page, SIGN_IN_ENTRY_SELECTORS)
            if entry is not None:
                try:
                    entry.click(timeout=5000)
                    worked["sign_in_entry"] = entry_sel
                    runlog(f"    clicked Sign-in entry via {entry_sel!r}")
                except Exception as exc:
                    runlog(f"    Sign-in entry click raised ({exc}); continuing.")
            else:
                runlog("    no visible Sign-in entry found; maybe already on the "
                       "login form. Continuing.")
            _settle()

            # Wait for the Gigya form: URL on login host OR an id/password field.
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    on_login_host = LOGIN_HOST in (page.url or "")
                except Exception:
                    on_login_host = False
                pw_vis, _ = first_visible_locator(page, LOGIN_PASSWORD_SELECTORS)
                id_vis, _ = first_visible_locator(page, LOGIN_ID_SELECTORS)
                if on_login_host or pw_vis is not None or id_vis is not None:
                    break
                time.sleep(1)
        except Exception as exc:
            runlog(f"    STEP A raised ({exc}); continuing to classification.")
        arkose_a = detect_arkose(page)
        dump_form(page, "stepA_loginform", out_dir, runlog)
        _record_step("A", page, arkose_a,
                     worked_sel={"sign_in_entry": worked.get("sign_in_entry")},
                     notes="opened login form")

        # ===== Step B — member number =====
        runlog("\n>>> STEP B — member number")
        try:
            dump_candidates(page, "stepB_id_fields", LOGIN_ID_SELECTORS, runlog)
            id_field, id_sel = first_visible_locator(page, LOGIN_ID_SELECTORS)
            if id_field is not None:
                try:
                    id_field.fill(number, timeout=5000)
                    worked["member_number_field"] = id_sel
                    runlog(f"    filled member number via {id_sel!r} "
                           f"(value {redact_member_number(number)})")
                except Exception as exc:
                    runlog(f"    member-number fill raised ({exc}); continuing.")
            else:
                runlog("    no visible member-number field found; continuing.")

            dump_candidates(page, "stepB_continue_buttons",
                            CONTINUE_SELECTORS, runlog)
            cont, cont_sel = first_visible_locator(page, CONTINUE_SELECTORS)
            if cont is not None:
                try:
                    cont.click(timeout=5000)
                    worked["continue_button"] = cont_sel
                    runlog(f"    clicked Continue/Next via {cont_sel!r}")
                except Exception as exc:
                    runlog(f"    continue click raised ({exc}); continuing.")
            else:
                runlog("    no visible Continue/Next button found; continuing.")
            _settle()
        except Exception as exc:
            runlog(f"    STEP B raised ({exc}); continuing to classification.")
        arkose_b = detect_arkose(page)
        dump_form(page, "stepB_afterid", out_dir, runlog)
        _record_step("B", page, arkose_b, worked_sel={
            "member_number_field": worked.get("member_number_field"),
            "continue_button": worked.get("continue_button"),
        }, notes="after member# + continue")

        # ===== Step C — password =====
        runlog("\n>>> STEP C — password")
        try:
            dump_candidates(page, "stepC_password_fields",
                            LOGIN_PASSWORD_SELECTORS, runlog)
            pw_field, pw_sel = first_visible_locator(page, LOGIN_PASSWORD_SELECTORS)
            if pw_field is not None:
                try:
                    pw_field.fill(password, timeout=5000)
                    worked["password_field"] = pw_sel
                    runlog(f"    filled password via {pw_sel!r} "
                           f"(value {mask_secret(password)})")
                except Exception as exc:
                    runlog(f"    password fill raised ({exc}); continuing.")

                # ---- SUBMIT the Gigya form RELIABLY ----
                # Primary: press Enter inside the (visible) password field —
                # this submits the native Gigya form even when its submit
                # control is an input[type=submit] our click ladder might miss.
                submit_method = None
                try:
                    page.locator('input[type="password"]:visible').first.press(
                        "Enter", timeout=5000)
                    submit_method = 'password:press(Enter)'
                    runlog("    submitted via Enter on password field")
                except Exception as exc:
                    runlog(f"    Enter-on-password raised ({exc}); "
                           "trying click ladder.")

                # Also try clicking a visible Gigya submit control (the real
                # control is input[type=submit] value='Sign in'). This runs
                # even after Enter — harmless if the form already navigated.
                dump_candidates(page, "stepC_submit_buttons",
                                SUBMIT_SELECTORS, runlog)
                submit, submit_sel = first_visible_locator(page, SUBMIT_SELECTORS)
                if submit is not None:
                    try:
                        submit.click(timeout=5000)
                        if submit_method is None:
                            submit_method = submit_sel
                        runlog(f"    clicked submit via {submit_sel!r}")
                    except Exception as exc:
                        runlog(f"    submit click raised ({exc}); continuing.")
                else:
                    runlog("    no visible submit control found in ladder "
                           "(Enter may have already submitted); continuing.")

                if submit_method is not None:
                    worked["submit"] = submit_method
                else:
                    runlog("    WARNING: no submit method fired.")

                # ---- WAIT for a post-submit state change (~25s) ----
                # Poll every ~1.5s for ANY progress signal: left the login
                # form URL, a 2FA field appeared, Arkose appeared, or a
                # visible error/validation message surfaced.
                runlog("    waiting up to ~25s for a post-submit state change...")
                started_on_form = LOGIN_FORM_URL_MARKER in (
                    _safe_url(page) or "")
                transition = None
                sdeadline = time.time() + 25
                while time.time() < sdeadline:
                    cur_url = _safe_url(page) or ""
                    if started_on_form and LOGIN_FORM_URL_MARKER not in cur_url:
                        transition = f"redirected off login form -> {cur_url}"
                        break
                    tf, _tfsel = first_visible_locator(page, TFA_CODE_SELECTORS)
                    if tf is not None:
                        transition = "2FA code field appeared"
                        break
                    if detect_arkose(page).get("present"):
                        transition = "Arkose challenge appeared"
                        break
                    err = _visible_error_text(page)
                    if err:
                        transition = f"error/validation message: {err}"
                        break
                    time.sleep(1.5)
                if transition:
                    runlog(f"    post-submit transition: {transition}")
                else:
                    runlog("    no post-submit transition observed within ~25s "
                           "(still on the login form?).")
                _settle()
            else:
                runlog("    no visible password field appeared after step B — "
                       "the two-step flow may be blocked here (selector-blocked "
                       "or Arkose-gated). Continuing to classification.")
        except Exception as exc:
            runlog(f"    STEP C raised ({exc}); continuing to classification.")
        arkose_c = detect_arkose(page)
        dump_form(page, "stepC_aftersubmit", out_dir, runlog)
        try:
            runlog(f"    page.url after submit: {page.url}")
        except Exception:
            pass
        _record_step("C", page, arkose_c, worked_sel={
            "password_field": worked.get("password_field"),
            "submit_button": worked.get("submit_button"),
        }, notes="after password submit")

        # ===== Step D — classify final state =====
        runlog("\n>>> STEP D — classify final state (NO 2FA code entered)")
        # Give the post-submit transition a moment to land.
        _settle()
        # Re-run Arkose detection here too (post-submit may escalate Arkose).
        arkose_d = detect_arkose(page)

        cur_url = _safe_url(page)
        # The single most reliable progress signal: did we leave the Gigya
        # login form URL? While we're still on clogin/pages/login the login
        # did NOT progress (form unsubmitted or credentials rejected).
        still_on_login_form = LOGIN_FORM_URL_MARKER in cur_url

        # Is the login form (loginID / password field) still present?
        id_still, _ = first_visible_locator(page, LOGIN_ID_SELECTORS)
        pw_still, _ = first_visible_locator(page, LOGIN_PASSWORD_SELECTORS)
        login_form_present = (id_still is not None) or (pw_still is not None)

        # logged-in DOM signal (only meaningful once we've LEFT the form).
        logged_in_signal = False
        try:
            logged_in_signal = is_logged_in_dom(page)
        except Exception:
            logged_in_signal = False

        # Landed on an aircanada.com app / consent / afterLogin redirect chain?
        on_app_pages = (
            ("aircanada.com" in cur_url and not still_on_login_form)
            or "consent" in cur_url.lower()
            or "afterlogin" in cur_url.lower()
        )

        # (2) 2FA code field?
        tfa_field, tfa_sel = first_visible_locator(page, TFA_CODE_SELECTORS)

        classification = classify_final_state(
            still_on_login_form=still_on_login_form,
            login_form_present=login_form_present,
            logged_in_signal=logged_in_signal,
            on_app_pages=on_app_pages,
            tfa_present=tfa_field is not None,
            arkose_present=bool(arkose_d.get("present")),
        )

        notes_d = ""
        if classification == "tfa_required":
            # 2FA detection takes priority — Gigya advanced IN PLACE to the SMS
            # 2FA screen (the URL stays on clogin/pages/login). Scrape the
            # delivery method from the code field's aria-label.
            worked["tfa_code_field"] = tfa_sel
            reached_2fa = True
            tfa_method = scrape_tfa_delivery_method(page, tfa_field)
            if args.complete_2fa:
                # ----- Complete 2FA — dispatch by --mfa-method -----
                runlog(f"    2FA screen reached via {tfa_sel!r}; method="
                       f"{tfa_method}. --complete-2fa is ON, --mfa-method="
                       f"{args.mfa_method} — driving 2FA to completion.")
                if args.mfa_method == "email":
                    # Email (unattended): select Email delivery, then resolve
                    # the code via the mfa_request/mfa_response protocol.
                    comp = complete_email_2fa(page, runlog, tfa_field, tfa_sel,
                                              out_dir)
                else:
                    # SMS (original): poll ~/.searchaero/aeroplan_sms_code.
                    comp = complete_sms_2fa(page, runlog, tfa_field, tfa_sel,
                                            out_dir)
                classification = comp["classification"]
                tfa_method = comp.get("tfa_method") or tfa_method
                # Re-check Arkose AFTER 2FA (it can escalate post-2FA).
                arkose_d = detect_arkose(page)
                if classification == "logged_in_success":
                    notes_d = (f"2FA ({tfa_method}) completed; login SUCCEEDED. "
                               f"final url={_safe_url(page)}.")
                elif classification == "tfa_failed":
                    err = comp.get("error_text") or _visible_error_text(page)
                    notes_d = (f"2FA ({tfa_method}) submitted but login did NOT "
                               f"complete; {('error: ' + err) if err else 'no visible error'}.")
                else:  # tfa_required (timeout / no code)
                    notes_d = (f"2FA ({tfa_method}) reached but not completed "
                               f"({comp.get('error_text') or 'timed out / no code'}); "
                               "stopped cleanly.")
            else:
                # --no-complete-2fa: stop at the wall, enter NO code.
                notes_d = (f"2FA code field present via {tfa_sel!r}; delivery "
                           f"method => {tfa_method}. STOPPING — no code entered "
                           "(--no-complete-2fa).")
                runlog(f"    2FA WALL reached via {tfa_sel!r}; method="
                       f"{tfa_method}. NOT entering any code (--no-complete-2fa).")
        elif classification == "arkose_interactive":
            notes_d = (f"Arkose challenge present at final state "
                       f"({arkose_d.get('where')}); likely interactive. NOT "
                       f"auto-solving (by design).")
        elif classification == "logged_in_success":
            # SUCCESS requires actually leaving the login form AND a positive
            # signal (logged-in markers OR the app/consent redirect chain).
            notes_d = (f"left login form (url={cur_url}); "
                       f"logged_in_signal={logged_in_signal} "
                       f"on_app_pages={on_app_pages}.")
        elif classification == "login_form_still_present":
            # Submit failed or credentials rejected — NOT success.
            err = _visible_error_text(page)
            err_part = f"visible error: {err}" if err else "no visible error message"
            notes_d = (f"still on {LOGIN_FORM_URL_MARKER} with the login form "
                       f"showing (loginID/password still present); {err_part}. "
                       f"Submit failed or credentials rejected — NOT a success.")
            runlog(f"    LOGIN FORM STILL PRESENT (url={cur_url}); {err_part}.")
        else:
            snippet = ""
            try:
                text = (page.inner_text("body") or "")
                snippet = " ".join(text.split())[:400]
            except Exception:
                snippet = "(could not read body text)"
            notes_d = (f"unclassified final state (url={cur_url}); "
                       f"body snippet: {snippet}")

        dump_form(page, "stepD_final", out_dir, runlog)
        _record_step("D", page, arkose_d,
                     worked_sel={"tfa_code_field": worked.get("tfa_code_field")},
                     notes=notes_d)

        # ----- Build PHASE-1 VERDICT -----
        # Did Arkose appear at any step? Where first?
        arkose_first = None
        for rec in steps:
            ak = rec.get("arkose") or {}
            if ak.get("present"):
                arkose_first = (rec.get("step"), ak.get("where"))
                break
        if arkose_first:
            arkose_part = (f"appeared at step {arkose_first[0]} "
                           f"({arkose_first[1]})"
                           + ("; INTERACTIVE at final state"
                              if classification == "arkose_interactive" else ""))
        else:
            arkose_part = "none detected at any step"

        # Furthest step reached.
        reached = steps[-1]["step"] if steps else "0"

        # 2FA part: report the delivery method whenever 2FA was reached
        # (whether or not it was completed), else none.
        if reached_2fa or classification in ("tfa_required", "tfa_failed"):
            tfa_part = tfa_method
        else:
            tfa_part = "none"

        # Outcome.
        if classification == "logged_in_success":
            if reached_2fa:
                outcome = ("full scripted login SUCCEEDED (credentials + "
                           f"{args.mfa_method} 2FA via {tfa_method} completed; "
                           "left the login form with a logged-in signal)")
            else:
                outcome = ("scripted login SUCCEEDED (left the login form with "
                           "a logged-in signal, WITHOUT a 2FA wall)")
        elif classification == "tfa_failed":
            err = _visible_error_text(page)
            outcome = (f"reached 2FA ({tfa_method}) and submitted a code but "
                       "login did NOT complete"
                       + (f" (error: {err})" if err else
                          " (no visible error; inspect dumps)"))
        elif classification == "tfa_required":
            if arkose_first:
                outcome = (f"scripted login reached 2FA ({tfa_method}) but "
                           f"Arkose appeared at step {arkose_first[0]} — gate "
                           "it on Arkose")
            else:
                outcome = (f"reached the 2FA wall (method {tfa_method}); Arkose "
                           "did NOT escalate — 2FA is the only remaining gate "
                           "(no code completed)")
        elif classification == "arkose_interactive":
            outcome = "Arkose-walled (interactive FunCaptcha blocks scripted login)"
        elif classification == "login_form_still_present":
            err = _visible_error_text(page)
            if err:
                outcome = (f"login form still present after submit — credentials "
                           f"likely rejected (error: {err})")
            else:
                outcome = ("login form still present after submit — submit did "
                           "not advance the form (no visible error; inspect dumps)")
        else:
            outcome = ("unknown final state (form neither advanced nor surfaced "
                       "2FA/Arkose/error; inspect the dumps + body snippet)")

        verdict = (f"Scripted login reached step {reached}; "
                   f"Arkose: {arkose_part}; 2FA: {tfa_part}; -> {outcome}.")

        runlog("\n" + "=" * 72)
        runlog("PHASE-1 VERDICT")
        runlog("=" * 72)
        runlog(verdict)
        runlog(f"classification: {classification}")
        runlog(f"worked selectors: {worked}")
        runlog("=" * 72)

        _save_drive(out_dir, runlog, steps, classification, tfa_method, verdict,
                    worked, reached_2fa)

    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl-C). Tearing down cleanly.")
        _save_drive(out_dir, runlog, steps, classification, tfa_method,
                    verdict, worked, reached_2fa)
    except Exception as exc:
        runlog(f"UNEXPECTED top-level error ({exc}); saving partial results.")
        _save_drive(out_dir, runlog, steps, classification, tfa_method,
                    verdict, worked, reached_2fa)
    finally:
        _teardown(context, playwright)

    print("\n" + "=" * 72)
    print(f"Drive JSON : {out_dir / 'login_drive.json'}")
    print(f"Run log    : {out_dir / 'run.log'}")
    print(f"Screenshots: {out_dir} (stepA..stepD .png)")
    print("=" * 72)
    return 0


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def main(argv=None) -> int:
    # Observability: line-buffered stdout/stderr so background monitoring sees
    # progress live instead of in block-buffered chunks.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # Headed-only guard — REFUSE --headless (Akamai/Arkose non-viability).
    if args.headless:
        print("REFUSED: --headless is not supported.", file=sys.stderr)
        print(
            "Air Canada's stack (Akamai Bot Manager + Arkose FunCaptcha + "
            "securitytrfx + Glassbox) detects and blocks headless browsers via "
            "TLS/HTTP2 fingerprinting. This driver is HEADED-ONLY by design. "
            "Re-run without --headless.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        return run_dry_run(args)

    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())
