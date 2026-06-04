"""Aeroplan Phase 0 transport spike — instrumented, account-safe Playwright harness.

De-risks the core data-extraction mechanism for an Aeroplan award-search scraper
BEFORE any real scraper is built. The spike proves two things and measures one
number:

  1. Transport works: a Playwright persistent context, pre-authenticated by a
     ONE-TIME MANUAL login, navigates to the Aeroplan availability URL and
     INTERCEPTS the award JSON responses (air-calendars, air-bounds, polldapi,
     reward/market-token) with ZERO SigV4 work — the page's own SPA fires the
     signed calls; we only read the responses.
  2. Cold-navigate behavior: does a fresh navigate fire calendars AND bounds AND
     poll, or only the calendar strip? (Opt-in --with-date-click distinguishes
     "cold navigate fired it" vs "needed an in-SPA date click".)
  3. Session/credential lifetime (the gating number): re-navigate on a timer
     until the calls 401/403 or redirect to the login host, and record the
     time-to-expiry.

ACCOUNT SAFETY (baked in):
  - Headed-only. --headless is REFUSED (Akamai/Arkose make headless non-viable).
  - This script handles ZERO credentials: it never reads/writes credentials,
    never reads credential env vars, never touches Arkose. It reuses a
    MANUALLY-authenticated persistent browser profile.
  - --probe-interval floored at 5 minutes; hard cap on total navigations
    (--max-navigations, default 18); single account; single route; loud console
    output so a human stays in the loop.

This is a SPIKE, not a scraper. Out of scope (deferred to later phases):
page.evaluate(fetch) in-page transport, round-trip / multi-pax payloads, DB
writes, login automation, Arkose handling.

Usage (live run — MANUAL, human-in-the-loop; do NOT automate):
    .venv\\Scripts\\python.exe scripts\\experiments\\aeroplan_transport_spike.py \\
        --org YYZ --dest LAX --date 2026-08-15 --probe-interval 5 --max-duration 90

Usage (offline self-test — no browser launched):
    .venv\\Scripts\\python.exe scripts\\experiments\\aeroplan_transport_spike.py --dry-run
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent

# Target endpoint substrings — if response.url contains one of these we STASH
# the Response object (see recon: docs/findings/aeroplan/auth-recon.md §3).
TARGET_SUBSTRINGS = [
    "air-calendars",      # price strip (sync, ~9 KB)
    "air-bounds",         # flight-list search (async → pollId)
    "polldapi",           # poll until full flight list ready
    "reward/market-token",  # market token used as marketCode in air-bounds
]

# Stable, filesystem-safe label per target substring (for payload filenames).
ENDPOINT_LABELS = {
    "air-calendars": "air-calendars",
    "air-bounds": "air-bounds",
    "polldapi": "polldapi",
    "reward/market-token": "market-token",
}

# Availability URL host + path family (from the plan / recon). A cold navigate
# here is what the SPA loads; it then fires the signed API calls itself.
AVAILABILITY_HOST = "https://www.aircanada.com"
AVAILABILITY_PATH = "/aeroplan/redeem/availability/outbound"

# Expiry / redirect signal: a redirect to this host means the session lapsed
# (recon §2: "Login host: login.aircanada.com").
LOGIN_HOST = "login.aircanada.com"

# Minimum allowed probe interval, in minutes (account-safety floor).
PROBE_INTERVAL_FLOOR = 5

# Settle pause after networkidle, mirroring cookie_farm's "let the SPA settle
# 2-3s before touching the DOM" discipline.
SETTLE_SECONDS = 3


# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aeroplan_transport_spike.py",
        description=(
            "Aeroplan Phase 0 transport spike — headed, account-safe Playwright "
            "harness. Intercepts award JSON, measures session lifetime. Handles "
            "ZERO credentials (manual login only)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--org", default="YYZ",
                   help="Origin airport code (origin of the outbound).")
    p.add_argument("--dest", default="LAX",
                   help="Destination airport code.")
    p.add_argument("--date", default="2026-08-15",
                   help="Departure date (YYYY-MM-DD), near-term.")
    p.add_argument("--probe-interval", type=float, default=5.0,
                   help=("Minutes between lifetime probes. FLOOR enforced at "
                         f"{PROBE_INTERVAL_FLOOR}; lower values are refused."))
    p.add_argument("--max-duration", type=float, default=90.0,
                   help="Max total run time for the lifetime loop, in minutes.")
    p.add_argument("--max-navigations", type=int, default=18,
                   help="Hard cap on total navigations to the availability URL.")
    p.add_argument("--capture-timeout", type=float, default=35.0,
                   help=("Seconds to wait (polling the stash ~1s) after "
                         "domcontentloaded for the async award XHRs "
                         "(air-calendars, air-bounds, then polldapi) before "
                         "recording endpoint_status. air-calendars arrives ~17s "
                         "in, so the default carries margin. Records whatever is "
                         "captured on timeout — never hard-fails."))
    p.add_argument("--with-date-click", action="store_true",
                   help=("Opt-in: perform a SINGLE in-SPA date click as a second "
                         "observation, to distinguish 'cold navigate fired "
                         "bounds/poll' vs 'needed a click'."))
    p.add_argument("--dry-run", action="store_true",
                   help=("Offline self-test: exercise arg parsing, dir creation, "
                         "URL build, interceptor wiring sanity, and JSONL logger "
                         "init WITHOUT launching a browser. Prints a wiring "
                         "summary and exits 0."))
    p.add_argument("--headless", action="store_true",
                   help=("REFUSED. Headless is non-viable against Air Canada's "
                         "Akamai/Arkose stack; passing this prints a refusal and "
                         "exits non-zero."))
    p.add_argument("--profile-dir",
                   default=str(SCRIPT_DIR / ".aeroplan-profile"),
                   help="Persistent browser profile dir (manual login persists).")
    p.add_argument("--out-dir", default=None,
                   help=("Output dir for probes.jsonl + redacted payloads. "
                         "Defaults to logs/aeroplan_spike_<timestamp>."))
    return p


def resolve_out_dir(arg_out_dir) -> Path:
    if arg_out_dir:
        return Path(arg_out_dir).resolve()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (REPO_ROOT / "logs" / f"aeroplan_spike_{ts}").resolve()


# ----------------------------------------------------------------------------
# Availability URL builder (from recon path family)
# ----------------------------------------------------------------------------

def build_availability_url(org: str, dest: str, date: str) -> str:
    """Build the Aeroplan outbound-availability URL from org/dest/date.

    Path family (from the plan / recon):
      /aeroplan/redeem/availability/outbound
        ?org0=YYZ&dest0=LAX&departureDate0=2026-08-15&tripType=O&...

    tripType=O is one-way (round-trip / multi-pax are out of scope for Phase 0).
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
# JSONL logger
# ----------------------------------------------------------------------------

class JsonlLogger:
    """Append-only JSONL writer for one probe record per line."""

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
# Run log (observability mirror — both prints and appends to <out-dir>/run.log)
# ----------------------------------------------------------------------------

class RunLog:
    """Tiny helper that prints a message AND appends a timestamped line to
    <out-dir>/run.log, so background monitoring can tail progress.

    Best-effort: file-write failures never interrupt the run.
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

    Masks:
      - any value under a `frequentFlyer.cardNumber` key (recon request schema),
      - any key that LOOKS like a card-number / member-number field (cardNumber,
        card_number, aeroplanNumber, memberNumber, userId, etc.) anywhere in the
        tree. A live run leaked `"userId": "523781508"` (the Aeroplan number) in
        a polldapi body, so `userId` is explicitly covered.

    Recurses through dicts and lists. Returns a NEW structure; does not mutate
    the input. Phase 0 captures responses (which generally do not echo the
    card), but we redact defensively in case a request echo or member block
    leaks it.
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
# Wiring summary (dry-run + live banner)
# ----------------------------------------------------------------------------

def print_wiring_summary(args, availability_url, profile_dir, out_dir):
    print("=" * 70)
    print("AEROPLAN TRANSPORT SPIKE — WIRING SUMMARY")
    print("=" * 70)
    print(f"  Route             : {args.org} -> {args.dest} on {args.date}")
    print(f"  Resolved URL      : {availability_url}")
    print(f"  Profile dir       : {profile_dir}")
    print(f"  Out dir           : {out_dir}")
    print(f"  Probes JSONL      : {out_dir / 'probes.jsonl'}")
    print(f"  Target substrings : {TARGET_SUBSTRINGS}")
    print(f"  Login host signal : {LOGIN_HOST} (redirect => session expired)")
    print("  Probe schedule    :")
    print(f"      interval        : {args.probe_interval} min "
          f"(floor {PROBE_INTERVAL_FLOOR})")
    print(f"      max-duration    : {args.max_duration} min")
    print(f"      max-navigations : {args.max_navigations}")
    print(f"      capture-timeout : {args.capture_timeout} s")
    print(f"  With date click   : {args.with_date_click}")
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
    """Stash matching Response objects from the context-level handler.

    CRITICAL: inside the sync on("response") handler we read ONLY the sync
    properties response.url and response.status. We DO NOT call .json()/.body()
    inside the handler — that can DEADLOCK the driver. Bodies are drained
    OUTSIDE the handler, after wait_for_load_state("networkidle").
    """

    # Substrings that mark a response as "network-debug-worthy" — logged to
    # network_debug.log regardless of whether the target matcher fires, so we
    # have ground truth on the real endpoint URLs even if a substring is wrong.
    DEBUG_URL_MARKERS = ("dapi", "loyalty", "polldapi")

    def __init__(self, debug_log_path: Path = None):
        # list of (substring, url, status, response_obj)
        self.captured = []
        # Sticky record of expiry: (url, status) of the first 401/403 seen.
        self.expiry_status = None
        # Always-on ground-truth network debug log (append mode).
        self.debug_log_path = debug_log_path
        if self.debug_log_path is not None:
            try:
                self.debug_log_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                log.debug("could not create network_debug.log parent", exc_info=True)

    def _debug_log(self, response) -> None:
        """Append a ground-truth line for aircanada/dapi/loyalty responses.

        SYNC PROPERTY READS ONLY (url/status/request.method). Never .json()/
        .body() here. Independent of the target-substring stash logic.
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
        # Always-on ground-truth debug log (independent of target matcher).
        self._debug_log(response)
        for sub in TARGET_SUBSTRINGS:
            if sub in url:
                self.captured.append((sub, url, status, response))
                print(f"    [intercept] {status}  {sub}  ({url.split('?')[0]})")
                if status in (401, 403) and self.expiry_status is None:
                    self.expiry_status = (url, status)
                break

    def clear(self):
        self.captured = []
        # expiry_status is intentionally sticky across clears.


def drain_status_map(stash: ResponseStash) -> dict:
    """Build {substring: status} from the stash WITHOUT reading bodies.

    Status is a sync property, so this is safe to call anytime. Derived from the
    EXACT SAME `stash.captured` set that `capture_payloads` drains — so if a
    payload was captured/saved for an endpoint, the map reports its HTTP status
    (never null). Per endpoint we prefer the status of a SUCCESSFUL (2xx)
    captured response (the one whose body we actually save); if none of the
    captured responses for that endpoint is 2xx, we fall back to the last status
    seen. Stays null ONLY when nothing at all was captured for that endpoint.
    """
    status_map = {sub: None for sub in TARGET_SUBSTRINGS}
    for sub, _url, status, _resp in stash.captured:
        prev = status_map.get(sub)
        # Prefer a 2xx status (matches what capture_payloads actually saves);
        # otherwise keep the most recent status seen.
        if prev is None:
            status_map[sub] = status
        elif 200 <= status < 300 and not (200 <= prev < 300):
            status_map[sub] = status
        elif not (200 <= prev < 300):
            status_map[sub] = status
    return status_map


# ----------------------------------------------------------------------------
# Login detection / manual-login poll (adapted from _wait_for_login)
# ----------------------------------------------------------------------------

def is_logged_in(page, context) -> bool:
    """Positive logged-in signal via DOM + cookie jar.

    NO credentials are read here. Negative signal first (visible Sign in),
    then positive DOM/cookie evidence. Best-effort; failures => assume not
    logged in.
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
            # Gigya / AC gateway session cookies set after login.
            cookie_signal = bool(names & {
                "glt_3_zA5TRSBDlwybsx_1k8EyncAfJ2b62DJnoxPW60q4X9MqmBDJh1v_8QYaOTG8kZ8S",
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

    NO credentials are entered by this script. The human logs in (incl. Arkose
    + 2FA) at the keyboard. Returns True once logged in, False on timeout.
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
# Cold-navigate probe
# ----------------------------------------------------------------------------

def _stash_seen(stash: ResponseStash, substring: str) -> bool:
    """True if any response matching `substring` is currently in the stash."""
    return any(sub == substring for sub, _u, _s, _r in stash.captured)


def wait_for_award_xhrs(stash: ResponseStash, capture_timeout: float) -> None:
    """Bounded wait for the async award XHRs before recording/draining.

    The page never reaches network-idle (continuous heartbeat beacons), so we
    drive capture entirely off the response stash. The award calls complete fine
    within ~17s of navigate (air-calendars arrives LAST). We poll the stash
    ~every 1s for up to `capture_timeout` seconds until BOTH air-calendars AND
    air-bounds are seen,
    then give polldapi a few extra seconds to arrive. We NEVER hard-fail: on
    timeout we return and the caller records whatever was captured (partial is
    informative).
    """
    deadline = time.time() + max(0.0, capture_timeout)
    print(f"    waiting up to {capture_timeout:.0f}s for async award XHRs "
          "(air-calendars + air-bounds, then polldapi)...")
    # Phase 1: wait for the two driving endpoints.
    while time.time() < deadline:
        if _stash_seen(stash, "air-calendars") and _stash_seen(stash, "air-bounds"):
            break
        time.sleep(1.0)

    # Phase 2: give the async poll result (polldapi) a few extra seconds to land,
    # but never exceed the overall capture-timeout budget.
    poll_deadline = min(deadline, time.time() + 5.0) if time.time() < deadline \
        else time.time()
    while time.time() < poll_deadline:
        if _stash_seen(stash, "polldapi"):
            break
        time.sleep(1.0)

    seen = [sub for sub in TARGET_SUBSTRINGS if _stash_seen(stash, sub)]
    print(f"    capture wait done; endpoints seen so far: {seen or '(none)'}")


def cold_navigate_probe(page, stash: ResponseStash, availability_url: str,
                        nav_label: str, capture_timeout: float = 35.0) -> dict:
    """Clear the stash, navigate to the availability URL, wait for the SPA to
    settle, and report which target endpoints fired and their statuses.

    Returns a status map {substring: status_or_None}. Detects a login-host
    redirect as an expiry signal.
    """
    stash.clear()
    print(f"\n>>> [{nav_label}] COLD NAVIGATE -> {availability_url.split('?')[0]}")
    print("    (loud step: a human should stay in the loop here)")
    redirected_to_login = False
    try:
        page.goto(availability_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as exc:
        print(f"    navigate raised: {exc}")
    # The Aeroplan availability page fires CONTINUOUS tracking/heartbeat beacons
    # (bttrack heartbeat ~every 5s, GA, adentifi), so it NEVER reaches
    # network-idle. We therefore do NOT block on networkidle — that hangs the
    # probe indefinitely. Best-effort only with a SHORT timeout, swallowed so it
    # can never block; then a brief settle mirroring cookie_farm's discipline.
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass
    time.sleep(SETTLE_SECONDS)

    # Drive capture entirely off the response stash — the core fix. The award
    # XHRs complete fine within ~17s of navigate (air-calendars arrives LAST,
    # ~17s in) even though the page never idles. We poll the stash up to
    # capture_timeout. Never hard-fails on timeout; partial is fine.
    wait_for_award_xhrs(stash, capture_timeout)

    landed = page.url or ""
    if LOGIN_HOST in landed:
        redirected_to_login = True
        print(f"    !! redirected to login host ({LOGIN_HOST}) — session expired")

    status_map = drain_status_map(stash)
    fired = [sub for sub, st in status_map.items() if st is not None]
    print(f"    fired endpoints   : {fired or '(none)'}")
    print(f"    per-endpoint status: {status_map}")
    status_map["_redirected_to_login"] = redirected_to_login
    return status_map


# ----------------------------------------------------------------------------
# Optional --with-date-click second observation
# ----------------------------------------------------------------------------

def date_click_observation(page) -> dict:
    """Perform a SINGLE in-SPA date click to see whether bounds/poll fire on a
    click (vs the cold navigate). Best-effort: selectors guarded by try/except.

    Uses page.expect_response (the safe targeted pattern — body read on the
    returned value is OUTSIDE any handler). Returns {substring: status} for the
    endpoints observed during the click.
    """
    print("\n>>> [with-date-click] SINGLE in-SPA date click observation")
    result = {}
    # Try a few plausible calendar/date selectors; stop at the first usable one.
    date_selectors = [
        '[class*="calendar"] [class*="day"]:not([disabled])',
        'button[class*="day"]:not([disabled])',
        '[role="gridcell"] button:not([disabled])',
        '[data-testid*="date"]',
        '[class*="DatePicker"] button:not([disabled])',
    ]
    clickable = None
    for sel in date_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=2000):
                clickable = loc
                print(f"    using date selector: {sel}")
                break
        except Exception:
            continue
    if clickable is None:
        print("    no usable date element found — skipping click observation")
        return result

    # Watch for the highest-signal endpoint (air-bounds) on the click.
    try:
        with page.expect_response(
            lambda r: any(sub in r.url for sub in TARGET_SUBSTRINGS),
            timeout=15000,
        ) as info:
            try:
                clickable.click(timeout=5000)
            except Exception as exc:
                print(f"    date click raised: {exc}")
        resp = info.value
        # Body read here is SAFE — outside any sync handler.
        matched = next((s for s in TARGET_SUBSTRINGS if s in resp.url), "?")
        result[matched] = resp.status
        print(f"    click fired: {matched} -> {resp.status}")
    except Exception as exc:
        print(f"    no target response within 15s of click: {exc}")
    return result


# ----------------------------------------------------------------------------
# Redacted payload capture
# ----------------------------------------------------------------------------

def _body_has_flight_list(body) -> bool:
    """True if `body` looks like the air-bounds flight-list poll result.

    The flight-list polldapi resolves with `data.airBoundGroups` (the flight
    list + quota/seats). This is the MOST important payload and must never be
    silently dropped, so we detect it structurally rather than by URL.
    """
    try:
        data = body.get("data") if isinstance(body, dict) else None
        return isinstance(data, dict) and "airBoundGroups" in data
    except Exception:
        return False


def capture_payloads(stash: ResponseStash, out_dir: Path, already_saved: set,
                     runlog=None) -> None:
    """Save captured payloads, member-number redacted, from `stash.captured`.

    Bodies are drained HERE — outside the on("response") handler — by calling
    response.json() (safe, no deadlock).

    Multi-fire handling (Fix B): an endpoint that fires MORE THAN ONCE in a
    single search (notably `polldapi`: one resolves the market-token poll, a
    DIFFERENT one resolves the air-bounds flight-list poll) gets EVERY response
    saved to a distinct, capture-ordered file `<label>_1.json`, `<label>_2.json`,
    ... so the large flight-list poll is never silently dropped. Single-fire
    endpoints keep their existing `<label>.json` name. Additionally, any body
    containing `data.airBoundGroups` is ALSO saved as `polldapi_flightlist.json`
    (the representative flight list).

    `already_saved` carries dedup keys across probes within a run (filenames and
    a sentinel for the flight-list capture) so the same payload isn't rewritten.
    """
    def _emit(msg):
        if runlog is not None:
            runlog(msg)
        else:
            print(msg)

    def _write(path: Path, redacted) -> bool:
        # Raises on serialization/IO failure so the per-endpoint try/except
        # below can log it and move on WITHOUT aborting the rest of the loop.
        path.write_text(json.dumps(redacted, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        _emit(f"    saved redacted payload: {path}")
        return True

    # Count how many times each endpoint fired in the current captured set, so
    # we know whether to use the single-file name or numbered files.
    fire_counts: dict = {}
    for sub, _u, _s, _r in stash.captured:
        label = ENDPOINT_LABELS.get(sub, sub.replace("/", "_"))
        fire_counts[label] = fire_counts.get(label, 0) + 1

    # Per-probe ordinal so numbered filenames follow capture order.
    seen_ordinal: dict = {}

    for sub, url, status, resp in stash.captured:
        label = ENDPOINT_LABELS.get(sub, sub.replace("/", "_"))
        multi = fire_counts.get(label, 0) > 1
        if multi:
            seen_ordinal[label] = seen_ordinal.get(label, 0) + 1
            fname = f"{label}_{seen_ordinal[label]}.json"
        else:
            fname = f"{label}.json"

        # Per-endpoint defensive boundary: a failure on ONE captured response
        # (serialization error, body read, flight-list write, ...) must NEVER
        # abort saving the others. Each iteration is fully isolated. Dedup keys
        # are only added AFTER a successful write, so an endpoint that has not
        # yet been written on a prior probe still gets written here.
        try:
            if fname in already_saved:
                continue
            if status >= 400:
                # Don't persist error bodies as if they were schema evidence.
                continue
            body = resp.json()  # OUTSIDE the handler — safe, no deadlock.
            redacted = redact_card_numbers(body)
            path = out_dir / fname
            if _write(path, redacted):
                already_saved.add(fname)
        except Exception as exc:
            _emit(f"    ERROR saving {label} ({fname}): {exc}")
            # Keep going — do NOT mark as saved, so a later probe can retry.
            continue

        # Always also surface the flight-list poll under a stable name so the
        # most important payload is never buried among numbered polldapi files.
        # Isolated in its own try/except for the same reason.
        try:
            if (_body_has_flight_list(body)
                    and "polldapi_flightlist" not in already_saved):
                fl_path = out_dir / "polldapi_flightlist.json"
                if _write(fl_path, redacted):
                    already_saved.add("polldapi_flightlist")
        except Exception as exc:
            _emit(f"    ERROR saving polldapi_flightlist.json: {exc}")
            continue


# ----------------------------------------------------------------------------
# Dry-run self-test
# ----------------------------------------------------------------------------

def run_dry_run(args) -> int:
    """Exercise everything that does NOT require a browser, then exit 0.

    Validates: arg parsing (already done), profile/out-dir creation, URL
    construction, interceptor-wiring sanity, and JSONL logger init.
    """
    print("DRY RUN — no browser will be launched. Validating wiring only.\n")

    # Probe-interval floor must already have been enforced before we get here.
    availability_url = build_availability_url(args.org, args.dest, args.date)

    profile_dir = Path(args.profile_dir).resolve()
    out_dir = resolve_out_dir(args.out_dir)

    # Dir creation.
    profile_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [ok] profile dir created/exists: {profile_dir}")
    print(f"  [ok] out dir created/exists    : {out_dir}")

    # JSONL logger init + one self-test record.
    logger = JsonlLogger(out_dir / "probes.jsonl")
    logger.write({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": "dry_run_selftest",
        "route": f"{args.org}-{args.dest}",
        "date": args.date,
    })
    logger.close()
    print(f"  [ok] JSONL logger init + write : {out_dir / 'probes.jsonl'}")

    # Interceptor-wiring sanity: build a stash, push a fake matching response
    # shape through the matching logic (no real Response object, no browser).
    stash = ResponseStash()
    for sub in TARGET_SUBSTRINGS:
        fake_url = f"https://akamai-gw.dbaas.aircanada.com/loyalty/.../{sub}"
        matched = [s for s in TARGET_SUBSTRINGS if s in fake_url]
        assert matched == [sub] or sub in matched, f"matcher failed for {sub}"
    print(f"  [ok] interceptor matcher recognizes all {len(TARGET_SUBSTRINGS)} "
          "target substrings")

    # Redaction sanity (incl. the userId leak seen in a live polldapi body).
    sample = {"frequentFlyer": {"cardNumber": "1234567890", "companyCode": "AC"},
              "nested": [{"aeroplanNumber": "9999"}],
              "data": {"userId": "523781508", "marketCode": "DOM"}}
    red = redact_card_numbers(sample)
    assert red["frequentFlyer"]["cardNumber"] == "***REDACTED***"
    assert red["nested"][0]["aeroplanNumber"] == "***REDACTED***"
    assert red["data"]["userId"] == "***REDACTED***", "userId must be redacted"
    assert red["data"]["marketCode"] == "DOM"
    assert red["frequentFlyer"]["companyCode"] == "AC"
    # Also confirm the bare-key form `{"userId": "523781508"}` -> REDACTED.
    assert redact_card_numbers({"userId": "523781508"})["userId"] == "***REDACTED***"
    print("  [ok] member-number redaction masks card/userId fields, keeps others")

    # Flight-list detection sanity: a body with data.airBoundGroups is the one
    # ALSO saved as polldapi_flightlist.json; a market-token poll body is not.
    assert _body_has_flight_list({"data": {"airBoundGroups": [{"x": 1}]}})
    assert not _body_has_flight_list(
        {"data": {"sessionToken": "t", "marketCode": "DOM"}})
    print("  [ok] flight-list (data.airBoundGroups) detection => "
          "polldapi_flightlist.json")

    # End-to-end save-routine self-check (no browser): simulate a captured set
    # of air-calendars + air-bounds + 2 polldapi (one with data.airBoundGroups)
    # + market-token, run the REAL capture_payloads against a temp dir, and
    # assert every expected file is written. Guards the save regression where a
    # failure on one endpoint aborted saving the rest.
    import tempfile as _tempfile
    import shutil as _shutil

    class _FakeResp:
        def __init__(self, body):
            self._body = body
        def json(self):
            return self._body

    class _FakeStash:
        def __init__(self, captured):
            self.captured = captured

    tmp_save = Path(_tempfile.mkdtemp(prefix="aeroplan_spike_savecheck_"))
    try:
        captured = [
            ("air-calendars",
             "https://x/air-calendars", 200,
             _FakeResp({"calendar": [{"date": "2026-06-01", "points": 25000}]})),
            ("air-bounds",
             "https://x/air-bounds", 200,
             _FakeResp({"pollId": "abc123", "status": "PENDING"})),
            ("polldapi",
             "https://x/polldapi", 200,
             _FakeResp({"data": {"sessionToken": "t", "marketCode": "DOM"}})),
            ("polldapi",
             "https://x/polldapi", 200,
             _FakeResp({"data": {"airBoundGroups": [{"id": "g1", "seats": 4}]}})),
            ("reward/market-token",
             "https://x/reward/market-token", 200,
             _FakeResp({"data": {"sessionToken": "tok", "userId": "523781508"}})),
        ]
        save_log = []
        capture_payloads(_FakeStash(captured), tmp_save, set(),
                         runlog=save_log.append)
        produced = sorted(p.name for p in tmp_save.glob("*.json"))
        expected = {
            "air-calendars.json", "air-bounds.json", "market-token.json",
            "polldapi_1.json", "polldapi_2.json", "polldapi_flightlist.json",
        }
        missing = expected - set(produced)
        assert not missing, f"save routine dropped expected files: {missing}"
        # market-token must redact the leaked userId in the written file.
        mt = json.loads((tmp_save / "market-token.json").read_text(encoding="utf-8"))
        assert mt["data"]["userId"] == "***REDACTED***", "userId not redacted on disk"
        print(f"  [ok] capture_payloads self-check wrote all expected files: "
              f"{produced}")
    finally:
        _shutil.rmtree(tmp_save, ignore_errors=True)

    print()
    print_wiring_summary(args, availability_url, profile_dir, out_dir)
    print("\nDRY RUN COMPLETE — wiring valid. (No browser launched.)")
    return 0


# ----------------------------------------------------------------------------
# Live run
# ----------------------------------------------------------------------------

def run_live(args) -> int:
    """Launch headed Chrome, manual-login poll, cold-navigate + lifetime loop.

    Imports Playwright lazily so --dry-run/--help work even if Playwright isn't
    importable in some odd environment.
    """
    from playwright.sync_api import sync_playwright

    availability_url = build_availability_url(args.org, args.dest, args.date)
    profile_dir = Path(args.profile_dir).resolve()
    out_dir = resolve_out_dir(args.out_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    runlog = RunLog(out_dir / "run.log")

    print_wiring_summary(args, availability_url, profile_dir, out_dir)
    runlog("\nLIVE RUN — headed browser. A HUMAN must stay at the keyboard.")
    print("No credentials are read or written by this script.\n")

    logger = JsonlLogger(out_dir / "probes.jsonl")
    stash = ResponseStash(debug_log_path=out_dir / "network_debug.log")
    already_saved: set = set()

    # Process hygiene before launch (reused from cookie_farm).
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

    # Attach the response listener to the CONTEXT BEFORE navigating, so we
    # capture responses across all pages/frames in the persistent context.
    context.on("response", stash.handler)

    page = context.pages[0] if context.pages else context.new_page()

    exit_code = 0
    headline = None
    nav_count = 0
    start_time = time.time()
    deadline = start_time + args.max_duration * 60.0
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

        # ----- 2. Cold-navigate probe (the first observation) -----
        nav_count += 1
        cold_map = cold_navigate_probe(page, stash, availability_url,
                                       nav_label=f"nav {nav_count}",
                                       capture_timeout=args.capture_timeout)
        runlog(f"cold_navigate nav {nav_count}: endpoint_status="
               f"{ {k: v for k, v in cold_map.items() if not k.startswith('_')} }")
        capture_payloads(stash, out_dir, already_saved, runlog=runlog)
        logger.write(_probe_record(nav_count, cold_map, True, kind="cold_navigate"))

        # ----- 3. Optional single date-click observation -----
        if args.with_date_click:
            click_map = date_click_observation(page)
            capture_payloads(stash, out_dir, already_saved, runlog=runlog)
            logger.write({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "event": "date_click_observation",
                "navigation_count": nav_count,
                "click_status_map": click_map,
            })

        # ----- 4. Lifetime-probe loop -----
        print("\n>>> Entering lifetime-probe loop.")
        print(f"    interval={args.probe_interval}min  "
              f"max-duration={args.max_duration}min  "
              f"max-navigations={args.max_navigations}")
        while True:
            if nav_count >= args.max_navigations:
                print(f"\nReached --max-navigations cap ({args.max_navigations}). "
                      "Stopping (no expiry observed).")
                break
            if time.time() >= deadline:
                print(f"\nReached --max-duration ({args.max_duration}min). "
                      "Stopping (no expiry observed).")
                break

            # Sleep the probe interval (loudly), but wake early at the deadline.
            sleep_until = min(time.time() + args.probe_interval * 60.0, deadline)
            print(f"\n... waiting until next probe "
                  f"({args.probe_interval}min); "
                  f"{nav_count}/{args.max_navigations} navigations used ...")
            while time.time() < sleep_until:
                time.sleep(min(10, max(0, sleep_until - time.time())))
            if time.time() >= deadline:
                print(f"\nReached --max-duration ({args.max_duration}min). "
                      "Stopping (no expiry observed).")
                break

            nav_count += 1
            probe_map = cold_navigate_probe(page, stash, availability_url,
                                            nav_label=f"nav {nav_count}",
                                            capture_timeout=args.capture_timeout)
            runlog(f"lifetime_probe nav {nav_count}: endpoint_status="
                   f"{ {k: v for k, v in probe_map.items() if not k.startswith('_')} }")
            capture_payloads(stash, out_dir, already_saved, runlog=runlog)
            logged_in_now = is_logged_in(page, context)
            logger.write(_probe_record(nav_count, probe_map, logged_in_now,
                                       kind="lifetime_probe"))

            # Expiry detection: 401/403 on a target endpoint, login-host
            # redirect, or DOM/cookie shows logged-out.
            expired = (
                stash.expiry_status is not None
                or probe_map.get("_redirected_to_login")
                or not logged_in_now
            )
            if expired:
                elapsed_min = (time.time() - start_time) / 60.0
                reason = (
                    f"401/403 ({stash.expiry_status[1]})"
                    if stash.expiry_status is not None
                    else "login-host redirect"
                    if probe_map.get("_redirected_to_login")
                    else "logged-out DOM/cookie signal"
                )
                headline = (f"TIME-TO-EXPIRY: ~{elapsed_min:.1f} min "
                            f"({nav_count} navigations) — reason: {reason}")
                runlog(f"expiry-detected: {headline}")
                logger.write({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "event": "session_expired",
                    "navigation_count": nav_count,
                    "elapsed_minutes": round(elapsed_min, 2),
                    "reason": reason,
                })
                break

        if headline is None:
            elapsed_min = (time.time() - start_time) / 60.0
            headline = (f"NO EXPIRY OBSERVED within {elapsed_min:.1f} min / "
                        f"{nav_count} navigations — session still valid at stop.")
    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl-C). Tearing down cleanly.")
    finally:
        logger.close()
        _teardown(context, playwright)

    print("\n" + "=" * 70)
    runlog(headline or "Run ended without a recorded headline.")
    print(f"Probes log : {out_dir / 'probes.jsonl'}")
    print(f"Payloads   : {out_dir}")
    runlog(f"exit: code={exit_code}")
    print("=" * 70)
    return exit_code


def _probe_record(nav_count, status_map, logged_in, kind):
    clean = {k: v for k, v in status_map.items() if not k.startswith("_")}
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": kind,
        "navigation_count": nav_count,
        "logged_in": bool(logged_in),
        "redirected_to_login": bool(status_map.get("_redirected_to_login")),
        "endpoint_status": clean,
    }


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
    # (tail -f / poll) sees progress live instead of in block-buffered chunks.
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

    # Probe-interval floor — refuse anything below the safe minimum.
    if args.probe_interval < PROBE_INTERVAL_FLOOR:
        print(
            f"REFUSED: --probe-interval {args.probe_interval} is below the "
            f"account-safety floor of {PROBE_INTERVAL_FLOOR} minutes. "
            "Lower intervals risk account throttling. Use >= "
            f"{PROBE_INTERVAL_FLOOR}.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        return run_dry_run(args)

    return run_live(args)


if __name__ == "__main__":
    sys.exit(main())
