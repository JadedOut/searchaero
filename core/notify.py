"""Notification functions for searchaero watchlist alerts via Discord webhooks."""

import json
import os
import sys
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".searchaero")
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "config.json")


def load_notify_config() -> dict:
    """Load notification configuration from config file and env vars.

    Priority: env vars override config file values.

    Returns:
        Dict with key: discord_webhook_url.
    """
    config = {
        "discord_webhook_url": "",
    }

    # Read from config file if it exists
    if os.path.isfile(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "discord_webhook_url" in data:
                config["discord_webhook_url"] = data["discord_webhook_url"]
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: failed to read config: {exc}", file=sys.stderr)

    # Env var overrides
    env_webhook = os.getenv("SEARCHAERO_DISCORD_WEBHOOK_URL")
    if env_webhook is not None:
        config["discord_webhook_url"] = env_webhook

    return config


def save_notify_config(discord_webhook_url=None):
    """Save notification configuration to config file.

    Reads existing config first (if any), merges in provided values,
    then writes back.  Only updates keys that are provided (not None).

    Args:
        discord_webhook_url: Discord webhook URL for notifications.
    """
    os.makedirs(_CONFIG_DIR, exist_ok=True)

    # Read existing config to preserve other keys
    data = {}
    if os.path.isfile(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

    if discord_webhook_url is not None:
        data["discord_webhook_url"] = discord_webhook_url

    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


def send_discord(webhook_url: str, embeds: list = None, content: str = None) -> bool:
    """Send a message to a Discord webhook.

    Args:
        webhook_url: The Discord webhook URL to POST to.
        embeds: Optional list of embed dicts (Discord embed objects).
        content: Optional plain text message content.

    Returns:
        True on 2xx response, False on error.
    """
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    if not payload:
        return False

    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(webhook_url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "searchaero/1.0")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return True
    except urllib.error.URLError as exc:
        print(f"Discord webhook send failed: {exc}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"Discord webhook send error: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Watch notification formatter
# ---------------------------------------------------------------------------


def notify_watch_matches(watch: dict, matches: list, config: dict) -> bool:
    """Format and send a notification for watchlist matches via Discord.

    Args:
        watch: Dict with keys: origin, destination, max_miles, cabin, etc.
        matches: List of dicts with keys: date, cabin, award_type, miles, taxes_cents.
        config: Dict from load_notify_config() with discord_webhook_url.

    Returns:
        True on success, False on failure.
    """
    if not matches:
        return False

    origin = watch.get("origin", "???")
    dest = watch.get("destination", "???")
    max_miles = watch.get("max_miles", 0)

    # Find the cheapest match
    cheapest = min(matches, key=lambda m: m.get("miles", 999999))

    miles = cheapest.get("miles", 0)
    taxes_cents = cheapest.get("taxes_cents", 0) or 0
    taxes_dollars = f"${taxes_cents / 100:.2f}"
    cabin = cheapest.get("cabin", "unknown")
    award_type = cheapest.get("award_type", "")
    date = cheapest.get("date", "")

    fields = [
        {"name": "Cabin", "value": cabin, "inline": True},
        {"name": "Award Type", "value": award_type, "inline": True},
        {"name": "Miles", "value": f"{miles:,}", "inline": True},
        {"name": "Taxes", "value": taxes_dollars, "inline": True},
        {"name": "Date", "value": str(date), "inline": True},
    ]

    if len(matches) > 1:
        fields.append({
            "name": "Additional Matches",
            "value": f"+ {len(matches) - 1} more match{'es' if len(matches) - 1 > 1 else ''}",
            "inline": False,
        })

    fields.append({
        "name": "Threshold",
        "value": f"\u2264{max_miles:,} miles",
        "inline": False,
    })

    embed = {
        "title": f"Award Deal: {origin} \u2192 {dest}",
        "color": 0x00b894,
        "fields": fields,
    }

    webhook_url = config.get("discord_webhook_url", "")
    if not webhook_url:
        print("No notification channels configured, skipping notification", file=sys.stderr)
        return False

    return send_discord(webhook_url, embeds=[embed])
