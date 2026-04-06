# Plan: Burn-In Test — Continuous Scraping for 10 Min → 1 Hour → 48+ Hours

## Task Description
Phase 1 Step 3: Run the scraper continuously to observe real-world behavior. Start with a **10-minute supervised run** (Claude Code monitors output and intervenes on errors), then a 1-hour test across multiple Canada routes, then extend to 48+ hours. The goal is to surface session expiry timing, error patterns, rate limit behavior, and response consistency — constraints that the scaling model depends on.

## Objective
Produce a structured burn-in log (JSONL) from progressively longer scraping runs (10 min → 1 hour → 48 hours). Analyze the logs to determine: how long sessions last before expiring, what errors occur and at what frequency, whether United rate-limits at this volume, and whether response data is consistent across routes and time. These findings gate Step 4 (scaling to all Canada routes).

## Problem Statement
The scraper works for a single route (Step 2 validated this). But we don't know:
- How long a login session lasts before expiring
- Whether Akamai cookie burns increase over time
- Whether United rate-limits after sustained scraping (even at low volume)
- Whether response data quality degrades over time (e.g., fewer solutions, empty responses)
- What happens when the browser sits idle between cycles

These are empirical questions that only a sustained run can answer.

## Solution Approach
Build a continuous runner script that loops through a route list, scrapes all 12 windows per route, logs every request/response as structured JSONL, then sleeps and repeats. The runner handles session expiry by detecting redirect-to-login and re-triggering the login flow. After the run, an analysis script reads the logs and produces a burn-in report with key metrics.

**The user runs this themselves — no Claude Code needed.** The Playwright browser runs headed so the user can manually log in if MFA triggers. For the MFA code, the user can simply `echo 123456 > scripts/experiments/.mfa_code` after checking their email.

## Relevant Files
Use these files to complete the task:

- `scrape.py` — Current single-route CLI. The continuous runner builds on top of this, reusing `scrape_route()`.
- `scripts/experiments/cookie_farm.py` — Playwright cookie farm. Needs session-expiry detection added.
- `scripts/experiments/hybrid_scraper.py` — curl_cffi + cookie farm hybrid. Already has cookie burn detection/recovery.
- `scripts/experiments/united_api.py` — API request building and response parsing.
- `scripts/experiments/gmail_mfa.py` — MFA file handoff. User writes code manually for burn-in.
- `core/db.py` — Database operations. Unchanged.
- `core/models.py` — Validation. Unchanged.
- `scripts/verify_data.py` — Existing data verification. Used after burn-in to spot-check.

### New Files
- `scripts/burn_in.py` — Continuous runner script with JSONL logging and session recovery.
- `scripts/analyze_burn_in.py` — Log analysis script that produces the burn-in report.
- `routes/canada_test.txt` — Route list for the burn-in test (10-20 routes).

## Implementation Phases

### Phase 1: Foundation
- Create route list file with 10-20 Canada routes
- Add session-expiry detection to the cookie farm (detect redirect to login page during cookie refresh)

### Phase 2: Core Implementation
- Build continuous runner script (`burn_in.py`) that:
  - Reads route list from file
  - Loops through routes, calling `scrape_route()` for each
  - Logs every API call as a JSONL record (timestamp, route, window, status, elapsed_ms, success, error, cookie_refreshed, solutions_count)
  - Logs cycle-level summaries (total routes, success rate, duration)
  - Handles session expiry: detects it, pauses, re-triggers login flow, resumes
  - Handles Ctrl+C gracefully: prints summary, saves final stats
  - Supports `--duration` flag (e.g., `--duration 60` for 60 minutes, `--duration 2880` for 48 hours)
  - Sleeps between cycles (configurable `--cycle-delay`, default 60s)

### Phase 3: Integration & Polish
- Build analysis script that reads JSONL logs and outputs:
  - Overall success rate
  - Session expiry events (timestamps, duration between them)
  - Error breakdown by type
  - Response time distribution (min, avg, p50, p95, max)
  - Cookie burn frequency
  - Per-route success rates (to detect route-specific issues)
  - Hourly success rate trend (to detect degradation over time)
- Test the full flow: start burn-in, let it run through at least 1 cycle, verify logs, run analysis

## Team Orchestration

- You operate as the team lead and orchestrate the team to execute the plan.
- You're responsible for deploying the right team members with the right context to execute the plan.
- IMPORTANT: You NEVER operate directly on the codebase. You use `Task` and `Task*` tools to deploy team members to do the building, validating, testing, deploying, and other tasks.
  - This is critical. Your job is to act as a high level director of the team, not a builder.
  - Your role is to validate all work is going well and make sure the team is on track to complete the plan.
  - You'll orchestrate this by using the Task* Tools to manage coordination between the team members.
  - Communication is paramount. You'll use the Task* Tools to communicate with the team members and ensure they're on track to complete the plan.
- Take note of the session id of each team member. This is how you'll reference them.

### Team Members

- Builder
  - Name: runner-builder
  - Role: Build the continuous runner script, route list, and session-expiry detection
  - Agent Type: backend-architect
  - Resume: true

- Builder
  - Name: analysis-builder
  - Role: Build the log analysis script
  - Agent Type: backend-architect
  - Resume: true

- Builder
  - Name: validator
  - Role: Validate all scripts work end-to-end, run tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create Canada Test Route List
- **Task ID**: create-route-list
- **Depends On**: none
- **Assigned To**: runner-builder
- **Agent Type**: backend-architect
- **Parallel**: true
- Create `routes/canada_test.txt` with 15 Canada routes (mix of origins: YYZ, YVR, YUL, YYC, YOW)
- Use popular US destinations: LAX, SFO, ORD, JFK, EWR, DEN, SEA, IAH, MIA, BOS
- Format: one `ORIGIN DEST` per line, comments with `#`
- Include a header comment explaining the file format

### 2. Add Session Expiry Detection to Cookie Farm
- **Task ID**: session-expiry-detection
- **Depends On**: none
- **Assigned To**: runner-builder
- **Agent Type**: backend-architect
- **Parallel**: true (can run alongside task 1)
- Add a `check_session()` method to `CookieFarm` that navigates to united.com and checks if still logged in
- Modify `refresh_cookies()` to detect if the page redirected to a login state after refresh
- Return a boolean or raise a specific exception so the runner can handle re-login
- Keep changes minimal — don't restructure the class

### 3. Build Continuous Runner Script
- **Task ID**: build-runner
- **Depends On**: create-route-list, session-expiry-detection
- **Assigned To**: runner-builder
- **Agent Type**: backend-architect
- **Parallel**: false
- Create `scripts/burn_in.py` with the following behavior:
  - CLI args: `--routes-file` (required), `--duration` (minutes, default 60), `--delay` (seconds between API calls, default 7), `--cycle-delay` (seconds between full cycles, default 60), `--refresh-interval` (cookie refresh every N calls, default 2), `--headless` (flag), `--log-dir` (default `logs/`), `--create-schema` (flag)
  - On start: print banner with config, connect to DB, start cookie farm, start hybrid scraper
  - Main loop: iterate through routes, call `scrape_route()` for each, check elapsed time against `--duration`
  - JSONL logging: after each route completes, write a JSON line to `logs/burn_in_YYYYMMDD_HHMMSS.jsonl` with: `{"timestamp", "route", "cycle", "windows_ok", "windows_failed", "solutions_found", "solutions_stored", "solutions_rejected", "duration_seconds", "errors": [...]}`
  - Session recovery: after each route, call `check_session()`. If expired, pause scraping, call `ensure_logged_in()` (user logs in manually since running headed), resume.
  - Ctrl+C handling: catch KeyboardInterrupt, print summary of the run so far, exit cleanly
  - End of run: print final summary (total cycles, total routes scraped, overall success rate, total records stored, total errors, run duration)
- Import and reuse `scrape_route()` from `scrape.py` (move it to a shared location if needed, or import directly)
- The script must be runnable standalone: `python scripts/burn_in.py --routes-file routes/canada_test.txt --duration 60`

### 4. Build Log Analysis Script
- **Task ID**: build-analysis
- **Depends On**: build-runner (needs to know the JSONL schema)
- **Assigned To**: analysis-builder
- **Agent Type**: backend-architect
- **Parallel**: false
- Create `scripts/analyze_burn_in.py` that reads a JSONL log file and prints:
  - **Run overview**: start time, end time, total duration, total cycles, total routes scraped
  - **Success metrics**: overall window success rate, overall route success rate (a route "succeeds" if at least 8/12 windows succeed)
  - **Session events**: list all session-expiry events with timestamps and time since last expiry (this is the key measurement)
  - **Error breakdown**: count by error type (cookie burn, HTTP 403, HTTP 429, timeout, etc.)
  - **Response time stats**: min, avg, median, p95, max across all successful calls
  - **Per-route breakdown**: success rate per route (to identify problematic routes)
  - **Hourly trend**: success rate bucketed by hour (to detect degradation over time)
- CLI: `python scripts/analyze_burn_in.py logs/burn_in_YYYYMMDD_HHMMSS.jsonl`
- Keep it simple — use only stdlib (json, statistics, collections, datetime). No pandas.

### 5. Validate All Scripts
- **Task ID**: validate-all
- **Depends On**: build-runner, build-analysis
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `routes/canada_test.txt` has correct format and valid IATA codes
- Verify `scripts/burn_in.py` parses CLI args correctly (dry-run with `--help`)
- Verify `scripts/analyze_burn_in.py` can parse a sample JSONL file
- Run existing test suite to ensure nothing broke: `python -m pytest tests/ -v`
- Verify imports work: `python -c "from scripts.burn_in import ..."` or equivalent

## Acceptance Criteria
- `routes/canada_test.txt` exists with 15+ valid Canada routes
- `scripts/burn_in.py` runs standalone, accepts `--routes-file` and `--duration`, produces JSONL logs
- `scripts/burn_in.py` detects session expiry and prompts for re-login (headed mode)
- `scripts/burn_in.py` handles Ctrl+C gracefully with a summary
- `scripts/analyze_burn_in.py` reads JSONL and prints a structured report
- Existing tests still pass
- All scripts use the project venv (`scripts/experiments/.venv`)

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# Existing tests still pass
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v

# Route list exists and has content
cat routes/canada_test.txt

# Burn-in script help works
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py --help

# Analysis script help works
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe scripts/analyze_burn_in.py --help
```

## Notes

### Phase A: 10-minute supervised run (Claude Code monitors)
Claude Code runs the burn-in script directly and watches the output in real time. If Playwright crashes, session expires, or errors spike, Claude Code can intervene (restart the farm, adjust parameters, provide MFA codes via Gmail MCP). This catches infrastructure issues before committing to a longer run.

```bash
# 1. Make sure Docker is running
docker compose up -d

# 2. Claude Code runs this and monitors output
cd C:/Users/jiami/local_workspace/seataero
scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py \
  --routes-file routes/canada_test.txt \
  --duration 10 \
  --create-schema

# 3. Analyze the 10-minute results
scripts/experiments/.venv/Scripts/python.exe scripts/analyze_burn_in.py logs/burn_in_*.jsonl
```

**What Claude Code watches for:**
- Playwright browser launch succeeding
- Login flow completing (auto-login or manual + MFA handoff)
- First cycle completing without crashes
- Cookie refresh working (no burns spiraling)
- JSONL log being written correctly

**Go/no-go for 1-hour test:** If the 10-minute run completes at least 1 full cycle with >50% window success rate and no unrecoverable crashes, proceed to the 1-hour test.

**Phase A Results (2026-04-02):** PASSED. 4 routes scraped (YYZ-LAX/SFO/ORD/JFK), 48/48 windows (100% success rate), 5,057 solutions stored, 0 errors, 0 cookie burns, 11.7 min runtime. Data verified against united.com — prices match (calendar API returns cheapest across all flights including connections, which can be lower than nonstop-only prices shown by Seats.aero). Bugs found and fixed during run: (1) taxes parser only looked for USD but Canadian site returns CAD; (2) login detection was too strict, improved with multiple positive signals and bearer token fallback; (3) auto-login retry logic added for United's intermittent "Something went wrong" error on email submission.

### Phase B: 1-hour test (user monitors, Claude Code available)
```bash
scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py \
  --routes-file routes/canada_test.txt \
  --duration 60 \
  --create-schema

# After it finishes, analyze:
scripts/experiments/.venv/Scripts/python.exe scripts/analyze_burn_in.py logs/burn_in_*.jsonl
```

### Phase C: 48-hour test (after 1-hour test looks good)
```bash
scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py \
  --routes-file routes/canada_test.txt \
  --duration 2880 \
  --create-schema
```

### What to watch for during the burn-in
- **Session expiry timing**: How many hours between forced re-logins? This determines if we need automated MFA for Step 4.
- **Cookie burn frequency**: If burns happen every N calls, we may need to increase refresh interval or add longer cooldowns.
- **Rate limiting**: Any HTTP 429s? If so, at what request rate?
- **Data consistency**: Does the same route return similar data when scraped hours apart? Saver availability shouldn't change every hour.
- **Error clustering**: Do errors cluster by time-of-day or by route? Might indicate maintenance windows or route-specific API quirks.

### IMAP upgrade (optional, for 48-hour unattended run)
If the 1-hour test shows session expires frequently (e.g., every 2-4 hours), we should upgrade `gmail_mfa.py` to use direct IMAP before the 48-hour run. This would make MFA fully automated with zero Claude Code dependency. This is a separate task — not part of this plan.
