---
name: flights
description: Search, scrape, and monitor United MileagePlus award flights via the searchaero CLI
---

# Flight Search Skill

You have access to the `searchaero` CLI for United MileagePlus award flight data.

## Preamble (run first)

```bash
# Detect searchaero invocation method
if command -v searchaero >/dev/null 2>&1 && searchaero search --help 2>&1 | grep -q "mfa-method"; then
  SEARCHAERO="searchaero"
elif [ -x ".venv/Scripts/python" ] && [ -f "cli.py" ]; then
  SEARCHAERO="PYTHONIOENCODING=utf-8 .venv/Scripts/python cli.py"
elif [ -x ".venv/bin/python" ] && [ -f "cli.py" ]; then
  SEARCHAERO="PYTHONIOENCODING=utf-8 .venv/bin/python cli.py"
else
  SEARCHAERO=""
fi
# Check database
[ -f ~/.searchaero/data.db ] && echo "DB: ok" || echo "DB: missing"
echo "SEARCHAERO: ${SEARCHAERO:-NONE}"
```

Use the `SEARCHAERO` value printed above as the command prefix for all searchaero commands in this skill. Replace `searchaero` with `$SEARCHAERO` in every command.

If `SEARCHAERO` is `NONE`: tell the user searchaero is not available. Suggest:
- If they want to install: `pip install searchaero` then `searchaero setup`
- If they're in the source directory: check that `.venv` exists and `cli.py` is present

Do not proceed with any scrape or query commands until a valid invocation method is detected.

If `DB` is `missing`: tell the user the database hasn't been initialized. Suggest running `$SEARCHAERO setup` first.

## Quick Reference

| Action | Command |
|--------|---------|
| Check cache | `$SEARCHAERO query ORIG DEST --json` |
| Show table | `$SEARCHAERO query ORIG DEST` |
| Show graph | `$SEARCHAERO query ORIG DEST --graph` |
| Show summary | `$SEARCHAERO query ORIG DEST --summary` |
| Find deals | `$SEARCHAERO deals --json` |
| DB status | `$SEARCHAERO status --json` |
| Scrape fresh | `$SEARCHAERO search ORIG DEST --mfa-file --mfa-method sms` |
| Add alert | `$SEARCHAERO alert add ORIG DEST --max-miles N` |
| Check alerts | `$SEARCHAERO alert check --json` |
| Add watch | `$SEARCHAERO watch add ORIG DEST --max-miles N` |
| Check watches | `$SEARCHAERO watch check --json` |

## Workflow

### Step 1: Check cache
Run:
```bash
$SEARCHAERO query ORIG DEST --json 2>&1
```
- If output contains flight data (JSON array): display results to the user. Done.
- If output contains `"error": "no_results"`: proceed to Step 2.

### Step 2: Scrape fresh data
Tell the user: "Starting a fresh scrape — this takes about 2 minutes."
Run in background:
```bash
$SEARCHAERO search ORIG DEST --mfa-file --mfa-method sms 2>&1
```
Then proceed to Step 3.

### Step 2b: Verify process started
Wait 5 seconds, then read the first 10 lines of the background command's output:
- If output contains `Traceback`, `ModuleNotFoundError`, or `error:`: the scrape failed at startup. Read the full error, report it to the user, and stop. Do NOT proceed to MFA polling.
- If output contains `Logging in` or `Starting cookie farm`: the browser launched successfully. Proceed to Step 3.
- If there is no output yet: wait 5 more seconds and check again. If still no output after 15 seconds total, check if the process exited (failed silently).

### Step 3: Handle MFA
Poll for MFA request every 10 seconds, up to 6 times (60 seconds):
```bash
cat ~/.searchaero/mfa_request 2>/dev/null || echo "NO_MFA"
```
- If output is `NO_MFA`: wait 10 seconds and poll again.
- If output is JSON: MFA is required. Use `AskUserQuestion` to prompt for the code:
  - header: "MFA Code"
  - question: "United sent a verification code. Enter the 6-digit code:"
  - options: [{"label": "Enter code", "description": "Type the 6-digit code in the text field below"}]
  - multiSelect: false
  - The user will type their code in the free-text input. Extract exactly 6 digits.
- Write the code:
  ```bash
  echo -n "DIGITS_HERE" > ~/.searchaero/mfa_response
  ```
- Then wait for the background search command to complete.
- If 6 polls pass with no MFA request: the scrape may have completed without MFA, or failed. Check if the background command finished.

### Step 4: Display results
After the scrape completes, run:
```bash
$SEARCHAERO query ORIG DEST
```
Display the output verbatim.

## Error Handling

- If `$SEARCHAERO search` exits with an error, read stderr.
  - If error mentions "circuit_break" or "Akamai": tell the user rate limiting was detected. Suggest waiting 10 minutes before retrying.
  - If error mentions "MFA" or "timeout": the verification code wasn't provided in time. Ask if the user wants to try again.
  - If error mentions "browser" or "crash": the browser crashed. One more retry is acceptable.
- **Escalation rule**: if a scrape fails twice for the same route in this conversation, STOP. Tell the user what failed and suggest running `$SEARCHAERO doctor` for diagnostics. Do not retry a third time.

## Presentation

- Default: `$SEARCHAERO query ORIG DEST` shows a Rich table
- Price trend: `$SEARCHAERO query ORIG DEST --graph` shows ASCII chart
- Deal summary: `$SEARCHAERO query ORIG DEST --summary` shows summary card
- Cross-route deals: `$SEARCHAERO deals` shows best deals across all routes
- Specific date: `$SEARCHAERO query ORIG DEST --date YYYY-MM-DD` shows detail for one date
- Date range: `$SEARCHAERO query ORIG DEST --from YYYY-MM-DD --to YYYY-MM-DD`
- Cabin filter: add `--cabin economy|business|first` to any query
- **Important:** CLI output (tables, graphs, summaries) is collapsed behind "ctrl+o to expand" in the UI. The user may not see it. After running any presentation command, reproduce the key output in your response text so the user sees it without expanding.

## Post-Scrape Actions

After displaying results, the user may ask for follow-up actions:

- **"email me the results" / "send to my email"**: Look for an available MCP tool that can **send** an email (not just draft). Check all email-related MCP servers for a tool whose description mentions sending via SMTP — this is typically a tool named something like `send_email` or `send_mail`. Prefer any local SMTP-capable email MCP over the Anthropic-hosted `claude.ai Gmail` integration, which can only create drafts. Format a clean HTML email with a summary table of cheapest options (route, date, miles, taxes) and include the price chart if requested. When including ASCII graphs in emails, paste the EXACT CLI output into a `<pre>` block — never manually truncate, rewrite, or shorten lines, as this breaks the character alignment. If no send-capable tool is available, fall back to the `claude.ai Gmail` MCP to create a draft, and tell the user: "No email MCP with send capability is connected — I've created a draft in Gmail instead. You can review and send it from there."
- **"save to file" / "export"**: Run `$SEARCHAERO query ORIG DEST --csv > filename.csv` or `$SEARCHAERO query ORIG DEST --json > filename.json`.
- **"set an alert"**: Use the alert commands in the Alerts section below.
- **"watch this route"**: Use the watch commands in the Watches section below.

Note: "email" in this context means *deliver results via email* — it is unrelated to MFA configuration.

## Alerts and Watches

### Alerts (check manually)
```
$SEARCHAERO alert add YYZ LAX --max-miles 50000 --cabin business
$SEARCHAERO alert check --json
$SEARCHAERO alert list
$SEARCHAERO alert remove ID
```

### Watches (push notifications via ntfy)
```
$SEARCHAERO watch add YYZ LAX --max-miles 50000 --every 12h
$SEARCHAERO watch check
$SEARCHAERO watch list
$SEARCHAERO watch remove ID
$SEARCHAERO watch setup --ntfy-topic MY_TOPIC
$SEARCHAERO watch run  # foreground daemon
```

## Rules

- Do NOT query the database directly via SQL or import core modules
- When query returns no results, AUTOMATICALLY start a scrape without asking for confirmation
- Default to `--mfa-method sms` for interactive sessions. Only use `--mfa-method email` for unattended/cron workflows (see Unattended Mode section).
- Display CLI output verbatim — do not reformat Rich tables or ASCII charts
- After any scrape completes, you MUST run `$SEARCHAERO query ORIG DEST` and display results to the user BEFORE taking any post-scrape action (email, alert, watch, export)
- For email delivery, ALWAYS prefer `mcp__email__send_email` (SMTP send) over `mcp__claude_ai_Gmail__create_draft` (draft only). Only use the Gmail draft MCP if `mcp__email__send_email` is not available.

## Unattended / Cron Mode

For automated scrapes without a user present (cron jobs, scheduled tasks), use email MFA with Gmail auto-retrieval:
```bash
$SEARCHAERO search ORIG DEST --mfa-file --mfa-method email 2>&1
```
When `--mfa-method email` is used:
- Poll `~/.searchaero/mfa_request` as usual
- Search Gmail for the most recent email from `united@united.com` with subject containing "verification"
- Extract the 6-digit code and write to `~/.searchaero/mfa_response`

This mode requires Gmail MCP to be connected. Only use it when explicitly setting up automation — never as the default for interactive sessions.

## After Completion

If any commands failed unexpectedly, you took a wrong approach and had to backtrack, or you discovered something about this user's setup that would save time in future sessions — save it to Claude Code memory.

Test: would knowing this have saved 5+ minutes in this session? If yes, save it. If no, skip it. Don't save obvious things or transient errors (network blips, rate limits, one-time typos).

Examples of good learnings:
- "searchaero binary at ~/.local/bin was stale — user deleted it, using .venv/Scripts/python cli.py instead"
- "Windows needs PYTHONIOENCODING=utf-8 for CLI commands with Unicode output"
- "The email MCP server requires env vars via claude mcp add -e, not CLI -e flags"

Examples of things NOT to save:
- Flight prices (change daily)
- Scrape duration (already known)
- Transient network errors
