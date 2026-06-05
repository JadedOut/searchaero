# Frequently Asked Questions

### How does searchaero integrate with Claude Code?

Searchaero uses a `/flights` agent skill — a prompt file that teaches Claude the CLI workflow. When you ask about flights, Claude automatically runs the right `searchaero` commands, handles MFA verification, and presents results.

The skill is **program-aware**: it detects the award program from your words. Say "Aeroplan", "Air Canada", or "AC" and it scrapes Air Canada Aeroplan (HEADED, single-route, email-2FA); say nothing about a program and it defaults to United MileagePlus (SMS MFA). See `docs/findings/aeroplan/phase-4-flights-skill.md`.

For automatic MFA code retrieval, make sure Claude Code has access to Gmail tools. United verification emails come from `united@united.com` (or you type the SMS code manually); Aeroplan codes come from Air Canada / Aeroplan email and are resolved the same way (with an ask-you fallback). Aeroplan has no SMS path.

## Why Playwright?

Searchaero uses **curl_cffi for all flight data requests**. However, United's login flow requires Playwright for **cookie farming**.

United's authentication sits behind Akamai bot detection and SMS/email-based MFA, which means we need a real browser session to log in and capture the resulting auth cookies. Those cookies expire, so Playwright needs to periodically re-authenticate to keep them fresh. Once `cookie_farm.py` has a valid session, every subsequent API call (searching routes, fetching availability) goes through plain HTTP via `curl`/`requests`.

In short:
- **Playwright** — used once to log in and harvest cookies
- **curl_cffi** — used for everything else (all flight queries, all data fetching), with browser-grade TLS fingerprints to avoid bot detection

Note: Playwright runs in headless mode by default for batch scrapes (`--headless`). A headed (visible) browser is only needed if you want to watch the login flow.

## Scraping

### Why did my scrape fail with "BROWSER CRASH detected"?

United's Akamai bot detection flagged your request. This is usually transient — **just retry the same command.** The second attempt almost always works. If it keeps failing, your IP may be temporarily blocked:

- Wait 10–15 minutes and try again
- Use a proxy: `searchaero search YYZ LAX --proxy socks5://user:pass@host:port`

### How often should I re-scrape?

Award pricing changes frequently. For routes you're actively monitoring:

- **Casual browsing:** Scrape once, data is good for a few days
- **Active booking:** Re-scrape every 24 hours (`searchaero query --refresh` does this automatically)
- **Price watching:** Set up a watch with `searchaero watch add` — it handles scraping and notifications, but your AI agent must be left on.

### How long does a full scrape take?

- **Single route:** ~2 minutes (12 API calls covering 337 days)
- **15 routes:** ~30 minutes with 1 worker


## MFA / Login

### Why am I being asked for an MFA code?

United requires two-factor authentication on login. By default, United sends a 6-digit code via **SMS** to the phone number on your MileagePlus account. You can also choose **email-based MFA**, which lets the agent handle verification automatically via Gmail.

### How does MFA work with the agent?

Two modes:

- **SMS (default):** The agent asks you to type the 6-digit code in the chat.
- **Email:** The agent runs `searchaero search --mfa-method email`. United sends the code to your email. The agent then searches Gmail (via Gmail tools) for the most recent email from `united@united.com` with "verification" in the subject, extracts the 6-digit code, and submits it. This is useful for automated/loop workflows where no one is watching the chat.

Email MFA requires that Claude Code has access to Gmail tools (`gmail_search_messages`, `gmail_read_message`).

### How long does the MFA code last?

You have about 5 minutes to enter the code. If it expires, just re-run the command — United will send a new code.

### Do I need to enter the code every time?

MFA is required once per `searchaero search` invocation. If you're scraping multiple routes in one batch, you'll only be prompted once.

## Database

### Where is my data stored?

SQLite database at `~/.searchaero/data.db`. Override with `--db-path` or the `SEARCHAERO_DB` environment variable.

### How do I reset the database?

Delete the file and re-run setup:

```bash
rm ~/.searchaero/data.db
searchaero setup
```

### Can I back up my data?

Yes — just copy `~/.searchaero/data.db`. It's a standard SQLite file. The database uses WAL mode, so copy it when no scrapes are running for a clean backup.

### My database seems corrupted. What do I do?

```bash
# Check database health
searchaero doctor

# If corrupted, delete and recreate
rm ~/.searchaero/data.db
searchaero setup
```

You'll lose cached data but can re-scrape it.

## Notifications

### How do push notifications work?

Searchaero sends notifications via Discord webhook. Set up a webhook in any Discord channel and configure it:

1. In Discord: Server Settings → Integrations → Webhooks → New Webhook
2. Copy the webhook URL
3. Configure: `searchaero watch setup --discord-webhook-url https://discord.com/api/webhooks/...`
4. Add watches and run the daemon: `searchaero watch run`

You can also set the webhook URL via the `SEARCHAERO_DISCORD_WEBHOOK_URL` environment variable.

### Are Discord webhook notifications private?

Yes -- notifications go only to the channel where the webhook is configured. Use a private channel or DM channel for sensitive alerts.

## Agent Integration

### Which AI agents work with searchaero?

Claude Code with the `/flights` skill. See the README for setup instructions.


### The agent is trying to run SQL or import modules directly

The skill instructions tell the agent not to do this, but it may occasionally happen. If this happens, remind the agent: "Use the searchaero CLI commands (or just ask naturally), not raw SQL or direct module imports."

## Proxy / IP Issues

### Why do I need a proxy?

You probably don't for light use (a few routes per day). But repeated scraping from the same IP can trigger United's Akamai bot detection, resulting in blocks. A proxy helps by rotating your IP.

### How do I use a proxy?

```bash
# Via CLI flag
searchaero search YYZ LAX --proxy socks5://user:pass@host:port

# Via environment variable
export PROXY_URL="socks5://user:pass@host:port"
```

---

## More documentation

- [Getting Started](getting-started.md) — step-by-step setup walkthrough
- [Command Reference](commands.md) — every CLI command, flag, and example
- [README](../README.md) — project overview
