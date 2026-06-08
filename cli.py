"""searchaero CLI entry point."""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime

from core import db, presentation
from core.matching import CABIN_FILTER_MAP as _CABIN_FILTER_MAP, compute_match_hash as _compute_match_hash
from core.output import get_console, print_error
from core.routes import load_routes as _load_routes
from core.scheduler import (
    load_schedules, save_schedule, remove_schedule,
    generate_bat, register_task, delete_task, query_task,
    check_wake_timers, enable_wake_timers, require_windows,
    SCHEDULES_DIR,
    MAX_ROUTES, count_routes_in_file, count_routes_in_files,
    estimate_scrape_minutes, compute_min_interval,
    add_route_group, remove_route_group, get_all_routes_files,
    get_total_route_count, is_old_format,
)

_CLI_DIR = os.path.dirname(os.path.abspath(__file__))
ORCHESTRATE_PY = os.path.join(_CLI_DIR, "scripts", "orchestrate.py")

from scrape import scrape_route, _scrape_with_crash_detection, detect_browser_crash
from core.cookie_farm import CookieFarm
from core.hybrid_scraper import HybridScraper

def _log(msg: str):
    """Print a timestamped progress line to stderr (visible even in --json mode)."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


def _prompt_sms_code() -> str:
    """Prompt the user for their SMS verification code."""
    _log("SMS verification code sent to your phone")
    return input("Enter SMS code: ").strip()


_MFA_DIR = os.path.join(os.path.expanduser("~"), ".searchaero")
_MFA_REQUEST = os.path.join(_MFA_DIR, "mfa_request")
_MFA_RESPONSE = os.path.join(_MFA_DIR, "mfa_response")


def _prompt_sms_file(timeout: int = 300, mfa_method: str = "email") -> str:
    """Wait for MFA code via filesystem handoff.

    Writes a request to ~/.searchaero/mfa_request, then polls
    ~/.searchaero/mfa_response until the code appears or timeout.

    Args:
        timeout: Maximum seconds to wait (default: 300).
        mfa_method: MFA delivery channel — "sms" or "email" (default: "email").

    Returns:
        The MFA code string.

    Raises:
        RuntimeError: If no code is provided within the timeout.
    """
    os.makedirs(_MFA_DIR, exist_ok=True)

    # Clean up stale response file
    if os.path.exists(_MFA_RESPONSE):
        os.remove(_MFA_RESPONSE)

    # Write request file
    request = {
        "requested_at": datetime.now().isoformat(),
        "message": "Enter verification code",
        "mfa_method": mfa_method,
        "response_file": _MFA_RESPONSE,
    }
    with open(_MFA_REQUEST, "w") as f:
        json.dump(request, f)

    _log(f"MFA code required — write code to: {_MFA_RESPONSE}")

    # Poll for response
    elapsed = 0
    poll_interval = 2
    while elapsed < timeout:
        if os.path.exists(_MFA_RESPONSE):
            with open(_MFA_RESPONSE, "r") as f:
                code = f.read().strip()
            # Clean up both files
            for path in (_MFA_REQUEST, _MFA_RESPONSE):
                if os.path.exists(path):
                    os.remove(path)
            if code:
                _log("MFA code received via file")
                return code
        time.sleep(poll_interval)
        elapsed += poll_interval

    # Clean up request file on timeout
    if os.path.exists(_MFA_REQUEST):
        os.remove(_MFA_REQUEST)

    raise RuntimeError(
        f"MFA code not provided within {timeout}s. "
        f"Expected code in: {_MFA_RESPONSE}"
    )


def _get_mfa_prompt(args) -> callable:
    """Return the appropriate MFA prompt callable based on CLI flags."""
    if getattr(args, "mfa_file", False):
        mfa_method = getattr(args, "mfa_method", "email")
        return lambda: _prompt_sms_file(mfa_method=mfa_method)
    return _prompt_sms_code


def _cleanup_mfa_files():
    """Remove stale MFA files from a previous run."""
    for path in (_MFA_REQUEST, _MFA_RESPONSE):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def _signal_login_complete():
    """Write a 'logged_in' signal to mfa_request so external pollers stop waiting."""
    os.makedirs(_MFA_DIR, exist_ok=True)
    with open(_MFA_REQUEST, "w") as f:
        json.dump({"status": "logged_in"}, f)


_CABIN_GROUPS = {
    "economy": "Economy",
    "premium_economy": "Economy",
    "business": "Business",
    "business_pure": "Business",
    "first": "First",
    "first_pure": "First",
}


# _CABIN_FILTER_MAP imported from core.matching

_SORT_KEYS = {
    "date": lambda r: (r["date"], r["cabin"], r["miles"]),
    "miles": lambda r: (r["miles"], r["date"], r["cabin"]),
    "cabin": lambda r: (r["cabin"], r["date"], r["miles"]),
}


def cmd_setup(args):
    """Run environment checks and report readiness.

    Returns:
        int: 0 if all checks pass, 1 if some failed.
    """
    # Migration: check for credentials at old location
    old_env = os.path.join(_CLI_DIR, "scripts", "experiments", ".env")
    new_env = os.path.join(os.path.expanduser("~"), ".searchaero", ".env")
    if os.path.isfile(old_env) and not os.path.isfile(new_env):
        print(f"Credentials found at old location. Run:\n  cp {old_env} {new_env}", file=sys.stderr)

    results = {}

    # ------------------------------------------------------------------
    # Check 1: Database
    # ------------------------------------------------------------------
    from core import db

    db_path = args.db_path  # None means use default
    try:
        conn = db.get_connection(db_path)
        db.ensure_schema(conn)
        actual_path = db_path or os.getenv("SEARCHAERO_DB", db.DEFAULT_DB_PATH)
        results["database"] = {"path": actual_path, "status": "ok"}
        conn.close()
    except Exception as e:
        actual_path = db_path or os.getenv("SEARCHAERO_DB", db.DEFAULT_DB_PATH)
        results["database"] = {"path": actual_path, "status": f"error: {e}"}

    # ------------------------------------------------------------------
    # Check 2: Playwright
    # ------------------------------------------------------------------
    import importlib.metadata

    try:
        pw_version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        pw_version = None

    if os.name == "nt":
        pw_browsers = os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "ms-playwright"
        )
    else:
        pw_browsers = os.path.expanduser("~/.cache/ms-playwright")

    browsers_installed = bool(glob.glob(os.path.join(pw_browsers, "chromium-*")))

    # Auto-install Chromium if package present but browsers missing
    browsers_auto_installed = False
    if pw_version is not None and not browsers_installed and not getattr(args, 'no_browser_install', False) and not args.json:
        console = get_console()
        console.print("  Chromium not found. Installing... (this may download ~170MB)")
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            # Re-check that browsers actually landed
            browsers_installed = bool(glob.glob(os.path.join(pw_browsers, "chromium-*")))
            if browsers_installed:
                browsers_auto_installed = True
                console.print("  [green]✓ Chromium installed[/green]")
            else:
                console.print("  [red]✗ Install reported success but browsers not found[/red]")
        else:
            console.print(f"  [red]✗ Install failed:[/red] {result.stderr.strip()}")

    browsers_skipped = pw_version is not None and not browsers_installed and getattr(args, 'no_browser_install', False)

    results["playwright"] = {
        "package": pw_version,
        "browsers": browsers_installed,
        "browsers_auto_installed": browsers_auto_installed,
        "browsers_skipped": browsers_skipped,
    }

    # ------------------------------------------------------------------
    # Check 3: Credentials
    # ------------------------------------------------------------------
    env_file = os.path.join(os.path.expanduser("~"), ".searchaero", ".env")
    required_keys = [
        "UNITED_MP_NUMBER",
        "UNITED_PASSWORD",
    ]

    creds = {"file": env_file, "file_exists": os.path.isfile(env_file)}

    if creds["file_exists"]:
        with open(env_file, "r") as f:
            lines = f.readlines()

        env_map = {}
        for line in lines:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                env_map[key.strip()] = value.strip()

        for key in required_keys:
            value = env_map.get(key, "")
            creds[key] = bool(value and not value.startswith("your_"))
    else:
        for key in required_keys:
            creds[key] = False

    # Interactive credential setup — offer to create .env if missing/incomplete
    needs_setup = (
        not creds["file_exists"]
        or not creds.get("UNITED_MP_NUMBER")
        or not creds.get("UNITED_PASSWORD")
    )
    if needs_setup and not args.json and sys.stdin.isatty():
        console = get_console()
        console.print()
        console.print("[bold yellow]Credentials not found.[/bold yellow] Let's set them up.")
        console.print(f"  This will write to: [dim]{env_file}[/dim]")
        console.print()
        try:
            mp_number = input("  MileagePlus number: ").strip()
            password = input("  Password: ").strip()
            if mp_number and password:
                os.makedirs(os.path.dirname(env_file), exist_ok=True)
                # Use .env.sample as template if available
                env_sample = os.path.join(os.path.dirname(env_file), ".env.sample")
                env_lines = []
                if os.path.isfile(env_sample):
                    with open(env_sample, "r") as f:
                        for line in f:
                            stripped = line.strip()
                            if stripped.startswith("UNITED_MP_NUMBER="):
                                env_lines.append(f"UNITED_MP_NUMBER={mp_number}\n")
                            elif stripped.startswith("UNITED_PASSWORD="):
                                env_lines.append(f"UNITED_PASSWORD={password}\n")
                            else:
                                env_lines.append(line)
                else:
                    env_lines = [
                        f"UNITED_MP_NUMBER={mp_number}\n",
                        f"UNITED_PASSWORD={password}\n",
                    ]
                with open(env_file, "w") as f:
                    f.writelines(env_lines)
                creds["file_exists"] = True
                creds["UNITED_MP_NUMBER"] = True
                creds["UNITED_PASSWORD"] = True
                console.print("  [green]✓ Credentials saved[/green]")
            else:
                console.print("  [dim]Skipped — you can edit the file manually later.[/dim]")
        except (EOFError, KeyboardInterrupt):
            console.print()
            console.print("  [dim]Skipped.[/dim]")
    elif needs_setup and not args.json:
        # Non-interactive mode — warn clearly instead of silently skipping
        console = get_console()
        console.print()
        env_sample = os.path.join(os.path.dirname(env_file), ".env.sample")
        console.print("[bold yellow]⚠ Credentials missing.[/bold yellow] Cannot prompt (non-interactive mode).")
        console.print(f"  Copy the template:  [bold]cp {env_sample} {env_file}[/bold]")
        console.print(f"  Then edit:          [bold]{env_file}[/bold]")
        console.print("  Or re-run [bold]searchaero setup[/bold] in an interactive terminal.")

    results["credentials"] = creds

    # ------------------------------------------------------------------
    # Check 4: Schedules & Power (Windows only, non-blocking)
    # ------------------------------------------------------------------
    if sys.platform == "win32":
        sched_info = {"schedules": [], "wake_timers": None}
        try:
            scheds = load_schedules()
            sched_info["schedules"] = [
                {"name": s.get("name"), "interval": s.get("interval_minutes")}
                for s in scheds
            ]
            if scheds:
                wake = check_wake_timers()
                sched_info["wake_timers"] = {
                    "ac": wake["ac"], "dc": wake["dc"]
                }
        except Exception:
            pass
        results["schedules"] = sched_info

    # ------------------------------------------------------------------
    # Check 5: Summary
    # ------------------------------------------------------------------
    checks_passed = 0

    # Database passes if status is "ok"
    db_ok = results["database"]["status"] == "ok"
    if db_ok:
        checks_passed += 1

    # Playwright passes if package installed AND browsers installed
    if results["playwright"]["package"] is not None and results["playwright"]["browsers"]:
        checks_passed += 1

    # Credentials passes if file exists AND UNITED_MP_NUMBER set AND UNITED_PASSWORD set
    if (
        results["credentials"]["file_exists"]
        and results["credentials"].get("UNITED_MP_NUMBER")
        and results["credentials"].get("UNITED_PASSWORD")
    ):
        checks_passed += 1

    checks_total = 3
    results["checks_passed"] = checks_passed
    results["checks_total"] = checks_total

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        _print_setup_report(results)

    # Exit 0 if the database is ready. Browsers and credentials are
    # checked at point of use (first scrape and /flights skill respectively).
    return 0 if db_ok else 1


def _print_setup_report(results):
    """Print a human-readable setup report with Rich formatting."""
    console = get_console()
    console.print("[bold]searchaero setup[/bold]")
    console.print()

    # Database
    db_info = results["database"]
    console.print("[bold]Database[/bold]")
    console.print(f"  Path:    [dim]{db_info['path']}[/dim]")
    if db_info["status"] == "ok":
        console.print("  Status:  [green]\u2713 Created (schema initialized)[/green]")
    else:
        console.print(f"  Status:  [red]\u2717 {db_info['status']}[/red]")
    console.print()

    # Playwright
    pw = results["playwright"]
    console.print("[bold]Playwright[/bold]")
    if pw["package"] is None:
        console.print("  Package:  [red]\u2717 not installed[/red]")
    else:
        console.print(f"  Package:  [green]\u2713 installed ({pw['package']})[/green]")
    if pw["browsers"]:
        if pw.get("browsers_auto_installed"):
            console.print("  Browsers: [green]\u2713 installed (auto)[/green]")
        else:
            console.print("  Browsers: [green]\u2713 installed[/green]")
    elif pw.get("browsers_skipped"):
        console.print("  Browsers: [yellow]\u26a0 not installed (skipped \u2014 --no-browser-install)[/yellow]")
    else:
        console.print("  Browsers: [red]\u2717 not installed[/red]")
    console.print()

    # Credentials
    creds = results["credentials"]
    if creds["file_exists"]:
        console.print(f"[bold]Credentials[/bold] [dim]({creds['file']})[/dim]")
    else:
        console.print(f"[bold]Credentials[/bold] [dim]({creds['file']})[/dim] - [red]not found[/red]")
    for key in ["UNITED_MP_NUMBER", "UNITED_PASSWORD"]:
        if creds.get(key):
            console.print(f"  {key + ':':20s} [green]\u2713 set[/green]")
        else:
            console.print(f"  {key + ':':20s} [red]\u2717 not set[/red]")
    console.print()

    # Schedules & Power (if present)
    sched = results.get("schedules")
    if sched:
        scheds = sched.get("schedules", [])
        wake = sched.get("wake_timers")
        if scheds:
            console.print("[bold]Schedules & Power[/bold]")
            console.print(f"  Active schedules: {len(scheds)}")
            for s in scheds:
                console.print(f"    - {s['name']} (every {s['interval']} min)")
            if wake:
                ac_icon = "\u2713" if wake["ac"] == "enabled" else "\u2717"
                ac_color = "green" if wake["ac"] == "enabled" else "red"
                console.print(f"  Wake timers (AC): [{ac_color}]{ac_icon} {wake['ac']}[/{ac_color}]")
            console.print("  [dim]Reminder: PC must sleep, not shut down, for wake-to-scrape.[/dim]")
            console.print()

    # Summary
    passed = results["checks_passed"]
    total = results["checks_total"]
    if passed == total:
        console.print(f"[bold green]Result: {passed}/{total} checks passed[/bold green]")
    else:
        console.print(f"[bold yellow]Result: {passed}/{total} checks passed[/bold yellow]")


def cmd_search(args):
    """Run award availability scraping — single route, batch, or parallel."""
    # Parse --months into list of ints
    if args.months:
        try:
            args._months_list = [int(m.strip()) for m in args.months.split(",")]
            if not all(1 <= m <= 12 for m in args._months_list):
                print("Error: --months values must be between 1 and 12")
                return 1
        except ValueError:
            print("Error: --months must be comma-separated numbers (e.g., 6,7,12)")
            return 1
    else:
        args._months_list = None

    has_route = bool(args.route)
    has_file = bool(args.file)

    # Validate: need one of route or file, not both, not neither
    if has_route and has_file:
        print("Error: provide either ORIGIN DEST or --file, not both")
        return 1
    if not has_route and not has_file:
        print("Error: provide either ORIGIN DEST or --file ROUTES_FILE")
        return 1

    # Validate --workers requires --file
    if args.workers > 1 and not has_file:
        print("Error: --workers requires --file")
        return 1

    # Aeroplan is single-route only in this MVP — batch/parallel not supported
    if getattr(args, "program", "united") == "aeroplan":
        if has_file:
            print("Error: --file is not supported for --program aeroplan (single-route only)")
            return 1
        if args.workers > 1:
            print("Error: --workers is not supported for --program aeroplan (single-route only)")
            return 1

    if has_file:
        # Validate file exists
        if not os.path.isfile(args.file):
            print(f"Error: routes file not found: {args.file}")
            return 1
        if args.workers > 1:
            # Parallel mode: delegate to orchestrate.py via subprocess.
            # The orchestrator manages independent browser instances across
            # multiple processes, which is hard to replicate in-process.
            return _search_parallel(args)
        else:
            return _search_batch(args)
    else:
        # Validate route args
        if len(args.route) != 2:
            print("Error: provide exactly two route codes: ORIGIN DEST")
            return 1
        orig, dest = args.route[0].upper(), args.route[1].upper()
        if not (orig.isalpha() and len(orig) == 3):
            print(f"Error: invalid IATA code: {args.route[0]}")
            return 1
        if not (dest.isalpha() and len(dest) == 3):
            print(f"Error: invalid IATA code: {args.route[1]}")
            return 1
        args.route = [orig, dest]
        return _search_single_inproc(args)


def _scrape_route_live(origin, dest, conn, delay=3.0, json_mode=False, headless=True, mfa_prompt=None, proxy=None, ephemeral=False, mfa_method="sms", months=None, from_date=None, to_date=None):
    """Scrape a single route in-process. Reusable by both search and query --refresh.

    Starts CookieFarm, logs in, scrapes matching windows,
    handles browser crash with one retry, cleans up.

    Args:
        origin: IATA origin code (uppercase).
        dest: IATA destination code (uppercase).
        conn: SQLite connection (schema must already exist).
        delay: Seconds between API calls.
        json_mode: If True, suppress verbose stdout output.
        headless: Accepted for API compat but IGNORED — cookie_farm force-runs
            United headed (Akamai blocks headless). The visible window always opens.
        proxy: Proxy URL (e.g., socks5://user:pass@host:port). Also reads PROXY_URL env var.
        ephemeral: If True, use ephemeral browser profile (default: persistent).
        mfa_method: MFA delivery channel — "sms" or "email" (default: "sms").

    Returns:
        dict with keys: found, stored, rejected, errors, total_windows, circuit_break, error_messages.
    """
    farm = None
    scraper = None
    _cleanup_mfa_files()
    try:
        _log("Starting cookie farm...")
        farm = CookieFarm(headless=headless, ephemeral=ephemeral, proxy=proxy)
        farm.start()
        _log("Logging in to United...")
        farm.ensure_logged_in(mfa_prompt=mfa_prompt or _prompt_sms_code, mfa_method=mfa_method)
        _log("Login confirmed")
        _signal_login_complete()

        _log("Starting hybrid scraper...")
        scraper = HybridScraper(farm, refresh_interval=2)
        scraper.start()
        window_desc = f"months {months}" if months else "12 windows"
        _log(f"Scraper ready — scraping {origin}-{dest} ({window_desc})")

        totals, browser_crashed = _scrape_with_crash_detection(
            origin, dest, conn, scraper, delay=delay,
            verbose=not json_mode,
            months=months, from_date=from_date, to_date=to_date,
        )

        if browser_crashed:
            _log("BROWSER CRASH detected — restarting browser and retrying (this is usually transient)...")
            scraper.stop()
            farm.restart()
            scraper.start()
            scraper.reset_backoff()
            totals, _ = _scrape_with_crash_detection(
                origin, dest, conn, scraper, delay=delay,
                verbose=not json_mode,
                months=months, from_date=from_date, to_date=to_date,
            )

        return totals

    finally:
        if scraper:
            try:
                _log("Stopping scraper...")
                scraper.stop()
                _log("Scraper stopped")
            except Exception as e:
                _log(f"WARNING: scraper.stop() failed: {e}")
        if farm:
            try:
                _log("Stopping cookie farm (killing browser)...")
                farm.stop()
                _log("Cookie farm stopped")
            except Exception as e:
                _log(f"WARNING: farm.stop() failed: {e}")


def _aeroplan_warmup(session):
    """Absorb Air Canada's post-login OIDC consent redirect after a FRESH login.

    After `ensure_logged_in` confirms login, Air Canada finishes an OIDC consent
    hop (…/clogin/pages/proxy?mode=afterConsent…) that fires asynchronously —
    often DURING the first scrape window's page.goto, interrupting it so window 1
    captures nothing (observed live 2026-06-03: earliest ~3 days lost). The
    scraper's pre-window URL poll doesn't help because the redirect hasn't
    started yet when it checks. The fix is a throwaway NAVIGATE to the home page
    so the consent hop completes here, before window 1. Best-effort; never raises.
    """
    page = getattr(session, "page", None)
    if page is None:
        return
    try:
        from core.aeroplan_session import AIRCANADA_HOME
        page.goto(AIRCANADA_HOME, wait_until="domcontentloaded", timeout=60000)
    except Exception as exc:
        _log(f"  warm-up navigate interrupted (expected on fresh login): {exc}")
    # Poll up to ~20s for the redirect chain to quiesce off login/consent URLs.
    # Bail immediately if page.url is not a real string (mock/test) — keeps the
    # offline CLI tests instant.
    markers = ("clogin", "afterlogin", "afterconsent", "login.aircanada",
               "socialize", "idpresponse")
    deadline = time.time() + 20.0
    stable_since = None
    last = None
    while time.time() < deadline:
        try:
            url = page.url
        except Exception:
            url = None
        if not isinstance(url, str):
            return  # not a real browser page (e.g. under test) — nothing to do
        on_redirect = any(m in url.lower() for m in markers)
        if not on_redirect:
            if url == last and stable_since is not None:
                if time.time() - stable_since >= 2.0:
                    break
            else:
                stable_since = time.time()
        else:
            stable_since = None
        last = url
        time.sleep(0.5)
    time.sleep(2)  # extra fixed settle once the chain has quiesced


def _scrape_route_aeroplan_live(origin, dest, conn, *, delay, json_mode, mfa_method, months, from_date, to_date, max_reauths=4, deadline_seconds=None):
    """Scrape a single route from Aeroplan in-process (HEADED browser only).

    Constructs an AeroplanSession (headed — never headless), ensures login,
    then drives the route through a BOUNDED re-auth-and-resume loop
    (`core.aeroplan_runner`). A single Aeroplan session lasts only ~30-40 min,
    so a wide date span may outlive one session: when the scraper surfaces
    `expired=True` with windows remaining, the loop re-authenticates
    (`ensure_logged_in` + warm-up) and resumes from the next unscraped window,
    accumulating totals across batches. Aeroplan uses 5-day windows.

    Args:
        origin: IATA origin code (uppercase).
        dest: IATA destination code (uppercase).
        conn: SQLite connection (schema must already exist).
        delay: Seconds between API calls.
        json_mode: If True, suppress verbose stdout output.
        mfa_method: MFA delivery channel (Aeroplan defaults to "email").
        months: Optional list of month ints to restrict scraping.
        from_date / to_date: Optional date-window filters (YYYY-MM-DD).
        max_reauths: Max re-authentications before stopping (default 4).
        deadline_seconds: Overall wall-clock budget for the span (None = none).

    Returns:
        Aggregated dict with keys: found, stored, rejected, errors,
        total_windows, expired, error_messages, plus reauths, batches and
        span_complete (False iff a cap stopped the span before full coverage).
    """
    from core.aeroplan_session import AeroplanSession
    from core.aeroplan_scraper import scrape_route_aeroplan
    from core.aeroplan_runner import run_aeroplan_route_with_reauth

    session = None
    try:
        _log("Starting Aeroplan session (headed browser)...")
        session = AeroplanSession()  # headed only — do NOT pass headless=True
        session.start()

        _log("Logging in to Aeroplan...")
        result = session.ensure_logged_in(mfa_method=mfa_method)
        if not result.ok:
            detail = getattr(result, "detail", None)
            msg = f"Aeroplan login failed (status: {result.status})"
            if detail:
                msg += f": {detail}"
            if not json_mode:
                print(f"Error: {msg}")
            raise RuntimeError(msg)
        _log("Aeroplan login confirmed")

        # Warm-up navigate to absorb the post-login OIDC consent redirect before
        # window 1 (otherwise the first window's goto gets interrupted and that
        # window captures nothing — observed live 2026-06-03).
        _log("Warm-up navigate (settling post-login redirect chain)...")
        _aeroplan_warmup(session)

        _log(f"Scraping {origin}-{dest} via Aeroplan (5-day windows, bounded re-auth loop)...")
        totals = run_aeroplan_route_with_reauth(
            origin, dest, session, conn,
            scrape_fn=scrape_route_aeroplan,
            warmup=_aeroplan_warmup,
            mfa_method=mfa_method,
            from_date=from_date, to_date=to_date, months=months,
            max_reauths=max_reauths, deadline_seconds=deadline_seconds,
            verbose=not json_mode,
            delay=delay,
        )
        if not totals.get("span_complete", True):
            _log(
                f"WARNING: Aeroplan span for {origin}-{dest} NOT fully covered "
                f"(reauths={totals.get('reauths')}, batches={totals.get('batches')}) "
                f"— a re-auth/deadline cap was hit."
            )
        return totals

    finally:
        if session:
            try:
                _log("Stopping Aeroplan session (killing browser)...")
                session.stop()
                _log("Aeroplan session stopped")
            except Exception as e:
                _log(f"WARNING: session.stop() failed: {e}")


def _search_single_inproc(args):
    """Scrape a single route in-process using the hybrid scraper pipeline."""
    orig, dest = args.route
    conn = None
    program = getattr(args, "program", "united")

    try:
        _log("Connecting to database...")
        conn = db.get_connection(args.db_path)
        db.ensure_schema(conn)

        if program == "aeroplan":
            totals = _scrape_route_aeroplan_live(
                orig, dest, conn,
                delay=args.delay, json_mode=args.json, mfa_method=args.mfa_method,
                months=args._months_list, from_date=args.search_from, to_date=args.search_to,
            )
        else:
            mfa_prompt = _get_mfa_prompt(args)
            totals = _scrape_route_live(orig, dest, conn, delay=args.delay, json_mode=args.json, headless=args.headless, mfa_prompt=mfa_prompt, proxy=getattr(args, 'proxy', None), ephemeral=args.ephemeral, mfa_method=args.mfa_method, months=args._months_list, from_date=args.search_from, to_date=args.search_to)

        # Output results
        if args.json:
            payload = {
                "route": f"{orig}-{dest}",
                "found": totals["found"],
                "stored": totals["stored"],
                "rejected": totals["rejected"],
                "errors": totals["errors"],
            }
            if "expired" in totals:
                payload["expired"] = totals["expired"]
            # Surface re-auth/cap signals so unattended JSON consumers can detect
            # a partially-covered span (the non-JSON path warns; JSON must too).
            for k in ("span_complete", "reauths", "batches"):
                if k in totals:
                    payload[k] = totals[k]
            print(json.dumps(payload, indent=2))
        else:
            console = get_console()
            console.print()
            summary = (f"[bold]{orig}-{dest}[/bold]: "
                       f"[green]{totals['found']}[/green] found, "
                       f"[green]{totals['stored']}[/green] stored, "
                       f"{totals['rejected']} rejected, "
                       f"{totals['errors']} errors")
            if "expired" in totals:
                summary += f", {totals['expired']} expired"
            console.print(summary)
            console.print()
            console.print(f"  [dim]→ Query results:[/dim] searchaero query {orig} {dest}")
            console.print(f"  [dim]→ Business class:[/dim] searchaero query {orig} {dest} --cabin business --sort miles")

        return 0

    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "route": f"{orig}-{dest}"}))
        else:
            err_str = str(exc).lower()
            print(f"Error: {exc}")
            if "browser" in err_str or "crash" in err_str or "akamai" in err_str:
                print(f"\n  Tip: This is usually transient. Retry the same command.")
                print(f"  If it persists, wait 10 min or use --proxy.")
            elif "mfa" in err_str or "sms" in err_str or "timeout" in err_str:
                print(f"\n  Tip: Re-run the search — United will send a new SMS code.")
        return 1

    finally:
        if conn:
            try:
                _log("Closing database connection...")
                conn.close()
                _log("Database connection closed")
            except Exception as e:
                _log(f"WARNING: conn.close() failed: {e}")


def _search_batch(args):
    """Scrape multiple routes from a file in-process using one browser session."""
    conn = None
    farm = None
    scraper = None

    try:
        mfa_prompt = _get_mfa_prompt(args)

        # Read routes from file (one "ORIGIN DEST" per line, skip blank/comment)
        routes = _load_routes(args.file)

        if not routes:
            print("Error: no valid routes found in file")
            return 1

        _log(f"Loaded {len(routes)} routes from {args.file}")

        # Connect to database and ensure schema exists
        _log("Connecting to database...")
        conn = db.get_connection(args.db_path)
        db.ensure_schema(conn)

        # Start cookie farm
        _log("Starting cookie farm...")
        farm = CookieFarm(headless=args.headless, ephemeral=args.ephemeral, proxy=getattr(args, 'proxy', None))
        farm.start()
        _log("Logging in to United...")
        farm.ensure_logged_in(mfa_prompt=mfa_prompt, mfa_method=args.mfa_method)
        _log("Login confirmed")

        # Start hybrid scraper
        _log("Starting hybrid scraper...")
        scraper = HybridScraper(farm, refresh_interval=2)
        scraper.start()
        _log("Scraper ready — starting batch")

        # Scrape each route, aggregating totals
        per_route = []
        agg = {"found": 0, "stored": 0, "rejected": 0, "errors": 0}
        consecutive_circuit_breaks = 0
        total_burns = 0
        BURN_LIMIT = 10
        aborted = False
        abort_reason = None

        for idx, (orig, dest) in enumerate(routes, 1):
            _log(f"Route {idx}/{len(routes)}: {orig}-{dest}")

            totals = scrape_route(
                orig, dest, conn, scraper,
                delay=args.delay, verbose=not args.json,
                months=args._months_list, from_date=args.search_from, to_date=args.search_to,
            )
            per_route.append({"route": f"{orig}-{dest}", **totals})
            for key in agg:
                agg[key] += totals.get(key, 0)
            _log(f"  {orig}-{dest} done — {totals['found']} found, {totals['stored']} stored, {totals['errors']} errors")

            # Browser crash detection
            if detect_browser_crash(totals):
                _log(f"  BROWSER CRASH on {orig}-{dest} — restarting browser...")
                scraper.stop()
                farm.restart()
                farm.ensure_logged_in(mfa_prompt=mfa_prompt, mfa_method=args.mfa_method)
                scraper.start()
                scraper.reset_backoff()
                _log(f"  Browser restarted, retrying {orig}-{dest}...")
                # Subtract old totals before retry
                for key in agg:
                    agg[key] -= totals.get(key, 0)
                # Retry the route once
                totals = scrape_route(
                    orig, dest, conn, scraper,
                    delay=args.delay, verbose=not args.json,
                    months=args._months_list, from_date=args.search_from, to_date=args.search_to,
                )
                per_route[-1] = {"route": f"{orig}-{dest}", **totals}
                for key in agg:
                    agg[key] += totals.get(key, 0)
                time.sleep(10)

            # Circuit breaker handling
            if totals.get("circuit_break"):
                total_burns += 1
                consecutive_circuit_breaks += 1
                if total_burns >= BURN_LIMIT:
                    _log(f"  BURN LIMIT REACHED ({total_burns}/{BURN_LIMIT}) — aborting batch")
                    aborted = True
                    abort_reason = "burn_limit"
                    break
                if consecutive_circuit_breaks >= 2:
                    _log("  2 consecutive circuit breaks — aborting batch")
                    aborted = True
                    abort_reason = "consecutive_circuit_breaks"
                    break
                _log("  Circuit breaker: refreshing session...")
                scraper.stop()
                farm.refresh_cookies()
                farm.ensure_logged_in(mfa_prompt=mfa_prompt, mfa_method=args.mfa_method)
                scraper.start()
                scraper.reset_backoff()
                _log("  Session refreshed, continuing")
            else:
                consecutive_circuit_breaks = 0

        _log(f"Batch complete: {len(per_route)}/{len(routes)} routes — {agg['found']} found, {agg['stored']} stored, {agg['errors']} errors")

        # Output results
        if args.json:
            output = {
                "routes": per_route,
                "totals": agg,
            }
            if aborted:
                output["aborted"] = True
                output["abort_reason"] = abort_reason
            print(json.dumps(output, indent=2))
        else:
            console = get_console()
            console.print()
            console.print(f"[bold]Batch complete[/bold]: {len(per_route)} route(s)")
            console.print(f"  Found:    [green]{agg['found']}[/green]")
            console.print(f"  Stored:   [green]{agg['stored']}[/green]")
            console.print(f"  Rejected: {agg['rejected']}")
            console.print(f"  Errors:   {agg['errors']}")
            if aborted:
                console.print(f"  [red]Aborted: {abort_reason}[/red]")

        # Exit code: 1 if total failure (all errors, nothing found)
        if agg["errors"] > 0 and agg["found"] == 0:
            return 1
        return 0

    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"Error: {exc}")
        return 1

    finally:
        if scraper:
            try:
                _log("Stopping scraper...")
                scraper.stop()
                _log("Scraper stopped")
            except Exception as e:
                _log(f"WARNING: scraper.stop() failed: {e}")
        if farm:
            try:
                _log("Stopping cookie farm (killing browser)...")
                farm.stop()
                _log("Cookie farm stopped")
            except Exception as e:
                _log(f"WARNING: farm.stop() failed: {e}")
        if conn:
            try:
                _log("Closing database connection...")
                conn.close()
                _log("Database connection closed")
            except Exception as e:
                _log(f"WARNING: conn.close() failed: {e}")


def _search_parallel(args):
    """Delegate to orchestrate.py via subprocess for multi-worker parallel scraping.

    Parallel mode uses subprocess because the orchestrator manages independent
    browser instances across multiple processes — replicating that in-process
    would require complex multiprocessing with Playwright contexts.
    """
    cmd = [sys.executable, ORCHESTRATE_PY, "--routes-file", args.file,
           "--workers", str(args.workers), "--create-schema",
           "--delay", str(args.delay)]
    if args.headless:
        cmd.append("--headless")
    if args.db_path:
        cmd.extend(["--db-path", args.db_path])
    if args.skip_scanned:
        cmd.append("--skip-scanned")
    else:
        cmd.append("--no-skip-scanned")
    if args.json:
        result = subprocess.run(cmd, capture_output=True, text=True)
        summary = {
            "command": " ".join(cmd),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        print(json.dumps(summary, indent=2))
        return result.returncode
    else:
        result = subprocess.run(cmd)
        return result.returncode


def cmd_query(args):
    """Query stored availability data and display results.

    Returns:
        int: 0 on success, 1 on error or no results.
    """
    import datetime as _dt

    # Validate route codes
    if len(args.route) != 2:
        print("Error: provide exactly two route codes: ORIGIN DEST")
        return 1
    origin, dest = args.route[0].upper(), args.route[1].upper()
    if not (origin.isalpha() and len(origin) == 3):
        print(f"Error: invalid IATA code: {args.route[0]}")
        return 1
    if not (dest.isalpha() and len(dest) == 3):
        print(f"Error: invalid IATA code: {args.route[1]}")
        return 1

    # Validate --date is mutually exclusive with --from/--to
    if args.date and (args.date_from or args.date_to):
        print("Error: --date cannot be combined with --from/--to")
        return 1

    # Validate --csv is mutually exclusive with --json
    if args.csv and args.json:
        print("Error: --csv cannot be combined with --json")
        return 1

    # Validate --history is mutually exclusive with --from/--to
    if args.history and (args.date_from or args.date_to):
        print("Error: --history cannot be combined with --from/--to")
        return 1

    # Validate --refresh is mutually exclusive with --history
    if getattr(args, 'refresh', False) and args.history:
        print("Error: --refresh cannot be combined with --history")
        return 1

    # Validate --graph and --summary cannot be combined with --history
    if args.graph and args.history:
        print("Error: --graph cannot be combined with --history")
        return 1
    if args.summary and args.history:
        print("Error: --summary cannot be combined with --history")
        return 1

    if args.table_view and args.history:
        print("Error: --table-view cannot be combined with --history")
        return 1

    # Validate format flags are mutually exclusive
    format_flags = sum([args.graph, args.summary, args.csv, args.json, bool(args.table_view)])
    if format_flags > 1:
        print("Error: --graph, --summary, --csv, --json, and --table-view are mutually exclusive")
        return 1

    # Validate date format if provided
    if args.date:
        try:
            _dt.date.fromisoformat(args.date)
        except ValueError:
            print(f"Error: invalid date format: {args.date} (expected YYYY-MM-DD)")
            return 1

    if args.date_from:
        try:
            _dt.date.fromisoformat(args.date_from)
        except ValueError:
            print(f"Error: invalid date format: {args.date_from} (expected YYYY-MM-DD)")
            return 1

    if args.date_to:
        try:
            _dt.date.fromisoformat(args.date_to)
        except ValueError:
            print(f"Error: invalid date format: {args.date_to} (expected YYYY-MM-DD)")
            return 1

    # Validate --from <= --to if both provided
    if args.date_from and args.date_to:
        if args.date_from > args.date_to:
            print(f"Error: --from ({args.date_from}) must be before --to ({args.date_to})")
            return 1

    # Expand cabin filter
    cabin_filter = _CABIN_FILTER_MAP.get(args.cabin) if args.cabin else None

    if args.history:
        return _cmd_query_history(args, origin, dest, cabin_filter)

    conn = db.get_connection(args.db_path)
    freshness = None
    refreshed = False
    try:
        # Check freshness and auto-scrape if requested
        freshness = db.get_route_freshness(conn, origin, dest,
                                           ttl_seconds=int(getattr(args, 'ttl', 12.0) * 3600))
        if getattr(args, 'refresh', False) and freshness["is_stale"]:
            if freshness["has_data"]:
                age_hours = freshness["age_seconds"] / 3600
                _log(f"Data for {origin}-{dest} is stale (age: {age_hours:.1f}h, TTL: {getattr(args, 'ttl', 12.0)}h) — scraping fresh data...")
            else:
                _log(f"No data for {origin}-{dest} — scraping fresh data...")
            try:
                db.ensure_schema(conn)
                mfa_prompt = _get_mfa_prompt(args)
                _scrape_route_live(origin, dest, conn, json_mode=args.json, mfa_prompt=mfa_prompt, proxy=getattr(args, 'proxy', None), mfa_method=args.mfa_method)
                refreshed = True
                _log("Scrape complete — querying fresh data")
                # Re-check freshness after scrape
                freshness = db.get_route_freshness(conn, origin, dest,
                                                   ttl_seconds=int(getattr(args, 'ttl', 12.0) * 3600))
            except Exception as exc:
                _log(f"WARNING: Auto-scrape failed: {exc}")
                _log("Returning cached data (may be stale)")

        rows = db.query_availability(conn, origin, dest, date=args.date,
                                     date_from=args.date_from, date_to=args.date_to,
                                     cabin=cabin_filter, program=args.program)
    finally:
        conn.close()

    if not rows:
        if args.json:
            print(json.dumps({"error": "no_results", "message": f"No availability found for {origin}-{dest}", "suggestion": "Run 'searchaero search' to scrape data first"}))
        else:
            console = get_console()
            console.print(f"No availability found for [bold]{origin}-{dest}[/bold]")
            console.print(f"  [dim]→ Scrape data first:[/dim] searchaero search {origin} {dest}")
            console.print(f"  [dim]→ Or auto-scrape:[/dim]   searchaero query {origin} {dest} --refresh")
        return 1

    # Apply sort
    if args.sort != "date":
        rows = sorted(rows, key=_SORT_KEYS[args.sort])

    # Presentation output modes
    if args.graph:
        # Aggregate to per-date cheapest miles
        by_date = {}
        for r in rows:
            d = r["date"]
            if d not in by_date or r["miles"] < by_date[d]["miles"]:
                by_date[d] = {"date": d, "miles": r["miles"],
                              "cabin": r["cabin"], "award_type": r["award_type"]}
        trend = sorted(by_date.values(), key=lambda x: x["date"])
        print(presentation.format_price_chart(trend, origin, dest, cabin_filter=args.cabin))
        return 0

    if args.table_view == "programs":
        print(presentation.format_programs_table(rows, origin, dest))
        return 0

    if args.summary:
        summary = presentation.compute_summary(rows)
        print(presentation.format_summary_card(summary, origin, dest, count=len(rows)))
        return 0

    # Output
    if args.json:
        output_rows = rows
        if args.fields:
            selected = [f.strip() for f in args.fields.split(",")]
            # Validate field names
            valid_fields = {"date", "cabin", "award_type", "miles", "taxes_cents", "scraped_at"}
            invalid = set(selected) - valid_fields
            if invalid:
                print(json.dumps({"error": "invalid_args", "message": f"Unknown fields: {', '.join(sorted(invalid))}", "suggestion": f"Valid fields: {', '.join(sorted(valid_fields))}"}))
                return 1
            output_rows = [{k: v for k, v in row.items() if k in selected} for row in rows]
        if getattr(args, 'meta', False):
            from core.output import build_meta, build_freshness
            from core.schema import get_schema
            schema = get_schema("query")
            meta = build_meta(schema.get("output_fields", {}))
            freshness_meta = build_freshness(freshness, getattr(args, 'ttl', 12.0), refreshed)
            print(json.dumps({"data": output_rows, **meta, **freshness_meta}, indent=2))
        else:
            print(json.dumps(output_rows, indent=2))
        return 0

    if args.csv:
        _print_query_csv(rows)
        return 0

    if args.date:
        _print_query_detail(rows, origin, dest, args.date)
    else:
        _print_query_summary(rows, origin, dest)
    return 0


def _cmd_query_history(args, origin, dest, cabin_filter):
    """Handle --history mode for cmd_query."""
    conn = db.get_connection(args.db_path)
    try:
        if args.date:
            rows = db.query_history(conn, origin, dest, date=args.date, cabin=cabin_filter, program=args.program)
            if not rows:
                if args.json:
                    print(json.dumps({"error": "no_results", "message": f"No price history for {origin}-{dest} on {args.date}", "suggestion": "Run 'searchaero search' to scrape data first"}))
                else:
                    print(f"No price history for {origin}-{dest} on {args.date}")
                return 1
            if args.sort != "date":
                rows = sorted(rows, key=_SORT_KEYS[args.sort])
            if args.json:
                if getattr(args, 'meta', False):
                    from core.output import build_meta
                    from core.schema import get_schema
                    schema = get_schema("query")
                    meta = build_meta(schema.get("output_fields", {}))
                    print(json.dumps({"data": rows, **meta}, indent=2))
                else:
                    print(json.dumps(rows, indent=2))
            elif args.csv:
                _print_query_csv(rows)
            else:
                _print_query_history_detail(rows, origin, dest, args.date)
        else:
            stats = db.get_history_stats(conn, origin, dest, cabin=cabin_filter, program=args.program)
            if not stats:
                if args.json:
                    print(json.dumps({"error": "no_results", "message": f"No price history for {origin}-{dest}", "suggestion": "Run 'searchaero search' to scrape data first"}))
                else:
                    print(f"No price history for {origin}-{dest}")
                return 1
            if args.json:
                if getattr(args, 'meta', False):
                    from core.output import build_meta
                    from core.schema import get_schema
                    schema = get_schema("query")
                    meta = build_meta(schema.get("output_fields", {}))
                    print(json.dumps({"data": stats, **meta}, indent=2))
                else:
                    print(json.dumps(stats, indent=2))
            elif args.csv:
                _print_query_csv(stats)
            else:
                current_rows = db.query_availability(conn, origin, dest, cabin=cabin_filter, program=args.program)
                _print_query_history_summary(stats, current_rows, origin, dest, conn=conn)
    finally:
        conn.close()
    return 0


def _print_query_summary(rows, origin, dest):
    """Print a date-by-cabin summary table using Rich."""
    from collections import defaultdict
    from rich.table import Table

    dates = defaultdict(dict)  # date -> {cabin_group: lowest_miles}
    for row in rows:
        if row["award_type"] != "Saver":
            continue
        group = _CABIN_GROUPS.get(row["cabin"])
        if not group:
            continue
        d = row["date"]
        current = dates[d].get(group)
        if current is None or row["miles"] < current:
            dates[d][group] = row["miles"]

    if not dates:
        # No saver fares -- fall back to showing all award types
        for row in rows:
            group = _CABIN_GROUPS.get(row["cabin"])
            if not group:
                continue
            d = row["date"]
            current = dates[d].get(group)
            if current is None or row["miles"] < current:
                dates[d][group] = row["miles"]

    cabins = ["Economy", "Business", "First"]
    table = Table(title=f"{origin} \u2192 {dest}  ({len(dates)} dates found)")
    table.add_column("Date", style="bold")
    for c in cabins:
        table.add_column(c, justify="right")

    for d in sorted(dates):
        cols = []
        for c in cabins:
            miles = dates[d].get(c)
            cols.append(f"[green]{miles:,}[/green]" if miles else "[dim]\u2014[/dim]")
        table.add_row(d, *cols)

    get_console().print(table)


def _print_query_detail(rows, origin, dest, date):
    """Print all availability records for a specific date using Rich."""
    from rich.table import Table

    table = Table(title=f"{origin} \u2192 {dest}  {date}")
    table.add_column("Cabin", style="bold")
    table.add_column("Type")
    table.add_column("Miles", justify="right")
    table.add_column("Taxes", justify="right")
    table.add_column("Updated", style="dim")

    for row in rows:
        taxes = f"${row['taxes_cents'] / 100:.2f}" if row["taxes_cents"] is not None else "[dim]\u2014[/dim]"
        miles = f"[green]{row['miles']:,}[/green]"
        table.add_row(row["cabin"], row["award_type"], miles, taxes, row["scraped_at"])

    get_console().print(table)


def _print_query_csv(rows):
    """Print query results as CSV to stdout."""
    import csv
    import sys

    if not rows:
        return
    writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)


def _print_query_history_detail(rows, origin, dest, date):
    """Print price history timeline for a specific flight date using Rich."""
    from rich.table import Table

    table = Table(title=f"{origin} \u2192 {dest}  {date}  Price History ({len(rows)} observations)")
    table.add_column("Observed", style="dim")
    table.add_column("Cabin", style="bold")
    table.add_column("Type")
    table.add_column("Miles", justify="right")
    table.add_column("Taxes", justify="right")

    for row in rows:
        taxes = f"${row['taxes_cents'] / 100:.2f}" if row["taxes_cents"] is not None else "[dim]\u2014[/dim]"
        miles = f"[green]{row['miles']:,}[/green]"
        scraped = row["scraped_at"][:16]
        table.add_row(scraped, row["cabin"], row["award_type"], miles, taxes)

    get_console().print(table)


def _print_query_history_summary(stats, current_rows, origin, dest, conn=None):
    """Print route-level price history summary using Rich."""
    from collections import defaultdict
    from rich.table import Table

    # Group stats by cabin group + award_type
    grouped = defaultdict(lambda: {"lowest": float("inf"), "highest": 0, "observations": 0})
    for s in stats:
        group = _CABIN_GROUPS.get(s["cabin"])
        if not group:
            continue
        key = (group, s["award_type"])
        grouped[key]["lowest"] = min(grouped[key]["lowest"], s["lowest_miles"])
        grouped[key]["highest"] = max(grouped[key]["highest"], s["highest_miles"])
        grouped[key]["observations"] += s["observations"]

    # Get current values per group + award_type
    current = {}
    for row in current_rows:
        group = _CABIN_GROUPS.get(row["cabin"])
        if not group:
            continue
        key = (group, row["award_type"])
        cur = current.get(key)
        if cur is None or row["miles"] < cur:
            current[key] = row["miles"]

    table = Table(title=f"{origin} \u2192 {dest}  Price History")
    table.add_column("Cabin", style="bold")
    table.add_column("Type")
    table.add_column("Lowest", justify="right")
    table.add_column("Highest", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Obs", justify="right")

    for cabin_group in ["Economy", "Business", "First"]:
        for award_type in ["Saver", "Standard"]:
            key = (cabin_group, award_type)
            g = grouped.get(key)
            if not g or g["observations"] == 0:
                continue
            low = f"[green]{g['lowest']:,}[/green]"
            high = f"[red]{g['highest']:,}[/red]"
            cur_val = current.get(key)
            cur = f"{cur_val:,}" if cur_val else "[dim]\u2014[/dim]"
            table.add_row(cabin_group, award_type, low, high, cur, str(g["observations"]))

    get_console().print(table)


def cmd_deals(args):
    """Find best deals across all cached routes."""
    max_results = max(1, min(getattr(args, 'max_results', 10), 25))
    cabin_filter = _CABIN_FILTER_MAP.get(args.cabin) if args.cabin else None

    conn = db.get_connection(args.db_path)
    try:
        deals = db.find_deals_query(conn, cabin=cabin_filter, max_results=max_results)
    finally:
        conn.close()

    if not deals:
        if args.json:
            print(json.dumps({"deals_found": 0, "message": "No deals found."}))
        else:
            print("No deals found. Data may be too fresh for comparison.")
        return 0

    if args.json:
        print(json.dumps({"deals_found": len(deals), "deals": deals}, indent=2))
    else:
        print(presentation.format_deals_table(deals, cabin_filter=args.cabin))
    return 0


def _format_size(size_bytes):
    """Format byte count as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _print_status_report(stats):
    """Print a human-readable status report with Rich formatting."""
    console = get_console()
    console.print("[bold]searchaero status[/bold]")
    console.print()

    # Database
    db_stats = stats["database"]
    console.print("[bold]Database[/bold]")
    console.print(f"  Path:          [dim]{db_stats['path']}[/dim]")
    console.print(f"  Size:          {_format_size(db_stats['size_bytes'])}")
    console.print()

    # Availability
    avail = stats["availability"]
    console.print("[bold]Availability[/bold]")
    if avail["total_rows"] == 0:
        console.print("  [dim]No data yet. Run 'searchaero search' to scrape availability.[/dim]")
    else:
        console.print(f"  Records:       [green]{avail['total_rows']:,}[/green]")
        console.print(f"  Routes:        [green]{avail['routes_covered']:,}[/green]")
        date_range = f"{avail['date_range_start']} to {avail['date_range_end']}" if avail["date_range_start"] else "\u2014"
        console.print(f"  Date range:    {date_range}")
        latest = avail["latest_scrape"] or "\u2014"
        console.print(f"  Latest scrape: {latest}")
    console.print()

    # Jobs
    jobs = stats["jobs"]
    console.print("[bold]Scrape Jobs[/bold]")
    if jobs["total_jobs"] == 0:
        console.print("  [dim]No scrape jobs recorded yet.[/dim]")
    else:
        console.print(f"  Completed:     [green]{jobs['completed']:,}[/green]")
        console.print(f"  Failed:        [red]{jobs['failed']:,}[/red]")
        console.print(f"  Total:         {jobs['total_jobs']:,}")


def cmd_status(args):
    """Show database statistics and data coverage.

    Returns:
        int: 0 always (status is informational).
    """
    actual_path = args.db_path or os.getenv("SEARCHAERO_DB", db.DEFAULT_DB_PATH)

    if not os.path.exists(actual_path):
        if args.json:
            print(json.dumps({"error": "no_database", "path": actual_path}))
        else:
            print(f"No database found at {actual_path}")
            print("Run 'searchaero setup' to initialize.")
        return 0

    conn = db.get_connection(args.db_path)
    try:
        avail_stats = db.get_scrape_stats(conn)
        job_stats = db.get_job_stats(conn)
    finally:
        conn.close()

    file_size = os.path.getsize(actual_path)

    stats = {
        "database": {
            "path": actual_path,
            "size_bytes": file_size,
        },
        "availability": avail_stats,
        "jobs": job_stats,
    }

    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        _print_status_report(stats)

    return 0


def cmd_alert(args):
    """Manage price alerts."""
    if not args.alert_command:
        print("Usage: searchaero alert {add,list,remove,check}")
        print("Run 'searchaero alert <command> --help' for details.")
        return 1

    if args.alert_command == "add":
        return _alert_add(args)
    if args.alert_command == "list":
        return _alert_list(args)
    if args.alert_command == "remove":
        return _alert_remove(args)
    if args.alert_command == "check":
        return _alert_check(args)
    return 0


def _alert_add(args):
    """Add a new price alert."""
    import datetime as _dt

    origin, dest = args.route[0].upper(), args.route[1].upper()
    if not (origin.isalpha() and len(origin) == 3):
        print(f"Error: invalid IATA code: {args.route[0]}")
        return 1
    if not (dest.isalpha() and len(dest) == 3):
        print(f"Error: invalid IATA code: {args.route[1]}")
        return 1

    if args.max_miles <= 0:
        print(f"Error: --max-miles must be positive, got {args.max_miles}")
        return 1

    if args.date_from:
        try:
            _dt.date.fromisoformat(args.date_from)
        except ValueError:
            print(f"Error: invalid date format: {args.date_from} (expected YYYY-MM-DD)")
            return 1
    if args.date_to:
        try:
            _dt.date.fromisoformat(args.date_to)
        except ValueError:
            print(f"Error: invalid date format: {args.date_to} (expected YYYY-MM-DD)")
            return 1
    if args.date_from and args.date_to and args.date_from > args.date_to:
        print(f"Error: --from ({args.date_from}) must be before --to ({args.date_to})")
        return 1

    conn = db.get_connection(args.db_path)
    try:
        alert_id = db.create_alert(conn, origin, dest, args.max_miles,
                                   cabin=args.cabin, date_from=args.date_from,
                                   date_to=args.date_to)
    finally:
        conn.close()

    if args.json:
        print(json.dumps({"id": alert_id, "status": "created"}))
    else:
        parts = [f"{origin}-{dest}"]
        if args.cabin:
            parts.append(args.cabin)
        parts.append(f"\u2264{args.max_miles:,} miles")
        if args.date_from or args.date_to:
            dr = f"{args.date_from or '...'} to {args.date_to or '...'}"
            parts.append(dr)
        print(f"Alert #{alert_id} created: {', '.join(parts)}")
    return 0


def _alert_list(args):
    """List price alerts."""
    show_all = getattr(args, "all", False)
    conn = db.get_connection(args.db_path)
    try:
        alerts = db.list_alerts(conn, active_only=not show_all)
    finally:
        conn.close()

    if not alerts:
        if args.json:
            print(json.dumps([]))
        else:
            print("No active alerts." if not show_all else "No alerts.")
        return 0

    if args.json:
        print(json.dumps(alerts, indent=2))
        return 0

    print(f"{'ID':>4}  {'Route':<10}{'Cabin':<12}{'Max Miles':>10}  {'Date Range':<24}{'Status'}")
    for a in alerts:
        route = f"{a['origin']}-{a['destination']}"
        cabin = a["cabin"] or "any"
        miles = f"{a['max_miles']:,}"
        date_range = ""
        if a.get("date_from") or a.get("date_to"):
            date_range = f"{a.get('date_from') or '...'} to {a.get('date_to') or '...'}"
        status = "active" if a["active"] else "expired"
        print(f"{a['id']:>4}  {route:<10}{cabin:<12}{miles:>10}  {date_range:<24}{status}")
    return 0


def _alert_remove(args):
    """Remove a price alert by ID."""
    conn = db.get_connection(args.db_path)
    try:
        removed = db.remove_alert(conn, args.id)
    finally:
        conn.close()

    if not removed:
        print(f"Error: alert #{args.id} not found")
        return 1

    if args.json:
        print(json.dumps({"id": args.id, "status": "removed"}))
    else:
        print(f"Alert #{args.id} removed")
    return 0



# _compute_match_hash imported from core.matching


def _alert_check(args):
    """Check all active alerts against current availability data."""
    conn = db.get_connection(args.db_path)
    try:
        expired = db.expire_past_alerts(conn)
        alerts = db.list_alerts(conn, active_only=True)

        if not alerts:
            if args.json:
                print(json.dumps({"alerts_checked": 0, "alerts_triggered": 0, "expired": expired}))
            else:
                if expired:
                    print(f"({expired} alert(s) auto-expired)")
                    print()
                print("No active alerts.")
            return 0

        results = []
        for alert in alerts:
            cabin_filter = _CABIN_FILTER_MAP.get(alert["cabin"]) if alert.get("cabin") else None
            matches = db.check_alert_matches(
                conn, alert["origin"], alert["destination"], alert["max_miles"],
                cabin=cabin_filter, date_from=alert.get("date_from"),
                date_to=alert.get("date_to"))

            if not matches:
                continue

            match_hash = _compute_match_hash(matches)
            if match_hash == alert.get("last_notified_hash"):
                continue

            db.update_alert_notification(conn, alert["id"], match_hash)
            results.append({"alert": alert, "matches": matches})
    finally:
        conn.close()

    if args.json:
        json_results = []
        for r in results:
            json_results.append({
                "alert_id": r["alert"]["id"],
                "origin": r["alert"]["origin"],
                "destination": r["alert"]["destination"],
                "cabin": r["alert"]["cabin"],
                "max_miles": r["alert"]["max_miles"],
                "matches": r["matches"],
            })
        print(json.dumps({
            "alerts_checked": len(alerts),
            "alerts_triggered": len(results),
            "expired": expired,
            "results": json_results,
        }, indent=2))
    else:
        if expired:
            print(f"({expired} alert(s) auto-expired)")
            print()
        if not results:
            print(f"Checked {len(alerts)} alert(s) \u2014 no new matches.")
        else:
            print(f"Checked {len(alerts)} alert(s) \u2014 {len(results)} triggered:")
            print()
            for r in results:
                a = r["alert"]
                cabin_str = f" {a['cabin']}" if a.get("cabin") else ""
                print(f"Alert #{a['id']}: {a['origin']}-{a['destination']}{cabin_str} \u2264{a['max_miles']:,} miles")
                print(f"  {len(r['matches'])} matching fare(s):")
                for m in r["matches"][:10]:
                    taxes = f"${m['taxes_cents'] / 100:.2f}" if m.get("taxes_cents") is not None else "\u2014"
                    program = (m.get("program") or "").title()
                    print(f"    {m['date']}  {program:<10}{m['cabin']:<18}{m['award_type']:<10}{m['miles']:>8,} miles  {taxes}")
                if len(r["matches"]) > 10:
                    print(f"    ... and {len(r['matches']) - 10} more")
                print()
    return 0


def cmd_watch(args):
    """Manage watched routes with notifications."""
    if not args.watch_command:
        print("Usage: searchaero watch {add,list,remove,check,run,setup}")
        print("Run 'searchaero watch <command> --help' for details.")
        return 1

    if args.watch_command == "add":
        return _watch_add(args)
    if args.watch_command == "list":
        return _watch_list(args)
    if args.watch_command == "remove":
        return _watch_remove(args)
    if args.watch_command == "check":
        return _watch_check(args)
    if args.watch_command == "run":
        return _watch_run(args)
    if args.watch_command == "setup":
        return _watch_setup(args)
    return 0


def _watch_add(args):
    """Add a new watch."""
    import datetime as _dt
    from core.watchlist import parse_interval

    origin, dest = args.route[0].upper(), args.route[1].upper()
    if not (origin.isalpha() and len(origin) == 3):
        print(f"Error: invalid IATA code: {args.route[0]}")
        return 1
    if not (dest.isalpha() and len(dest) == 3):
        print(f"Error: invalid IATA code: {args.route[1]}")
        return 1

    if args.max_miles <= 0:
        print(f"Error: --max-miles must be positive, got {args.max_miles}")
        return 1

    try:
        interval = parse_interval(args.every)
    except ValueError as e:
        print(f"Error: invalid interval: {args.every}")
        return 1

    if args.date_from:
        try:
            _dt.date.fromisoformat(args.date_from)
        except ValueError:
            print(f"Error: invalid date format: {args.date_from} (expected YYYY-MM-DD)")
            return 1
    if args.date_to:
        try:
            _dt.date.fromisoformat(args.date_to)
        except ValueError:
            print(f"Error: invalid date format: {args.date_to} (expected YYYY-MM-DD)")
            return 1
    if args.date_from and args.date_to and args.date_from > args.date_to:
        print(f"Error: --from ({args.date_from}) must be before --to ({args.date_to})")
        return 1

    conn = db.get_connection(args.db_path)
    try:
        watch_id = db.create_watch(conn, origin, dest, args.max_miles,
                                   cabin=args.cabin, date_from=args.date_from,
                                   date_to=args.date_to,
                                   check_interval_minutes=interval)
    finally:
        conn.close()

    if args.json:
        print(json.dumps({"id": watch_id, "status": "created", "check_interval_minutes": interval}))
    else:
        parts = [f"{origin}-{dest}"]
        parts.append(f"\u2264{args.max_miles:,} miles")
        if args.cabin:
            parts.append(args.cabin)
        # Format interval for display
        if interval == 60:
            every_str = "hourly"
        elif interval % 1440 == 0:
            days = interval // 1440
            every_str = f"{days}d" if days > 1 else "daily"
        elif interval % 60 == 0:
            every_str = f"{interval // 60}h"
        else:
            every_str = f"{interval}m"
        parts.append(f"every {every_str}")
        print(f"Watch #{watch_id} created: {', '.join(parts)}")
    return 0


def _watch_list(args):
    """List watched routes."""
    show_all = getattr(args, "all", False)
    conn = db.get_connection(args.db_path)
    try:
        watches = db.list_watches(conn, active_only=not show_all)
    finally:
        conn.close()

    if not watches:
        if args.json:
            print(json.dumps([]))
        else:
            print("No active watches." if not show_all else "No watches.")
        return 0

    if args.json:
        print(json.dumps(watches, indent=2))
        return 0

    print(f"{'ID':>4}  {'Route':<10}{'Cabin':<12}{'Max Miles':>10}  {'Every':<9}{'Last Checked':<21}{'Status'}")
    for w in watches:
        route = f"{w['origin']}-{w['destination']}"
        cabin = w["cabin"] or "any"
        miles = f"{w['max_miles']:,}"
        interval_mins = w["check_interval_minutes"]
        if interval_mins == 60:
            every = "hourly"
        elif interval_mins % 1440 == 0:
            days = interval_mins // 1440
            every = f"{days}d" if days > 1 else "daily"
        elif interval_mins % 60 == 0:
            every = f"{interval_mins // 60}h"
        else:
            every = f"{interval_mins}m"
        last_checked = w.get("last_checked_at") or "\u2014"
        status = "active" if w["active"] else "expired"
        print(f"{w['id']:>4}  {route:<10}{cabin:<12}{miles:>10}  {every:<9}{last_checked:<21}{status}")
    return 0


def _watch_remove(args):
    """Remove a watch by ID."""
    conn = db.get_connection(args.db_path)
    try:
        removed = db.remove_watch(conn, args.id)
    finally:
        conn.close()

    if not removed:
        if args.json:
            print(json.dumps({"error": "not_found"}))
        else:
            print(f"Watch #{args.id} not found.")
        return 1

    if args.json:
        print(json.dumps({"status": "removed"}))
    else:
        print(f"Watch #{args.id} removed.")
    return 0


def _watch_check(args):
    """Check watches and send notifications."""
    from core.watchlist import check_watches

    scrape = not getattr(args, "no_scrape", False)
    notify_flag = not getattr(args, "no_notify", False)

    conn = db.get_connection(args.db_path)
    try:
        result = check_watches(conn, scrape=scrape, notify_enabled=notify_flag,
                               db_path=args.db_path, verbose=not args.json)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Watches checked: {result['watches_checked']}")
        print(f"Watches triggered: {result['watches_triggered']}")
        print(f"Scrapes triggered: {result['scrapes_triggered']}")
        print(f"Notifications sent: {result['notifications_sent']}")
    return 0


def _watch_run(args):
    """Start watch daemon (foreground, Ctrl+C to stop)."""
    from core.watchlist import check_watches

    _log("Watch daemon started. Press Ctrl+C to stop.")
    try:
        while True:
            conn = db.get_connection(args.db_path)
            try:
                result = check_watches(conn, scrape=True, notify_enabled=True,
                                       db_path=args.db_path, verbose=True)
                _log(f"Check complete: {result['watches_checked']} checked, "
                     f"{result['watches_triggered']} triggered, "
                     f"{result['notifications_sent']} notified")
            finally:
                conn.close()

            # Sleep for minimum interval of active watches, or 60 minutes
            conn2 = db.get_connection(args.db_path)
            try:
                watches = db.list_watches(conn2)
                if watches:
                    sleep_mins = min(w["check_interval_minutes"] for w in watches)
                else:
                    sleep_mins = 60
            finally:
                conn2.close()

            _log(f"Next check in {sleep_mins} minutes...")
            time.sleep(sleep_mins * 60)
    except KeyboardInterrupt:
        _log("Watch daemon stopped.")
    return 0


def _watch_setup(args):
    """Configure notification settings (Discord webhook)."""
    from core.notify import save_notify_config

    save_notify_config(
        discord_webhook_url=args.discord_webhook_url,
    )

    # Warning if no channels configured
    has_discord = bool(args.discord_webhook_url)
    if not has_discord:
        print("Warning: no notification channels configured. "
              "Set --discord-webhook-url.",
              file=sys.stderr)

    if args.json:
        result = {"status": "configured"}
        if args.discord_webhook_url:
            result["discord_webhook_url"] = args.discord_webhook_url
        print(json.dumps(result))
    else:
        if args.discord_webhook_url:
            print(f"Configured: discord_webhook_url={args.discord_webhook_url}")
        else:
            print("No notification settings changed.")
    return 0


def cmd_doctor(args):
    """Run comprehensive diagnostics — database, credentials, Playwright, Discord, data freshness."""
    console = get_console()
    console.print("[bold]searchaero doctor[/bold]")
    console.print()
    issues = []

    # 1. Database health
    console.print("[bold]Database[/bold]")
    db_path = args.db_path or os.getenv("SEARCHAERO_DB", db.DEFAULT_DB_PATH)
    if os.path.isfile(db_path):
        size_mb = os.path.getsize(db_path) / (1024 * 1024)
        console.print(f"  Path:   [dim]{db_path}[/dim]")
        console.print(f"  Size:   {size_mb:.1f} MB")
        try:
            conn = db.get_connection(args.db_path)
            # Integrity check
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] == "ok":
                console.print("  Health: [green]✓ integrity check passed[/green]")
            else:
                console.print(f"  Health: [red]✗ integrity check failed: {result[0]}[/red]")
                issues.append("Database integrity check failed — consider deleting and recreating with 'searchaero setup'")

            # Row count and freshness
            row_count = conn.execute("SELECT COUNT(*) FROM availability").fetchone()[0]
            console.print(f"  Rows:   {row_count:,}")
            if row_count > 0:
                latest = conn.execute("SELECT MAX(scraped_at) FROM availability").fetchone()[0]
                if latest:
                    from datetime import timezone
                    scraped_dt = datetime.fromisoformat(latest.replace("Z", "+00:00")) if "Z" in latest else datetime.fromisoformat(latest)
                    if scraped_dt.tzinfo is None:
                        scraped_dt = scraped_dt.replace(tzinfo=timezone.utc)
                    age_hours = (datetime.now(timezone.utc) - scraped_dt).total_seconds() / 3600
                    if age_hours < 24:
                        console.print(f"  Latest: [green]{latest} ({age_hours:.1f}h ago)[/green]")
                    elif age_hours < 72:
                        console.print(f"  Latest: [yellow]{latest} ({age_hours:.1f}h ago)[/yellow]")
                    else:
                        console.print(f"  Latest: [red]{latest} ({age_hours:.1f}h ago — stale)[/red]")
                        issues.append(f"Data is {age_hours:.0f}h old — consider re-scraping")
                route_count = conn.execute("SELECT COUNT(DISTINCT origin || '-' || destination) FROM availability").fetchone()[0]
                console.print(f"  Routes: {route_count}")
            else:
                console.print("  [dim]No data yet — run 'searchaero search' to scrape.[/dim]")
                issues.append("No data in database")
            conn.close()
        except Exception as e:
            console.print(f"  Health: [red]✗ error: {e}[/red]")
            issues.append(f"Database error: {e}")
    else:
        console.print(f"  Path:   [dim]{db_path}[/dim]")
        console.print("  Status: [red]✗ not found[/red]")
        console.print("  [dim]Run 'searchaero setup' to create it.[/dim]")
        issues.append("Database not found")
    console.print()

    # 2. Playwright
    console.print("[bold]Playwright[/bold]")
    import importlib.metadata
    try:
        pw_version = importlib.metadata.version("playwright")
        console.print(f"  Package:  [green]✓ {pw_version}[/green]")
    except importlib.metadata.PackageNotFoundError:
        console.print("  Package:  [red]✗ not installed[/red]")
        issues.append("Playwright not installed — run: pip install playwright")

    if os.name == "nt":
        pw_browsers = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")
    else:
        pw_browsers = os.path.expanduser("~/.cache/ms-playwright")
    browsers_installed = bool(glob.glob(os.path.join(pw_browsers, "chromium-*")))
    if browsers_installed:
        console.print("  Browsers: [green]✓ chromium installed[/green]")
    else:
        console.print("  Browsers: [red]✗ not installed[/red] [dim](run: playwright install chromium)[/dim]")
        issues.append("Chromium not installed — run: playwright install chromium")
    console.print()

    # 3. Credentials
    console.print("[bold]Credentials[/bold]")
    env_file = os.path.join(os.path.expanduser("~"), ".searchaero", ".env")
    if os.path.isfile(env_file):
        console.print(f"  File:     [green]✓ {env_file}[/green]")
        with open(env_file, "r") as f:
            content = f.read()
        has_mp = "UNITED_MP_NUMBER=" in content and "your_" not in content.split("UNITED_MP_NUMBER=")[1].split("\n")[0]
        has_pw = "UNITED_PASSWORD=" in content and "your_" not in content.split("UNITED_PASSWORD=")[1].split("\n")[0]
        console.print(f"  MP#:      {'[green]✓ set[/green]' if has_mp else '[red]✗ not set[/red]'}")
        console.print(f"  Password: {'[green]✓ set[/green]' if has_pw else '[red]✗ not set[/red]'}")
        if not has_mp or not has_pw:
            issues.append("Credentials incomplete — run 'searchaero setup' to configure")
    else:
        console.print(f"  File:     [red]✗ not found[/red] [dim]({env_file})[/dim]")
        console.print("  [dim]Run 'searchaero setup' to create it interactively.[/dim]")
        issues.append("Credentials file missing — run 'searchaero setup'")
    console.print()

    # 4. Discord notifications
    console.print("[bold]Notifications (Discord)[/bold]")
    try:
        from core.notify import load_notify_config
        cfg = load_notify_config()
        webhook_url = cfg.get("discord_webhook_url")
        if webhook_url:
            console.print(f"  Webhook: [green]✓ configured[/green]")
        else:
            console.print("  Webhook: [dim]not configured (optional)[/dim]")
            console.print("  [dim]Set up with: searchaero watch setup --discord-webhook-url URL[/dim]")
    except Exception:
        console.print("  [dim]not configured (optional)[/dim]")
    console.print()

    # 5. Summary
    if issues:
        console.print(f"[bold yellow]Found {len(issues)} issue{'s' if len(issues) != 1 else ''}:[/bold yellow]")
        for issue in issues:
            console.print(f"  [yellow]•[/yellow] {issue}")
    else:
        console.print("[bold green]All checks passed — everything looks good.[/bold green]")

    return 0 if not issues else 1


_HELP_TOPICS = {
    "mfa": """
[bold]MFA / SMS Verification[/bold]

United requires two-factor authentication via SMS on first login.

[bold]What happens:[/bold]
  1. You run a search (CLI or agent)
  2. Searchaero logs into united.com with your credentials
  3. United sends a 6-digit SMS code to your phone
  4. You enter the code when prompted

[bold]How to enter the code:[/bold]
  • CLI: Type it at the "Enter SMS code:" prompt
  • Agent (MCP): The agent asks you in the chat — just type the 6 digits
  • Headless/automated: Use --mfa-file flag; write code to ~/.searchaero/mfa_response

[bold]Tips:[/bold]
  • MFA is only needed once per browser session (usually several hours)
  • If the code expires (5 min), just re-run the command — United sends a new one
  • Batch scrapes (--file) only need MFA once for all routes
""",
    "proxy": """
[bold]Proxy / IP Rotation[/bold]

United's Akamai bot detection can block your IP after repeated scraping.

[bold]Symptoms:[/bold]
  • "BROWSER CRASH detected" errors
  • Scrapes returning 0 results
  • Consistent failures after initial success

[bold]Solutions (easiest first):[/bold]
  1. [bold]Wait and retry[/bold] — blocks are usually temporary (10-15 min)
  2. [bold]Use a proxy:[/bold]
     searchaero search YYZ LAX --proxy socks5://user:pass@host:port
     Or set the PROXY_URL environment variable.
[bold]For heavy use:[/bold]
  • Parallel scraping (--workers 3) is fine but increases block risk
  • Increase --delay (default 3s) to reduce detection risk
""",
    "watches": """
[bold]Watchlist & Notifications[/bold]

Watches automatically monitor routes and notify you when prices drop.

[bold]Setup:[/bold]
  1. Configure Discord webhook for notifications:
     searchaero watch setup --discord-webhook-url https://discord.com/api/webhooks/...

  2. Add a watch:
     searchaero watch add YYZ LAX --max-miles 20000 --cabin economy --every 12h

  3. Run the daemon:
     searchaero watch run     (foreground, Ctrl+C to stop)

  Or run a one-shot check:
     searchaero watch check

[bold]How it works:[/bold]
  • The daemon checks your watches on their schedule (e.g., every 12h)
  • If cached data is stale, it scrapes fresh data first
  • When a match is found (price ≤ threshold), it sends a notification
  • Discord webhook notifications

[bold]Manage watches:[/bold]
  searchaero watch list          — see all active watches
  searchaero watch remove <id>   — remove a watch
""",
    "alerts": """
[bold]Price Alerts[/bold]

Alerts are one-shot checks against cached data (no daemon needed).

[bold]Add an alert:[/bold]
  searchaero alert add YYZ LAX --max-miles 70000 --cabin business
  searchaero alert add YYZ LHR --max-miles 50000 --from 2026-06-01 --to 2026-08-31

[bold]Check alerts:[/bold]
  searchaero alert check         — evaluate all active alerts
  searchaero alert check --json  — machine-readable output

[bold]Manage:[/bold]
  searchaero alert list           — see all active alerts
  searchaero alert list --all     — include expired ones
  searchaero alert remove <id>    — delete an alert

[bold]Alerts vs Watches:[/bold]
  • Alerts: manual check, no notifications, no auto-scrape
  • Watches: automatic schedule, push notifications, auto-scrape stale data
  Use watches for ongoing monitoring, alerts for quick spot-checks.
""",
    "scraping": """
[bold]Scraping Guide[/bold]

Searchaero scrapes United's award calendar API via a real headed Chrome browser
(headless is blocked by United's Akamai Bot Manager, so the window is always visible).

[bold]Single route:[/bold]
  searchaero search YYZ LAX                    (~2 min, 12 API calls)

[bold]Batch (from file):[/bold]
  searchaero search --file routes/canada_test.txt    (15 routes, ~30 min)

[bold]Parallel:[/bold]
  searchaero search --file routes/canada_us_all.txt --workers 3

[bold]Options:[/bold]
  --headless        NO-OP for United (Akamai blocks headless; always runs headed)
  --proxy URL       Route traffic through a proxy
  --delay N         Seconds between API calls (default: 3.0)
  --mfa-file        Use file-based MFA instead of stdin prompt

[bold]What gets scraped:[/bold]
  • Full 337-day booking window from today
  • All cabins: economy, business, first
  • Both Saver and Standard award types
  • One API call returns ~30 days of data (12 calls = full window)

[bold]Data freshness:[/bold]
  • Data doesn't auto-refresh — re-scrape when you need fresh prices
  • Use --refresh on queries: searchaero query YYZ LAX --refresh
  • Or set up watches for automatic re-scraping
""",
    "schedule": """
[bold]Scheduled Scraping (Windows Task Scheduler)[/bold]

Automate recurring scrapes using Windows Task Scheduler.

[bold]Quick start:[/bold]
  searchaero schedule add --routes routes/yyz_wuh.txt --interval 60 --months 6,7,12

This creates a Task Scheduler task that runs every 60 minutes, scraping
only months 6 (June), 7 (July), and 12 (December).

[bold]Commands:[/bold]
  schedule add       Register a new scheduled scrape
  schedule list      List all scheduled scrapes with live status
  schedule remove    Remove a scheduled scrape
  schedule enable    Re-enable a paused schedule
  schedule disable   Manually pause a schedule
  schedule status    Show wake timers, task health, and recent logs

[bold]Options for 'schedule add':[/bold]
  --routes, -r       Path to routes file (required)
  --interval, -i     Minutes between runs (minimum 60, default: 60)
  --months           Month filter (e.g., 6,7,12)
  --name             Task name (default: auto from filename)
  --no-eval          Disable Claude watch evaluation (default: eval ON)
  --env-file         Path to .env for mfa_responder
  --from / --to      Date range filter (YYYY-MM-DD)
  --no-wake          Skip wake timer configuration

[bold]Sleep vs Shutdown:[/bold]
  Your PC must sleep (close the lid), not shut down. Task Scheduler can
  wake a sleeping PC to run tasks, but it cannot start a powered-off PC.
  If the PC was off during a scheduled run, StartWhenAvailable catches up
  once — but only the next time the PC wakes.

[bold]Wake Timers:[/bold]
  'schedule add' automatically enables AC wake timers via powercfg.
  If it fails (requires admin), run manually:
    powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
    powercfg /setactive SCHEME_CURRENT

[bold]Logs:[/bold]
  searchaero schedule status
  Or view directly: type %USERPROFILE%\\.searchaero\\logs\\task_scheduler.log
""",
}


def cmd_help_topic(args):
    """Show focused help on a specific topic."""
    console = get_console()
    topic = args.topic.lower() if args.topic else None

    if not topic or topic not in _HELP_TOPICS:
        console.print("[bold]Available help topics:[/bold]")
        console.print()
        console.print("  [bold]mfa[/bold]        SMS verification and login")
        console.print("  [bold]proxy[/bold]      IP rotation and Akamai blocks")
        console.print("  [bold]watches[/bold]    Watchlist and push notifications")
        console.print("  [bold]alerts[/bold]     Price alert setup and usage")
        console.print("  [bold]scraping[/bold]   How scraping works, options, timing")
        console.print("  [bold]schedule[/bold]   Scheduled scraping via Task Scheduler")
        console.print()
        console.print("[dim]Usage: searchaero help <topic>[/dim]")
        return 0

    console.print(_HELP_TOPICS[topic])
    return 0


def cmd_schema(args):
    """Show command schemas for agent introspection."""
    from core.schema import get_schema, get_all_commands

    if args.target is None:
        # List all commands
        commands = get_all_commands()
        print(json.dumps(commands, indent=2))
    else:
        try:
            schema = get_schema(args.target)
            print(json.dumps(schema, indent=2))
        except KeyError:
            from core.output import print_error
            from core.schema import get_all_commands
            available = [c["command"] for c in get_all_commands()]
            print_error(
                "not_found",
                f"Unknown command: {args.target}",
                suggestion=f"Available commands: {', '.join(available)}",
                json_mode=True,  # schema is always JSON
            )
            return 1
    return 0


# ---------------------------------------------------------------------------
# schedule command
# ---------------------------------------------------------------------------

def cmd_schedule(args):
    """Manage scheduled scraping tasks."""
    console = get_console()

    try:
        require_windows()
    except RuntimeError as e:
        print_error(str(e))
        return 1

    sub = getattr(args, "schedule_command", None)
    if not sub:
        console.print("[bold]searchaero schedule[/bold] -- manage scheduled scraping tasks")
        console.print()
        console.print("  schedule add      Register a new scheduled scrape")
        console.print("  schedule list     List all scheduled scrapes")
        console.print("  schedule remove   Remove a scheduled scrape")
        console.print("  schedule enable   Re-enable a paused schedule")
        console.print("  schedule disable  Manually pause a schedule")
        console.print("  schedule status   Show schedule health and power config")
        console.print()
        console.print("[dim]Use 'searchaero schedule <command> --help' for details.[/dim]")
        return 0

    if sub == "add":
        return _schedule_add(args)
    elif sub == "list":
        return _schedule_list(args)
    elif sub == "remove":
        return _schedule_remove(args)
    elif sub == "enable":
        return _schedule_enable(args)
    elif sub == "disable":
        return _schedule_disable(args)
    elif sub == "status":
        return _schedule_status(args)
    return 0


def _schedule_add(args):
    """Add a route group to the consolidated schedule.

    Creates a new master schedule if none exists, or appends a route group
    to the existing one.  Validates route counts, interval, and uniqueness.
    """
    console = get_console()

    # --- 1. Load existing schedules and find master ---
    schedules = load_schedules()

    # Check for old-format entries
    for s in schedules:
        if is_old_format(s):
            print_error(
                f"Old-format schedule '{s.get('name', '?')}' detected. "
                "Run `searchaero schedule migrate` to convert to the new "
                "consolidated format before adding routes."
            )
            return 1

    # Find existing master schedule (first entry, if any)
    master = schedules[0] if schedules else None

    # --- 2. Resolve routes path and validate ---
    routes_path = os.path.abspath(args.routes)
    if not os.path.isfile(routes_path):
        print_error(f"Routes file not found: {routes_path}")
        return 1

    new_route_count = count_routes_in_file(routes_path)
    if new_route_count == 0:
        print_error(f"Routes file is empty: {routes_path}")
        return 1

    # --- 3. Derive names ---
    # Group name always comes from routes filename
    group_basename = os.path.splitext(os.path.basename(routes_path))[0]
    group_name = group_basename.replace("_", "-")

    # Master schedule name: --name overrides, else inherit, else "master"
    if args.name:
        master_name = args.name
    elif master:
        master_name = master["name"]
    else:
        master_name = "master"

    # --- 4. Validate route count ---
    current_total = get_total_route_count(master) if master else 0
    new_total = current_total + new_route_count
    if new_total > MAX_ROUTES:
        print_error(
            f"Route limit exceeded: {current_total} existing + "
            f"{new_route_count} new = {new_total} (max {MAX_ROUTES}).\n"
            f"Remove a route group first with `searchaero schedule remove <group>`."
        )
        return 1

    # --- 5. Validate group name uniqueness ---
    if master:
        existing_group_names = [
            g.get("name") for g in master.get("route_groups", [])
        ]
        if group_name in existing_group_names:
            print_error(
                f"Route group '{group_name}' already exists. "
                "Use a different routes filename or remove the existing group first."
            )
            return 1

    # --- 6. Determine interval ---
    if args.interval is not None:
        interval = args.interval
    elif master:
        interval = master.get("interval_minutes", 60)
    else:
        interval = 60

    min_interval = compute_min_interval(new_total, program=args.program)
    if interval < min_interval:
        est = estimate_scrape_minutes(new_total, program=args.program)
        print_error(
            f"Interval {interval} min is too short for {new_total} routes "
            f"(est. scrape ~{est} min + 45 min buffer).\n"
            f"Minimum interval: {min_interval} min. "
            f"Use --interval {min_interval} or higher."
        )
        return 1

    project_dir = _CLI_DIR
    python_exe = sys.executable
    use_eval = not args.no_eval
    env_file = args.env_file

    # Build the new group dict
    new_group = {
        "name": group_name,
        "routes_file": routes_path,
        "months": args.months,
        "date_from": args.date_from,
        "date_to": args.date_to,
        "program": args.program,
        "added_at": datetime.now().isoformat(),
    }

    if master is None:
        # --- CREATE new master schedule ---
        entry = {
            "name": master_name,
            "task_name": f"searchaero-{master_name}",
            "route_groups": [new_group],
            "interval_minutes": interval,
            "eval": use_eval,
            "env_file": env_file,
            "bat_path": "",
            "created_at": datetime.now().isoformat(),
        }

        # Generate .bat
        _log(f"Generating launcher: {master_name}.bat")
        bat_path = generate_bat(
            schedule_name=master_name,
            project_dir=project_dir,
            python_exe=python_exe,
            eval=use_eval,
            env_file=env_file,
        )
        entry["bat_path"] = bat_path

        # Register schtasks
        _log(f"Registering task: searchaero-{master_name}")
        success, message = register_task(master_name, bat_path, interval)
        if not success:
            print_error(f"Task registration failed: {message}")
            return 1

        # Wake timer handling
        if not args.no_wake:
            wake_info = check_wake_timers()
            if wake_info["ac"] != "enabled":
                _log("Enabling wake timers (AC)...")
                wake_ok, wake_msg = enable_wake_timers(ac=True)
                if not wake_ok:
                    console.print(f"[yellow]Warning:[/yellow] {wake_msg}")

        save_schedule(entry)
    else:
        # --- APPEND to existing master schedule ---
        add_route_group(master, new_group)

        old_interval = master.get("interval_minutes", 60)
        interval_changed = args.interval is not None and interval != old_interval

        # Update interval if user explicitly provided --interval
        if args.interval is not None:
            master["interval_minutes"] = interval

        # Update eval/env if provided
        if use_eval != master.get("eval", True):
            master["eval"] = use_eval
        if env_file and env_file != master.get("env_file"):
            master["env_file"] = env_file

        # Regenerate .bat
        _log(f"Regenerating launcher: {master_name}.bat")
        bat_path = generate_bat(
            schedule_name=master_name,
            project_dir=project_dir,
            python_exe=python_exe,
            eval=master.get("eval", True),
            env_file=master.get("env_file"),
        )
        master["bat_path"] = bat_path

        # Re-register schtasks if interval changed
        if interval_changed:
            _log(f"Updating task interval: searchaero-{master_name}")
            success, message = register_task(master_name, bat_path, interval)
            if not success:
                print_error(f"Task re-registration failed: {message}")
                return 1

        save_schedule(master)

    # --- Print success summary ---
    total_routes = new_total
    num_groups = len(master["route_groups"]) if master else 1
    est_minutes = estimate_scrape_minutes(total_routes)
    buffer = interval - est_minutes

    console.print()
    console.print("[bold green]Schedule updated successfully[/bold green]")
    console.print(f"  [bold]Routes:[/bold]       {total_routes} across {num_groups} group{'s' if num_groups != 1 else ''} (max {MAX_ROUTES})")
    console.print(f"  [bold]Est. scrape:[/bold]  ~{est_minutes} min")
    console.print(f"  [bold]Interval:[/bold]     {interval} min")
    console.print(f"  [bold]Buffer:[/bold]       ~{buffer} min")
    console.print()
    console.print("[dim]Note: Your PC must sleep (close lid), not shut down. "
                  "Full power-off prevents wake-to-scrape.[/dim]")
    return 0


def _schedule_list(args):
    """Show consolidated schedule with route groups."""
    console = get_console()
    schedules = load_schedules()

    if not schedules:
        console.print("No schedules registered.")
        console.print("[dim]Use 'searchaero schedule add --routes <file>' to create one.[/dim]")
        return 0

    from rich.table import Table

    for s in schedules:
        name = s.get("name", "?")
        interval = s.get("interval_minutes", "?")
        groups = s.get("route_groups", [])

        # Query live status from Task Scheduler
        info = query_task(name)
        if info:
            status = info.get("status", "Unknown")
            next_run = info.get("next_run", "N/A")
        else:
            status = "Not found"
            next_run = "N/A"

        console.print(f"[bold]Schedule:[/bold] {name}  |  [bold]Interval:[/bold] {interval} min  |  [bold]Next run:[/bold] {next_run}  |  [bold]Status:[/bold] {status}")
        console.print()

        if groups:
            table = Table(title="Route Groups")
            table.add_column("Group Name", style="bold")
            table.add_column("Routes File")
            table.add_column("Routes")
            table.add_column("Months")
            table.add_column("Date Range")

            total_routes = 0
            for g in groups:
                g_name = g.get("name", "?")
                routes_file = g.get("routes_file", "?")
                routes_basename = os.path.basename(routes_file)
                try:
                    route_count = count_routes_in_file(routes_file)
                except (OSError, FileNotFoundError):
                    route_count = "?"
                if isinstance(route_count, int):
                    total_routes += route_count

                months = g.get("months") or "-"
                date_from = g.get("date_from")
                date_to = g.get("date_to")
                if date_from and date_to:
                    date_range = f"{date_from} to {date_to}"
                elif date_from:
                    date_range = f"from {date_from}"
                elif date_to:
                    date_range = f"to {date_to}"
                else:
                    date_range = "-"

                table.add_row(g_name, routes_basename, str(route_count), months, date_range)

            console.print(table)
            est = estimate_scrape_minutes(total_routes) if isinstance(total_routes, int) else "?"
            console.print()
            console.print(f"  [bold]Total:[/bold] {total_routes} routes  |  [bold]Est. scrape:[/bold] ~{est} min")
        else:
            console.print("  [dim]No route groups.[/dim]")

    return 0


def _schedule_remove(args):
    """Remove a route group or the entire master schedule.

    If *name* matches a route group within the master schedule, that group is
    removed.  If no groups remain (or *name* matches the master schedule name),
    the entire schedule is torn down (task + .bat + registry entry).
    """
    console = get_console()
    name = args.name

    schedules = load_schedules()
    if not schedules:
        console.print(f"[yellow]No schedules registered -- nothing to remove.[/yellow]")
        return 0

    master = schedules[0]
    master_name = master.get("name", "master")
    groups = master.get("route_groups", [])
    group_names = [g.get("name") for g in groups]

    # Check if name matches a route group within the master
    if name in group_names and name != master_name:
        # Remove the group
        remove_route_group(master, name)
        remaining_groups = master.get("route_groups", [])

        if remaining_groups:
            # Regenerate .bat and save
            project_dir = _CLI_DIR
            python_exe = sys.executable
            bat_path = generate_bat(
                schedule_name=master_name,
                project_dir=project_dir,
                python_exe=python_exe,
                eval=master.get("eval", True),
                env_file=master.get("env_file"),
            )
            master["bat_path"] = bat_path
            save_schedule(master)

            total_routes = get_total_route_count(master)
            num_groups = len(remaining_groups)
            console.print(f"[green]Route group '{name}' removed.[/green]")
            console.print(f"  Remaining: {total_routes} routes across {num_groups} group{'s' if num_groups != 1 else ''}")
            return 0
        else:
            # No groups remain -- full teardown
            console.print(f"[dim]Last route group removed -- tearing down schedule '{master_name}'.[/dim]")
            # Fall through to full teardown below

    elif name == master_name:
        # Full teardown requested by master name
        pass
    else:
        # Name doesn't match any group or the master
        console.print(f"[yellow]'{name}' not found. Available groups: {', '.join(group_names)}. Master schedule: {master_name}[/yellow]")
        return 1

    # --- Full teardown ---
    success, message = delete_task(master_name)
    if not success:
        console.print(f"[yellow]Warning:[/yellow] {message}")

    bat_path = master.get("bat_path") or os.path.join(SCHEDULES_DIR, f"{master_name}.bat")
    if os.path.isfile(bat_path):
        os.remove(bat_path)

    removed = remove_schedule(master_name)
    if removed:
        console.print(f"[green]Schedule '{master_name}' removed (task + .bat + registry).[/green]")
    else:
        console.print(f"[yellow]Schedule '{master_name}' not found in registry.[/yellow]")

    return 0


def _schedule_enable(args):
    """Re-enable a paused schedule."""
    import json as _json
    console = get_console()
    name = args.name

    state_file = os.path.join(os.path.expanduser("~"), ".searchaero", "scrape_state.json")
    all_states = {}
    if os.path.isfile(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                all_states = _json.load(f)
        except (_json.JSONDecodeError, OSError):
            all_states = {}

    state = all_states.get(name, {})
    state["disabled"] = False
    state["consecutive_failures"] = 0
    state["disabled_at"] = None
    state["disabled_reason"] = None
    all_states[name] = state

    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        _json.dump(all_states, f, indent=2)

    console.print(f"[green]Schedule '{name}' re-enabled.[/green]")
    return 0


def _schedule_disable(args):
    """Manually pause a schedule."""
    import json as _json
    console = get_console()
    name = args.name

    state_file = os.path.join(os.path.expanduser("~"), ".searchaero", "scrape_state.json")
    all_states = {}
    if os.path.isfile(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                all_states = _json.load(f)
        except (_json.JSONDecodeError, OSError):
            all_states = {}

    state = all_states.get(name, {})
    state["disabled"] = True
    state["disabled_at"] = datetime.now().isoformat()
    state["disabled_reason"] = "manually disabled"
    all_states[name] = state

    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        _json.dump(all_states, f, indent=2)

    console.print(f"[yellow]Schedule '{name}' paused.[/yellow] Run `searchaero schedule enable {name}` to resume.")
    return 0


def _schedule_status(args):
    """Show schedule health, timing, and power config."""
    console = get_console()

    # Wake timers
    console.print("[bold]Wake Timers[/bold]")
    wake = check_wake_timers()
    ac_color = "green" if wake["ac"] == "enabled" else "red"
    dc_color = "green" if wake["dc"] == "enabled" else "dim"
    console.print(f"  AC (plugged in): [{ac_color}]{wake['ac']}[/{ac_color}]")
    console.print(f"  DC (battery):    [{dc_color}]{wake['dc']}[/{dc_color}]")
    console.print()

    # Load backoff state for all schedules
    import json as _json
    state_file = os.path.join(os.path.expanduser("~"), ".searchaero", "scrape_state.json")
    all_states = {}
    if os.path.isfile(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                all_states = _json.load(f)
        except (_json.JSONDecodeError, OSError):
            all_states = {}

    # Schedules
    schedules = load_schedules()
    if schedules:
        from rich.table import Table

        table = Table(title="Scheduled Scrapes")
        table.add_column("Name", style="bold")
        table.add_column("Interval")
        table.add_column("Status")
        table.add_column("Failures")
        table.add_column("Next Run")
        table.add_column("Last Result")

        for s in schedules:
            name = s.get("name", "?")
            interval_val = s.get("interval_minutes", 60)
            interval = f"{interval_val} min"

            # Backoff state
            sched_state = all_states.get(name, {})
            is_disabled = sched_state.get("disabled", False)
            failures = sched_state.get("consecutive_failures", 0)

            if is_disabled:
                reason = sched_state.get("disabled_reason", "paused")
                failures_str = f"[red]{failures} (PAUSED: {reason})[/red]"
            elif failures > 0:
                failures_str = f"[yellow]{failures}[/yellow]"
            else:
                failures_str = "[green]0[/green]"

            info = query_task(name)
            if info:
                status = info.get("status", "Unknown")
                if is_disabled:
                    status = "[red]PAUSED[/red]"
                next_run = info.get("next_run", "N/A")
                last_result = info.get("last_result", "N/A")
            else:
                status = "[red]Not found[/red]"
                next_run = "N/A"
                last_result = "N/A"

            table.add_row(name, interval, status, failures_str, next_run, last_result)

        console.print(table)
        console.print()

        # Timing section for consolidated schedules
        for s in schedules:
            groups = s.get("route_groups", [])
            if groups:
                name = s.get("name", "?")
                interval_val = s.get("interval_minutes", 60)
                try:
                    total_routes = get_total_route_count(s)
                except (OSError, FileNotFoundError):
                    total_routes = 0
                num_groups = len(groups)
                est = estimate_scrape_minutes(total_routes)
                buffer = interval_val - est
                min_buffer = 45
                buffer_color = "green" if buffer >= min_buffer else "red"

                console.print("[bold]Timing[/bold]")
                console.print(f"  Estimated scrape:  ~{est} min ({total_routes} routes across {num_groups} group{'s' if num_groups != 1 else ''})")
                console.print(f"  Interval:          {interval_val} min")
                console.print(f"  Buffer margin:     [{buffer_color}]~{buffer} min (minimum {min_buffer} min)[/{buffer_color}]")
                console.print()
    else:
        console.print("[bold]Schedules:[/bold] None registered")
    console.print()

    # Tail log
    log_path = os.path.join(os.path.expanduser("~"), ".searchaero", "logs", "task_scheduler.log")
    if os.path.isfile(log_path):
        console.print("[bold]Recent Log (last 10 lines):[/bold]")
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in lines[-10:]:
                console.print(f"  [dim]{line.rstrip()}[/dim]")
        except OSError:
            console.print("  [dim](could not read log)[/dim]")
    else:
        console.print("[dim]No log file yet (~/.searchaero/logs/task_scheduler.log)[/dim]")

    console.print()
    console.print("[dim]Note: Your PC must sleep (close lid), not shut down. "
                  "Full power-off prevents wake-to-scrape.[/dim]")
    return 0


def main(argv=None):
    """CLI entry point.

    Args:
        argv: Argument list for testing. None means use sys.argv[1:].

    Returns:
        int: Exit code (0 = success).
    """
    # Force UTF-8 stdout/stderr so non-ASCII output (Rich box-drawing tables,
    # the "→" tip glyphs, etc.) never raises UnicodeEncodeError when stdout is
    # redirected to a file on Windows (the console defaults to cp1252). Best-
    # effort: harmless if stdout was replaced (e.g. pytest capture).
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # Shared parent parser for flags common to all subcommands.
    # Using parents=[] on each subparser lets --json/--meta/--db-path appear
    # after the subcommand name (e.g., "searchaero query YYZ LAX --json").
    shared_parser = argparse.ArgumentParser(add_help=False)
    shared_parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database (default: ~/.searchaero/data.db)",
    )
    shared_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON",
    )
    shared_parser.add_argument(
        "--meta",
        action="store_true",
        default=False,
        help="Include _meta block with field type hints in JSON output",
    )

    parser = argparse.ArgumentParser(
        prog="searchaero",
        description="United MileagePlus award flight search CLI",
    )

    subparsers = parser.add_subparsers(dest="command")
    setup_parser = subparsers.add_parser("setup", help="Check environment and dependencies",
                          parents=[shared_parser])
    setup_parser.add_argument("--no-browser-install", action="store_true", default=False,
                              help="Skip automatic Chromium browser installation")

    search_parser = subparsers.add_parser("search", help="Search for award flights",
                                          parents=[shared_parser])
    search_parser.add_argument("route", nargs="*", help="ORIGIN DEST (e.g., YYZ LAX)")
    search_parser.add_argument("--file", "-f", default=None, help="Path to routes file")
    search_parser.add_argument("--workers", "-w", type=int, default=1, help="Number of parallel workers (default: 1)")
    search_parser.add_argument("--headless", action="store_true", help="(no-op for United — Akamai blocks headless, so it always runs headed)")
    search_parser.add_argument("--proxy", type=str, default=None,
        help="Proxy URL (e.g., socks5://user:pass@host:port). Also reads PROXY_URL env var.")
    search_parser.add_argument("--delay", type=float, default=3.0, help="Seconds between API calls (default: 3.0)")
    search_parser.add_argument("--skip-scanned", "--no-skip-scanned", action=argparse.BooleanOptionalAction, default=True, help="Skip already-scanned routes (parallel mode)")
    search_parser.add_argument("--program", choices=["united", "aeroplan"], default="united",
                               help="Award program to scrape (default: united). 'aeroplan' is single-route only.")
    search_parser.add_argument("--mfa-file", action="store_true", default=False,
                               help="Use file-based MFA handoff (~/.searchaero/mfa_response) instead of stdin prompt")
    search_parser.add_argument("--mfa-method", choices=["sms", "email"], default="email",
                               help="MFA delivery channel (default: sms). Use 'email' for automated workflows.")
    search_parser.add_argument("--ephemeral", action="store_true", default=False,
                               help="Use ephemeral browser profile (default: persistent)")
    search_parser.add_argument("--months", type=str, default=None,
                               help="Comma-separated month numbers to scrape (e.g., 6,7,12)")
    search_parser.add_argument("--from", dest="search_from", default=None,
                               help="Only scrape windows overlapping this date or later (YYYY-MM-DD)")
    search_parser.add_argument("--to", dest="search_to", default=None,
                               help="Only scrape windows overlapping this date or earlier (YYYY-MM-DD)")

    query_parser = subparsers.add_parser("query", help="Query stored availability data",
                                          parents=[shared_parser])
    query_parser.add_argument("route", nargs=2, metavar=("ORIGIN", "DEST"), help="Origin and destination IATA codes")
    query_parser.add_argument("--date", "-d", default=None, help="Show detail for a specific date (YYYY-MM-DD)")
    query_parser.add_argument("--from", dest="date_from", default=None,
                              help="Start date for range filter (YYYY-MM-DD, inclusive)")
    query_parser.add_argument("--to", dest="date_to", default=None,
                              help="End date for range filter (YYYY-MM-DD, inclusive)")
    query_parser.add_argument("--cabin", "-c", default=None,
                              choices=["economy", "business", "first"],
                              help="Filter by cabin class")
    query_parser.add_argument("--program", choices=["united", "aeroplan"], default=None,
                              help="Filter by award program (default: all programs)")
    query_parser.add_argument("--csv", action="store_true", default=False,
                              help="Output results as CSV")
    query_parser.add_argument("--sort", "-s", default="date",
                              choices=["date", "miles", "cabin"],
                              help="Sort order (default: date)")
    query_parser.add_argument("--history", action="store_true", default=False,
                              help="Show price history (route summary or per-date timeline)")
    query_parser.add_argument("--fields", default=None,
                              help="Comma-separated list of fields to include in JSON output")
    query_parser.add_argument("--refresh", action="store_true", default=False,
                              help="Auto-scrape if cached data is stale or missing")
    query_parser.add_argument("--ttl", type=float, default=12.0,
                              help="Hours before cached data is considered stale (default: 12)")
    query_parser.add_argument("--mfa-file", action="store_true", default=False,
                              help="Use file-based MFA handoff for --refresh scrapes")
    query_parser.add_argument("--mfa-method", choices=["sms", "email"], default="email",
                              help="MFA delivery channel for --refresh scrapes (default: sms)")
    query_parser.add_argument("--graph", action="store_true", default=False,
                              help="Show price trend as ASCII chart")
    query_parser.add_argument("--summary", action="store_true", default=False,
                              help="Show deal summary card")
    query_parser.add_argument("--table-view", default=None, choices=["programs"],
                              help="Alternative table layout (programs: multi-program flat table)")

    subparsers.add_parser("status", help="Show database statistics and coverage",
                          parents=[shared_parser])

    deals_parser = subparsers.add_parser("deals", help="Find best deals across all cached routes",
                                          parents=[shared_parser])
    deals_parser.add_argument("--cabin", "-c", default=None,
                              choices=["economy", "business", "first"],
                              help="Filter by cabin class")
    deals_parser.add_argument("--max-results", type=int, default=10,
                              help="Maximum deals to show (1-25, default 10)")

    alert_parser = subparsers.add_parser("alert", help="Manage price alerts")
    alert_sub = alert_parser.add_subparsers(dest="alert_command")

    alert_add = alert_sub.add_parser("add", help="Add a new price alert",
                                     parents=[shared_parser])
    alert_add.add_argument("route", nargs=2, metavar=("ORIGIN", "DEST"),
                           help="Origin and destination IATA codes")
    alert_add.add_argument("--max-miles", type=int, required=True,
                           help="Maximum miles threshold")
    alert_add.add_argument("--cabin", "-c", default=None,
                           choices=["economy", "business", "first"],
                           help="Filter by cabin class")
    alert_add.add_argument("--from", dest="date_from", default=None,
                           help="Start date for travel window (YYYY-MM-DD)")
    alert_add.add_argument("--to", dest="date_to", default=None,
                           help="End date for travel window (YYYY-MM-DD)")

    alert_list = alert_sub.add_parser("list", help="List alerts",
                                     parents=[shared_parser])
    alert_list.add_argument("--all", "-a", action="store_true", default=False,
                            help="Include expired alerts")

    alert_remove = alert_sub.add_parser("remove", help="Remove an alert",
                                       parents=[shared_parser])
    alert_remove.add_argument("id", type=int, help="Alert ID to remove")

    alert_sub.add_parser("check", help="Check alerts against current data",
                         parents=[shared_parser])

    watch_parser = subparsers.add_parser("watch", help="Manage watched routes with notifications")
    watch_sub = watch_parser.add_subparsers(dest="watch_command")

    watch_add = watch_sub.add_parser("add", help="Add a route to your watchlist",
                                     parents=[shared_parser])
    watch_add.add_argument("route", nargs=2, metavar=("ORIGIN", "DEST"),
                           help="Origin and destination IATA codes")
    watch_add.add_argument("--max-miles", type=int, required=True,
                           help="Maximum miles threshold for notifications")
    watch_add.add_argument("--cabin", "-c", default=None,
                           choices=["economy", "business", "first"],
                           help="Filter by cabin class")
    watch_add.add_argument("--from", dest="date_from", default=None,
                           help="Start date for travel window (YYYY-MM-DD)")
    watch_add.add_argument("--to", dest="date_to", default=None,
                           help="End date for travel window (YYYY-MM-DD)")
    watch_add.add_argument("--every", default="12h",
                           help="Check frequency: hourly, 6h, 12h, daily, twice-daily (default: 12h)")

    watch_list = watch_sub.add_parser("list", help="List watched routes",
                                      parents=[shared_parser])
    watch_list.add_argument("--all", "-a", action="store_true", default=False,
                            help="Include expired watches")

    watch_remove = watch_sub.add_parser("remove", help="Remove a watch",
                                        parents=[shared_parser])
    watch_remove.add_argument("id", type=int, help="Watch ID to remove")

    watch_check = watch_sub.add_parser("check", help="Check watches and send notifications",
                                       parents=[shared_parser])
    watch_check.add_argument("--no-scrape", action="store_true", default=False,
                             help="Skip scraping stale routes")
    watch_check.add_argument("--no-notify", action="store_true", default=False,
                             help="Skip sending notifications")

    watch_sub.add_parser("run", help="Start watch daemon (foreground, Ctrl+C to stop)",
                         parents=[shared_parser])

    watch_setup = watch_sub.add_parser("setup", help="Configure notification settings",
                                       parents=[shared_parser])
    watch_setup.add_argument("--discord-webhook-url", required=False,
                             help="Discord webhook URL for notifications")

    schedule_parser = subparsers.add_parser("schedule", help="Manage scheduled scraping tasks (Windows Task Scheduler)")
    schedule_sub = schedule_parser.add_subparsers(dest="schedule_command")

    schedule_add = schedule_sub.add_parser("add", help="Add a route group to the schedule",
                                           parents=[shared_parser])
    schedule_add.add_argument("--routes", "-r", required=True,
                              help="Path to routes file")
    schedule_add.add_argument("--interval", "-i", type=int, default=None,
                              help="Minutes between scrape runs (minimum computed from route count)")
    schedule_add.add_argument("--months", default=None,
                              help="Comma-separated month numbers (e.g., 6,7,12)")
    schedule_add.add_argument("--name", default=None,
                              help="Master schedule name (default: master)")
    schedule_add.add_argument("--no-eval", action="store_true", default=False,
                              help="Disable Claude watch evaluation after scrape")
    schedule_add.add_argument("--env-file", default=None,
                              help="Path to .env file for mfa_responder")
    schedule_add.add_argument("--from", dest="date_from", default=None,
                              help="Only scrape from this date (YYYY-MM-DD)")
    schedule_add.add_argument("--to", dest="date_to", default=None,
                              help="Only scrape up to this date (YYYY-MM-DD)")
    schedule_add.add_argument("--no-wake", action="store_true", default=False,
                              help="Skip wake timer configuration")
    schedule_add.add_argument("--program", choices=["united", "aeroplan"],
                              default="united",
                              help="Award program for this route group (default: united). "
                                   "Aeroplan runs headed single-route and requires a larger "
                                   "minimum interval.")

    schedule_sub.add_parser("list", help="List all scheduled scrapes",
                            parents=[shared_parser])

    schedule_remove = schedule_sub.add_parser("remove", help="Remove a scheduled scrape",
                                              parents=[shared_parser])
    schedule_remove.add_argument("name", help="Schedule name to remove")

    schedule_enable = schedule_sub.add_parser("enable", help="Re-enable a paused schedule",
                                              parents=[shared_parser])
    schedule_enable.add_argument("name", help="Schedule name to enable")

    schedule_disable = schedule_sub.add_parser("disable", help="Manually pause a schedule",
                                               parents=[shared_parser])
    schedule_disable.add_argument("name", help="Schedule name to disable")

    schedule_sub.add_parser("status", help="Show schedule health and power config",
                            parents=[shared_parser])

    schema_parser = subparsers.add_parser("schema", help="Show command schemas for agent introspection",
                                          parents=[shared_parser])
    schema_parser.add_argument("target", nargs="?", default=None, help="Command name (e.g., 'query', 'alert add')")

    subparsers.add_parser("doctor", help="Run comprehensive diagnostics",
                          parents=[shared_parser])

    help_parser = subparsers.add_parser("help", help="Show help on a specific topic (mfa, proxy, watches, alerts, scraping)")
    help_parser.add_argument("topic", nargs="?", default=None,
                             help="Topic name: mfa, proxy, watches, alerts, scraping")

    args = parser.parse_args(argv)

    if not args.command:
        console = get_console()
        console.print("[bold]searchaero[/bold] — United MileagePlus award flight search")
        console.print()
        console.print("[bold]Get started:[/bold]")
        console.print("  searchaero setup                  Check environment and configure credentials")
        console.print("  searchaero search YYZ LAX         Scrape award availability for a route")
        console.print("  searchaero query YYZ LAX          Query cached results")
        console.print()
        console.print("[bold]Monitor prices:[/bold]")
        console.print("  searchaero watch add YYZ LAX --max-miles 20000")
        console.print("  searchaero alert add YYZ LAX --max-miles 70000 --cabin business")
        console.print()
        console.print("[bold]Automate:[/bold]")
        console.print("  searchaero schedule add --routes routes/yyz_wuh.txt --interval 60")
        console.print()
        console.print("[bold]Diagnostics:[/bold]")
        console.print("  searchaero doctor                 Run comprehensive health checks")
        console.print("  searchaero status                 Show database stats and coverage")
        console.print("  searchaero help <topic>           Help on: mfa, proxy, watches, alerts, scraping")
        console.print()
        console.print("[dim]Use 'searchaero <command> --help' for detailed usage of any command.[/dim]")
        return 0

    if args.command == "setup":
        return cmd_setup(args)

    if args.command == "search":
        return cmd_search(args)

    if args.command == "query":
        return cmd_query(args)

    if args.command == "deals":
        return cmd_deals(args)

    if args.command == "status":
        return cmd_status(args)

    if args.command == "alert":
        return cmd_alert(args)

    if args.command == "watch":
        return cmd_watch(args)

    if args.command == "schedule":
        return cmd_schedule(args)

    if args.command == "schema":
        return cmd_schema(args)

    if args.command == "doctor":
        return cmd_doctor(args)

    if args.command == "help":
        return cmd_help_topic(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
