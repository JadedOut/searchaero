#!/usr/bin/env python3
"""
Stop hook: validates that the agent ran `searchaero query` after a scrape.

If ~/.searchaero/.awaiting_query exists and is recent (< 30 min), blocks
the agent from stopping until it presents results to the user.

Exit codes:
- 0: Validation passed (continue)
- 1: Validation failed (block — must run query first)

Reads JSON from stdin (Claude hook protocol).
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LOG_FILE = SCRIPT_DIR / "validate_query_after_scrape.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, mode='a')]
)
logger = logging.getLogger(__name__)

MARKER_FILE = Path.home() / ".searchaero" / ".awaiting_query"

BLOCK_REASON = (
    "VALIDATION FAILED: You scraped a route but never presented the results.\n\n"
    "ACTION REQUIRED: Run `searchaero query ORIG DEST --program united` to display "
    "results to the user before finishing. Replace ORIG DEST with the route you "
    "scraped. Do not stop until results are displayed."
)


def build_block_reason(command):
    """Build a program-aware block reason from the stored scrape command.

    Detects the program from the command string so the guardrail names the
    program actually scraped. United (or no --program) -> "united".
    """
    program = "aeroplan" if "--program aeroplan" in (command or "") else "united"
    return (
        "VALIDATION FAILED: You scraped a route but never presented the results.\n\n"
        f"ACTION REQUIRED: Run `searchaero query ORIG DEST --program {program}` to "
        "display results to the user before finishing. Replace ORIG DEST with the "
        "route you scraped. Do not stop until results are displayed."
    )


def main():
    logger.info("=" * 60)
    logger.info("Hook started: validate_query_after_scrape (Stop)")

    try:
        # Read stdin JSON
        try:
            input_data = json.load(sys.stdin)
        except (json.JSONDecodeError, EOFError):
            logger.warning("Malformed or empty stdin JSON")
            print(json.dumps({"result": "continue"}))
            sys.exit(0)

        # Check for stop_hook_active to prevent infinite loops
        if input_data.get("stop_hook_active") is True:
            logger.info("stop_hook_active is true — allowing through to prevent loop")
            print(json.dumps({"result": "continue"}))
            sys.exit(0)

        # Check if marker file exists
        if not MARKER_FILE.exists():
            logger.info("No marker file — allowing through")
            print(json.dumps({"result": "continue"}))
            sys.exit(0)

        # Marker exists — read and check staleness
        logger.info(f"Marker file found: {MARKER_FILE}")
        try:
            marker_text = MARKER_FILE.read_text(encoding="utf-8")
            marker_data = json.loads(marker_text)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Marker file has malformed JSON: {e} — deleting and allowing through")
            try:
                MARKER_FILE.unlink()
            except OSError:
                pass
            print(json.dumps({"result": "continue"}))
            sys.exit(0)

        # Check if marker is stale (> 30 minutes old)
        scraped_at_str = marker_data.get("scraped_at", "")
        try:
            scraped_at = datetime.fromisoformat(scraped_at_str)
            # Ensure timezone-aware comparison
            now = datetime.now(timezone.utc)
            if scraped_at.tzinfo is None:
                scraped_at = scraped_at.replace(tzinfo=timezone.utc)
            age_minutes = (now - scraped_at).total_seconds() / 60.0
        except (ValueError, TypeError):
            logger.warning("Could not parse scraped_at timestamp — deleting stale marker")
            try:
                MARKER_FILE.unlink()
            except OSError:
                pass
            print(json.dumps({"result": "continue"}))
            sys.exit(0)

        if age_minutes > 30:
            logger.info(f"Marker is {age_minutes:.1f} min old (> 30 min) — stale, deleting and allowing through")
            try:
                MARKER_FILE.unlink()
            except OSError:
                pass
            print(json.dumps({"result": "continue"}))
            sys.exit(0)

        # Marker is recent — block
        command = marker_data.get("command", "")
        reason = build_block_reason(command)
        logger.warning(f"BLOCK: Marker is {age_minutes:.1f} min old — agent must query before stopping")
        logger.info(f"Scrape command was: {command or 'unknown'}")
        print(json.dumps({"result": "block", "reason": reason}))
        sys.exit(1)

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        print(json.dumps({"result": "continue"}))
        sys.exit(0)


if __name__ == "__main__":
    main()
