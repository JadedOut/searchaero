#!/usr/bin/env python3
"""MFA responder — watches for ~/.searchaero/mfa_request, fetches code from Gmail via IMAP."""

import email
import email.header
import email.utils
import imaplib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_MFA_DIR = os.path.join(os.path.expanduser("~"), ".searchaero")
_MFA_REQUEST = os.path.join(_MFA_DIR, "mfa_request")
_MFA_RESPONSE = os.path.join(_MFA_DIR, "mfa_response")

IMAP_SERVER = "imap.gmail.com"
POLL_INTERVAL_SECS = 10
MAX_EMAIL_RETRIES = 15        # 15 × 10s = 2.5 min
MAX_EMAIL_AGE_SECS = 180      # reject codes older than 3 min
WATCH_POLL_SECS = 2           # how often to check for mfa_request file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [mfa_responder] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _strip_html_to_text(html: str) -> str:
    """Strip HTML to visible text, skipping <style> and <script> content."""
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self._pieces: list[str] = []
            self._skip_depth = 0  # depth counter for nested skip tags

        def handle_starttag(self, tag: str, attrs):
            t = tag.lower()
            if t in ("style", "script"):
                self._skip_depth += 1
            elif t in ("br", "p", "div", "tr"):
                self._pieces.append("\n")

        def handle_endtag(self, tag: str):
            t = tag.lower()
            if t in ("style", "script"):
                self._skip_depth = max(0, self._skip_depth - 1)

        def handle_data(self, data: str):
            if self._skip_depth == 0:
                self._pieces.append(data)

    s = _Stripper()
    s.feed(html)
    return "".join(s._pieces)


def extract_code_from_email(subject: str, body: str) -> str | None:
    """Extract a 6-digit MFA code from an email subject or HTML body.

    Strategy:
      1. Check subject line for a 6-digit code.
      2. Strip HTML from body to visible text.
      3. Contextual search: look for 6-digit code near anchor phrases.
      4. Fallback: find all 6-digit codes in stripped text, reject trivial
         patterns (all zeros, all same digit), return first remaining match.
    """
    # 1. Subject regex
    m = re.search(r"\b(\d{6})\b", subject)
    if m:
        log.info("MFA code found via subject match")
        return m.group(1)

    # 2. Strip HTML
    text = _strip_html_to_text(body)

    # 3. Contextual search near anchor phrases
    ctx = re.search(
        r"(?:verification code|your code is|enter this code|code:)\s*[^\d]{0,50}(\d{6})",
        text,
        re.IGNORECASE,
    )
    if ctx:
        log.info("MFA code found via contextual match")
        return ctx.group(1)

    # 4. Fallback — all 6-digit matches, reject trivial patterns
    for fm in re.finditer(r"\b(\d{6})\b", text):
        candidate = fm.group(1)
        # Reject all-same-digit codes (000000, 111111, …, 999999)
        if len(set(candidate)) == 1:
            continue
        log.info("MFA code found via fallback match")
        return candidate

    return None


def get_email_code(
    imap_account: str, imap_password: str, sender_filter: str = "@united.com"
) -> str | None:
    """Connect to Gmail IMAP and extract the MFA code.

    Adapted from mintapi's get_email_code() (signIn.py:81-178).
    Guards: sender filter, age check (3 min), newest-first, limit 3, delete-after-read.

    The ``sender_filter`` is a literal-ish substring matched case-insensitively
    against the email ``From`` header (e.g. ``@united.com``, ``aircanada.com``,
    ``@aeroplan.com``). Defaults to ``@united.com`` for backward compatibility.
    """
    try:
        client = imaplib.IMAP4_SSL(IMAP_SERVER)
    except imaplib.IMAP4.error:
        log.error("Failed to connect to IMAP server")
        return None

    try:
        client.login(imap_account, imap_password)
    except imaplib.IMAP4.error:
        log.error("IMAP login failed — check SEARCHAERO_GMAIL_SENDER / SEARCHAERO_GMAIL_APP_PASSWORD")
        return None

    code = None
    num_to_delete = None

    for attempt in range(MAX_EMAIL_RETRIES):
        time.sleep(POLL_INTERVAL_SECS)
        log.info("Polling for MFA email (attempt %d/%d)", attempt + 1, MAX_EMAIL_RETRIES)

        rv, _ = client.select("INBOX")
        if rv != "OK":
            log.error("Failed to open INBOX")
            break

        rv, data = client.search(None, "ALL")
        if rv != "OK" or not data[0]:
            continue

        # Newest-first, check at most 3 (mintapi pattern: signIn.py:105-107)
        checked = 0
        for num in data[0].split()[::-1]:
            checked += 1
            if checked > 3:
                break

            rv, msg_data = client.fetch(num, "(BODY.PEEK[])")
            if rv != "OK":
                continue

            msg = email.message_from_bytes(msg_data[0][1])

            # --- Sender filter (mintapi pattern: signIn.py:123-124) ---
            frm = str(email.header.make_header(email.header.decode_header(msg["From"])))
            if not re.search(re.escape(sender_filter), frm, re.IGNORECASE):
                continue

            # --- Age check (mintapi pattern: signIn.py:134-144) ---
            date_tuple = email.utils.parsedate_tz(msg["Date"])
            if date_tuple:
                email_time = datetime.fromtimestamp(email.utils.mktime_tz(date_tuple))
                age = (datetime.now() - email_time).total_seconds()
                if age > MAX_EMAIL_AGE_SECS:
                    log.debug("Email too old (%.0fs), skipping", age)
                    continue
            else:
                continue  # can't verify age — skip

            # --- Code extraction ---
            subject = str(email.header.make_header(email.header.decode_header(msg["Subject"])))

            # Extract body (multipart walk: prefer text/plain, fall back to text/html)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(errors="replace")
                            break
                    elif part.get_content_type() == "text/html":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(errors="replace")
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode(errors="replace")

            code = extract_code_from_email(subject, body)
            if code:
                num_to_delete = num
                log.info("Found MFA code in email")
                break

        if code:
            # Delete email after read (mintapi pattern: signIn.py:169-173)
            if num_to_delete:
                client.store(num_to_delete, "+FLAGS", "\\Deleted")
                client.expunge()
                log.info("Deleted MFA email")
            break

    client.logout()
    return code


def _process_pending_request(gmail_account, gmail_password):
    """Process a single pending mfa_request file, if present, exactly once.

    Reads + PARSES the request, then CONSUMES (deletes) the request file BEFORE
    acting on it, so each request is answered exactly once. Without this the
    watch loop re-detects an already-answered request on its next tick and polls
    Gmail for a code that was already consumed/deleted (the "double-request"
    wart). On a partial/garbled read the file is left in place to retry next tick.

    Returns one of: "none" (no request), "retry" (unparseable — left in place),
    "signal" (login-complete signal), "skipped" (non-email method),
    "answered" (code fetched + written), "no_code" (email request, no code found).
    """
    if not os.path.isfile(_MFA_REQUEST):
        return "none"
    try:
        with open(_MFA_REQUEST, "r") as f:
            request = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Likely a partial write in progress — retry next tick, do NOT consume.
        return "retry"

    # Consume the request now: it will be answered exactly once. (Guarded — a
    # racing deleter on the login side is harmless.)
    try:
        os.remove(_MFA_REQUEST)
    except OSError:
        pass

    # Skip if this is a login-complete signal (cli.py:120-124).
    if request.get("status") == "logged_in":
        log.info("Login complete signal received, resuming watch")
        return "signal"

    # Only respond to email MFA requests.
    if request.get("mfa_method") != "email":
        log.info("MFA method is '%s', not 'email' — skipping", request.get("mfa_method"))
        return "skipped"

    sender_filter = request.get("sender_filter", "@united.com")
    log.info("MFA request detected (sender filter '%s'), fetching code from Gmail...", sender_filter)
    code = get_email_code(gmail_account, gmail_password, sender_filter)

    if code:
        response_path = request.get("response_file", _MFA_RESPONSE)
        os.makedirs(os.path.dirname(response_path), exist_ok=True)
        with open(response_path, "w") as f:
            f.write(code)
        log.info("Wrote MFA code to %s", response_path)
        return "answered"

    log.error("Failed to retrieve MFA code from email")
    return "no_code"


def main():
    # Load .env from project root or ~/.searchaero/.env
    for env_path in [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        os.path.join(_MFA_DIR, ".env"),
    ]:
        if os.path.isfile(env_path):
            load_dotenv(env_path)
            break

    gmail_account = os.getenv("SEARCHAERO_GMAIL_SENDER")
    gmail_password = os.getenv("SEARCHAERO_GMAIL_APP_PASSWORD")

    if not gmail_account or not gmail_password:
        log.error("Set SEARCHAERO_GMAIL_SENDER and SEARCHAERO_GMAIL_APP_PASSWORD env vars")
        sys.exit(1)

    log.info("Watching %s for MFA requests...", _MFA_REQUEST)

    while True:
        _process_pending_request(gmail_account, gmail_password)
        time.sleep(WATCH_POLL_SECS)


if __name__ == "__main__":
    main()
