# Getting Started with Searchaero

A step-by-step walkthrough from zero to your first award flight query.

## Prerequisites

- Python 3.13+
- A United MileagePlus account (free to create at united.com)
- A phone number or email linked to your MP account for verification (SMS is the default; email is also supported)

## Step 1: Install

```bash
uv tool install searchaero
```

Or install from source:

```bash
git clone https://github.com/JadedOut/searchaero.git
cd searchaero
uv tool install .
```

## Step 2: Set up credentials and verify

```bash
searchaero setup
```

This does three things:

1. **Creates the database** at `~/.searchaero/data.db`
2. **Installs Playwright browsers** (Chromium) if not already present
3. **Prompts for credentials** — if `~/.searchaero/.env` is missing or incomplete, it asks for your MileagePlus number and password interactively and creates the file for you

You should see three green checkmarks when done:

```
Database
  Path:    ~/.searchaero/data.db
  Status:  ✓ Created (schema initialized)

Playwright
  Package:  ✓ installed
  Browsers: ✓ installed

Credentials (~/.searchaero/.env)
  UNITED_MP_NUMBER:  ✓ set
  UNITED_PASSWORD:   ✓ set

Result: 3/3 checks passed
```

If anything shows red, follow the hint next to it. Use `--no-browser-install` if you manage browsers externally (CI/Docker).

> **Manual alternative:** If you prefer, create `~/.searchaero/.env` yourself with two lines:
> ```
> UNITED_MP_NUMBER=AB123456
> UNITED_PASSWORD=your_password_here
> ```
> Then run `searchaero setup` to verify.

## Step 3: Your first scrape

Let's scrape Toronto (YYZ) to Los Angeles (LAX):

```bash
searchaero search YYZ LAX
```

**What happens:**
1. A Chromium browser launches (headless by default)
2. It logs into united.com with your credentials
3. **United sends an SMS code to your phone** — enter it when prompted:
   ```
   [14:32:01] SMS verification code sent to your phone
   Enter SMS code: 123456
   ```
   > **Tip:** For automated workflows, use `--mfa-method email` — the agent reads the verification code from Gmail automatically, no manual input needed.
4. The scraper fetches award availability (~12 API calls covering 337 days)
5. Results are saved to your local database

You'll see:
```
YYZ-LAX: 342 found, 342 stored, 0 rejected, 0 errors
```

**MFA is required once per scrape invocation.** If you scrape multiple routes in one batch (e.g., `--file`), you'll only be prompted once.

## Step 4: Query your results

```bash
# See all availability
searchaero query YYZ LAX

# Filter to business class, sorted by price
searchaero query YYZ LAX --cabin business --sort miles

# Check a specific date
searchaero query YYZ LAX --date 2026-07-15

# Get JSON output (for scripts or agents)
searchaero query YYZ LAX --json
```

## Step 5: Use with Claude Code

In Claude Code, just ask about flights:

- *"What's the cheapest business class from Toronto to London in July?"*
- *"Find deals under 30K miles from any airport I've scraped"*
- *"Show me a price chart for YYZ to LAX"*
- *"Set up a watch for YYZ-LHR business under 70K miles"*
- *"Scrape fresh data for Vancouver to Tokyo"*

The `/flights` skill teaches Claude the full workflow: check cache, scrape if needed, handle MFA, present results.

You can also invoke the skill directly with `/flights`.

## Step 6: Set up price alerts (optional)

Get notified when prices drop below a threshold:

```bash
# Watch YYZ-LAX economy under 20,000 miles, check every 12 hours
searchaero watch add YYZ LAX --max-miles 20000 --cabin economy --every 12h

# Start the background daemon
searchaero watch run
```

For push notifications to your phone, set up ntfy (see the README's "Push notifications" section).

## Step 7: Set up email delivery (optional)

Have Claude email you flight summaries directly. This uses a local SMTP/IMAP MCP server.

**1. Create a Gmail App Password:**
- Go to [Google App Passwords](https://myaccount.google.com/apppasswords)
- Generate a new app password for "Mail"
- Copy the 16-character password (looks like `xxxx xxxx xxxx xxxx`)

**2. Add the email MCP server:**

```bash
claude mcp add email \
  -s user \
  -e SMTP_HOST=smtp.gmail.com \
  -e SMTP_PORT=465 \
  -e SMTP_SECURE=true \
  -e IMAP_HOST=imap.gmail.com \
  -e IMAP_PORT=993 \
  -e IMAP_SECURE=true \
  -e EMAIL_USER=you@gmail.com \
  -e "EMAIL_PASS=your app password here" \
  -- npx mcp-mail-server
```

Replace `you@gmail.com` and the app password with your own.

**3. Verify it connects:**

```bash
claude mcp list
```

You should see `email: ... ✓ Connected`. If it shows `✗ Failed`, check that:
- Your app password is correct (not your regular Gmail password)
- IMAP is enabled in Gmail settings (Settings → See all settings → Forwarding and POP/IMAP → Enable IMAP)

**4. Try it:**

Ask Claude: *"Scrape YYZ to LAX and email me the cheapest options"*

The email will be sent directly from your Gmail account.

## What to do next

- **Scrape more routes:** `searchaero search --file routes/canada_test.txt` (15 test routes)
- **Check data coverage:** `searchaero status`
- **Find deals across all routes:** Use your agent: *"Find the cheapest deals across all scraped routes"*
- **Run diagnostics:** `searchaero doctor` (checks database, credentials, Playwright, ntfy)
- **Browse help topics:** `searchaero help mfa`, `searchaero help proxy`, `searchaero help watches`

## Common gotchas

1. **SMS code expired?** Re-run the search — United sends a new code each time.
2. **Akamai blocked your IP?** Wait 10 minutes and retry.
3. **Data looks stale?** Data doesn't auto-refresh. Re-scrape with `searchaero search` or use `searchaero query --refresh`.
4. **Any route?** Yes — searchaero works with any origin/destination that United serves. Just `searchaero search ORIGIN DEST`.
5. **Don't run multiple scrapes at once.** Multiple simultaneous browser sessions from the same IP will trigger Akamai's rate limiting. One scrape at a time.

## More documentation

- [Command Reference](commands.md) — every CLI command, flag, and example
- [FAQ](faq.md) — common questions and troubleshooting
- [README](../README.md) — project overview
