#!/usr/bin/env python3
"""Scheduled scrape orchestrator — starts mfa_responder, runs searchaero search, logs results.

Orchestrates the 3-step autonomous scraping pipeline:
  1. Clean stale MFA handoff files
  2. Launch mfa_responder.py as a background daemon
  3. Run `searchaero search` with file-based MFA, ephemeral browser, headless mode

Usage with Claude /schedule:
    python scripts/scheduled_scrape.py --routes routes/canada_us_all.txt

Usage with Windows Task Scheduler (via .bat wrapper):
    scheduled_scrape.bat

Environment variables required by mfa_responder:
    SEARCHAERO_GMAIL_SENDER       — Gmail address for IMAP login
    SEARCHAERO_GMAIL_APP_PASSWORD — Gmail app password for IMAP login
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MFA_DIR = os.path.join(os.path.expanduser("~"), ".searchaero")
_MFA_REQUEST = os.path.join(_MFA_DIR, "mfa_request")
_MFA_RESPONSE = os.path.join(_MFA_DIR, "mfa_response")
_LOG_DIR = os.path.join(_MFA_DIR, "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "scheduled_scrape.jsonl")
_LOCK_FILE = os.path.join(_MFA_DIR, "scrape.lock")
_STATE_FILE = os.path.join(_MFA_DIR, "scrape_state.json")

def _read_state(schedule_name):
    """Read backoff state for a schedule. Returns default state if missing."""
    if not schedule_name:
        return {"consecutive_failures": 0, "disabled": False}
    if not os.path.isfile(_STATE_FILE):
        return {"consecutive_failures": 0, "disabled": False, "disabled_at": None, "disabled_reason": None, "last_failure_at": None}
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            all_states = json.load(f)
        return all_states.get(schedule_name, {"consecutive_failures": 0, "disabled": False, "disabled_at": None, "disabled_reason": None, "last_failure_at": None})
    except (json.JSONDecodeError, OSError):
        return {"consecutive_failures": 0, "disabled": False, "disabled_at": None, "disabled_reason": None, "last_failure_at": None}


def _write_state(schedule_name, state):
    """Write backoff state for a schedule."""
    if not schedule_name:
        return
    all_states = {}
    if os.path.isfile(_STATE_FILE):
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                all_states = json.load(f)
        except (json.JSONDecodeError, OSError):
            all_states = {}
    all_states[schedule_name] = state
    os.makedirs(_MFA_DIR, exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_states, f, indent=2)


def _acquire_lock():
    """Exit silently if another scrape is already running."""
    if os.path.isfile(_LOCK_FILE):
        try:
            pid = int(open(_LOCK_FILE).read().strip())
            os.kill(pid, 0)  # signal 0 = check if PID is alive
            print(f"Another scrape is running (PID {pid}), skipping.", file=sys.stderr)
            sys.exit(0)
        except (OSError, ValueError):
            pass  # stale lock — PID is dead, proceed
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))


def _release_lock():
    """Remove the lock file."""
    try:
        if os.path.isfile(_LOCK_FILE):
            os.remove(_LOCK_FILE)
    except OSError:
        pass


def _clean_mfa_files():
    """Remove stale MFA handoff files from previous runs."""
    for fname in ("mfa_request", "mfa_response"):
        fpath = os.path.join(_MFA_DIR, fname)
        if os.path.isfile(fpath):
            os.remove(fpath)
            print(f"Cleaned stale {fname}", file=sys.stderr)


def _append_window_flags(cmd, args, months, date_from, date_to):
    """Append --months/--from/--to flags to cmd.

    Per-group overrides (months/date_from/date_to) take priority; otherwise fall
    back to the corresponding args values.  Mutates and returns cmd.
    """
    m = months if months is not None else args.months
    df = date_from if date_from is not None else args.search_from
    dt = date_to if date_to is not None else args.search_to
    if m:
        cmd.extend(["--months", m])
    if df:
        cmd.extend(["--from", df])
    if dt:
        cmd.extend(["--to", dt])
    return cmd


def _build_search_cmd(args, routes_file=None, months=None,
                      date_from=None, date_to=None):
    """Build the searchaero search command list.

    When called with explicit route/month/date params (multi-group mode),
    those override the corresponding args values.  When called without them,
    falls back to args (backward compat).
    """
    rf = routes_file or args.routes
    cmd = [
        sys.executable, os.path.join(_PROJECT_ROOT, "cli.py"),
        "search",
        "--file", rf,
        "--mfa-file",
        "--mfa-method", "email",
        "--ephemeral",
        "--headless",
        "--json",
        "--delay", str(args.delay),
    ]
    if args.db_path:
        cmd.extend(["--db-path", args.db_path])
    return _append_window_flags(cmd, args, months, date_from, date_to)


def _parse_routes_file(routes_file):
    """Parse a routes file into a list of (origin, dest) tuples.

    Thin wrapper over the canonical ``core.routes.load_routes`` parser (the
    same one used by cli.py, hybrid_scraper, burn_in, orchestrate) plus a guard
    for a missing/empty path.  Route lines are space-separated ``ORIGIN DEST``;
    blank lines and ``#`` comments are skipped and codes are uppercased.
    """
    sys.path.insert(0, _PROJECT_ROOT)
    from core.routes import load_routes

    return load_routes(routes_file) if routes_file and os.path.isfile(routes_file) else []


def _build_aeroplan_search_cmd(args, origin, dest, months=None,
                               date_from=None, date_to=None):
    """Build a HEADED, SINGLE-ROUTE Aeroplan search command list.

    Aeroplan is single-route only (positional ORIGIN DEST, never --file) and
    must run headed (no --headless / --ephemeral).  MFA is email/file-based.
    """
    cmd = [
        sys.executable, os.path.join(_PROJECT_ROOT, "cli.py"),
        "search",
        "--program", "aeroplan",
        origin, dest,
        "--mfa-file",
        "--mfa-method", "email",
        # Scheduled/unattended run: route any deadline-hit alert to Discord.
        "--autonomous",
    ]
    if args.db_path:
        cmd.extend(["--db-path", args.db_path])
    return _append_window_flags(cmd, args, months, date_from, date_to)


def _build_group_search_cmds(args, grp):
    """Return a list of (label, command) pairs for a route group.

    Program-aware dispatch:
      - program == "united" (or missing) -> exactly one command (unchanged
        united shape via _build_search_cmd), labelled by the group name.
      - program == "aeroplan" -> one HEADED single-route command per route in
        the group's routes file, labelled "<group> <ORIGIN>-<DEST>".
    """
    program = grp.get("program", "united")
    grp_name = grp.get("name", "default")
    routes_file = grp.get("routes_file")
    months = grp.get("months")
    date_from = grp.get("date_from")
    date_to = grp.get("date_to")

    if program == "aeroplan":
        cmds = []
        for origin, dest in _parse_routes_file(routes_file):
            label = f"{grp_name} {origin}-{dest}"
            cmd = _build_aeroplan_search_cmd(
                args, origin, dest,
                months=months, date_from=date_from, date_to=date_to,
            )
            cmds.append((label, cmd))
        return cmds

    # United (default): exactly one command, unchanged shape.
    cmd = _build_search_cmd(
        args,
        routes_file=routes_file,
        months=months,
        date_from=date_from,
        date_to=date_to,
    )
    return [(grp_name, cmd)]


def _build_responder_cmd(args):
    """Build the mfa_responder launch command."""
    cmd = [sys.executable, os.path.join(_PROJECT_ROOT, "scripts", "mfa_responder.py")]
    return cmd


def _write_log(entry: dict):
    """Append a JSONL log entry to the scheduled scrape log file."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _notify_failure(error_msg: str, routes_file: str, duration: int):
    """Best-effort failure notification via Discord."""
    try:
        sys.path.insert(0, _PROJECT_ROOT)
        from core.notify import load_notify_config, send_discord
        config = load_notify_config()
        webhook_url = config.get("discord_webhook_url", "")
        if webhook_url:
            send_discord(
                webhook_url,
                content=f"**Scheduled scrape failed**\nError: {error_msg}\nRoutes: {routes_file}\nDuration: {duration}s",
            )
    except Exception:
        pass  # Notification is best-effort


def _load_env_file(env_file: str):
    """Load environment variables from a file (KEY=VALUE per line)."""
    if not os.path.isfile(env_file):
        print(f"Warning: env file not found: {env_file}", file=sys.stderr)
        return
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def _parse_routes_from_result(search_result):
    """Extract route tuples from search JSON output.

    The search --json output has format: {"routes": [{"route": "YYZ-LAX", ...}, ...]}
    Returns list of (origin, dest) tuples.
    """
    if not search_result or not isinstance(search_result, dict):
        return []
    routes = search_result.get("routes", [])
    result = []
    for r in routes:
        route_str = r.get("route", "")
        if "-" in route_str:
            parts = route_str.split("-")
            if len(parts) == 2 and len(parts[0]) == 3 and len(parts[1]) == 3:
                result.append((parts[0].upper(), parts[1].upper()))
    return result


_ROUTES_SENTINEL = "__USE_SCHEDULE__"


def main():
    parser = argparse.ArgumentParser(
        description="Orchestrate mfa_responder + searchaero search for scheduled scraping",
    )
    parser.add_argument(
        "--routes", default=_ROUTES_SENTINEL,
        help="Path to routes file (default: from schedule registry, or routes/canada_test.txt)",
    )
    parser.add_argument(
        "--delay", type=float, default=3.0,
        help="Seconds between API calls (default: 3.0)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print commands without executing, then exit 0",
    )
    parser.add_argument(
        "--db-path", default=None,
        help="Override database path",
    )
    parser.add_argument(
        "--env-file",
        default=os.path.join(os.path.expanduser("~"), ".searchaero", ".env"),
        help="Path to .env file for mfa_responder environment variables "
             "(default: ~/.searchaero/.env, the canonical credential file). "
             "All creds — airline logins AND Gmail (SEARCHAERO_GMAIL_SENDER / "
             "SEARCHAERO_GMAIL_APP_PASSWORD) — live there, so unattended runs "
             "find the Gmail responder creds without any extra flag.",
    )
    parser.add_argument(
        "--no-eval", action="store_true", default=False,
        help="Skip the watch evaluation + email step",
    )
    parser.add_argument(
        "--register-scheduler", action="store_true", default=False,
        help="Print (or run) the schtasks command to register in Windows Task Scheduler",
    )
    parser.add_argument(
        "--months", default=None,
        help="Comma-separated month numbers to scrape (e.g., 6,7,12). Passed through to searchaero search.",
    )
    parser.add_argument(
        "--from", dest="search_from", default=None,
        help="Only scrape windows from this date (YYYY-MM-DD). Passed through to searchaero search.",
    )
    parser.add_argument(
        "--to", dest="search_to", default=None,
        help="Only scrape windows up to this date (YYYY-MM-DD). Passed through to searchaero search.",
    )
    parser.add_argument(
        "--schedule-name", default=None,
        help="Schedule name for backoff tracking (set by generated .bat)",
    )
    args = parser.parse_args()

    # Load env file if provided
    if args.env_file:
        _load_env_file(args.env_file)

    if args.register_scheduler:
        bat_path = os.path.join(_PROJECT_ROOT, "scripts", "scheduled_scrape.bat")
        schtasks_cmd = (
            f'schtasks /create /tn "searchaero-scheduled-scrape" '
            f'/tr "{bat_path}" '
            f'/sc daily /st 08:00 /ri 720 /du 24:00 '
            f'/f'
        )
        print("=== Windows Task Scheduler Registration ===")
        print(f"Command:\n  {schtasks_cmd}\n")
        print("To register, run the above command in an elevated (admin) terminal.")
        print("To verify: schtasks /query /tn \"searchaero-scheduled-scrape\"")
        print("To remove:  schtasks /delete /tn \"searchaero-scheduled-scrape\" /f")
        print()
        print("After creating the task, go to the task's Properties -> Conditions tab:")
        print("    Check \"Wake the computer to run this task\"")
        print("    Check \"Start the task only if the computer is on AC power\"")
        print()
        print("WARNING: Your PC must be plugged in (AC power) for wake-from-sleep")
        print("   to work reliably. Sleep-wake via Task Scheduler does not work on battery.")
        return 0

    # --- Determine mode: multi-group vs single-file ---
    # Multi-group mode: --schedule-name given and --routes was NOT explicitly set
    multi_group = args.schedule_name and args.routes == _ROUTES_SENTINEL
    route_groups = []

    if multi_group:
        # Load route groups from schedule registry
        sys.path.insert(0, _PROJECT_ROOT)
        from core.scheduler import load_schedules as _load_schedules
        all_schedules = _load_schedules()
        schedule_entry = None
        for s in all_schedules:
            if s.get("name") == args.schedule_name:
                schedule_entry = s
                break
        if not schedule_entry:
            print(f"Schedule '{args.schedule_name}' not found in registry.", file=sys.stderr)
            return 1
        route_groups = schedule_entry.get("route_groups", [])
        if not route_groups:
            print(f"Schedule '{args.schedule_name}' has no route groups.", file=sys.stderr)
            return 1
    else:
        # Single-file mode (backward compat)
        if args.routes == _ROUTES_SENTINEL:
            args.routes = os.path.join(_PROJECT_ROOT, "routes", "canada_test.txt")
        route_groups = [{
            "name": "default",
            "routes_file": args.routes,
            "months": args.months,
            "date_from": args.search_from,
            "date_to": args.search_to,
        }]

    # --- Dry run ---
    if args.dry_run:
        responder_cmd = _build_responder_cmd(args)
        print("=== DRY RUN ===")
        print(f"Responder: {' '.join(responder_cmd)}")
        for i, grp in enumerate(route_groups):
            for label, search_cmd in _build_group_search_cmds(args, grp):
                print(f"Search [{label}]: {' '.join(search_cmd)}")
        print(f"Log file:  {_LOG_FILE}")
        if args.no_eval:
            print("Eval:      SKIPPED (--no-eval)")
        else:
            print("Eval:      enabled (watches.yaml -> Claude API -> Discord)")
        return 0

    # --- Step 0: Acquire lock (prevent concurrent runs) ---
    _acquire_lock()

    # --- Backoff check ---
    state = _read_state(args.schedule_name)
    if state.get("disabled"):
        sched_label = args.schedule_name or "this schedule"
        print(f"Schedule '{sched_label}' is paused ({state.get('disabled_reason', '3 consecutive failures')}). "
              f"Run `searchaero schedule enable {sched_label}` to resume.", file=sys.stderr)
        _release_lock()
        return 0

    # --- Step 1: Clean stale MFA files ---
    _clean_mfa_files()

    # --- Step 2: Start mfa_responder ---
    responder_cmd = _build_responder_cmd(args)
    print(f"Starting mfa_responder...", file=sys.stderr)
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    responder = subprocess.Popen(
        responder_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
    )

    # --- Step 3: Wait for responder initialization ---
    time.sleep(3)

    start_time = time.monotonic()
    exit_code = 0  # overall exit code (non-zero if any group fails)
    all_routes_scraped = []

    try:
        # --- Step 4: Run searchaero search for each route group ---
        for grp_idx, grp in enumerate(route_groups):
            grp_name = grp.get("name", f"group-{grp_idx}")
            grp_routes_file = grp.get("routes_file", "")

            # Program-aware: united -> 1 command; aeroplan -> 1 per route.
            for label, search_cmd in _build_group_search_cmds(args, grp):
                cmd_start = time.monotonic()

                print(f"Running '{label}': {' '.join(search_cmd)}", file=sys.stderr)
                result = subprocess.run(
                    search_cmd,
                    capture_output=True,
                    text=True,
                    cwd=_PROJECT_ROOT,
                )
                cmd_exit = result.returncode
                cmd_duration = int(time.monotonic() - cmd_start)

                if cmd_exit == 0:
                    search_result = None
                    try:
                        search_result = json.loads(result.stdout)
                    except (json.JSONDecodeError, ValueError):
                        search_result = {"raw_stdout": result.stdout[:2000]}

                    log_entry = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "success",
                        "group_name": grp_name,
                        "label": label,
                        "routes_file": grp_routes_file,
                        "duration_seconds": cmd_duration,
                        "search_result": search_result,
                        "exit_code": 0,
                        "eval_method": "deferred",
                    }
                    _write_log(log_entry)
                    print(f"'{label}' completed in {cmd_duration}s", file=sys.stderr)

                    # Collect routes for eval
                    all_routes_scraped.extend(_parse_routes_from_result(search_result))
                else:
                    stderr_tail = (result.stderr or "")[-500:]
                    error_msg = f"searchaero search exited with code {cmd_exit} (label: {label})"

                    log_entry = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "error",
                        "group_name": grp_name,
                        "label": label,
                        "routes_file": grp_routes_file,
                        "duration_seconds": cmd_duration,
                        "error": error_msg,
                        "stderr_tail": stderr_tail,
                        "exit_code": cmd_exit,
                        "eval_method": "skipped",
                    }
                    _write_log(log_entry)
                    print(f"'{label}' failed ({error_msg})", file=sys.stderr)
                    if stderr_tail:
                        print(f"stderr: {stderr_tail}", file=sys.stderr)
                    _notify_failure(error_msg, grp_routes_file, cmd_duration)
                    exit_code = cmd_exit  # propagate failure

        # --- Step 5: Post-run summary and eval ---
        total_duration = int(time.monotonic() - start_time)

        if exit_code == 0:
            # All groups succeeded -- reset backoff
            if args.schedule_name:
                state["consecutive_failures"] = 0
                state["disabled"] = False
                _write_state(args.schedule_name, state)

            # Evaluate watches once for all routes scraped
            if not args.no_eval and all_routes_scraped:
                try:
                    sys.path.insert(0, _PROJECT_ROOT)
                    from core.eval_watches import run_eval_and_notify
                    from core.notify import load_notify_config

                    notify_config = load_notify_config()
                    email_sent = run_eval_and_notify(
                        db_path=args.db_path,
                        routes_scraped=all_routes_scraped,
                        config=notify_config,
                    )
                    if email_sent:
                        print("Watch alert sent", file=sys.stderr)
                except Exception as exc:
                    print(f"Eval/notify step failed (non-fatal): {exc}", file=sys.stderr)

            # Summary log entry
            summary_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "summary",
                "schedule_name": args.schedule_name,
                "groups_count": len(route_groups),
                "total_duration_seconds": total_duration,
                "routes_scraped": len(all_routes_scraped),
                "exit_code": 0,
                "eval_method": "skipped" if args.no_eval else "eval_watches",
            }
            _write_log(summary_entry)
            print(f"All groups completed in {total_duration}s ({len(route_groups)} groups, {len(all_routes_scraped)} routes)", file=sys.stderr)
        else:
            # At least one group failed -- backoff tracking
            if args.schedule_name:
                state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
                state["last_failure_at"] = datetime.now(timezone.utc).isoformat()
                if state["consecutive_failures"] >= 3:
                    state["disabled"] = True
                    state["disabled_at"] = datetime.now(timezone.utc).isoformat()
                    state["disabled_reason"] = "3 consecutive failures"
                    try:
                        sys.path.insert(0, _PROJECT_ROOT)
                        from core.notify import load_notify_config, send_discord
                        config = load_notify_config()
                        webhook_url = config.get("discord_webhook_url", "")
                        if webhook_url:
                            send_discord(webhook_url, content=(
                                f"**Schedule `{args.schedule_name}` auto-paused** after 3 consecutive failures.\n"
                                f"Run `searchaero schedule enable {args.schedule_name}` to resume."
                            ))
                    except Exception:
                        pass
                _write_state(args.schedule_name, state)

    except Exception as exc:
        duration = int(time.monotonic() - start_time)
        error_msg = f"Exception: {exc}"

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "error",
            "schedule_name": args.schedule_name,
            "duration_seconds": duration,
            "error": error_msg,
            "stderr_tail": "",
            "exit_code": 1,
            "eval_method": "skipped",
        }
        _write_log(log_entry)
        print(f"Scrape exception: {exc}", file=sys.stderr)

        _notify_failure(error_msg, str(route_groups), duration)

        # Backoff tracking
        if args.schedule_name:
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
            state["last_failure_at"] = datetime.now(timezone.utc).isoformat()
            if state["consecutive_failures"] >= 3:
                state["disabled"] = True
                state["disabled_at"] = datetime.now(timezone.utc).isoformat()
                state["disabled_reason"] = "3 consecutive failures"
                try:
                    sys.path.insert(0, _PROJECT_ROOT)
                    from core.notify import load_notify_config, send_discord
                    config = load_notify_config()
                    webhook_url = config.get("discord_webhook_url", "")
                    if webhook_url:
                        send_discord(webhook_url, content=(
                            f"**Schedule `{args.schedule_name}` auto-paused** after 3 consecutive failures.\n"
                            f"Last error: {error_msg}\n"
                            f"Run `searchaero schedule enable {args.schedule_name}` to resume."
                        ))
                except Exception:
                    pass
            _write_state(args.schedule_name, state)

        exit_code = 1

    finally:
        # --- Step 6: Release lock + kill the responder ---
        _release_lock()
        responder.terminate()
        try:
            responder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            responder.kill()
            responder.wait()
        print("mfa_responder stopped", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
