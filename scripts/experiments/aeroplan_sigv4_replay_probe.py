"""Aeroplan air-calendars SigV4 replay probe — instrumented, account-safe.

Determines what auth the `air-calendars` AWS API-Gateway endpoint ACTUALLY
enforces, by re-firing the SAME call from INSIDE the booted, authenticated page
with a MODIFIED body (bigger `flexibility`, far-shifted date) under several
header strategies — then reading the resulting STATUS CODES + returned day-count.

The decision this unblocks (transport-spike Addendum, "Design consequence"):

  - `air-calendars` is the SigV4-signed endpoint. The SPA hardcodes
    `itineraries[0].flexibility = 2` (= 5 days/call). If we could replay the
    call in-page with a FAT flexibility (e.g. 30 → ~2 months in ONE call), the
    scraper becomes "one boot → a few huge calls" (a huge win).
  - But a modified body likely breaks the SigV4 signature (the sig covers the
    body hash) → 403. If strictly enforced, we must fall back to
    navigate-per-5-days.

This spike answers, empirically, WHICH (if any) header strategy returns 200 with
a WIDE day-count for a modified body. That single fact decides the architecture.

HOW (live path — the USER runs this later, manually):
  1. Launch headed persistent context. If not already logged in, run a BEST-
     EFFORT Gigya auto-login (--auto-login, default; AEROPLAN_NUMBER +
     AEROPLAN_PASSWORD from ~/.searchaero/.env) that degrades to a manual-login
     poll on ANY failure (missing creds, Arkose, fields not found). Settle the
     post-login redirect chain.
  2. BOOT + CAPTURE one clean air-calendars POST via `page.expect_response`:
     grab its FULL request headers (incl. live SigV4 `authorization`, `x-amz-*`,
     `x-custom-id-token`, `x-api-key`, `ama-*`, `content-type`) and the JSON
     body. Record the baseline response status + day-count. Keep REAL
     headers/body IN MEMORY; redact before any disk write.
  3. BUILD a MODIFIED body: bigger `flexibility` (--flex, default 30) + far-
     shifted `departureDateTime` (--replay-date, default = --date + 60 days).
  4. RUN the COMPREHENSIVE CORS BATTERY — 8 in-page `fetch` experiments (each a
     `page.evaluate` POST with mode:'cors') IN ONE SESSION, across the header
     sets (H_full_auth / H_no_sigv4 / H_idtoken / H_apikey), bodies (B_exact /
     B_mod) and BOTH credentials modes (omit / include). Header hygiene strips
     the junk forbidden headers (user-agent, accept*, priority, sec-*) so only
     clean auth headers are sent. A context-level `response` listener CAPTURES
     THE ACTUAL OPTIONS PREFLIGHT for air-calendars (status + ACA-* headers) so
     we can see exactly what CORS rejects.
  5. Write all results (incl. preflight records) to
     <out-dir>/sigv4_probe_results.json + a run.log table, and print a VERDICT:
     the first 200-with-wide-day-count path if any; else "SigV4 body-bound" if
     control 200s but modified body 403s; else the captured preflight ACA-*
     values explaining the CORS rejection.

ACCOUNT SAFETY (baked in):
  - Headed-only. --headless is REFUSED (Akamai/Arkose make headless non-viable).
  - Best-effort auto-login reads creds from .env ONLY (never hardcoded, never
    logged) and NEVER auto-solves Arkose — it degrades to manual. --no-auto-login
    forces manual. Reuses a persistent profile across runs.
  - Fires ~8 EXTRA air-calendars calls in ONE session (modest; logged). Single
    account, single route, loud human-in-the-loop notes.
  - Redacts authorization, x-amz-security-token, x-custom-id-token, any cookie,
    and cardNumber in ANY saved file.

This is a SPIKE, not a scraper. Out of scope: air-bounds/polldapi, DB writes,
login automation, Arkose, round-trip / multi-pax.

Usage (live run — MANUAL, human-in-the-loop; do NOT automate):
    .venv\\Scripts\\python.exe scripts\\experiments\\aeroplan_sigv4_replay_probe.py \\
        --org YYZ --dest LAX --date 2026-08-15 --flex 30

Usage (offline self-test — no browser launched):
    .venv\\Scripts\\python.exe scripts\\experiments\\aeroplan_sigv4_replay_probe.py --dry-run
"""

import argparse
import copy
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent

# The ONLY endpoint substring we capture / replay (the SigV4-signed price strip).
TARGET_SUBSTRING = "air-calendars"

# Availability URL host + path family (transport spike §3 / auth-recon §3). A
# cold navigate loads the SPA, which then fires the signed air-calendars POST.
AVAILABILITY_HOST = "https://www.aircanada.com"
AVAILABILITY_PATH = "/aeroplan/redeem/availability/outbound"

# Expiry / redirect signal: a redirect to this host means the session lapsed.
LOGIN_HOST = "login.aircanada.com"

# Settle pause after navigate (cookie_farm discipline: let the SPA settle).
SETTLE_SECONDS = 3

# Date format for the availability URL's departureDate0 param + date arithmetic.
DATE_FMT = "%Y-%m-%d"

# air-calendars POST body uses a millisecond datetime: YYYY-MM-DDT00:00:00.000.
BODY_DT_FMT = "%Y-%m-%dT00:00:00.000"

# Default forward shift for the replay date (days), so the modified body asks for
# a CLEARLY different window than the captured baseline.
DEFAULT_REPLAY_SHIFT_DAYS = 60

# Default fat flexibility for the modified body (30 ≈ ±30 days ≈ 2 months/call).
DEFAULT_FLEX = 30

# Browser-forbidden / auto headers a page `fetch` must NOT set manually
# (case-insensitive). Cookies travel via credentials:'include', never a manual
# `cookie` header. `origin`/`referer` are set by the browser automatically.
#
# The prior probe forwarded these junk forbidden headers (user-agent, sec-ch-ua*,
# sec-fetch-*, priority, accept*), which a page `fetch` is NOT allowed to set —
# they almost certainly contributed to the CORS preflight rejection. We now strip
# them aggressively, keeping ONLY the explicit auth headers + content-type.
FORBIDDEN_FETCH_HEADERS = frozenset({
    "host", "content-length", "connection", "cookie", "origin", "referer",
    "accept-encoding", "transfer-encoding", "te", "upgrade",
    # Junk forbidden / auto-managed headers the prior probe wrongly forwarded:
    "user-agent", "accept", "accept-language", "priority",
})

# Any header whose lowercase name starts with one of these prefixes is also
# stripped (the `sec-*` family: sec-fetch-*, sec-ch-ua*, etc.) — a page `fetch`
# may not set these, and they trip the CORS preflight.
FORBIDDEN_FETCH_PREFIXES = ("sec-",)

# .env location (reused from cookie_farm) + credential var names for Aeroplan.
ENV_FILE = Path.home() / ".searchaero" / ".env"
AEROPLAN_NUMBER_VAR = "AEROPLAN_NUMBER"
AEROPLAN_PASSWORD_VAR = "AEROPLAN_PASSWORD"

# Out-of-band SMS/2FA code drop file (the human / mfa_responder writes the code
# here; we poll for it). Mirrors the mfa_request/mfa_response pattern.
SMS_CODE_FILE = Path.home() / ".searchaero" / "aeroplan_sms_code"

# Resilient selector ladders for the Gigya login form (tried in order).
LOGIN_ID_SELECTORS = (
    'input[name*="loginID" i]',
    'input[name="username"]',
    'input[type="text"]:visible',
    'input[type="email"]:visible',
)
LOGIN_PASSWORD_SELECTORS = (
    'input[type="password"]:visible',
)
LOGIN_SUBMIT_SELECTORS = (
    'button[type=submit]',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'input[type=submit]',
)
# The "Sign in" entry point on aircanada.com that opens the Gigya login.
SIGN_IN_ENTRY_SELECTORS = (
    'a:has-text("Sign in"):visible',
    'button:has-text("Sign in"):visible',
    'a:has-text("Sign In"):visible',
    'button:has-text("Sign In"):visible',
)
# 2FA / one-time-code field on the post-password screen.
TFA_CODE_SELECTORS = (
    'input[autocomplete="one-time-code"]',
    'input[name*="code" i]',
    'input[type="tel"]',
)
# Arkose / interactive-challenge markers (we NEVER auto-solve these).
ARKOSE_SELECTORS = (
    'iframe[src*="arkoselabs" i]',
    '#arkose',
    '[id*="funcaptcha" i]',
)


# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aeroplan_sigv4_replay_probe.py",
        description=(
            "Aeroplan air-calendars SigV4 replay probe — headed, account-safe "
            "Playwright harness. Boots the SPA, captures a real signed "
            "air-calendars POST, then re-fires it in-page (8-experiment CORS "
            "battery: header sets x credentials modes) to learn what auth the "
            "gateway actually enforces. Best-effort auto-login reads creds from "
            ".env only and degrades to manual; --no-auto-login forces manual."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--org", default="YYZ",
                   help="Origin airport code (origin of the outbound).")
    p.add_argument("--dest", default="LAX",
                   help="Destination airport code.")
    p.add_argument("--date", default="2026-08-15",
                   help="Boot departure date (YYYY-MM-DD), near-term. The SPA "
                        "fires the baseline air-calendars POST for this date.")
    p.add_argument("--replay-date", default=None,
                   help=("Date (YYYY-MM-DD) injected into the MODIFIED replay "
                         "body's departureDateTime. Default = --date + "
                         f"{DEFAULT_REPLAY_SHIFT_DAYS} days."))
    p.add_argument("--flex", type=int, default=DEFAULT_FLEX,
                   help=("flexibility value injected into the MODIFIED body "
                         "(itineraries[0].flexibility). The SPA hardcodes 2 "
                         "(5 days); a fat value tests fat-window replay."))
    p.add_argument("--capture-timeout", type=float, default=35.0,
                   help=("Seconds to wait for the SPA's baseline air-calendars "
                         "POST after the boot navigate before giving up."))
    p.add_argument("--out-dir", default=None,
                   help=("Output dir for sigv4_probe_results.json + run.log + "
                         "redacted captured request. Defaults to "
                         "logs/aeroplan_sigv4_<ts>."))
    p.add_argument("--profile-dir",
                   default=str(SCRIPT_DIR / ".aeroplan-profile"),
                   help="Persistent browser profile dir (manual login persists).")
    p.add_argument("--auto-login", dest="auto_login", action="store_true",
                   default=True,
                   help=("Best-effort auto-login via Gigya using AEROPLAN_NUMBER "
                         "+ AEROPLAN_PASSWORD from ~/.searchaero/.env (default). "
                         "Degrades to manual login on any failure (missing "
                         "fields, Arkose challenge, etc.). Never hard-crashes."))
    p.add_argument("--no-auto-login", dest="auto_login", action="store_false",
                   help="Force MANUAL login only (skip best-effort auto-login).")
    p.add_argument("--dry-run", action="store_true",
                   help=("Offline self-test: arg parsing, replay-date "
                         "defaulting + ISO formatting, body modification, "
                         "clean header filtering (strips user-agent/sec-*/"
                         "accept/priority), H_* set subsetting, the 8-experiment "
                         "CORS matrix build, and redaction — WITHOUT a browser. "
                         "Prints the experiment definitions + a wiring summary "
                         "and exits 0."))
    p.add_argument("--headless", action="store_true",
                   help=("REFUSED. Headless is non-viable against Air Canada's "
                         "Akamai/Arkose stack; passing this prints a refusal "
                         "and exits non-zero."))
    return p


def resolve_out_dir(arg_out_dir) -> Path:
    if arg_out_dir:
        return Path(arg_out_dir).resolve()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (REPO_ROOT / "logs" / f"aeroplan_sigv4_{ts}").resolve()


def resolve_replay_date(date: str, replay_date) -> str:
    """Return the replay date (YYYY-MM-DD). Defaults to date + 60 days."""
    if replay_date:
        # Validate format (raises ValueError on bad input — surfaced to the user).
        datetime.strptime(replay_date, DATE_FMT)
        return replay_date
    base = datetime.strptime(date, DATE_FMT)
    return (base + timedelta(days=DEFAULT_REPLAY_SHIFT_DAYS)).strftime(DATE_FMT)


def body_datetime(date: str) -> str:
    """Convert a YYYY-MM-DD date to the body's YYYY-MM-DDT00:00:00.000 form."""
    return datetime.strptime(date, DATE_FMT).strftime(BODY_DT_FMT)


# ----------------------------------------------------------------------------
# Availability URL builder (from recon path family)
# ----------------------------------------------------------------------------

def build_availability_url(org: str, dest: str, date: str) -> str:
    """Build the Aeroplan outbound-availability URL from org/dest/date.

    Standard params (transport spike §3 / auth-recon §3):
      /aeroplan/redeem/availability/outbound
        ?org0=YYZ&dest0=LAX&departureDate0=2026-08-15&lang=en-CA&tripType=O
         &ADT=1&YTH=0&CHD=0&INF=0&INS=0&marketCode=DOM

    A cold navigate to this deep-link auto-executes the search; the SPA then
    fires the signed air-calendars POST itself (no date-click needed).
    """
    params = {
        "org0": org,
        "dest0": dest,
        "departureDate0": date,
        "lang": "en-CA",
        "tripType": "O",
        "ADT": "1",
        "YTH": "0",
        "CHD": "0",
        "INF": "0",
        "INS": "0",
        "marketCode": "DOM",
    }
    return f"{AVAILABILITY_HOST}{AVAILABILITY_PATH}?{urlencode(params)}"


# ----------------------------------------------------------------------------
# Modified-body construction
# ----------------------------------------------------------------------------

def modify_body(captured_body: dict, flex: int, replay_date: str) -> dict:
    """Build the MODIFIED replay body from the captured air-calendars body.

    Sets `itineraries[0].flexibility = flex` and
    `itineraries[0].departureDateTime = <replay_date>T00:00:00.000`, leaving
    EVERYTHING ELSE identical (deep-copied so the captured body is untouched).

    Defensive: if `itineraries[0]` is missing, it's created so the modification
    is always observable; never raises.
    """
    body = copy.deepcopy(captured_body) if isinstance(captured_body, dict) else {}
    itins = body.get("itineraries")
    if not isinstance(itins, list) or not itins:
        itins = [{}]
        body["itineraries"] = itins
    if not isinstance(itins[0], dict):
        itins[0] = {}
    itins[0]["flexibility"] = flex
    itins[0]["departureDateTime"] = body_datetime(replay_date)
    return body


# ----------------------------------------------------------------------------
# Header hygiene for in-page fetch
# ----------------------------------------------------------------------------

def filter_fetch_headers(headers: dict, allow_keys=None) -> dict:
    """Filter captured request headers into a set safe to pass to in-page fetch.

    Drops (case-insensitive):
      - any HTTP/2 pseudo-header (key starts with ':' — :authority/:method/
        :path/:scheme),
      - the browser-forbidden / auto-managed headers in FORBIDDEN_FETCH_HEADERS
        (host, content-length, connection, cookie, origin, referer,
        accept-encoding, transfer-encoding, te, upgrade) PLUS the junk forbidden
        headers the prior probe wrongly forwarded (user-agent, accept,
        accept-language, priority),
      - anything in the `sec-*` family (FORBIDDEN_FETCH_PREFIXES: sec-fetch-*,
        sec-ch-ua*, etc.) — a page `fetch` may not set these and they trip the
        CORS preflight.
    KEEPS the auth-bearing headers: authorization, x-amz-*, x-api-key,
    x-custom-id-token, ama-*, content-type.

    Cookies are NEVER forwarded here — the in-page fetch uses
    credentials:'include' so the browser attaches them automatically.

    When `allow_keys` is given (an iterable of header names, matched case-
    insensitively), the result is further restricted to ONLY those keys — used
    for the drop_sigv4 / idtoken_apikey / apikey_only strategies.
    """
    allow_lower = None
    if allow_keys is not None:
        allow_lower = {str(k).lower() for k in allow_keys}

    out = {}
    for k, v in (headers or {}).items():
        if not isinstance(k, str):
            continue
        kl = k.lower()
        if k.startswith(":"):
            continue  # HTTP/2 pseudo-header
        if kl in FORBIDDEN_FETCH_HEADERS:
            continue
        if any(kl.startswith(p) for p in FORBIDDEN_FETCH_PREFIXES):
            continue  # sec-fetch-* / sec-ch-ua* family
        if allow_lower is not None and kl not in allow_lower:
            continue
        out[k] = v
    return out


# ----------------------------------------------------------------------------
# Experiment definitions
# ----------------------------------------------------------------------------

# Each experiment is described declaratively so the SAME code drives both the
# dry-run printout and the live page.evaluate calls. `allow_keys=None` means
# "all captured headers (minus forbidden)"; `use_modified_body=False` means the
# EXACT captured body.
#
# This is the COMPREHENSIVE CORS battery: the prior probe's 5 in-page fetches
# ALL failed with `TypeError: Failed to fetch` — a CORS/preflight block, not a
# server 403. Even the exact-replay control failed. So we now (a) send only
# clean auth headers (see filter_fetch_headers), (b) try BOTH credentials modes
# (omit + include), and (c) capture the actual OPTIONS preflight response (see
# run_live's context.on("response") listener) to see exactly what CORS rejects.

# Header sets (lowercase canonical names). Each H_* is fed to
# filter_fetch_headers(captured, allow_keys=<set>) so only these keys (drawn
# from the captured request) survive.
H_FULL_AUTH = (
    "authorization", "x-amz-date", "x-amz-security-token", "x-api-key",
    "x-custom-id-token", "ama-session-token", "ama-client-ref", "content-type",
)
H_NO_SIGV4 = (
    "x-custom-id-token", "x-api-key", "ama-session-token", "content-type",
)
H_IDTOKEN = ("x-custom-id-token", "x-api-key", "content-type")
H_APIKEY = ("x-api-key", "content-type")


def experiment_specs():
    """Return the ordered list of experiment specs (name, headers, body, creds).

    Eight experiments, run IN ONE SESSION after a single clean air-calendars
    capture. `headers` names the H_* set; `use_modified_body` picks B_exact vs
    B_mod; `credentials` is the fetch credentials mode under test.
    """
    return [
        {
            "name": "control_exact_omit",
            "header_set": H_FULL_AUTH, "header_set_name": "H_full_auth",
            "use_modified_body": False, "credentials": "omit",
            "isolates": ("CONTROL with credentials:'omit': re-fires the EXACT "
                         "captured request, full auth headers, no cookies. "
                         "Proves whether the in-page replay path works at all "
                         "once junk forbidden headers are stripped."),
        },
        {
            "name": "control_exact_include",
            "header_set": H_FULL_AUTH, "header_set_name": "H_full_auth",
            "use_modified_body": False, "credentials": "include",
            "isolates": ("CONTROL with credentials:'include': EXACT body, full "
                         "auth headers, cookies attached. The truest replay of "
                         "the original signed request."),
        },
        {
            "name": "full_modbody_omit",
            "header_set": H_FULL_AUTH, "header_set_name": "H_full_auth",
            "use_modified_body": True, "credentials": "omit",
            "isolates": ("Tests body-hash binding of the SigV4 sig with full "
                         "auth headers + MODIFIED body, no cookies."),
        },
        {
            "name": "full_modbody_include",
            "header_set": H_FULL_AUTH, "header_set_name": "H_full_auth",
            "use_modified_body": True, "credentials": "include",
            "isolates": ("Tests body-hash binding of the SigV4 sig with full "
                         "auth headers + MODIFIED body + cookies. 200 (wide "
                         "day-count) here = body NOT covered by the signature."),
        },
        {
            "name": "drop_sigv4_include",
            "header_set": H_NO_SIGV4, "header_set_name": "H_no_sigv4",
            "use_modified_body": True, "credentials": "include",
            "isolates": ("Drops authorization / x-amz-*; keeps id-token + "
                         "api-key + Amadeus session + cookies. 200 = no SigV4 "
                         "required."),
        },
        {
            "name": "drop_sigv4_omit",
            "header_set": H_NO_SIGV4, "header_set_name": "H_no_sigv4",
            "use_modified_body": True, "credentials": "omit",
            "isolates": ("Same as drop_sigv4_include but no cookies — isolates "
                         "whether cookies are load-bearing vs the id-token."),
        },
        {
            "name": "idtoken_include",
            "header_set": H_IDTOKEN, "header_set_name": "H_idtoken",
            "use_modified_body": True, "credentials": "include",
            "isolates": ("Floor probe: only x-custom-id-token + x-api-key + "
                         "content-type (+ cookies). Is the OIDC id-token + "
                         "api-key enough on their own?"),
        },
        {
            "name": "apikey_include",
            "header_set": H_APIKEY, "header_set_name": "H_apikey",
            "use_modified_body": True, "credentials": "include",
            "isolates": ("THE FLOOR: only x-api-key + content-type (+ cookies). "
                         "200 here = endpoint is effectively cookie+api-key "
                         "gated (the cheapest replay)."),
        },
    ]


def build_experiment(spec: dict, captured_headers: dict, original_body: dict,
                     modified_body: dict):
    """Resolve a spec into the concrete (headers, body, credentials) to fetch."""
    body = modified_body if spec["use_modified_body"] else original_body
    headers = filter_fetch_headers(captured_headers,
                                   allow_keys=spec["header_set"])
    return {"name": spec["name"], "headers": headers, "body": body,
            "credentials": spec["credentials"]}


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
# Redaction
# ----------------------------------------------------------------------------

# Header keys whose VALUES are secrets/tokens and must be masked before any save
# (case-insensitive, matched on the canonical lowercase name).
SECRET_HEADER_KEYS = frozenset({
    "authorization", "x-amz-security-token", "x-custom-id-token", "cookie",
})


def redact_headers(headers: dict) -> dict:
    """Mask secret-bearing header values (authorization, x-amz-security-token,
    x-custom-id-token, cookie) before persisting. Returns a NEW dict; keeps
    non-secret headers (x-api-key, x-amz-date, ama-*, content-type) intact.
    """
    out = {}
    for k, v in (headers or {}).items():
        if isinstance(k, str) and k.lower() in SECRET_HEADER_KEYS:
            out[k] = "***REDACTED***"
        else:
            out[k] = v
    return out


def redact_card_numbers(obj):
    """Defensively mask the Aeroplan card / member number in a captured payload.

    Masks any value under a `frequentFlyer.cardNumber` key, and any key that
    LOOKS like a card-number / member-number field (cardNumber, aeroplanNumber,
    memberNumber, userId, etc.) anywhere in the tree. Recurses through
    dicts/lists; returns a NEW structure. (Reused verbatim from
    aeroplan_calendar_recon.py.)
    """
    card_key_markers = ("cardnumber", "card_number", "aeroplannumber",
                        "membernumber", "membershipnumber", "ffp", "loyaltynumber",
                        "userid")

    def _looks_like_card_key(key: str) -> bool:
        k = key.lower().replace(" ", "").replace("_", "")
        return any(marker.replace("_", "") in k for marker in card_key_markers)

    def _walk(node, parent_key=None):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if isinstance(k, str) and _looks_like_card_key(k) and not isinstance(v, (dict, list)):
                    out[k] = "***REDACTED***"
                elif parent_key == "frequentFlyer" and k == "cardNumber":
                    out[k] = "***REDACTED***"
                else:
                    out[k] = _walk(v, parent_key=k)
            return out
        if isinstance(node, list):
            return [_walk(item, parent_key=parent_key) for item in node]
        return node

    return _walk(obj)


def redact_captured(headers: dict, body: dict) -> dict:
    """Build the on-disk-safe captured-request record (headers + body redacted)."""
    return {
        "headers": redact_headers(headers),
        "body": redact_card_numbers(body) if isinstance(body, dict) else body,
    }


# ----------------------------------------------------------------------------
# air-calendars RESPONSE day-count helper (Python side, for the baseline)
# ----------------------------------------------------------------------------

def day_count_from_body(body) -> dict:
    """Count returned days + date range from an air-calendars response body.

    Shape: `{ data[], ... }`, one entry per `departureDate`. Returns
    {"day_count": int, "date_range": [min, max] | None}. Never raises.
    """
    dates = []
    try:
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict):
                    d = entry.get("departureDate")
                    if isinstance(d, str):
                        dates.append(d)
    except Exception:
        log.debug("day-count parse failed", exc_info=True)
    dates = sorted(dates)
    return {
        "day_count": len(dates),
        "date_range": [dates[0], dates[-1]] if dates else None,
    }


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
# Login detection / manual-login poll (adapted from transport spike)
# ----------------------------------------------------------------------------

def is_logged_in(page, context) -> bool:
    """Positive logged-in signal via DOM + cookie jar. NO credentials read.
    Negative signal first (visible Sign in), then positive DOM/cookie evidence.
    Best-effort; failures => assume not logged in.
    """
    try:
        try:
            sign_in = page.locator(
                'a:has-text("Sign in"):visible, '
                'button:has-text("Sign in"):visible, '
                'a:has-text("Sign In"):visible, '
                'button:has-text("Sign In"):visible'
            )
            if sign_in.count() > 0:
                return False
        except Exception:
            log.debug("sign-in button check failed", exc_info=True)

        try:
            content = page.content().lower()
        except Exception:
            content = ""
        dom_signal = any([
            "sign out" in content,
            "aeroplan number" in content,
            "your aeroplan" in content,
            "my account" in content and "sign in" not in content,
        ])

        cookie_signal = False
        try:
            cookies = context.cookies()
            names = {c.get("name", "") for c in cookies}
            cookie_signal = bool(names & {
                "gig_canary", "ac_session", "AC_SESSION",
            }) or any(n.startswith("glt_") for n in names)
        except Exception:
            log.debug("cookie signal check failed", exc_info=True)

        return dom_signal or cookie_signal
    except Exception:
        log.debug("login check failed", exc_info=True)
        return False


def wait_for_manual_login(page, context) -> bool:
    """Print manual-login instructions and poll every 10s (timeout ~30 min).
    NO credentials are entered by this script. Returns True once logged in.
    """
    print()
    print("=" * 60)
    print("MANUAL LOGIN REQUIRED — this script enters ZERO credentials")
    print("=" * 60)
    print("1. The browser is open at aircanada.com.")
    print("2. Click 'Sign in' and log into YOUR Aeroplan account.")
    print("3. Complete the Arkose challenge and 2FA (SMS or email) yourself.")
    print("4. Once logged in, this script detects it automatically and proceeds.")
    print("   (Single account, single route — a normal-looking session.)")
    print("=" * 60)
    print("\nPolling for login (every 10s, timeout 30min)...")
    deadline = time.time() + 1800  # 30 minutes
    while time.time() < deadline:
        time.sleep(10)
        if is_logged_in(page, context):
            print("Login confirmed!")
            return True
        print("  Still waiting for manual login...")
    print("ERROR: Manual login timed out after 30 minutes.")
    return False


# ----------------------------------------------------------------------------
# Best-effort auto-login (Gigya) — with manual fallback
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


def _first_visible_locator(page, selectors):
    """Return the first locator (from `selectors`, in order) that has a visible
    match, else None. Best-effort; never raises.
    """
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=1500):
                return loc
        except Exception:
            log.debug("selector probe failed: %s", sel, exc_info=True)
            continue
    return None


def _detect_arkose(page) -> bool:
    """True if an Arkose / interactive FunCaptcha challenge is present."""
    for sel in ARKOSE_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            log.debug("arkose probe failed: %s", sel, exc_info=True)
    return False


def _read_and_clear_sms_code(runlog) -> str:
    """Poll SMS_CODE_FILE (~5 min) for an out-of-band 2FA code.

    Writes a NEED-SMS-CODE marker first (handled by caller). When the file
    appears, returns its stripped contents and deletes it. Returns "" on
    timeout. Never raises.
    """
    deadline = time.time() + 300  # 5 minutes
    while time.time() < deadline:
        try:
            if SMS_CODE_FILE.exists():
                code = SMS_CODE_FILE.read_text(encoding="utf-8").strip()
                try:
                    SMS_CODE_FILE.unlink()
                except Exception:
                    log.debug("sms code file unlink failed", exc_info=True)
                if code:
                    return code
        except Exception:
            log.debug("sms code file read failed", exc_info=True)
        time.sleep(5)
    runlog("    auto-login: SMS code file timed out (5 min).")
    return ""


def attempt_auto_login(page, context, runlog) -> bool:
    """Best-effort Gigya auto-login. Returns True if it confirms login.

    Runs ONLY when called (caller gates on not-already-logged-in + --auto-login).
    Every step is wrapped in try/except: on ANY failure it returns False so the
    caller can fall back to the manual-login poll. Credentials are read from
    .env only and the password is NEVER logged.

    Flow:
      1. From aircanada.com, click a "Sign in" entry to open the Gigya login.
      2. Fill the member-number + password fields via resilient selector ladders.
      3. Click submit.
      4. Poll up to ~3 min for ONE of: logged-in / 2FA code field / Arkose.
         - logged-in  -> True.
         - 2FA field  -> write NEED-SMS-CODE, poll the SMS code file (5 min),
                         fill+submit, then keep polling for logged-in.
         - Arkose     -> log + return False (manual fallback).
    """
    number, password = load_aeroplan_credentials()
    if not (number and password):
        runlog("    auto-login: AEROPLAN_NUMBER / AEROPLAN_PASSWORD not set in "
               f"{ENV_FILE} — falling back to manual login.")
        return False

    runlog("    auto-login: credentials found; attempting best-effort Gigya fill.")

    # ----- 1. Click the Sign in entry on aircanada.com -----
    try:
        entry = _first_visible_locator(page, SIGN_IN_ENTRY_SELECTORS)
        if entry is not None:
            entry.click(timeout=5000)
            runlog("    auto-login: clicked Sign in entry.")
            time.sleep(SETTLE_SECONDS)
        else:
            runlog("    auto-login: no Sign in entry found (maybe already on "
                   "the login form); continuing.")
    except Exception as exc:
        runlog(f"    auto-login: Sign in click raised ({exc}); continuing.")

    # If Arkose is already up, bail to manual immediately.
    if _detect_arkose(page):
        runlog("    auto-login: ARKOSE-INTERACTIVE — falling back to manual.")
        return False

    # ----- 2. Fill member-number + password -----
    try:
        id_field = _first_visible_locator(page, LOGIN_ID_SELECTORS)
        pw_field = _first_visible_locator(page, LOGIN_PASSWORD_SELECTORS)
        if id_field is None or pw_field is None:
            runlog("    auto-login: could not find login fields "
                   f"(id={id_field is not None}, pw={pw_field is not None}) "
                   "— falling back to manual login.")
            return False
        id_field.fill(number, timeout=5000)
        pw_field.fill(password, timeout=5000)
        runlog("    auto-login: filled member number + password.")
    except Exception as exc:
        runlog(f"    auto-login: field fill raised ({exc}) — manual fallback.")
        return False

    # ----- 3. Submit -----
    try:
        submit = _first_visible_locator(page, LOGIN_SUBMIT_SELECTORS)
        if submit is None:
            runlog("    auto-login: no submit button found — manual fallback.")
            return False
        submit.click(timeout=5000)
        runlog("    auto-login: clicked submit; polling outcome (~3 min).")
    except Exception as exc:
        runlog(f"    auto-login: submit raised ({exc}) — manual fallback.")
        return False

    # ----- 4. Poll for logged-in / 2FA / Arkose (up to ~3 min) -----
    sms_handled = False
    deadline = time.time() + 180  # 3 minutes
    while time.time() < deadline:
        try:
            if is_logged_in(page, context):
                runlog("    auto-login: login confirmed.")
                return True
        except Exception:
            log.debug("auto-login is_logged_in failed", exc_info=True)

        if _detect_arkose(page):
            runlog("    auto-login: ARKOSE-INTERACTIVE — falling back to manual.")
            return False

        if not sms_handled:
            code_field = _first_visible_locator(page, TFA_CODE_SELECTORS)
            if code_field is not None:
                runlog("    auto-login: NEED-SMS-CODE — 2FA code field detected. "
                       f"Write the code to {SMS_CODE_FILE} (waiting up to 5 min).")
                code = _read_and_clear_sms_code(runlog)
                if not code:
                    runlog("    auto-login: no 2FA code supplied — manual fallback.")
                    return False
                try:
                    code_field.fill(code, timeout=5000)
                    submit = _first_visible_locator(page, LOGIN_SUBMIT_SELECTORS)
                    if submit is not None:
                        submit.click(timeout=5000)
                    runlog("    auto-login: submitted 2FA code; continuing to poll.")
                except Exception as exc:
                    runlog(f"    auto-login: 2FA submit raised ({exc}) — manual.")
                    return False
                sms_handled = True
                # Reset the deadline window after code entry so we have time.
                deadline = time.time() + 120
        time.sleep(5)

    runlog("    auto-login: timed out waiting for login — manual fallback.")
    return False


# Substrings that mark a URL as still being in the login / post-login redirect
# chain. While page.url contains any of these, the SPA has not yet landed.
LOGIN_REDIRECT_MARKERS = (
    "clogin", "afterLogin", "afterConsent", "consent", "login.aircanada",
    "socialize", "idpresponse",
)


def _is_login_redirect_url(url: str) -> bool:
    """True if `url` is still on a login / post-login redirect page."""
    if not url:
        return False
    return any(marker in url for marker in LOGIN_REDIRECT_MARKERS)


def wait_post_login_settle(page, runlog=None) -> None:
    """Settle after login BEFORE the first navigate.

    Air Canada's post-login redirect chain (clogin/.../afterConsent/idpresponse)
    keeps navigating for a moment after login is detected; starting the boot
    navigate mid-chain interrupts page.goto and captures nothing. Poll page.url
    (up to ~20s) until it is NO LONGER a login/redirect URL AND stable for ~2s,
    then settle an extra ~3s.
    """
    def _emit(msg):
        (runlog or print)(msg)

    deadline = time.time() + 20.0
    stable_since = None
    last_url = None
    while time.time() < deadline:
        try:
            url = page.url or ""
        except Exception:
            url = ""
        on_redirect = _is_login_redirect_url(url)
        if not on_redirect:
            if url == last_url and stable_since is not None:
                if time.time() - stable_since >= 2.0:
                    break
            else:
                stable_since = time.time()
        else:
            stable_since = None
        last_url = url
        time.sleep(0.5)

    time.sleep(3)
    try:
        final_url = page.url or ""
    except Exception:
        final_url = ""
    _emit(f"post-login settle: {final_url}")


# ----------------------------------------------------------------------------
# In-page fetch (the experiment primitive)
# ----------------------------------------------------------------------------

# JS run inside the booted, authenticated page. Performs ONE POST and reports
# {status, ok, dayCount, dateRange, bodyLen, error}. day-count is parsed from
# the response JSON's data[] (departureDate per entry); errors are caught so a
# strategy that throws still yields a structured result.
_FETCH_JS = r"""
async (args) => {
    const out = {status: null, ok: false, dayCount: null,
                 dateRange: null, bodyLen: null, error: null};
    try {
        const resp = await fetch(args.url, {
            method: "POST",
            mode: "cors",
            headers: args.headers,
            body: args.body,
            credentials: args.credentials || "include",
        });
        out.status = resp.status;
        out.ok = resp.ok;
        const text = await resp.text();
        out.bodyLen = text.length;
        try {
            const j = JSON.parse(text);
            if (j && Array.isArray(j.data)) {
                out.dayCount = j.data.length;
                if (j.data.length > 0) {
                    out.dateRange = [
                        j.data[0].departureDate,
                        j.data[j.data.length - 1].departureDate,
                    ];
                }
            }
        } catch (parseErr) {
            // Non-JSON body (e.g. a 403 HTML/error page) — leave dayCount null.
        }
    } catch (err) {
        out.error = String(err);
    }
    return out;
}
"""


def run_experiment_in_page(page, url, exp: dict) -> dict:
    """Execute ONE experiment's POST inside the page and return its result dict.

    `exp` is the output of build_experiment(): {name, headers, body,
    credentials}. The body is JSON-serialized here so the page receives a
    string. Never raises — a thrown JS error is captured in the result.
    """
    body_str = json.dumps(exp["body"], ensure_ascii=False)
    args = {
        "url": url,
        "headers": exp["headers"],
        "body": body_str,
        "credentials": exp.get("credentials", "include"),
    }
    try:
        result = page.evaluate(_FETCH_JS, args)
    except Exception as exc:
        result = {"status": None, "ok": False, "dayCount": None,
                  "dateRange": None, "bodyLen": None, "error": str(exc)}
    result["name"] = exp["name"]
    return result


# ----------------------------------------------------------------------------
# Wiring summary + experiment printout (dry-run + live banner)
# ----------------------------------------------------------------------------

def print_experiment_definitions(captured_headers_example=None):
    """Print the 8-experiment CORS battery: name, header set, body, credentials,
    and what it isolates. `captured_headers_example` (optional) lets us show the
    CONCRETE filtered header keys each strategy would send.
    """
    print("-" * 70)
    print("EXPERIMENTS (CORS battery — run in this order, ONE session):")
    print("-" * 70)
    for i, spec in enumerate(experiment_specs(), start=1):
        body_kind = "B_mod (MODIFIED)" if spec["use_modified_body"] else "B_exact (captured)"
        print(f"  {i}. {spec['name']}")
        print(f"       headers    : {spec['header_set_name']}")
        print(f"       body       : {body_kind}")
        print(f"       credentials: {spec['credentials']}")
        if captured_headers_example is not None:
            keys = sorted(filter_fetch_headers(
                captured_headers_example, allow_keys=spec["header_set"]).keys())
            print(f"       -> keys    : {keys}")
        print(f"       isolates   : {spec['isolates']}")
    print("-" * 70)


def print_wiring_summary(args, base_url, replay_date, profile_dir, out_dir):
    print("=" * 70)
    print("AEROPLAN SIGV4 REPLAY PROBE — WIRING SUMMARY")
    print("=" * 70)
    print(f"  Route             : {args.org} -> {args.dest}")
    print(f"  Boot date         : {args.date}")
    print(f"  Replay date       : {replay_date} (modified body departureDateTime)")
    print(f"  Replay flexibility: {args.flex} (SPA hardcodes 2 = 5 days)")
    print(f"  Boot URL          : {base_url}")
    print(f"  Profile dir       : {profile_dir}")
    print(f"  Out dir           : {out_dir}")
    print(f"  Results JSON      : {out_dir / 'sigv4_probe_results.json'}")
    print(f"  Captured request  : {out_dir / 'air-calendars_captured_request.json'} (redacted)")
    print(f"  Target substring  : {TARGET_SUBSTRING} (the SigV4-signed endpoint)")
    print(f"  Login host signal : {LOGIN_HOST} (redirect => session expired)")
    print(f"  Auto-login        : {'ON (--auto-login)' if getattr(args, 'auto_login', True) else 'OFF (--no-auto-login)'}")
    print("  Extra API calls   : 8 air-calendars replays in ONE session (CORS battery)")
    print("=" * 70)


# ----------------------------------------------------------------------------
# Dry-run self-test
# ----------------------------------------------------------------------------

def run_dry_run(args) -> int:
    """Exercise everything that does NOT require a browser, then exit 0."""
    print("DRY RUN — no browser will be launched. Validating wiring only.\n")

    base_url = build_availability_url(args.org, args.dest, args.date)
    replay_date = resolve_replay_date(args.date, args.replay_date)

    profile_dir = Path(args.profile_dir).resolve()
    out_dir = resolve_out_dir(args.out_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [ok] profile dir created/exists: {profile_dir}")
    print(f"  [ok] out dir created/exists    : {out_dir}")

    # ---- replay-date defaulting (date + 60d) + ISO formatting ----
    if args.replay_date is None:
        expected = (datetime.strptime(args.date, DATE_FMT)
                    + timedelta(days=DEFAULT_REPLAY_SHIFT_DAYS)).strftime(DATE_FMT)
        assert replay_date == expected, f"default replay-date wrong: {replay_date}"
    body_dt = body_datetime(replay_date)
    assert body_dt == f"{replay_date}T00:00:00.000", f"body dt format wrong: {body_dt}"
    print(f"  [ok] replay date     : {replay_date} (default = date + "
          f"{DEFAULT_REPLAY_SHIFT_DAYS}d when unset)")
    print(f"  [ok] body departureDateTime: {body_dt}")
    print(f"  [ok] boot URL        : {base_url}")

    # ---- Body modification against a SYNTHETIC captured body ----
    synthetic_body = {
        "searchPreferences": {"showUnavailableEntries": False, "showMilesPrice": True},
        "corporateCodes": ["REWARD"],
        "travelers": [{"passengerTypeCode": "ADT"}],
        "currencyCode": "CAD",
        "itineraries": [{
            "originLocationCode": "YYZ", "destinationLocationCode": "LAX",
            "departureDateTime": "2026-08-15T00:00:00.000",
            "isRequestedBound": True, "flexibility": 2,
            "commercialFareFamilies": ["RWDECO", "RWDPRECC", "RWDBUS", "RWDFIRST"],
        }],
        "frequentFlyer": {"cardNumber": "523781508", "companyCode": "AC",
                          "priorityCode": "9"},
    }
    modified = modify_body(synthetic_body, flex=args.flex, replay_date=replay_date)
    assert modified["itineraries"][0]["flexibility"] == args.flex, \
        f"flexibility not set: {modified['itineraries'][0].get('flexibility')}"
    assert modified["itineraries"][0]["departureDateTime"] == body_dt, \
        "departureDateTime not set on modified body"
    # Other fields preserved.
    assert modified["currencyCode"] == "CAD"
    assert modified["itineraries"][0]["originLocationCode"] == "YYZ"
    assert modified["itineraries"][0]["destinationLocationCode"] == "LAX"
    assert modified["itineraries"][0]["commercialFareFamilies"] == \
        ["RWDECO", "RWDPRECC", "RWDBUS", "RWDFIRST"]
    assert modified["frequentFlyer"]["companyCode"] == "AC"
    # Captured body untouched (deep copy).
    assert synthetic_body["itineraries"][0]["flexibility"] == 2, \
        "modify_body mutated the captured body!"
    assert synthetic_body["itineraries"][0]["departureDateTime"] == \
        "2026-08-15T00:00:00.000"
    print(f"  [ok] body modification: flexibility -> {args.flex}, "
          f"departureDateTime -> {body_dt}; all other fields preserved; "
          "captured body untouched")

    # ---- filter_fetch_headers ----
    captured_headers = {
        ":authority": "akamai-gw.dbaas.aircanada.com",
        ":method": "POST",
        ":path": "/loyalty/dapidynamic/1ASIUDALAC/v2/search/air-calendars",
        ":scheme": "https",
        "host": "api-gw.dbaas.aircanada.com",
        "content-length": "812",
        "connection": "keep-alive",
        "cookie": "bm_sz=abc; ak_bmsc=def",
        "origin": "https://www.aircanada.com",
        "referer": "https://www.aircanada.com/aeroplan/redeem/availability/outbound",
        "accept-encoding": "gzip, deflate, br",
        # Junk forbidden headers the prior probe wrongly forwarded:
        "user-agent": "Mozilla/5.0 ...",
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-CA,en;q=0.9",
        "priority": "u=1, i",
        "sec-ch-ua": '"Chromium";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        # Real auth headers we must KEEP:
        "authorization": "AWS4-HMAC-SHA256 Credential=...",
        "x-amz-date": "20260602T000000Z",
        "x-amz-security-token": "FwoGZXIvYXdz...",
        "x-custom-id-token": "eyJhbGciOi...",
        "x-api-key": "static-gateway-key-123",
        "ama-session-token": "amasess:1",
        "ama-client-ref": "amaref:2",
        "content-type": "application/json",
    }
    filtered = filter_fetch_headers(captured_headers)
    lower = {k.lower() for k in filtered}
    # Pseudo-headers gone.
    assert not any(k.startswith(":") for k in filtered), "pseudo-header survived"
    # Forbidden gone — including the junk forbidden headers + sec-* family.
    for forbidden in ("host", "content-length", "connection", "cookie", "origin",
                      "referer", "accept-encoding", "user-agent", "accept",
                      "accept-language", "priority"):
        assert forbidden not in lower, f"{forbidden} should be filtered out"
    assert not any(k.startswith("sec-") for k in lower), \
        f"sec-* header survived clean filter: {sorted(lower)}"
    # Auth headers kept.
    for kept in ("authorization", "x-amz-date", "x-amz-security-token",
                 "x-custom-id-token", "x-api-key", "ama-session-token",
                 "ama-client-ref", "content-type"):
        assert kept in lower, f"{kept} should be kept"
    print(f"  [ok] filter_fetch_headers (clean): stripped pseudo-headers + "
          f"host/cookie/origin/referer + user-agent/accept*/priority/sec-*; kept "
          f"{sorted(lower)}")

    # H_* set subsetting.
    h_full = {k.lower() for k in filter_fetch_headers(
        captured_headers, allow_keys=H_FULL_AUTH)}
    assert h_full == set(H_FULL_AUTH), f"H_full_auth wrong: {h_full}"
    drop = filter_fetch_headers(captured_headers, allow_keys=H_NO_SIGV4)
    drop_lower = {k.lower() for k in drop}
    assert "authorization" not in drop_lower, "H_no_sigv4 must drop authorization"
    assert "x-amz-security-token" not in drop_lower, "H_no_sigv4 must drop x-amz-security-token"
    assert drop_lower == set(H_NO_SIGV4), f"H_no_sigv4 wrong: {drop_lower}"
    ida = {k.lower() for k in filter_fetch_headers(
        captured_headers, allow_keys=H_IDTOKEN)}
    assert ida == {"x-custom-id-token", "x-api-key", "content-type"}, f"H_idtoken wrong: {ida}"
    ako = {k.lower() for k in filter_fetch_headers(
        captured_headers, allow_keys=H_APIKEY)}
    assert ako == {"x-api-key", "content-type"}, f"H_apikey wrong: {ako}"
    print(f"  [ok] H_* subsetting: H_full_auth={sorted(h_full)};\n"
          f"       H_no_sigv4={sorted(drop_lower)}; H_idtoken={sorted(ida)}; "
          f"H_apikey={sorted(ako)}")

    # ---- Redaction ----
    red_headers = redact_headers(captured_headers)
    assert red_headers["authorization"] == "***REDACTED***"
    assert red_headers["x-amz-security-token"] == "***REDACTED***"
    assert red_headers["x-custom-id-token"] == "***REDACTED***"
    assert red_headers["cookie"] == "***REDACTED***"
    # Non-secret headers preserved.
    assert red_headers["x-api-key"] == "static-gateway-key-123", "x-api-key must survive"
    assert red_headers["x-amz-date"] == "20260602T000000Z", "x-amz-date must survive"
    assert red_headers["ama-session-token"] == "amasess:1"
    red_body = redact_card_numbers(synthetic_body)
    assert red_body["frequentFlyer"]["cardNumber"] == "***REDACTED***", "cardNumber must redact"
    # Combined captured-record builder.
    captured_record = redact_captured(captured_headers, synthetic_body)
    assert captured_record["headers"]["authorization"] == "***REDACTED***"
    assert captured_record["body"]["frequentFlyer"]["cardNumber"] == "***REDACTED***"
    print("  [ok] redaction: masked authorization / x-amz-security-token / "
          "x-custom-id-token / cookie / cardNumber; kept x-api-key + x-amz-date")

    # ---- Experiment build sanity: the 8-experiment CORS matrix ----
    specs = experiment_specs()
    assert [s["name"] for s in specs] == [
        "control_exact_omit", "control_exact_include",
        "full_modbody_omit", "full_modbody_include",
        "drop_sigv4_include", "drop_sigv4_omit",
        "idtoken_include", "apikey_include",
    ], "experiment order/names wrong"
    assert len(specs) == 8, f"expected 8 experiments, got {len(specs)}"
    # control_exact_* use EXACT body; the other 6 use MODIFIED body.
    for s in specs:
        exp = build_experiment(s, captured_headers, synthetic_body, modified)
        if s["use_modified_body"]:
            assert exp["body"] is modified, f"{s['name']} must use MODIFIED body"
        else:
            assert exp["body"] is synthetic_body, f"{s['name']} must use EXACT body"
        assert exp["credentials"] in ("omit", "include"), \
            f"{s['name']} bad credentials: {exp['credentials']}"
    # Both credentials modes are exercised across the matrix.
    creds_modes = {s["credentials"] for s in specs}
    assert creds_modes == {"omit", "include"}, f"creds modes incomplete: {creds_modes}"
    # B_mod has the requested flex baked in (default 30).
    assert DEFAULT_FLEX == 30, f"DEFAULT_FLEX expected 30, got {DEFAULT_FLEX}"
    bmod_exp = build_experiment(specs[3], captured_headers, synthetic_body, modified)
    assert bmod_exp["body"]["itineraries"][0]["flexibility"] == args.flex, \
        "B_mod flexibility not applied to experiment body"
    print(f"  [ok] experiment build: 8-experiment CORS matrix; control_exact_* "
          f"use B_exact, the other 6 use B_mod (flex={args.flex}); both "
          f"credentials modes {sorted(creds_modes)} exercised")

    # ---- The 8 experiment definitions (with concrete header keys) ----
    print()
    print_experiment_definitions(captured_headers_example=captured_headers)

    print()
    print_wiring_summary(args, base_url, replay_date, profile_dir, out_dir)
    print("\nDRY RUN COMPLETE — wiring valid. (No browser launched.)")
    return 0


# ----------------------------------------------------------------------------
# Live run
# ----------------------------------------------------------------------------

def run_live(args) -> int:
    """Launch headed Chrome, manual-login poll, capture a real air-calendars
    POST, then run the 5 in-page replay experiments and print a verdict.

    Imports Playwright lazily so --dry-run/--help work even without Playwright.
    """
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as PWTimeout

    profile_dir = Path(args.profile_dir).resolve()
    out_dir = resolve_out_dir(args.out_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    runlog = RunLog(out_dir / "run.log")

    base_url = build_availability_url(args.org, args.dest, args.date)
    replay_date = resolve_replay_date(args.date, args.replay_date)

    print_wiring_summary(args, base_url, replay_date, profile_dir, out_dir)
    runlog("\nLIVE RUN — headed browser. A HUMAN must stay at the keyboard.")
    print("Auto-login (if enabled) reads creds from .env only; it NEVER solves "
          "Arkose and degrades to manual login on any failure.")
    print("This session fires ~8 EXTRA air-calendars calls (CORS battery).\n")

    kill_orphaned_chrome(profile_dir)
    time.sleep(1)  # let the OS release file locks
    runlog(f"launch: headed chrome, profile={profile_dir}")

    playwright = sync_playwright().start()
    # Persistent-context launch kwargs reused VERBATIM from cookie_farm.start().
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        channel="chrome",
        viewport={"width": 1280, "height": 800},
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )

    page = context.pages[0] if context.pages else context.new_page()

    # ---- Preflight capture: record EVERY air-calendars OPTIONS response ----
    # This is the critical instrument: the prior probe failed with `Failed to
    # fetch` (a CORS block), so we must see what the OPTIONS preflight returns.
    preflight_records = []

    def _on_response(resp):
        try:
            req = resp.request
            if req.method == "OPTIONS" and TARGET_SUBSTRING in resp.url:
                hdrs = {}
                try:
                    hdrs = resp.headers
                except Exception:
                    hdrs = {}
                lower = {str(k).lower(): v for k, v in (hdrs or {}).items()}
                preflight_records.append({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "status": resp.status,
                    "acao": lower.get("access-control-allow-origin"),
                    "acam": lower.get("access-control-allow-methods"),
                    "acah": lower.get("access-control-allow-headers"),
                    "acac": lower.get("access-control-allow-credentials"),
                })
        except Exception:
            log.debug("preflight on_response handler failed", exc_info=True)

    try:
        context.on("response", _on_response)
    except Exception:
        log.debug("could not attach preflight listener", exc_info=True)

    exit_code = 0
    results = []
    verdict = "NO REPLAY PATH FOUND — fall back to navigate-per-5-days."
    extra_call_count = 0
    try:
        # ----- 1. Ensure logged in (manual) -----
        print(">>> Navigating to aircanada.com to check login state...")
        try:
            page.goto(f"{AVAILABILITY_HOST}/", wait_until="domcontentloaded",
                      timeout=60000)
        except Exception as exc:
            print(f"    initial navigate raised: {exc}")
        time.sleep(SETTLE_SECONDS)

        if is_logged_in(page, context):
            runlog("login-detected: already logged in (reusing persistent profile).")
        else:
            logged_in = False
            if args.auto_login:
                runlog(">>> Attempting best-effort auto-login (--auto-login).")
                try:
                    logged_in = attempt_auto_login(page, context, runlog)
                except Exception as exc:
                    runlog(f"    auto-login raised unexpectedly ({exc}) — "
                           "falling back to manual.")
                    logged_in = False
                if logged_in:
                    runlog("login-detected: auto-login confirmed.")
            else:
                runlog(">>> Auto-login disabled (--no-auto-login); manual only.")

            if not logged_in:
                # Ultimate fallback: human completes login in the browser.
                if not wait_for_manual_login(page, context):
                    runlog("Aborting: not logged in.")
                    _teardown(context, playwright)
                    return 1
                runlog("login-detected: manual login confirmed.")

        # ----- 1b. Settle the post-login redirect chain BEFORE the boot navigate -----
        wait_post_login_settle(page, runlog=runlog)

        # ----- 2. Boot + capture a real air-calendars POST -----
        capture_url = None
        captured_headers = None
        captured_body = None
        runlog(f"\n>>> BOOT NAVIGATE (date={args.date}) -> {base_url.split('?')[0]}")
        runlog("    (loud step: a human should stay in the loop here)")
        try:
            with page.expect_response(
                lambda r: TARGET_SUBSTRING in r.url and r.request.method == "POST",
                timeout=args.capture_timeout * 1000,
            ) as info:
                page.goto(base_url, wait_until="domcontentloaded", timeout=45000)
            resp = info.value
            req = resp.request
            capture_url = req.url
            runlog(f"    [capture] air-calendars {resp.status}  {capture_url.split('?')[0]}")
            # Read EVERYTHING IMMEDIATELY (before any further navigation).
            captured_headers = req.all_headers()
            try:
                captured_body = req.post_data_json
            except Exception:
                captured_body = None
            if captured_body is None:
                raw = req.post_data
                captured_body = json.loads(raw) if raw else None
            baseline_body = resp.json()
            baseline = day_count_from_body(baseline_body)
            runlog(f"    baseline: status={resp.status} "
                   f"days={baseline['day_count']} range={baseline['date_range']}")
        except PWTimeout as exc:
            runlog(f"    !! timeout capturing baseline air-calendars: {exc}")
        except Exception as exc:
            runlog(f"    !! capture failed: {exc}")

        landed = page.url or ""
        if LOGIN_HOST in landed:
            runlog(f"    !! redirected to login host ({LOGIN_HOST}) — session expired")

        if not (capture_url and isinstance(captured_headers, dict)
                and isinstance(captured_body, dict)):
            runlog("ABORT: could not capture a usable baseline air-calendars "
                   "request (headers/body/url). Cannot run replay experiments.")
            _save_results(out_dir, runlog, args, replay_date, capture_url,
                          captured_headers, captured_body, results,
                          "ABORT: no baseline captured.", extra_call_count,
                          preflight_records)
            _teardown(context, playwright)
            return 1

        # Save the redacted captured request immediately (so we keep it even if
        # the experiments below fail).
        try:
            (out_dir / "air-calendars_captured_request.json").write_text(
                json.dumps({
                    "url": capture_url,
                    **redact_captured(captured_headers, captured_body),
                }, indent=2, ensure_ascii=False),
                encoding="utf-8")
            runlog(f"    saved redacted captured request: "
                   f"{out_dir / 'air-calendars_captured_request.json'}")
        except Exception as exc:
            runlog(f"    ERROR saving captured request: {exc}")

        # ----- 3. Build modified body -----
        modified_body = modify_body(captured_body, flex=args.flex,
                                    replay_date=replay_date)
        runlog(f"    modified body: flexibility={args.flex} "
               f"departureDateTime={body_datetime(replay_date)}")

        # ----- 4. Run the 8-experiment CORS battery IN ONE SESSION -----
        runlog("\n>>> RUNNING CORS BATTERY (in-page fetch, one session)")
        for spec in experiment_specs():
            exp = build_experiment(spec, captured_headers, captured_body,
                                   modified_body)
            runlog(f"  -- {spec['name']} [{spec['header_set_name']}, "
                   f"creds={spec['credentials']}]: "
                   f"headers={sorted(exp['headers'].keys())}")
            # Snapshot the preflight count before the call so we can associate
            # the MOST RECENT new OPTIONS record with this experiment.
            pf_before = len(preflight_records)
            result = run_experiment_in_page(page, capture_url, exp)
            extra_call_count += 1
            result["spec"] = spec["name"]
            result["header_set"] = spec["header_set_name"]
            result["credentials"] = spec["credentials"]
            result["used_modified_body"] = spec["use_modified_body"]
            # Associate the most recent OPTIONS preflight (if any new one fired).
            if len(preflight_records) > pf_before:
                result["preflight"] = preflight_records[-1]
            elif preflight_records:
                result["preflight"] = preflight_records[-1]
            else:
                result["preflight"] = None
            results.append(result)
            pf = result.get("preflight") or {}
            runlog(f"     status={result.get('status')} "
                   f"ok={result.get('ok')} "
                   f"dayCount={result.get('dayCount')} "
                   f"dateRange={result.get('dateRange')} "
                   f"bodyLen={result.get('bodyLen')} "
                   f"error={result.get('error')}")
            runlog(f"     preflight: status={pf.get('status')} "
                   f"acao={pf.get('acao')} acah={pf.get('acah')} "
                   f"acac={pf.get('acac')}")
            # Small settle between calls so we stay gentle on the account.
            page.wait_for_timeout(1500)

        # ----- 5. Verdict -----
        verdict = _build_verdict(results, preflight_records)

        # ----- table -----
        runlog("\n" + "=" * 78)
        runlog("SIGV4 / CORS BATTERY — RESULTS")
        runlog("=" * 78)
        runlog(f"  {'experiment':<22} {'creds':>8} {'status':>6} {'err':<18} "
               f"preflight(acao/acah/acac)")
        runlog("  " + "-" * 74)
        for r in results:
            pf = r.get("preflight") or {}
            pf_str = (f"{pf.get('acao')}/{pf.get('acah')}/{pf.get('acac')}"
                      if pf else "-")
            err = (r.get("error") or "")[:17]
            runlog(f"  {r.get('spec',''):<22} {str(r.get('credentials')):>8} "
                   f"{str(r.get('status')):>6} {err:<18} {pf_str}")
        runlog("  " + "-" * 74)
        runlog(f"  extra air-calendars calls fired this session: {extra_call_count}")
        runlog(f"  OPTIONS preflight records captured: {len(preflight_records)}")
        runlog("=" * 78)
        runlog(f"VERDICT: {verdict}")

        _save_results(out_dir, runlog, args, replay_date, capture_url,
                      captured_headers, captured_body, results, verdict,
                      extra_call_count, preflight_records)

    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl-C). Tearing down cleanly.")
    finally:
        _teardown(context, playwright)

    print("\n" + "=" * 70)
    print(f"Results JSON : {out_dir / 'sigv4_probe_results.json'}")
    print(f"Captured req : {out_dir / 'air-calendars_captured_request.json'} (redacted)")
    print(f"Run log      : {out_dir / 'run.log'}")
    runlog(f"exit: code={exit_code}")
    print("=" * 70)
    return exit_code


def _build_verdict(results, preflight_records) -> str:
    """Derive the VERDICT string from the battery results + preflight records.

    Priority:
      1. First experiment that returned 200 with a WIDE day-count (>5) =>
         the usable in-page-replay path.
      2. Else if a control_exact_* returned 200 but modified-body 403s =>
         SigV4 is body-bound, replay blocked.
      3. Else if EVERY experiment is `Failed to fetch` even after clean headers
         => report the captured preflight ACA-* values (the CORS rejection).
      4. Else a generic no-path message.
    """
    def _is_failed_to_fetch(r):
        err = (r.get("error") or "")
        return "Failed to fetch" in err or "TypeError" in err

    # 1. Usable wide-window replay (modified body, 200, >5 days).
    for r in results:
        if (r.get("used_modified_body") and r.get("status") == 200
                and isinstance(r.get("dayCount"), int) and r["dayCount"] > 5):
            return (f"USABLE IN-PAGE REPLAY: '{r['spec']}' returned 200 with "
                    f"{r['dayCount']} days ({r.get('dateRange')}). Fat-"
                    f"flexibility in-page scraping is viable — 'one boot -> a "
                    f"few huge calls'.")

    # 2. control_exact 200 but modified-body 403 => SigV4 body-bound.
    control_200 = any(
        (not r.get("used_modified_body")) and r.get("status") == 200
        for r in results)
    modbody_403 = any(
        r.get("used_modified_body") and r.get("status") == 403
        for r in results)
    if control_200 and modbody_403:
        return ("SigV4 body-bound, replay blocked: control_exact_* returned 200 "
                "but the modified body 403s. The signature covers the body hash "
                "-> fall back to navigate-per-5-days.")

    # 3. All Failed to fetch => report the CORS preflight ACA-* values.
    if results and all(_is_failed_to_fetch(r) for r in results):
        if preflight_records:
            pf = preflight_records[-1]
            return ("ALL `Failed to fetch` even after clean headers — a CORS "
                    f"block. Preflight OPTIONS status={pf.get('status')} "
                    f"acao={pf.get('acao')} acam={pf.get('acam')} "
                    f"acah={pf.get('acah')} acac={pf.get('acac')}. The endpoint "
                    "does not CORS-allow this in-page origin/headers -> in-page "
                    "fetch replay is not viable; drive the SPA instead.")
        return ("ALL `Failed to fetch` even after clean headers, and NO OPTIONS "
                "preflight was captured. The browser blocked the request before "
                "preflight (forbidden header / mixed content) OR no preflight "
                "fired -> in-page fetch replay is not viable.")

    # 4. Generic.
    return ("NO usable wide-window replay: no modified-body strategy returned "
            "200 with >5 days, and the failure mode isn't a clean body-bound "
            "403 or uniform CORS block. Inspect per-experiment status/preflight "
            "in the results JSON.")


def _save_results(out_dir, runlog, args, replay_date, capture_url,
                  captured_headers, captured_body, results, verdict,
                  extra_call_count, preflight_records=None) -> None:
    """Write sigv4_probe_results.json (redacted captured request inline)."""
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "route": f"{args.org}-{args.dest}",
        "boot_date": args.date,
        "replay_date": replay_date,
        "flex": args.flex,
        "capture_url": capture_url,
        "captured_request": (
            redact_captured(captured_headers, captured_body)
            if isinstance(captured_headers, dict) else None),
        "experiments": results,
        "preflight_records": preflight_records or [],
        "extra_calls_fired": extra_call_count,
        "verdict": verdict,
    }
    try:
        (out_dir / "sigv4_probe_results.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        runlog(f"saved results: {out_dir / 'sigv4_probe_results.json'}")
    except Exception as exc:
        runlog(f"ERROR saving results JSON: {exc}")


def _teardown(context, playwright):
    try:
        context.close()
    except Exception:
        log.debug("context close failed", exc_info=True)
    try:
        playwright.stop()
    except Exception:
        log.debug("playwright stop failed", exc_info=True)


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def main(argv=None) -> int:
    # Observability: make stdout/stderr line-buffered so background monitoring
    # sees progress live instead of in block-buffered chunks.
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
            "TLS/HTTP2 fingerprinting. This spike is HEADED-ONLY by design. "
            "Re-run without --headless.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        return run_dry_run(args)

    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())
