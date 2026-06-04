"""Watch evaluation via Claude API — evaluates user-defined conditions against fresh availability."""

import json
import os
import sys
from datetime import datetime, timezone

_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".searchaero")
_WATCHES_PATH = os.path.join(_CONFIG_DIR, "watches.yaml")
_LOG_DIR = os.path.join(_CONFIG_DIR, "logs")
_NOTIFICATION_LOG = os.path.join(_LOG_DIR, "watch_notifications.jsonl")


def load_watches(watches_path=None):
    """Load watches from YAML. Returns [] if file missing or invalid."""
    path = watches_path or _WATCHES_PATH
    if not os.path.isfile(path):
        return []
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or not isinstance(data.get("watches"), list):
            return []
        return data["watches"]
    except Exception:
        return []


def query_fresh_availability(db_path, routes_scraped):
    """Query DB for availability on just-scraped routes.

    Args:
        db_path: Path to SQLite database (or None for default).
        routes_scraped: List of (origin, dest) tuples.

    Returns:
        Dict mapping "ORIG DEST" to list of availability row dicts.
    """
    if not routes_scraped:
        return {}

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.db import get_connection, query_availability

    conn = get_connection(db_path)
    result = {}
    try:
        for origin, dest in routes_scraped:
            rows = query_availability(conn, origin, dest)
            if rows:
                result[f"{origin} {dest}"] = rows
    finally:
        conn.close()
    return result


def evaluate_watches(watches, availability, timeout=30):
    """Call claude CLI to evaluate watches against availability.

    Returns email body string if conditions met, None if no matches.
    Falls back to template on any CLI error.
    """
    if not watches or not availability:
        return None

    import subprocess
    import shutil

    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("Warning: claude CLI not found on PATH, skipping eval", file=sys.stderr)
        return None

    # Filter availability to only routes mentioned in watches (or all if routes=[])
    filtered_avail = {}
    for watch in watches:
        watch_routes = watch.get("routes", [])
        if not watch_routes:
            filtered_avail = availability
            break
        for route in watch_routes:
            if route in availability:
                filtered_avail[route] = availability[route]

    if not filtered_avail:
        return None

    # Truncate availability to avoid token blowup — keep only the cheapest per route/cabin
    compact_avail = {}
    for route, rows in filtered_avail.items():
        by_cabin = {}
        for row in rows:
            cabin = row.get("cabin", "unknown")
            by_cabin.setdefault(cabin, []).append(row)
        compact_rows = []
        for cabin, cabin_rows in by_cabin.items():
            sorted_rows = sorted(cabin_rows, key=lambda r: r.get("miles", 999999))
            compact_rows.extend(sorted_rows[:5])
        compact_avail[route] = compact_rows

    prompt_data = json.dumps({"watches": watches, "availability": compact_avail}, default=str)

    prompt = (
        "You are an award flight deal evaluator. Given watch conditions and current availability data, "
        "determine which watches are satisfied by the availability. If ANY watch condition is met, "
        "compose a concise, informative alert message (plain text, no markdown). Include: which watch "
        "matched, the specific fares that triggered it (route, cabin, miles, date), and a brief note on why "
        "it's notable. If NO watch conditions are met, respond with exactly: NO_MATCHES\n\n"
        f"Data:\n{prompt_data}"
    )

    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt, "--model", "haiku"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            print(f"claude CLI exited {result.returncode}, using template fallback", file=sys.stderr)
            return template_fallback(availability)

        text = result.stdout.strip()
        if text == "NO_MATCHES":
            return None
        return text

    except subprocess.TimeoutExpired:
        print("claude CLI timeout, using template fallback", file=sys.stderr)
        return template_fallback(availability)
    except Exception as exc:
        print(f"claude CLI error ({exc}), using template fallback", file=sys.stderr)
        return template_fallback(availability)


def template_fallback(availability):
    """Generate a simple template email listing all fresh availability."""
    if not availability:
        return None

    lines = ["Fresh award availability found:\n"]

    for route, rows in availability.items():
        # Summarize by cabin
        by_cabin = {}
        for row in rows:
            cabin = row.get("cabin", "unknown")
            by_cabin.setdefault(cabin, []).append(row)

        lines.append(f"  {route}:")
        for cabin, cabin_rows in sorted(by_cabin.items()):
            cheapest = min(cabin_rows, key=lambda r: r.get("miles", 999999))
            miles = cheapest.get("miles", 0)
            lines.append(f"    {cabin}: {len(cabin_rows)} fares, cheapest {miles:,} miles")
        lines.append("")

    total_fares = sum(len(rows) for rows in availability.values())
    lines.append(f"Total: {len(availability)} routes, {total_fares} fares")
    lines.append("\nThis is an automated fallback report (Claude API was unavailable).")

    return "\n".join(lines)


def run_eval_and_notify(db_path, routes_scraped, config):
    """Top-level function called by scheduled_scrape.py.

    Loads watches, evaluates against fresh availability, sends Discord notification if needed.

    Args:
        db_path: Path to SQLite database (or None for default).
        routes_scraped: List of (origin, dest) tuples that were just scraped.
        config: Dict from load_notify_config() with discord_webhook_url.

    Returns:
        True if notification was sent, False otherwise.
    """
    # Load watches
    watches = load_watches()
    if not watches:
        return False

    # Query fresh availability
    availability = query_fresh_availability(db_path, routes_scraped)
    if not availability:
        return False

    # Evaluate watches via Claude API
    email_body = evaluate_watches(watches, availability)
    if not email_body:
        return False

    # Send Discord notification
    webhook_url = config.get("discord_webhook_url", "")

    if not webhook_url:
        print("Discord webhook not configured, skipping notification", file=sys.stderr)
        return False

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.notify import send_discord

    sent = send_discord(webhook_url, content=email_body)

    # Log notification to history
    if sent:
        try:
            os.makedirs(_LOG_DIR, exist_ok=True)
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "watches_evaluated": [w.get("name", "unnamed") for w in watches],
                "notification_body": email_body[:500],
                "routes_scraped": [f"{o}-{d}" for o, d in routes_scraped],
                "discord_sent": True,
            }
            with open(_NOTIFICATION_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, default=str) + "\n")
        except Exception as exc:
            print(f"Warning: failed to write notification log: {exc}", file=sys.stderr)

    return sent
