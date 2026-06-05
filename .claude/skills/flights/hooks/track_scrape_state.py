#!/usr/bin/env python3
"""
PostToolUse hook: tracks scrape state by writing/removing a marker file.

When a `searchaero search` command runs, writes ~/.searchaero/.awaiting_query
so that the Stop hook can verify results were displayed before the agent stops.

When a `searchaero query` command runs, removes the marker (results presented).

Exit codes:
- 0: Always (this hook never blocks)

Reads JSON from stdin (Claude hook protocol).
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LOG_FILE = SCRIPT_DIR / "track_scrape_state.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, mode='a')]
)
logger = logging.getLogger(__name__)

MARKER_DIR = Path.home() / ".searchaero"
MARKER_FILE = MARKER_DIR / ".awaiting_query"


def main():
    logger.info("=" * 60)
    logger.info("Hook started: track_scrape_state (PostToolUse)")

    try:
        # Read stdin JSON
        try:
            input_data = json.load(sys.stdin)
        except (json.JSONDecodeError, EOFError):
            logger.warning("Malformed or empty stdin JSON")
            print(json.dumps({"result": "continue"}))
            sys.exit(0)

        # Extract command from tool_input
        tool_input = input_data.get("tool_input", {})
        if not isinstance(tool_input, dict):
            logger.warning("tool_input is not a dict")
            print(json.dumps({"result": "continue"}))
            sys.exit(0)

        command = tool_input.get("command", "")
        if not isinstance(command, str):
            logger.warning("command is not a string")
            print(json.dumps({"result": "continue"}))
            sys.exit(0)

        logger.info(f"Command: {command[:200]}")

        # Detect subcommand regardless of invocation method:
        # - "searchaero search" (installed binary)
        # - "cli.py search" (running from source via python cli.py)
        is_search = ("searchaero search" in command or "cli.py search" in command) and "--help" not in command
        is_query = ("searchaero query" in command or "cli.py query" in command) and "--help" not in command

        # Check for search (scrape started)
        if is_search:
            logger.info("Detected search command — writing marker file")
            try:
                os.makedirs(str(MARKER_DIR), exist_ok=True)
                marker_data = {
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    "command": command
                }
                MARKER_FILE.write_text(json.dumps(marker_data), encoding="utf-8")
                logger.info(f"Marker written: {MARKER_FILE}")
            except (OSError, PermissionError) as e:
                logger.error(f"Failed to write marker file: {e}")

        # Check for query (results presented)
        elif is_query:
            logger.info("Detected searchaero query — removing marker file")
            try:
                if MARKER_FILE.exists():
                    MARKER_FILE.unlink()
                    logger.info("Marker removed")
                else:
                    logger.info("No marker to remove")
            except (OSError, PermissionError) as e:
                logger.error(f"Failed to remove marker file: {e}")

        else:
            logger.info("Command does not match searchaero search/query — no action")

        print(json.dumps({"result": "continue"}))
        sys.exit(0)

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        print(json.dumps({"result": "continue"}))
        sys.exit(0)


if __name__ == "__main__":
    main()
