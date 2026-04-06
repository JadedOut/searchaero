"""MFA code retrieval for United Airlines login.

Uses a file-based handoff: the script writes a signal file when it needs
an MFA code, and polls for a response file containing the code. This lets
an external process (e.g., Claude Code with Gmail MCP) provide the code.

Signal file: .mfa_request  (script creates this when MFA is needed)
Response file: .mfa_code   (external process writes the 6-digit code here)
"""

import re
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
MFA_REQUEST_FILE = SCRIPT_DIR / ".mfa_request"
MFA_CODE_FILE = SCRIPT_DIR / ".mfa_code"


def fetch_united_mfa_code(
    timeout: int = 120,
    poll_interval: int = 3,
    **kwargs,
) -> str | None:
    """Wait for an MFA code to be provided via file.

    Creates .mfa_request to signal that a code is needed, then polls
    .mfa_code for the response.

    Args:
        timeout: Max seconds to wait (default 120)
        poll_interval: Seconds between file checks (default 3)
        **kwargs: Ignored (accepts gmail_address etc. for backward compat)

    Returns:
        The 6-digit code as a string, or None if not found within timeout.
    """
    # Clean up any stale files
    MFA_CODE_FILE.unlink(missing_ok=True)

    # Signal that we need a code
    MFA_REQUEST_FILE.write_text("NEED_MFA_CODE")
    print(f"  *** MFA CODE NEEDED ***")
    print(f"  Waiting for code in {MFA_CODE_FILE} (timeout {timeout}s)...")

    deadline = time.time() + timeout
    while time.time() < deadline:
        if MFA_CODE_FILE.exists():
            raw = MFA_CODE_FILE.read_text().strip()
            code = re.search(r'(\d{6})', raw)
            if code:
                print(f"  MFA code received: {code.group(1)}")
                # Clean up
                MFA_REQUEST_FILE.unlink(missing_ok=True)
                MFA_CODE_FILE.unlink(missing_ok=True)
                return code.group(1)

        remaining = int(deadline - time.time())
        if remaining > 0:
            time.sleep(poll_interval)

    print("  ERROR: Timed out waiting for MFA code.")
    MFA_REQUEST_FILE.unlink(missing_ok=True)
    return None
