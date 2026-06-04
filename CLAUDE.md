# Searchaero Project

## Python Environment
- Python venv path: `C:\Users\jiami\local_workspace\seataero-src\.venv`
- Python executable: `C:\Users\jiami\local_workspace\seataero-src\.venv\Scripts\python.exe`
- Always use this venv for running scripts and tests

## Running Tests
```bash
cd C:/Users/jiami/local_workspace/seataero-src
C:/Users/jiami/local_workspace/seataero-src/.venv/Scripts/python.exe -m pytest tests/ -v
```

## Agent Integration
For flight queries and scraping, use the `/flights` skill or call searchaero CLI commands directly. Do not use raw SQL or import core modules directly.

## Project Structure
- `cli.py` — Main CLI entry point (`searchaero` command)
- `core/` — Data models, database layer, scraper modules (cookie_farm, hybrid_scraper, united_api), shared logic (matching, routes)
- `core/aeroplan_session.py` — Reusable headed `AeroplanSession` manager (scripted Gigya login + 2FA, ~30–40 min TTL)
- `core/aeroplan_api.py` — Aeroplan transport/parse: availability URL build + `air-calendars` parse (cheapest-economy miles + taxes/day), card redaction
- `core/aeroplan_scraper.py` — Navigate-per-5-day Aeroplan `air-calendars` scraper: intercept → parse → store program-tagged rows (single-route, headed)
- `scrape.py` — Single-route scraper (called by CLI search)
- `scripts/burn_in.py` — Multi-route runner with JSONL logging (supports `--one-shot` for single-pass and `--burn-limit` for auto-exit on cookie burns)
- `scripts/orchestrate.py` — Parallel orchestrator: splits routes across N workers, monitors health via status files, kills burned-out workers
- `scripts/mfa_responder.py` — Autonomous MFA code resolver (Gmail IMAP). Watches `~/.searchaero/mfa_request`, writes codes to `~/.searchaero/mfa_response`
- `scripts/scheduled_scrape.py` — Wrapper script: starts mfa_responder → runs `searchaero search` → kills responder → logs to JSONL
- `scripts/scheduled_scrape.bat` — Reference .bat template (legacy; use `searchaero schedule add` instead)
- `core/scheduler.py` — Windows Task Scheduler management (schtasks, powercfg, .bat generation)
- `scripts/analyze_burn_in.py` — Burn-in log analysis and reporting
- `scripts/verify_data.py` — Data verification reporting
- `routes/canada_test.txt` — 15 Canada→US test routes
- `routes/canada_us_all.txt` — Full Canada→US route list for production runs

## Burn-In Testing
```bash
# Single worker, continuous mode (10 min example)
scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py \
  --routes-file routes/canada_test.txt --duration 10 --create-schema

# Single worker, one-shot mode (scrape all routes once, then exit)
scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py \
  --routes-file routes/canada_test.txt --one-shot --create-schema

# Orchestrated parallel run (3 workers, one-shot, auto-kill on 10 burns)
scripts/experiments/.venv/Scripts/python.exe scripts/orchestrate.py \
  --routes-file routes/canada_us_all.txt --workers 3 --headless --create-schema

# Analyze results
scripts/experiments/.venv/Scripts/python.exe scripts/analyze_burn_in.py logs/burn_in_*.jsonl
```

## Scheduled Scraping
```bash
# Register a scheduled scrape (creates Task Scheduler task + .bat + wake timers)
searchaero schedule add --routes routes/yyz_wuh.txt --interval 15 --months 6,7,12

# List schedules with live status
searchaero schedule list

# Health check: wake timers, task status, recent logs
searchaero schedule status

# Remove a schedule (deletes task + .bat + registry entry)
searchaero schedule remove yyz-wuh
```
- Generated .bat files and metadata stored in `~/.searchaero/schedules/`
- PC must sleep (not shut down) for wake-to-scrape
- See `searchaero help schedule` for full details

## Search / Query by Program
```bash
# Scrape Aeroplan (single-route only, headed AeroplanSession, MFA via email by default)
searchaero search --program aeroplan YYZ LAX

# Scrape United (default program)
searchaero search --program united YYZ LAX

# Query stored rows; omit --program to see all programs side by side
searchaero query --program aeroplan YYZ LAX
searchaero query YYZ LAX
```
- United and Aeroplan rows share one `availability` table, tagged by a `program` column.
- See `docs/findings/aeroplan/phase-2-scraper.md` for the Aeroplan scraper architecture
  and the account-safe live runbook (HEADED, single account/route, capped windows).

## Database
- SQLite at `~/.searchaero/data.db` (default)
- Override with `--db-path` flag or `SEARCHAERO_DB` env var
- Schema created via `searchaero setup` or `--create-schema` flag

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
