"""Aeroplan air-calendars recon — instrumented, account-safe Playwright harness.

Focuses on the `air-calendars` PRICE STRIP ONLY (ignores air-bounds / polldapi).
It answers three questions needed to design a RANGE scrape over the calendar:

  1. Date-window mapping — for a requested `departureDate0`, which dates does the
     strip return, and how many?
  2. Flexibility — capture the SPA's ACTUAL air-calendars REQUEST body to read
     the `flexibility` value it sends; AND test whether injecting flexibility-
     style params into the URL (`flexibility`, `flex`, `calendarFlexibility`)
     changes what comes back.
  3. Tiling — step the URL date across N windows and report combined COVERAGE
     (gaps / overlaps) so we can derive "days per request + step size".

ACCOUNT SAFETY (baked in):
  - Headed-only. --headless is REFUSED (Akamai/Arkose make headless non-viable).
  - Handles ZERO credentials: never reads/writes credentials, never reads
    credential env vars, never touches Arkose. Reuses a MANUALLY-authenticated
    persistent browser profile.
  - --windows hard-capped at 12; single account; single route; loud console
    output so a human stays in the loop.

This is a SPIKE, not a scraper. Out of scope: air-bounds/polldapi, page.evaluate
in-page transport, round-trip / multi-pax, DB writes, login automation, Arkose.

Usage (live run — MANUAL, human-in-the-loop; do NOT automate):
    .venv\\Scripts\\python.exe scripts\\experiments\\aeroplan_calendar_recon.py \\
        --org YYZ --dest LAX --date 2026-08-15 --windows 3 --step-days 5

Usage (offline self-test — no browser launched):
    .venv\\Scripts\\python.exe scripts\\experiments\\aeroplan_calendar_recon.py --dry-run
"""

import argparse
import json
import logging
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

# The ONLY endpoint substring we capture for this recon (recon §3: price strip).
TARGET_SUBSTRING = "air-calendars"

# Availability URL host + path family (recon §3 / transport spike). A cold
# navigate loads the SPA, which then fires the signed air-calendars call itself.
AVAILABILITY_HOST = "https://www.aircanada.com"
AVAILABILITY_PATH = "/aeroplan/redeem/availability/outbound"

# Expiry / redirect signal: a redirect to this host means the session lapsed
# (recon §2: "Login host: login.aircanada.com").
LOGIN_HOST = "login.aircanada.com"

# Hard cap on --windows (account safety — keep a normal-looking session).
WINDOWS_CAP = 12

# Settle pause after navigate, mirroring cookie_farm's "let the SPA settle
# 2-3s before touching the DOM" discipline.
SETTLE_SECONDS = 3

# Date format used by the URL builder and all window arithmetic.
DATE_FMT = "%Y-%m-%d"


# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aeroplan_calendar_recon.py",
        description=(
            "Aeroplan air-calendars recon — headed, account-safe Playwright "
            "harness. Maps the calendar price strip's date window, reads the "
            "SPA's flexibility, tests URL flexibility injection, and tiles "
            "windows to derive gaps/overlaps. Handles ZERO credentials."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--org", default="YYZ",
                   help="Origin airport code (origin of the outbound).")
    p.add_argument("--dest", default="LAX",
                   help="Destination airport code.")
    p.add_argument("--date", default="2026-08-15",
                   help="Base departure date (YYYY-MM-DD), near-term.")
    p.add_argument("--flexibility", type=int, default=None,
                   help=("Optional. When set, INJECT candidate flexibility "
                         "params into the URL (flexibility=<n>, flex=<n>, "
                         "calendarFlexibility=<n>) to test whether the SPA "
                         "honors them. Does NOT change the SPA's own request "
                         "body flexibility (which we capture separately)."))
    p.add_argument("--step-days", type=int, default=5,
                   help="Days to advance the URL date between tiled windows.")
    p.add_argument("--windows", type=int, default=1,
                   help=(f"Number of windows to scan. >1 = tile mode. Hard "
                         f"cap {WINDOWS_CAP} for account safety."))
    p.add_argument("--capture-timeout", type=float, default=35.0,
                   help=("Seconds to wait (polling the stash ~1s) after "
                         "navigate for the air-calendars response before "
                         "recording the window. Records whatever is captured "
                         "on timeout — never hard-fails."))
    p.add_argument("--dry-run", action="store_true",
                   help=("Offline self-test: arg parsing, URL build WITH and "
                         "WITHOUT flexibility injection, dir creation, the "
                         "synthetic air-calendars parse, coverage/gap/overlap "
                         "computation, and redaction — WITHOUT a browser. "
                         "Prints a wiring summary and exits 0."))
    p.add_argument("--headless", action="store_true",
                   help=("REFUSED. Headless is non-viable against Air Canada's "
                         "Akamai/Arkose stack; passing this prints a refusal "
                         "and exits non-zero."))
    p.add_argument("--profile-dir",
                   default=str(SCRIPT_DIR / ".aeroplan-profile"),
                   help="Persistent browser profile dir (manual login persists).")
    p.add_argument("--out-dir", default=None,
                   help=("Output dir for calendar_recon.jsonl + redacted "
                         "payloads. Defaults to logs/aeroplan_calendar_<ts>."))
    return p


def resolve_out_dir(arg_out_dir) -> Path:
    if arg_out_dir:
        return Path(arg_out_dir).resolve()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (REPO_ROOT / "logs" / f"aeroplan_calendar_{ts}").resolve()


# ----------------------------------------------------------------------------
# Availability URL builder (from recon path family) + flexibility injection
# ----------------------------------------------------------------------------

def build_availability_url(org: str, dest: str, date: str,
                           flexibility: int = None) -> str:
    """Build the Aeroplan outbound-availability URL from org/dest/date.

    Standard params (recon §3 / transport spike):
      /aeroplan/redeem/availability/outbound
        ?org0=YYZ&dest0=LAX&departureDate0=2026-08-15&lang=en-CA&tripType=O
         &ADT=1&YTH=0&CHD=0&INF=0&INS=0&marketCode=DOM

    When `flexibility` is set, INJECT three candidate param names so we can test
    empirically whether the SPA honors any of them: `flexibility`, `flex`,
    `calendarFlexibility`. The SPA may ignore all three (its real flexibility
    lives in the POST body, which we capture separately) — that null result is
    itself the answer.
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
    if flexibility is not None:
        # Candidate injection — three plausible param spellings at once.
        params["flexibility"] = str(flexibility)
        params["flex"] = str(flexibility)
        params["calendarFlexibility"] = str(flexibility)
    return f"{AVAILABILITY_HOST}{AVAILABILITY_PATH}?{urlencode(params)}"


def injected_flexibility_params(flexibility: int = None):
    """Return the dict of URL params we injected for this flexibility (or None).

    Recorded per-window so the JSONL shows exactly what we tried.
    """
    if flexibility is None:
        return None
    return {
        "flexibility": str(flexibility),
        "flex": str(flexibility),
        "calendarFlexibility": str(flexibility),
    }


def window_date(base_date: str, window_index: int, step_days: int) -> str:
    """Date string for window i: base_date + i*step_days (datetime arithmetic)."""
    base = datetime.strptime(base_date, DATE_FMT)
    return (base + timedelta(days=window_index * step_days)).strftime(DATE_FMT)


# ----------------------------------------------------------------------------
# JSONL logger
# ----------------------------------------------------------------------------

class JsonlLogger:
    """Append-only JSONL writer for one window record per line."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Touch the file so init failures surface immediately (dry-run sanity).
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            log.debug("jsonl close failed", exc_info=True)


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

def redact_card_numbers(obj):
    """Defensively mask the Aeroplan card / member number in a captured payload.

    Masks any value under a `frequentFlyer.cardNumber` key, and any key that
    LOOKS like a card-number / member-number field (cardNumber, aeroplanNumber,
    memberNumber, userId, etc.) anywhere in the tree. A live run leaked
    `"userId": "523781508"` (the Aeroplan number), so `userId` is explicitly
    covered. Recurses through dicts/lists; returns a NEW structure.
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


# ----------------------------------------------------------------------------
# air-calendars RESPONSE parsing
# ----------------------------------------------------------------------------

def parse_calendar_response(body) -> dict:
    """Parse an air-calendars RESPONSE body into per-date cheapest-economy fares.

    Response shape (recon §3): `{ data[], meta, dictionaries }`, one `data` entry
    per `departureDate`. The cheapest economy fare for a date is the MIN over
    `prices.unitPrices[].milesConversion.convertedMiles.base`, carrying along its
    sibling `totalTaxes` (cents CAD).

    Defensive: any missing entry/path yields a null fare for that date rather
    than raising. Returns:
        {
          "returned_dates": [sorted date strings],
          "day_count": int,
          "window_min": str|None,
          "window_max": str|None,
          "fares": { date: {"miles": int|None, "taxes_cents": int|None} },
        }
    """
    fares = {}
    try:
        data = body.get("data") if isinstance(body, dict) else None
    except Exception:
        data = None
    if not isinstance(data, list):
        data = []

    for entry in data:
        if not isinstance(entry, dict):
            continue
        date = entry.get("departureDate")
        if not isinstance(date, str):
            continue
        miles, taxes = _cheapest_economy(entry)
        # If a date appears twice, keep the cheaper miles.
        existing = fares.get(date)
        if existing is None:
            fares[date] = {"miles": miles, "taxes_cents": taxes}
        elif miles is not None and (
                existing["miles"] is None or miles < existing["miles"]):
            fares[date] = {"miles": miles, "taxes_cents": taxes}

    returned_dates = sorted(fares.keys())
    return {
        "returned_dates": returned_dates,
        "day_count": len(returned_dates),
        "window_min": returned_dates[0] if returned_dates else None,
        "window_max": returned_dates[-1] if returned_dates else None,
        "fares": fares,
    }


def _cheapest_economy(entry: dict):
    """Min miles over prices.unitPrices[].milesConversion.convertedMiles.base for
    one data entry, with the sibling totalTaxes. Returns (miles, taxes_cents),
    either of which may be None if the paths are missing/malformed.
    """
    best_miles = None
    best_taxes = None
    try:
        prices = entry.get("prices") if isinstance(entry, dict) else None
        unit_prices = prices.get("unitPrices") if isinstance(prices, dict) else None
        if not isinstance(unit_prices, list):
            return (None, None)
        for up in unit_prices:
            if not isinstance(up, dict):
                continue
            mc = up.get("milesConversion")
            if not isinstance(mc, dict):
                continue
            cm = mc.get("convertedMiles")
            if not isinstance(cm, dict):
                continue
            base = cm.get("base")
            if not isinstance(base, (int, float)):
                continue
            taxes = cm.get("totalTaxes")
            if best_miles is None or base < best_miles:
                best_miles = base
                best_taxes = taxes if isinstance(taxes, (int, float)) else None
    except Exception:
        log.debug("cheapest-economy parse failed", exc_info=True)
        return (None, None)
    return (best_miles, best_taxes)


# ----------------------------------------------------------------------------
# air-calendars REQUEST parsing (read the SPA's actual flexibility)
# ----------------------------------------------------------------------------

def extract_sent_flexibility(request) -> dict:
    """Read the SPA's ACTUAL air-calendars REQUEST body to find the flexibility
    it sent (recon §3: `itineraries[0].flexibility`).

    Tries `request.post_data_json` first, then falls back to `post_data` parsed
    via json.loads. Returns:
        { "sent_flexibility": int|None, "request_body": dict|None }
    The raw (UN-redacted) body is returned for the caller to redact before any
    on-disk persistence. Best-effort; never raises.
    """
    body = None
    try:
        body = request.post_data_json
    except Exception:
        body = None
    if body is None:
        try:
            raw = request.post_data
            if raw:
                body = json.loads(raw)
        except Exception:
            body = None

    sent = None
    try:
        if isinstance(body, dict):
            itins = body.get("itineraries")
            if isinstance(itins, list) and itins and isinstance(itins[0], dict):
                flex = itins[0].get("flexibility")
                if isinstance(flex, (int, float)):
                    sent = flex
    except Exception:
        log.debug("flexibility extraction failed", exc_info=True)
    return {"sent_flexibility": sent, "request_body": body if isinstance(body, dict) else None}


# ----------------------------------------------------------------------------
# Coverage / gap / overlap computation across windows
# ----------------------------------------------------------------------------

def compute_coverage(window_returned_dates) -> dict:
    """Given a list of per-window `returned_dates` (each a list of YYYY-MM-DD),
    compute the combined coverage across all windows.

    Returns:
        {
          "distinct_dates": [sorted union],
          "total_distinct": int,
          "union_min": str|None,
          "union_max": str|None,
          "gaps": [missing calendar days between union_min and union_max],
          "overlaps": [dates returned by >1 window],
        }

    GAPS are calendar days in [union_min, union_max] that NO window returned.
    OVERLAPS are dates returned by MORE THAN ONE window.
    """
    union = set()
    counts = {}
    for dates in window_returned_dates:
        for d in dates:
            union.add(d)
            counts[d] = counts.get(d, 0) + 1

    distinct = sorted(union)
    overlaps = sorted(d for d, c in counts.items() if c > 1)

    gaps = []
    if distinct:
        lo = datetime.strptime(distinct[0], DATE_FMT)
        hi = datetime.strptime(distinct[-1], DATE_FMT)
        cur = lo
        present = set(distinct)
        while cur <= hi:
            s = cur.strftime(DATE_FMT)
            if s not in present:
                gaps.append(s)
            cur += timedelta(days=1)

    return {
        "distinct_dates": distinct,
        "total_distinct": len(distinct),
        "union_min": distinct[0] if distinct else None,
        "union_max": distinct[-1] if distinct else None,
        "gaps": gaps,
        "overlaps": overlaps,
    }


# ----------------------------------------------------------------------------
# Wiring summary (dry-run + live banner)
# ----------------------------------------------------------------------------

def print_wiring_summary(args, base_url, injected_url, profile_dir, out_dir):
    print("=" * 70)
    print("AEROPLAN CALENDAR RECON — WIRING SUMMARY")
    print("=" * 70)
    print(f"  Route             : {args.org} -> {args.dest} base {args.date}")
    print(f"  URL (no inject)   : {base_url}")
    if injected_url is not None:
        print(f"  URL (--flexibility {args.flexibility} injected):")
        print(f"      {injected_url}")
        print(f"  Injected params   : {injected_flexibility_params(args.flexibility)}")
    else:
        print("  URL (--flexibility): not set — no injection")
    print(f"  Profile dir       : {profile_dir}")
    print(f"  Out dir           : {out_dir}")
    print(f"  Recon JSONL       : {out_dir / 'calendar_recon.jsonl'}")
    print(f"  Target substring  : {TARGET_SUBSTRING} (ONLY)")
    print(f"  Login host signal : {LOGIN_HOST} (redirect => session expired)")
    print("  Tiling            :")
    print(f"      windows         : {args.windows} (cap {WINDOWS_CAP})")
    print(f"      step-days       : {args.step_days}")
    print(f"      capture-timeout : {args.capture_timeout} s")
    # Show the window date schedule.
    n = min(max(1, args.windows), WINDOWS_CAP)
    schedule = [window_date(args.date, i, args.step_days) for i in range(n)]
    print(f"      window dates    : {schedule}")
    print("=" * 70)


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
# Response interceptor (CRITICAL pattern)
# ----------------------------------------------------------------------------

class ResponseStash:
    """Stash matching air-calendars Response objects from the context-level
    handler.

    CRITICAL: inside the sync on("response") handler we read ONLY the sync
    properties response.url and response.status. We DO NOT call .json()/.body()
    inside the handler — that can DEADLOCK the driver. Bodies (and request
    bodies) are drained OUTSIDE the handler after a bounded wait. The page never
    reaches networkidle, so we drive capture entirely off this stash.
    """

    # Substrings that mark a response as "network-debug-worthy" — logged to
    # network_debug.log regardless of whether the target matcher fires.
    DEBUG_URL_MARKERS = ("dapi", "loyalty", "air-calendars")

    def __init__(self, debug_log_path: Path = None):
        # list of (url, status, response_obj)
        self.captured = []
        # Sticky record of expiry: (url, status) of the first 401/403 seen.
        self.expiry_status = None
        self.debug_log_path = debug_log_path
        if self.debug_log_path is not None:
            try:
                self.debug_log_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                log.debug("could not create network_debug.log parent", exc_info=True)

    def _debug_log(self, response) -> None:
        """Append a ground-truth line for aircanada/dapi responses. SYNC PROPERTY
        READS ONLY (url/status/request.method). Never .json()/.body() here.
        """
        if self.debug_log_path is None:
            return
        try:
            url = response.url
            host_is_ac = "aircanada" in url
            marker_hit = any(m in url for m in self.DEBUG_URL_MARKERS)
            if not (host_is_ac or marker_hit):
                return
            try:
                method = response.request.method
            except Exception:
                method = "?"
            ts = datetime.now().isoformat(timespec="seconds")
            line = f"{ts} {method} {response.status} {url}\n"
            with open(self.debug_log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:
            log.debug("network_debug write failed", exc_info=True)

    def handler(self, response):
        # SYNC PROPERTY READS ONLY — never touch the body here.
        url = response.url
        status = response.status
        self._debug_log(response)
        if TARGET_SUBSTRING in url:
            self.captured.append((url, status, response))
            print(f"    [intercept] {status}  {TARGET_SUBSTRING}  "
                  f"({url.split('?')[0]})")
            if status in (401, 403) and self.expiry_status is None:
                self.expiry_status = (url, status)

    def clear(self):
        self.captured = []
        # expiry_status is intentionally sticky across clears.

    def seen(self) -> bool:
        """True if any air-calendars response is currently stashed."""
        return bool(self.captured)

    def latest_ok_response(self):
        """Return the most recent 2xx air-calendars Response, else the most
        recent of any status, else None. Body read happens OUTSIDE the handler.
        """
        ok = [r for (_u, st, r) in self.captured if 200 <= st < 300]
        if ok:
            return ok[-1]
        if self.captured:
            return self.captured[-1][2]
        return None


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


# Substrings that mark a URL as still being in the login / post-login redirect
# chain (recon §2). While page.url contains any of these, the SPA has not yet
# landed on the availability app and navigating would be interrupted.
LOGIN_REDIRECT_MARKERS = (
    "clogin", "afterLogin", "login.aircanada", "socialize", "idpresponse",
)


def _is_login_redirect_url(url: str) -> bool:
    """True if `url` is still on a login / post-login redirect page."""
    if not url:
        return False
    return any(marker in url for marker in LOGIN_REDIRECT_MARKERS)


def wait_post_login_settle(page, runlog=None) -> None:
    """Settle after login BEFORE the first window.

    Air Canada's post-login redirect chain (e.g. clogin/pages/proxy?mode=
    afterLogin) keeps navigating for a moment after login is detected. If we
    start window 1 mid-chain, `page.goto` is interrupted by that redirect and
    the window captures nothing. So poll `page.url` (up to ~20s) until it is NO
    LONGER on a login/redirect URL AND has been stable for ~2s, then settle an
    extra ~3s.
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

    # Extra fixed settle once the redirect chain has quiesced.
    time.sleep(3)
    try:
        final_url = page.url or ""
    except Exception:
        final_url = ""
    _emit(f"post-login settle: {final_url}")


# ----------------------------------------------------------------------------
# Per-window capture
# ----------------------------------------------------------------------------

def wait_for_calendar_xhr(stash: ResponseStash, capture_timeout: float) -> None:
    """Bounded wait for the air-calendars response before draining.

    The page never reaches network-idle (continuous heartbeat beacons), so we
    poll the stash ~every 1s for up to `capture_timeout` seconds until an
    air-calendars response is seen. We NEVER hard-fail: on timeout we return and
    the caller records whatever was captured.
    """
    deadline = time.time() + max(0.0, capture_timeout)
    print(f"    waiting up to {capture_timeout:.0f}s for the air-calendars XHR...")
    while time.time() < deadline:
        if stash.seen():
            break
        time.sleep(1.0)
    print(f"    capture wait done; air-calendars seen: {stash.seen()}")


def scan_window(page, stash: ResponseStash, url: str, win_label: str,
                requested_date: str, flexibility: int,
                capture_timeout: float, runlog=None) -> dict:
    """Navigate one window, capture the air-calendars response + request, and
    build the per-window recon dict.

    EAGER CAPTURE (Fix 2): the response body is read the INSTANT the
    air-calendars response arrives, while it is still valid. The old approach
    ("navigate, then poll the stash, then drain .json() later") let Chrome evict
    the body before the late read — `Network.getResponseBody: No resource with
    given identifier found`. Here we wrap the navigation in `page.expect_response`
    and read both the request flexibility and the response JSON immediately,
    BEFORE any subsequent navigation. The context-level `stash` listener is kept
    only for its network_debug.log side-effect; data capture no longer relies on
    it for the calendar response.

    Returns the per-window dict (also has an internal `_redirected_to_login`
    flag and the raw redacted response/request for the first-window save).
    """
    # Imported lazily here (only the live path calls scan_window).
    from playwright.sync_api import TimeoutError as PWTimeout

    def _emit(msg):
        (runlog or print)(msg)

    stash.clear()
    _emit(f"\n>>> [{win_label}] COLD NAVIGATE (date={requested_date}) -> "
          f"{url.split('?')[0]}")
    _emit("    (loud step: a human should stay in the loop here)")

    redirected_to_login = False
    parsed = {
        "returned_dates": [], "day_count": 0,
        "window_min": None, "window_max": None, "fares": {},
    }
    sent = {"sent_flexibility": None, "request_body": None}
    raw_response_redacted = None
    raw_request_redacted = None
    reason = None

    try:
        # Wrap the navigation in the response wait so we are already listening
        # when the SPA fires the signed air-calendars POST.
        with page.expect_response(
            lambda r: TARGET_SUBSTRING in r.url and r.request.method == "POST",
            timeout=capture_timeout * 1000,
        ) as info:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        resp = info.value
        _emit(f"    [eager] captured air-calendars response {resp.status}")
        # READ EVERYTHING IMMEDIATELY — before any further navigation evicts the
        # body. Request flexibility first (cheap), then the response JSON.
        sent = extract_sent_flexibility(resp.request)
        if sent.get("request_body") is not None:
            raw_request_redacted = redact_card_numbers(sent["request_body"])
        body = resp.json()  # read NOW, while the body is still resident.
        parsed = parse_calendar_response(body)
        raw_response_redacted = redact_card_numbers(body)
    except PWTimeout as exc:
        reason = f"timeout waiting for air-calendars response: {exc}"
        _emit(f"    !! {reason}")
    except Exception as exc:
        reason = str(exc)
        _emit(f"    !! capture failed: {reason}")

    landed = page.url or ""
    if LOGIN_HOST in landed:
        redirected_to_login = True
        _emit(f"    !! redirected to login host ({LOGIN_HOST}) — session expired")

    record = {
        "requested_date": requested_date,
        "injected_flexibility_params": injected_flexibility_params(flexibility),
        "sent_flexibility": sent.get("sent_flexibility"),
        "returned_dates": parsed["returned_dates"],
        "day_count": parsed["day_count"],
        "window_min": parsed["window_min"],
        "window_max": parsed["window_max"],
        "fares": parsed["fares"],
        # internal — stripped before JSONL write helpers below if needed.
        "_redirected_to_login": redirected_to_login,
        "_raw_response_redacted": raw_response_redacted,
        "_raw_request_redacted": raw_request_redacted,
    }
    if reason is not None:
        record["reason"] = reason

    _emit(f"    window summary    : requested={requested_date} "
          f"sent_flexibility={record['sent_flexibility']} "
          f"days={record['day_count']} "
          f"range={record['window_min']}..{record['window_max']}")
    return record


# ----------------------------------------------------------------------------
# Dry-run self-test
# ----------------------------------------------------------------------------

def run_dry_run(args) -> int:
    """Exercise everything that does NOT require a browser, then exit 0."""
    print("DRY RUN — no browser will be launched. Validating wiring only.\n")

    base_url = build_availability_url(args.org, args.dest, args.date)
    injected_url = (build_availability_url(args.org, args.dest, args.date,
                                           flexibility=args.flexibility)
                    if args.flexibility is not None else None)

    profile_dir = Path(args.profile_dir).resolve()
    out_dir = resolve_out_dir(args.out_dir)

    profile_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [ok] profile dir created/exists: {profile_dir}")
    print(f"  [ok] out dir created/exists    : {out_dir}")

    # URL build WITH and WITHOUT flexibility injection.
    print(f"  [ok] URL (no inject) : {base_url}")
    if injected_url is not None:
        assert "flexibility=" in injected_url
        assert "flex=" in injected_url
        assert "calendarFlexibility=" in injected_url
        print(f"  [ok] URL (--flexibility {args.flexibility} injected): "
              f"{injected_url}")
        print(f"       injected params: {injected_flexibility_params(args.flexibility)}")
    else:
        print("  [ok] --flexibility not set — no URL injection")

    # Window date schedule arithmetic.
    n = min(max(1, args.windows), WINDOWS_CAP)
    schedule = [window_date(args.date, i, args.step_days) for i in range(n)]
    assert schedule[0] == args.date, "window 0 must equal base date"
    print(f"  [ok] window date schedule ({n} windows, step {args.step_days}): "
          f"{schedule}")

    # JSONL logger init + one self-test record.
    logger = JsonlLogger(out_dir / "calendar_recon.jsonl")
    logger.write({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": "dry_run_selftest",
        "route": f"{args.org}-{args.dest}",
        "date": args.date,
    })
    logger.close()
    print(f"  [ok] JSONL logger init + write : {out_dir / 'calendar_recon.jsonl'}")

    # Interceptor matcher sanity.
    fake_url = ("https://akamai-gw.dbaas.aircanada.com/loyalty/dapidynamic/"
                "1ASIUDALAC/v2/search/air-calendars")
    assert TARGET_SUBSTRING in fake_url, "matcher failed for air-calendars"
    print(f"  [ok] interceptor matcher recognizes '{TARGET_SUBSTRING}'")

    # ---- Synthetic air-calendars RESPONSE parse ----
    # data[] with 2 dates; each with prices.unitPrices[].milesConversion.
    # convertedMiles.base + totalTaxes. The cheaper of two unitPrices on
    # date 1 must win.
    synthetic_body = {
        "data": [
            {
                "departureDate": "2026-08-15",
                "prices": {"unitPrices": [
                    {"milesConversion": {"convertedMiles": {
                        "base": 25000, "totalTaxes": 17060}}},
                    {"milesConversion": {"convertedMiles": {
                        "base": 12500, "totalTaxes": 9030}}},
                ]},
            },
            {
                "departureDate": "2026-08-16",
                "prices": {"unitPrices": [
                    {"milesConversion": {"convertedMiles": {
                        "base": 30000, "totalTaxes": 21000}}},
                ]},
            },
            # Defensive: a malformed entry (no prices) must yield null fare, not raise.
            {"departureDate": "2026-08-17"},
        ],
        "meta": {}, "dictionaries": {},
    }
    parsed = parse_calendar_response(synthetic_body)
    assert parsed["returned_dates"] == ["2026-08-15", "2026-08-16", "2026-08-17"], \
        f"dates wrong: {parsed['returned_dates']}"
    assert parsed["day_count"] == 3
    assert parsed["window_min"] == "2026-08-15"
    assert parsed["window_max"] == "2026-08-17"
    assert parsed["fares"]["2026-08-15"]["miles"] == 12500, "cheapest miles wrong"
    assert parsed["fares"]["2026-08-15"]["taxes_cents"] == 9030, "taxes wrong"
    assert parsed["fares"]["2026-08-16"]["miles"] == 30000
    assert parsed["fares"]["2026-08-17"]["miles"] is None, "malformed => null miles"
    print("  [ok] synthetic air-calendars parse: extracted dates + cheapest "
          "miles (12500) + taxes (9030); malformed entry => null")

    # ---- Synthetic REQUEST flexibility extraction ----
    class _FakeReq:
        def __init__(self, body):
            self._body = body
        @property
        def post_data_json(self):
            return self._body
        @property
        def post_data(self):
            return json.dumps(self._body)
    req_body = {"itineraries": [{"originLocationCode": "YYZ",
                                 "destinationLocationCode": "LAX",
                                 "flexibility": 3}],
                "frequentFlyer": {"cardNumber": "523781508"}}
    sent = extract_sent_flexibility(_FakeReq(req_body))
    assert sent["sent_flexibility"] == 3, f"sent flexibility wrong: {sent}"
    # Fallback path: post_data_json raises -> post_data json.loads.
    class _FakeReqFallback:
        @property
        def post_data_json(self):
            raise RuntimeError("not available")
        @property
        def post_data(self):
            return json.dumps({"itineraries": [{"flexibility": 7}]})
    assert extract_sent_flexibility(_FakeReqFallback())["sent_flexibility"] == 7
    print("  [ok] request flexibility extraction: reads itineraries[0]."
          "flexibility (3), with post_data fallback (7)")

    # ---- Coverage / gap / overlap computation ----
    # Window A returns 8/15..8/17, Window B returns 8/17..8/19 (overlap on 8/17),
    # Window C returns 8/22..8/23 (gap 8/20, 8/21 between the union min/max).
    wins = [
        ["2026-08-15", "2026-08-16", "2026-08-17"],
        ["2026-08-17", "2026-08-18", "2026-08-19"],
        ["2026-08-22", "2026-08-23"],
    ]
    cov = compute_coverage(wins)
    # Union = {8/15,16,17,18,19,22,23} = 7 distinct (8/17 overlaps, not double-counted).
    assert cov["total_distinct"] == 7, f"distinct wrong: {cov['total_distinct']}"
    assert cov["union_min"] == "2026-08-15"
    assert cov["union_max"] == "2026-08-23"
    assert cov["overlaps"] == ["2026-08-17"], f"overlap wrong: {cov['overlaps']}"
    assert cov["gaps"] == ["2026-08-20", "2026-08-21"], f"gaps wrong: {cov['gaps']}"
    print("  [ok] coverage: detected overlap [2026-08-17] and gaps "
          "[2026-08-20, 2026-08-21] across 3 windows")

    # ---- Redaction (incl. userId leak) ----
    sample = {"frequentFlyer": {"cardNumber": "1234567890", "companyCode": "AC"},
              "data": {"userId": "523781508", "marketCode": "DOM"}}
    red = redact_card_numbers(sample)
    assert red["frequentFlyer"]["cardNumber"] == "***REDACTED***"
    assert red["data"]["userId"] == "***REDACTED***", "userId must be redacted"
    assert red["data"]["marketCode"] == "DOM"
    assert red["frequentFlyer"]["companyCode"] == "AC"
    assert redact_card_numbers({"userId": "523781508"})["userId"] == "***REDACTED***"
    print("  [ok] redaction masks card/userId fields, keeps others")

    print()
    print_wiring_summary(args, base_url, injected_url, profile_dir, out_dir)
    print("\nDRY RUN COMPLETE — wiring valid. (No browser launched.)")
    return 0


# ----------------------------------------------------------------------------
# Live run
# ----------------------------------------------------------------------------

def run_live(args) -> int:
    """Launch headed Chrome, manual-login poll, tile windows, report coverage.

    Imports Playwright lazily so --dry-run/--help work even if Playwright isn't
    importable in some odd environment.
    """
    from playwright.sync_api import sync_playwright

    n_windows = min(max(1, args.windows), WINDOWS_CAP)
    profile_dir = Path(args.profile_dir).resolve()
    out_dir = resolve_out_dir(args.out_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    runlog = RunLog(out_dir / "run.log")

    base_url = build_availability_url(args.org, args.dest, args.date)
    injected_url = (build_availability_url(args.org, args.dest, args.date,
                                           flexibility=args.flexibility)
                    if args.flexibility is not None else None)

    print_wiring_summary(args, base_url, injected_url, profile_dir, out_dir)
    runlog("\nLIVE RUN — headed browser. A HUMAN must stay at the keyboard.")
    print("No credentials are read or written by this script.\n")

    logger = JsonlLogger(out_dir / "calendar_recon.jsonl")
    stash = ResponseStash(debug_log_path=out_dir / "network_debug.log")

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

    # Attach the response listener to the CONTEXT BEFORE navigating.
    context.on("response", stash.handler)

    page = context.pages[0] if context.pages else context.new_page()

    exit_code = 0
    headline = None
    window_returned_dates = []
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
            if not wait_for_manual_login(page, context):
                runlog("Aborting: not logged in.")
                logger.close()
                _teardown(context, playwright)
                return 1
            runlog("login-detected: manual login confirmed.")

        # ----- 1b. Settle the post-login redirect chain BEFORE window 1 -----
        # (Fix 1) Air Canada keeps navigating (clogin/.../afterLogin) right after
        # login is detected; starting window 1 mid-chain interrupts page.goto and
        # captures nothing. Wait for the redirect chain to quiesce first.
        wait_post_login_settle(page, runlog=runlog)

        # ----- 2. Tile windows -----
        for i in range(n_windows):
            req_date = window_date(args.date, i, args.step_days)
            url = build_availability_url(args.org, args.dest, req_date,
                                         flexibility=args.flexibility)
            record = scan_window(page, stash, url, win_label=f"window {i+1}/{n_windows}",
                                 requested_date=req_date, flexibility=args.flexibility,
                                 capture_timeout=args.capture_timeout, runlog=runlog)

            # First window: persist the raw REDACTED response + request bodies.
            if i == 0:
                if record.get("_raw_response_redacted") is not None:
                    try:
                        (out_dir / "air-calendars.json").write_text(
                            json.dumps(record["_raw_response_redacted"],
                                       indent=2, ensure_ascii=False),
                            encoding="utf-8")
                        runlog(f"    saved redacted response: "
                               f"{out_dir / 'air-calendars.json'}")
                    except Exception as exc:
                        runlog(f"    ERROR saving air-calendars.json: {exc}")
                if record.get("_raw_request_redacted") is not None:
                    try:
                        (out_dir / "air-calendars_request.json").write_text(
                            json.dumps(record["_raw_request_redacted"],
                                       indent=2, ensure_ascii=False),
                            encoding="utf-8")
                        runlog(f"    saved redacted request: "
                               f"{out_dir / 'air-calendars_request.json'}")
                    except Exception as exc:
                        runlog(f"    ERROR saving air-calendars_request.json: {exc}")

            window_returned_dates.append(record["returned_dates"])

            # JSONL line — strip the internal raw-body keys.
            clean = {k: v for k, v in record.items() if not k.startswith("_")}
            clean["ts"] = datetime.now().isoformat(timespec="seconds")
            clean["event"] = "calendar_window"
            clean["window_index"] = i
            clean["redirected_to_login"] = bool(record.get("_redirected_to_login"))
            logger.write(clean)
            runlog(f"window {i+1}/{n_windows}: requested={req_date} "
                   f"sent_flexibility={clean['sent_flexibility']} "
                   f"days={clean['day_count']}")

            # Expiry guard — stop tiling if the session lapsed.
            if record.get("_redirected_to_login") or stash.expiry_status is not None:
                runlog("expiry-detected: session lapsed mid-tile; stopping windows.")
                break

            # Small settle between windows (Fix 2) so the prior navigation
            # quiesces before the next cold navigate.
            if i + 1 < n_windows:
                page.wait_for_timeout(2500)

        # ----- 3. Coverage summary across windows -----
        cov = compute_coverage(window_returned_dates)
        runlog("\n" + "=" * 70)
        runlog("COVERAGE SUMMARY (the key output for range-scrape design)")
        runlog("=" * 70)
        runlog(f"  windows scanned   : {len(window_returned_dates)}")
        runlog(f"  total distinct days: {cov['total_distinct']}")
        runlog(f"  union range        : {cov['union_min']} .. {cov['union_max']}")
        runlog(f"  gaps ({len(cov['gaps'])})         : {cov['gaps']}")
        runlog(f"  overlaps ({len(cov['overlaps'])})     : {cov['overlaps']}")
        runlog(f"  distinct dates     : {cov['distinct_dates']}")
        logger.write({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "coverage_summary",
            "windows_scanned": len(window_returned_dates),
            "step_days": args.step_days,
            **cov,
        })
        headline = (f"COVERAGE: {cov['total_distinct']} distinct days "
                    f"({cov['union_min']}..{cov['union_max']}) over "
                    f"{len(window_returned_dates)} windows; "
                    f"{len(cov['gaps'])} gaps, {len(cov['overlaps'])} overlaps.")
    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl-C). Tearing down cleanly.")
    finally:
        logger.close()
        _teardown(context, playwright)

    print("\n" + "=" * 70)
    runlog(headline or "Run ended without a recorded headline.")
    print(f"Recon log : {out_dir / 'calendar_recon.jsonl'}")
    print(f"Payloads  : {out_dir}")
    runlog(f"exit: code={exit_code}")
    print("=" * 70)
    return exit_code


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

    # Window cap — refuse anything above the account-safety cap.
    if args.windows > WINDOWS_CAP:
        print(
            f"REFUSED: --windows {args.windows} exceeds the account-safety cap "
            f"of {WINDOWS_CAP}. More windows = more navigations = higher "
            f"throttling/deactivation risk. Use <= {WINDOWS_CAP}.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        return run_dry_run(args)

    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())
