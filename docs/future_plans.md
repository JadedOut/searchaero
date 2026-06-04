# Future Plans

## Phase 1: Autonomous MFA + Scheduled Scraping (Done)

Before expanding to new airlines, make the existing scraper fully autonomous — no human in the loop for MFA, no agent burning tokens for deterministic work. The scraping infrastructure exists; the missing piece is unattended re-authentication when United sessions expire.

### Design philosophy

The CLI does the mechanical work (scraping, storage, querying). An agent is only needed for tasks that require reasoning (interpreting results, answering natural-language questions). Autonomous scraping is deterministic — it should never require an LLM.

### ~~Step 1: MFA responder script (`scripts/mfa_responder.py`)~~ — Done (2026-05-05, fixes 2026-05-08)

A standalone companion script (~200 lines) that watches for `~/.searchaero/mfa_request` and answers it by reading the MFA code from Gmail via IMAP. Not imported by searchaero. Not part of the core. Communicates exclusively through the existing file protocol.

**Post-ship fixes (2026-05-08):** Two bugs blocked production use: (1) naive `\d{6}` regex matched CSS hex colors (#000000) in United's HTML emails — fixed with HTML stripping + contextual anchor matching near "verification code"; (2) `_select_mfa_method()` couldn't switch from SMS to email — United requires clicking "try a different way" first. Both fixed and E2E validated: YYZ-LAX (2x) + YYZ-WUH fully autonomous with email MFA.

**Flow:**

```
searchaero hits MFA
  → writes ~/.searchaero/mfa_request  { method: "email", need: "code" }
  → polls ~/.searchaero/mfa_response

mfa_responder.py (running alongside)
  → sees mfa_request
  → connects to Gmail via IMAP + app password
  → polls for United email (10s intervals, up to 15 retries = ~2.5 min)
  → age-checks email (reject anything older than 3 min)
  → extracts 6-digit code from email body
  → deletes email (prevents stale code on next run)
  → writes code to ~/.searchaero/mfa_response

searchaero picks up code → enters it → login completes → scraping continues
```

**Reference implementation: mintapi** (`github.com/mintapi/mintapi`). Their `get_email_code()` solves the same problem for Mint.com financial scraping — IMAP + app password, polling loop, email age guard, delete-after-read. 708 commits, production-proven for years. Key guards to replicate:

| Guard | What it prevents |
|-------|-----------------|
| Email age check (3 min) | Grabbing a code from a previous MFA attempt |
| Delete after read | Next cron run grabs yesterday's expired code |
| Newest-first + limit 3 | Regex matches a shipping confirmation instead of MFA email |
| Sender filter | Only process emails from United |

**Setup:** One-time: generate a Gmail app password (requires 2FA on Google account), add `GMAIL_APP_PASSWORD` to `.env`. That's it.

**Why a separate script, not built into searchaero:** Agent-agnostic means searchaero doesn't care who writes the `mfa_response` file. The responder script is one option. A Claude Code agent with Gmail MCP is another. A human typing the SMS code is another. The file protocol is the interface contract — everything above it is the caller's choice.

### ~~Step 2: Scheduled scraping + email alerts~~ — Done (2026-05-17)

**Prerequisites validated (2026-05-08):** E2E test confirmed the full pipeline works autonomously — `mfa_responder.py` + `cli.py search --mfa-file --mfa-method email --ephemeral` completed 3 scrapes (YYZ-LAX 2x, YYZ-WUH) without human intervention.

**Architecture decision (2026-05-17):** Single pipeline, all local. Scraping, watch evaluation, email composition, and delivery happen in one process triggered by Windows Task Scheduler. No separate digest layer, no Claude `/schedule`, no cloud dependency.

Key constraints that drove this:
- Scraping requires local Playwright + filesystem → must run on local machine
- Claude `/schedule` fires remote agents that can't access local browser/DB → eliminated
- DB persistence is free (scraper already writes to it) → kept for historical queries
- Email doesn't need a separate process — tack it onto the scrape output

```
┌───────────────────────────────────────────────────────────────┐
│  Windows Task Scheduler (every 12h)                            │
│    └── scheduled_scrape.bat                                    │
│          └── python scripts/scheduled_scrape.py                │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │ 1. SCRAPE (deterministic, ~18 min, $0)                   │  │
│  │    mfa_responder.py (background)                         │  │
│  │    searchaero search --file routes.txt                   │  │
│  │      --mfa-file --mfa-method email --ephemeral --json    │  │
│  │    → results + DB write (free side effect)               │  │
│  │                                                          │  │
│  │ 2. EVALUATE + NOTIFY (claude CLI, ~$0.01-0.05)            │  │
│  │    Load watch requirements from ~/.searchaero/watches.yaml│  │
│  │    Pass scrape results + watches to claude -p --model haiku│ │
│  │    → LLM evaluates conditions + composes alert            │  │
│  │    → send via Discord webhook (core.notify.send_discord)  │  │
│  │    → fallback: template notification if CLI unavailable    │  │
│  │                                                          │  │
│  │ 3. LOG (always)                                          │  │
│  │    JSONL to ~/.searchaero/logs/scheduled_scrape.jsonl     │  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

**Done (2026-05-10):** Wrapper script built and tested.
- `scripts/scheduled_scrape.py` — orchestrates the scrape pipeline (start responder → run search → eval watches → notify → kill responder). CLI: `--routes`, `--delay`, `--dry-run`, `--db-path`, `--env-file`, `--no-eval`, `--register-scheduler`. Writes JSONL logs to `~/.searchaero/logs/scheduled_scrape.jsonl`. Sends Discord notification on failure if configured.
- `core/eval_watches.py` — Claude API watch evaluation module. Loads `~/.searchaero/watches.yaml`, queries DB for fresh availability, calls Haiku to evaluate conditions, sends Discord webhook notification if matches found, falls back to template if API unavailable.
- `examples/watches.yaml` — sample watch config with natural-language conditions.
- `scripts/scheduled_scrape.bat` — Windows Task Scheduler launcher (documented with setup steps).
- `tests/test_scheduled_scrape.py` — 18 unit tests (all passing).
- `tests/test_eval_watches.py` — 12 unit tests (all passing).
- `--dry-run` validated: prints pipeline commands without executing.
- `--register-scheduler` prints the `schtasks /create` command for Windows Task Scheduler registration.

**Scheduling mechanism: Windows Task Scheduler** (decided 2026-05-17).

Options evaluated:
- ~~Claude `/schedule`~~ — remote triggers can't access local browser + filesystem. Killed.
- **Windows Task Scheduler** — zero dependencies, .bat launcher exists. Chosen.
- ~~APScheduler + NSSM~~ — upgrade path if Task Scheduler proves unreliable. Deferred.
- ~~OpenClaw cron~~ — adds LLM cost/nondeterminism for deterministic work. Killed.
- ~~Claude Desktop Scheduled Tasks~~ — requires Desktop app open, costs tokens. Killed.

**LLM in the pipeline (v1, superseded):** ~~The Claude API call (Step 2) evaluates user watch conditions against scrape results and composes the alert email. This is a single API call at the end of each scrape run — the LLM handles flexible natural-language watch requirements and writes contextual emails. Cost: ~$0.01-0.03/run (~$0.60/month at 2x/day). Template fallback if API is down.~~

**LLM in the pipeline (v2, current — 2026-05-25):** All eval paths use `claude` CLI — no Anthropic API key needed. Two paths, both using `claude -p --model haiku`:
1. **Inline path:** `scheduled_scrape.py` (without `--no-eval`) calls `core/eval_watches.py` → `subprocess.run(["claude", "-p", prompt, "--model", "haiku"])`. Loads watches, queries DB for fresh availability, passes both to Claude for evaluation, sends Discord notification on match.
2. **`.bat` two-step path:** (1) `scheduled_scrape.py --no-eval`, (2) `claude -p --verbose 0 < scripts/eval_prompt.txt`. The CLI session reads `watches.yaml`, queries DB via `searchaero query`, reasons about matches, sends Discord notifications.

Both paths use existing Claude Code auth. No `anthropic` pip dependency. Template fallback if `claude` CLI not found or times out. JSONL logs include `eval_method` field (`claude_cli`, `eval_watches`, `skipped`, `failed`).

New/changed files (v2):
- `scripts/eval_prompt.txt` — self-contained prompt for `claude -p` (reads watches, queries DB, evaluates, sends Discord notification)
- `scripts/scheduled_scrape.bat` — rewritten for two-step pipeline with fallback
- `scripts/scheduled_scrape.py` — added `eval_method` to JSONL logs, AC power warning to `--register-scheduler`
- `.claude/skills/flights/SKILL.md` — added Autonomous Mode section (watch rules, pipeline management, log inspection, config check)

**`searchaero schedule` command (2026-05-25, guardrails 2026-05-26, route consolidation 2026-05-27):** Replaced agentic `schtasks` generation with a first-class CLI command. **Route consolidation (2026-05-27):** `schedule add` now accumulates route groups into a single consolidated master schedule — one Task Scheduler task, one `.bat`, one browser session regardless of how many route groups exist. `schedule add --routes yyz_nrt.txt` creates the master; subsequent `schedule add --routes yyz_wuh.txt --months 6,7,12` appends groups. CLI validates total routes ≤ `MAX_ROUTES` (10) and interval ≥ `compute_min_interval()` (estimated scrape time + 45-min buffer, rounded to 15-min increments). `schedule list` shows per-group details with estimated scrape time and buffer margin. `schedule remove <group>` removes one group; `schedule remove master` tears down everything. `scheduled_scrape.py` loads route groups from registry via `--schedule-name` and runs one `searchaero search` per group sequentially. Timing constants in `core/scheduler.py`: `MAX_ROUTES=10`, `BUFFER_MINUTES=45`, `LOGIN_OVERHEAD_MINUTES=3`, `PER_ROUTE_MINUTES=2`. `enable`/`disable` manage per-schedule backoff state (auto-pause after 3 consecutive failures).

Task Scheduler settings (applied via XML patching):
- `StartWhenAvailable = true` — run once on PC wake after missed triggers (not N queued runs)
- `WakeToRun = true` — wake from sleep (AC power only)
- `DisallowStartIfOnBatteries = false` — don't kill mid-scrape on battery
- `AllowStartOnDemand = true`

Concurrency safeguard:
- **Lockfile** (`~/.searchaero/scrape.lock`) in `scheduled_scrape.py` — PID-based with stale lock detection. Prevents overlapping runs when triggers fire faster than scrapes complete.

**First production run (2026-05-25):** YYZ-WUH route at 15-minute intervals via Task Scheduler. 42 total runs: 16 consecutive successes (04:25–09:24 UTC), then 26 consecutive failures (09:38–15:53 UTC). PC woke from sleep and scraped autonomously — the pipeline works end-to-end. Failures are all login blocks: Akamai flagged the IP/fingerprint after ~5 hours of 15-min ephemeral browser sessions. The `--ephemeral` flag (fresh browser profile each run) likely made detection worse — each run looks like a new visitor.

**Scrape guardrails shipped (2026-05-26):** Three enhancements to address the production run failures:
- **60-min minimum interval enforced** — `MIN_INTERVAL_MINUTES = 60` in `core/scheduler.py`. `searchaero schedule add --interval 30` now rejects with clear error. Default changed from 15 to 60.
- **Backoff state machine** — `~/.searchaero/scrape_state.json` tracks consecutive failures per schedule. After 3 consecutive failures, schedule auto-disables with Discord alert. `searchaero schedule enable <name>` to resume. No more hammering a blocked IP.
- **Eval ON by default** — Generated `.bat` launchers now include watch evaluation unless `--no-eval`. Notification history logged to `~/.searchaero/logs/watch_notifications.jsonl`.
- **Notifications consolidated to Discord** — `core/notify.py` simplified to Discord webhooks only (removed ntfy + SMTP). Single config: `discord_webhook_url` in `~/.searchaero/config.json` or `SEARCHAERO_DISCORD_WEBHOOK_URL` env var.
- **Month/date filtering on scrape** — `scrape_route()` supports `--months 6,7,12` and `--from`/`--to` date range filters to reduce unnecessary API calls.

**What's left:** ← NEXT
- Stress test consolidated schedule with multiple route groups at 75-min intervals
- Test dropping `--ephemeral` to reuse browser cookies across runs (reduces "new visitor" signals to Akamai)
- Expand to `routes/canada_us_all.txt` using consolidated schedule (multiple groups, each with different month filters)

### ~~Step 3: `searchaero digest` command~~ — Collapsed into Step 2

Originally planned as a separate command that diffs DB snapshots. No longer needed — the scrape pipeline sends email alerts directly from scrape results + LLM evaluation. Historical diff features (price trends, disappeared availability) can be added to the watch evaluation prompt later — the DB already captures history via triggers.

### ~~Step 4: Scheduled digest delivery~~ — Collapsed into Step 2

Originally planned as a separate Claude `/schedule` trigger reading cached DB data. Eliminated: (1) `/schedule` can't access local machine, (2) a separate digest process adds complexity for no benefit when email can be sent at the end of the scrape run.

### Why this first

- Zero new scraping code. Uses existing United scraper + DB.
- Unblocks fully autonomous operation (the prerequisite for everything else).
- One pipeline, one trigger, one process — minimal moving parts.
- Users get value immediately — "what matched my watches overnight."

---

## Phase 2: Aeroplan Scraper (separate path)

Add Air Canada Aeroplan as a second, independent scraper. Keep it completely separate from the United code at first — don't try to abstract into a shared adapter pattern prematurely.

### Why separate

- Aeroplan uses different anti-bot (not Akamai — need to discover what they use)
- Requires login (no guest search like United)
- Different API endpoints, response format, award types
- Air Canada has sued seats.aero — need to understand the legal/technical landscape before committing to an approach
- Building it separately lets us see HOW different the two implementations really are before deciding on shared abstractions

### What to build

1. **`core/aeroplan/`** — standalone scraper module (cookie_farm, api client, response parser)
2. **`program` column in DB** — extend the availability table to tag results by program (united, aeroplan)
3. **`searchaero search --program aeroplan`** — CLI flag to choose which scraper to use
4. **`searchaero query --compare`** — show both programs' pricing side-by-side for the same route
5. **MFA per-program** — separate mfa_request/response files (`mfa_request_united`, `mfa_request_aeroplan`)

### Cross-program comparison (the killer feature)

```
YYZ → NRT  Jun 15  Business

Program          Miles    Taxes    Award Type
─────────────────────────────────────────────
United MP        75,000   $45     Everyday
Aeroplan         70,000   $89     Standard
Aeroplan         55,000   $89     Latitude (saver)
```

This is the insight no personal tool delivers well today: same seat, different price depending on which program you book through. Combined with knowledge of which credit card points transfer where, this tells users exactly where to transfer their points.

### Parallel scraping

Use the Task/TaskOutput pattern for multi-program searches:

```
/flights YYZ LAX (multi-program)
    ├── Task: united-scraper   (run_in_background: true)
    │       scrape United, handle MFA, return results
    └── Task: aeroplan-scraper (run_in_background: true)
            scrape Aeroplan, handle MFA, return results

orchestrator waits on TaskOutput for both, then merges + displays
```

Wall time stays ~2 min regardless of program count.

---

## Phase 3: MCP Decision Point

After Aeroplan works, evaluate whether to refactor into an MCP server. **This is a decision gate, not a foregone conclusion.** The answer depends on what we learn in Phase 2:

### Go MCP if:

- The two scrapers share very little code (different anti-bot, different cookie lifecycle, different session management) — meaning each airline is essentially its own service that benefits from process isolation
- We want to distribute this as a tool others can install (MCP is the standard interface for agent tools)
- We're adding 3+ airlines and the CLI tool surface area becomes unwieldy
- We need process isolation (AA browser crash shouldn't kill United scraper)

### Stay CLI if:

- Aeroplan and United scrapers share significant infrastructure (same cookie_farm pattern, same hybrid_scraper pattern, just different API endpoints and parsers) — meaning an adapter pattern inside the CLI is sufficient
- This stays a personal tool (no distribution need)
- Two programs is enough for our use case

### What the adapter pattern looks like (if scrapers ARE similar)

```python
class AirlineAdapter:
    name: str
    auth_required: bool
    booking_window_days: int

    async def start(self) -> None
    async def login(self, creds, mfa) -> None
    async def fetch(self, origin, dest, date) -> list[RawResult]
    async def stop(self) -> None
```

### What MCP looks like (if scrapers are very different)

```
MCP Server: "miles-search"
│
├── tool: search(program, origin, dest, date, cabin)
├── tool: query(origin, dest, cabin, date_range)
├── tool: digest(routes, since)
├── tool: deals(cabin, max_miles)
└── tool: watch(origin, dest, cabin, threshold)
│
└── internally routes to per-airline scraper services
```

### Programs to evaluate (priority order after Aeroplan)

1. United MileagePlus (done)
2. Air Canada Aeroplan (Phase 2)
3. American AAdvantage — only Citi/Bilt transfer partner, dynamic pricing
4. Singapore KrisFlyer — Chase/Amex/Citi/Cap1 all transfer here, fixed chart
5. British Airways Avios — distance-based pricing, unique sweet spots

---

## Context: Why This Matters (Transfer Points Arbitrage)

The same physical flight can cost wildly different miles depending on which loyalty program you book through. Credit card points (Chase, Amex, Bilt, etc.) transfer 1:1 to multiple airline programs. The value proposition:

```
Chase Ultimate Rewards ──┬──▶ United (75K)
                         ├──▶ Aeroplan (55K)    ◄── 27% cheaper, same seat
                         └──▶ KrisFlyer (63K)

"Transfer to Aeroplan" saves 20K miles = ~$400 in value
```

No tool does this comparison well for personal use today. seats.aero shows per-program availability but doesn't help you decide where to transfer your specific points. That's the gap.

---

## Eng Review Decisions (2026-05-05)

Reviewed by /plan-eng-review. 14 issues raised, all resolved.

### Revised Phase Ordering

```
Phase 1a: Scheduled scraping + alerts       (DONE — Task Scheduler + Claude CLI eval + guardrails)
Phase 1b: ~~Digest command~~ collapsed      (alerts sent inline with scrape, no separate digest)
Phase 2a: Aeroplan recon                     (~1 weekend, parallel with Phase 1 completion)
Phase 2b: United relocation + Aeroplan       (~2 weekends, requires 2a success)
          + credential namespacing
Phase 2c: Schema migration                  (~1 weekend, bundled with or after 2b)
          + schema_version framework
          + program on ALL 4 tables
          + trigger/index recreation
Phase 2d: CLI integration                   (~0.5 weekend)
Phase 3a: Adapter extraction                (~1 weekend)
Phase 3b: Third airline                     (~2-3 weekends)
```

### Architecture Decisions

1. **Schema migration safety:** Atomic transaction: BEGIN, CREATE new table, copy data, DROP old, RENAME new, recreate 3 indexes + 2 triggers, COMMIT. Add `schema_version` table + migration runner before first migration.
2. **Program column scope:** Add `program` to `availability`, `availability_history`, `alerts`, AND `watches`. Update both INSERT/UPDATE triggers to copy program field.
3. **Disappeared detection:** Compare `availability.scraped_at` within a route after a scrape. Rows with older `scraped_at` than the current batch weren't in the results = disappeared. Guard with `scrape_jobs` to distinguish "not scraped" from "scraped and gone."
4. **Phase 1.5 killed:** United code relocation deferred to Phase 2b (bundled with Aeroplan scraper). No wasted refactoring if recon fails.
5. **Digest output:** ~~Rich table + JSON only. Natural-language summary mode dropped.~~ Digest collapsed into scrape pipeline — LLM composes email directly from scrape results.
6. **Performance:** Add `idx_history_scraped_at` index on `availability_history(scraped_at)`. Batch freshness check via single GROUP BY query.
7. **Credential namespacing:** Added to Phase 2b scope. `cmd_setup()` and `CookieFarm._load_credentials()` must support per-program credential keys.
8. **Scheduling mechanism (2026-05-17, updated 2026-05-27):** Windows Task Scheduler with consolidated master schedule. `schedule add` accumulates route groups into a single schtasks task. CLI validates route count (≤10) and interval (≥ estimated scrape time + 45-min buffer). `scheduled_scrape.py` loads route groups from registry and iterates sequentially. Lockfile prevents concurrent runs. Claude `/schedule` eliminated (can't access local machine).
9. **LLM role in autonomous mode (2026-05-17, updated 2026-05-25):** ~~v1: Claude API (Haiku) evaluates watch conditions + composes email. Single call at end of scrape pipeline.~~ v2: All eval paths use `claude` CLI (`claude -p --model haiku`). Both `eval_watches.py` (inline) and `.bat` two-step pipeline use subprocess calls to `claude` — no `ANTHROPIC_API_KEY`, no `anthropic` pip dependency. Uses existing Claude Code auth. Template fallback if CLI unavailable. Flexible NL conditions stored in `~/.searchaero/watches.yaml`.

### Test Requirements

38 test cases specified across 4 test files. See eng review test plan artifact for details. Phase 2c migration tests are CRITICAL (one-way door).
