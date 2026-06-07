<!-- USAGE RULES
This document describes the project's high-level direction, scope, and technical strategy.

WHEN TO READ THIS FILE:
- When you need to understand the project's goals, scope, or strategic direction
- When making architectural decisions that depend on project vision
- When evaluating whether a proposed feature is in or out of scope
- When you need context on why certain technical choices were made

WHEN NOT TO READ THIS FILE:
- During routine implementation tasks where the direction is already clear
- When debugging or fixing bugs (use the code and logs instead)
- When writing tests or doing code reviews
- When the current task context already contains the needed information

This is a reference document, not a working document.
-->

# Project brief: United award flight search CLI

> **Note (April 2026, updated June 2026):** The interface is the CLI + `/flights` agent skill. **The MCP server (`mcp_server.py`) was REMOVED** in commit `917a514` ("replace MCP server with CLI + agent skill interface") — the 1247-line file is gone, its deps (`fastmcp`, `pydantic`) and the `searchaero-mcp` entry point dropped. Any reference below to an `mcp_server.py`, `@mcp.tool()` functions, or "MCP tools" is **stale historical text describing that deleted server** — it does not reflect the current codebase. Agents integrate by calling the CLI directly (`searchaero schema` for introspection, `--json` for structured output). (This is unrelated to the **Gmail MCP**, a separate live-session integration the `/flights` skill still uses for email-2FA.)

## What this project is

A free, open-source CLI tool for United MileagePlus award flight search. The CLI scrapes United's award search API, stores results in a local SQLite database, and lets you search availability from the command line. No hosted service, no web UI, no subscriptions.

## Design philosophy

**The CLI is a tool for AI agents to call, not a tool humans type into directly.**

The intended user experience is natural language: you ask a question ("what's the cheapest business class from Toronto to LA in July?"), and an AI agent — Claude Code, OpenClaw, or any other — translates that into the right `searchaero` CLI call, parses the structured output, and presents the answer. The CLI is the machine-readable API layer; the agent is the human-readable interface layer.

Core principles:

1. **Terminal-only.** No web UI. Everything happens in the terminal. The CLI can return structured data (`--json`, `--csv`) for agents to parse, or formatted tables/graphs for direct human reading. Future work may include prompt-engineering hints that help agents render rich terminal visualizations (sparklines, charts, color-coded tables).

2. **Agent/AI agnostic.** The CLI must not be coupled to any specific AI framework. Any agent (Claude Code, ChatGPT, Cursor, etc.) calls the CLI directly — `searchaero schema` provides runtime introspection, `--json` provides structured output. Works with shell scripts, cron, or a human typing commands. (A typed MCP server `mcp_server.py` once exposed these commands as MCP tools; it was removed in `917a514` in favor of the CLI + `/flights` skill — see the note at the top.)

3. **Scheduling via CLI.** `searchaero schedule add` manages a **single consolidated master schedule** via Windows Task Scheduler. Multiple route groups accumulate into one Task Scheduler task — `schedule add --routes yyz_nrt.txt` creates the master, subsequent `schedule add --routes yyz_wuh.txt --months 6,7,12` appends route groups to it. One `.bat`, one browser session, one schtasks task regardless of how many route groups exist. The CLI enforces `MAX_ROUTES = 10` and a minimum interval based on estimated scrape time + 45-min buffer (`compute_min_interval()`). The .bat payload calls `scripts/scheduled_scrape.py --schedule-name master` which loads route groups from the registry and runs one `searchaero search` per group sequentially (mfa_responder → search group 1 → search group 2 → ... → eval → notify). A lockfile (`~/.searchaero/scrape.lock`) prevents overlapping runs. `schedule list` shows per-group details with estimated scrape time; `schedule remove <group>` removes a single group; `schedule remove master` tears down everything. Metadata stored in `~/.searchaero/schedules.json`; generated files in `~/.searchaero/schedules/`.

4. **Two modes, two notification paths.** In **interactive mode** (user typing in chat), the calling agent handles notification delivery — it has access to Gmail MCP, Slack, whatever. In **autonomous mode** (scheduled pipeline, no agent in the loop), the pipeline sends Discord webhook notifications directly via `core.notify.send_discord`. Watch conditions are stored in `~/.searchaero/watches.yaml`; the LLM evaluates them and composes contextual alerts. Template fallback if the API is down.

5. **No agent instructions in config files.** Agent discoverability happens through `searchaero schema` (+ `--json`), not by embedding CLI manuals in agent-specific config files (.cursorrules, etc.). The tool describes itself; agents don't need a cheat sheet.

## Scope

- One airline program: United MileagePlus
- Geographic coverage: Any origin/destination United serves
- Date coverage: full 337 days (United's maximum award booking window)
- Refresh cadence: daily full sweep
- Runs locally — no server hosting required

## Technical approach

### Scraping United

As of 2026, United is rated 2/5 difficulty for scraping by Scraperly (https://scraperly.com/scrape/united-airlines). They use standard Cloudflare protection. Datacenter proxies are sufficient; residential proxies are not required.

**Key discovery:** United's award calendar view returns an entire month of lowest-price availability per API call. One request for YYZ-LAX returns ~30 days of pricing data (miles cost + taxes per day). This means covering 337 days for one route requires only ~12 requests (337 / 30), not 337 individual date searches.

**Login requirement:** As of late 2025, United requires MileagePlus login to view award pricing. This was explicitly done to block third-party search tools. The scraper needs to maintain authenticated sessions.

**Login:** Always via MileagePlus number (never email — Akamai blocks email login frequently but never blocks MP# login). `.env` requires only `UNITED_MP_NUMBER` and `UNITED_PASSWORD`.

**Session management:** Use Playwright with persistent browser contexts to save login state between runs. Sessions stay alive for hours; the hourly scrape cadence naturally keeps them warm. Re-authentication is only needed when sessions expire (roughly once per day).

**Anti-bot evasion:** United uses dual-layer bot protection: Cloudflare (TLS fingerprinting at the edge) and Akamai Bot Manager (JavaScript sensor cookies). curl_cffi with Chrome TLS impersonation handles Cloudflare, but Akamai requires a real browser to generate and maintain `_abck` cookies. The proven approach is a hybrid architecture: Playwright runs in the background as a "cookie farm" keeping Akamai cookies fresh, while curl_cffi makes the actual API calls using those cookies. See `docs/findings/united/curl-cffi-feasibility.md` and `docs/findings/united/hybrid-architecture.md`.

### Scrape volume math

**Verified**: The calendar endpoint (`/api/flight/FetchAwardCalendar`) returns 30 days of pricing per request, covering ALL cabin classes (economy, business, first, premium economy) and both saver/standard award types in a single response. See `docs/api-contract/united-calendar-api.md` for full API contract.

- ~2,000 routes x 12 monthly windows = ~24,000 requests for a full year sweep
- One full sweep per day: ~0.3 requests/second sustained
- Single worker completes in ~2 hours (with 5-10s delays between requests)
- No proxies needed, 1 MileagePlus account sufficient
- Can run on a laptop

### Data storage

SQLite with WAL mode. Zero setup — just a file at `~/.searchaero/data.db`. No Docker, no server, no connection strings.

At our actual write rates (a few upserts per second), SQLite in WAL mode handles this fine.

```sql
CREATE TABLE availability (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    date TEXT NOT NULL,
    cabin TEXT NOT NULL,
    award_type TEXT NOT NULL,
    miles INTEGER NOT NULL,
    taxes_cents INTEGER,
    scraped_at TEXT NOT NULL DEFAULT (datetime('now')),
    seats INTEGER,
    direct INTEGER,
    flights TEXT,
    UNIQUE(origin, destination, date, cabin, award_type)
);

CREATE INDEX idx_route_date_cabin ON availability(origin, destination, date, cabin);
CREATE INDEX idx_scraped ON availability(scraped_at);
CREATE INDEX idx_alert_match ON availability(origin, destination, cabin, miles);
```

**Upsert strategy:** `INSERT ... ON CONFLICT (origin, destination, date, cabin, award_type) DO UPDATE` to avoid duplicate rows.

**Price history:** An `availability_history` table automatically captures every price change via SQLite triggers. An AFTER INSERT trigger records first sightings; an AFTER UPDATE trigger (with `WHEN` clause checking miles/taxes_cents) records only actual price changes. No scraper modifications needed — triggers fire automatically on `upsert_availability`.

**Storage estimate:** ~50-100 MB for the full database at Canada scale.

### Alert system

Managed via CLI: `searchaero alert add YYZ LAX --cabin business --max-miles 70000`. Alerts are stored in the local database. After each scrape, matching is a simple query: "any new availability on this route, in this cabin, at or below this miles threshold, since last notification?"

**Deduplication:** The `alerts` table tracks `last_notified_at` and `last_notified_hash` (hash of the matching availability data). Only notify when the hash changes (new availability appeared, price dropped, or seats changed).

**Notification delivery:** The CLI exposes matches via `searchaero alert check --json`. Delivering those matches to the user (Telegram, email, terminal notification) is the responsibility of the calling agent or scheduler, not searchaero itself.

**Alert lifecycle:** Auto-expire alerts where all dates have passed.

### Watchlist system

The watchlist extends the alert system with automated scraping and push notifications. A watch = route + condition + schedule + notification:

```
searchaero watch add YYZ LAX --max-miles 20000 --cabin economy --every 12h
searchaero watch list                            # show active watches
searchaero watch remove 1                        # delete a watch
searchaero watch check                           # one-shot: scrape stale routes → evaluate → notify
searchaero watch run                             # foreground daemon: continuous check loop
searchaero watch setup --discord-webhook-url URL  # configure Discord webhook notifications
```

**How it works:** `watch check` (or `watch run` in a loop) finds due watches, groups by route, checks freshness via `get_route_freshness()`, scrapes stale routes via `burn_in.py --one-shot`, evaluates conditions using `check_alert_matches()`, deduplicates via content-hash (same as alerts), and sends Discord webhook notifications for new matches.

**Notification architecture:** searchaero returns structured match data; the calling agent routes it to the user's preferred channel. Two delivery paths:
1. **Agent-mediated** — Agent calls `check_watches`, gets match JSON, then delivers via whatever MCP tools it has (Gmail MCP for email, Slack MCP, etc.). Zero config inside searchaero. Works in any Claude Code session or scheduled agent.
2. **Direct Discord** — User configures a Discord webhook URL (`searchaero watch setup --discord-webhook-url URL`); searchaero POSTs rich embeds directly via stdlib `urllib.request`. Works headless (cron, daemon) with no agent in the loop. Config in `~/.searchaero/config.json` with env var override (`SEARCHAERO_DISCORD_WEBHOOK_URL`).

Discord is a user choice, not a fallback. Users who don't want Discord never configure it; their agent handles delivery instead. Both paths can coexist — Discord fires for headless runs, agent-mediated delivery fires for interactive sessions.

**Watch vs Alert:** Watches subsume alerts for active monitoring. Alerts remain for passive "check what we have" use cases. Users who want automated monitoring use `watch`; users who just want to check current data use `alert check`.

**`watches` table:** Same schema pattern as `alerts`, plus `check_interval_minutes` (default 720 = 12h), `last_checked_at`, and `last_notified_at`/`last_notified_hash` for dedup. Auto-expire watches where `date_to` is in the past.

**Watch CLI:** `watch add/list/remove/check` manage watches; `watch check` evaluates against cached data and sends Discord webhooks directly if configured. (Historical: the removed MCP server also exposed `add_watch`/`list_watches`/`remove_watch`/`check_watches` tools — those are gone with `mcp_server.py`.)

### Interface: CLI

The primary interface is a `searchaero` CLI that wraps the scraping pipeline and database queries into simple commands:

```
searchaero search YYZ LAX                          # scrape one route
searchaero search YYZ LAX --months 6,7,12           # only June, July, December windows
searchaero search YYZ LAX --from 2026-06-01 --to 2026-07-31  # date range filter
searchaero search --file routes/canada_us_all.txt   # scrape from route file
searchaero search --file routes.txt --workers 3     # parallel scrape
searchaero query YYZ LAX                            # query stored results (table)
searchaero query YYZ LAX --json                     # query stored results (JSON)
searchaero query YYZ LAX --date 2026-05-01          # detail for a specific date
searchaero query YYZ LAX --from 2026-05-01 --to 2026-06-01  # date range filter
searchaero query YYZ LAX --cabin business           # filter by cabin class
searchaero query YYZ LAX --sort miles --csv         # sort + CSV export
searchaero query YYZ LAX --history                  # route-level price history summary
searchaero query YYZ LAX --date 2026-05-01 --history # price timeline for a date
searchaero query YYZ LAX --json --fields date,miles  # select specific JSON fields
searchaero query YYZ LAX --json --meta               # JSON with _meta type hints
searchaero alert add YYZ LAX --max-miles 70000       # create a price alert
searchaero alert add YYZ LAX --max-miles 70000 --cabin business --from 2026-05-01 --to 2026-06-01
searchaero alert list                               # show active alerts
searchaero alert list --all                         # include expired alerts
searchaero alert remove 1                           # delete alert by ID
searchaero alert check                              # evaluate alerts against current data
searchaero watch add YYZ LAX --max-miles 20000 --every 12h  # watch a route
searchaero watch add YYZ LAX --max-miles 20000 --cabin economy --from 2026-05-01 --to 2026-06-01
searchaero watch list                               # show active watches
searchaero watch list --all                         # include expired watches
searchaero watch remove 1                           # delete watch by ID
searchaero watch check                              # one-shot: scrape stale → evaluate → notify
searchaero watch check --no-scrape --no-notify      # evaluate only, no side effects
searchaero watch run                                # foreground daemon (Ctrl+C to stop)
searchaero watch setup --discord-webhook-url URL    # configure Discord notifications
searchaero status                                   # DB stats, coverage, freshness
searchaero setup                                    # init DB schema, check credentials
searchaero schedule add --routes routes/yyz_nrt.txt              # create master schedule with 1 route group
searchaero schedule add --routes routes/yyz_wuh.txt --months 6,7,12  # append route group (2 groups now)
searchaero schedule add --routes routes/yyz_mia.txt --interval 90   # append + update interval
searchaero schedule list                            # show route groups with est. scrape time
searchaero schedule remove yyz-nrt                  # remove one route group, keep others
searchaero schedule remove master                   # tear down entire schedule
searchaero schedule enable master                   # re-enable after auto-pause
searchaero schedule disable master                  # manually pause schedule
searchaero schedule status                          # wake timers, timing, task health, failure counts
searchaero schema                                   # list all commands (JSON)
searchaero schema query                             # full parameter + output schema
```

Every command supports `--json` for machine-readable output. Terminal output uses Rich-formatted colored tables with sparklines when stdout is a TTY; piped output degrades to plain text or auto-switches to JSON.

### Project layout

```
searchaero/
  cli.py                         # main() + subcommand dispatch
  pyproject.toml                 # [project.scripts] searchaero = "cli:main"
  core/
    db.py                        # schema, queries, upsert (SQLite)
    models.py                    # AwardResult dataclass, validation
    cookie_farm.py               # Playwright browser management
    hybrid_scraper.py            # curl_cffi + cookie farm
    united_api.py                # request/response building
    matching.py                  # shared route-matching logic
    routes.py                    # route file parsing
    notify.py                    # Discord webhook notifications (stdlib urllib only, no ntfy/SMTP)
    output.py                    # Rich tables, sparklines, auto-TTY detection
    schema.py                    # command schema introspection for agents
    watchlist.py                 # watchlist runner (check, scrape, evaluate, notify)
  scripts/
    burn_in.py                   # multi-route runner (standalone, JSONL logging)
    orchestrate.py               # parallel worker orchestrator (used by CLI --workers)
  scrape.py                      # scrape_route() — imported in-process by CLI
  routes/                        # route list files
```

The CLI imports `scrape_route()` from `scrape.py` in-process for single-route and batch search. Parallel search (`--workers > 1`) delegates to `orchestrate.py` via subprocess (each worker needs its own browser instance). Query/status/alert operations use `core/db.py` directly.

### Agent integration: CLI + `/flights` skill

Agents drive searchaero by **calling the CLI** and parsing its output. `searchaero schema [command]` returns JSON describing every command (parameters, types, choices, defaults, output fields), and every command supports `--json`. The `/flights` agent skill (`.claude/skills/flights/SKILL.md`) is the reference orchestration: it detects the program from natural language, checks the cache (`query --json`), scrapes if stale (`search`), handles MFA, and presents results — the natural-language layer on top of the machine-readable CLI.

```
AI Agent  ──runs──▶  searchaero CLI  ──import──▶  core/db.py / scrape pipeline
   (NL in,                                            │
    tables out)        └── MFA via file handoff: ~/.searchaero/mfa_request / mfa_response
```

> **Historical (removed):** an MCP server (`mcp_server.py`, FastMCP, ~13 typed tools —
> `query_flights`, `get_flight_details`, `search_route`, `submit_mfa`, etc.) was the
> original agent interface. It was removed in `917a514` (replaced by this CLI + skill
> approach); `fastmcp`/`pydantic` deps and the `searchaero-mcp` entry point went with it.
> The summary/detail "list-get" token-efficiency ideas it pioneered now live in the CLI's
> `query` (summary) vs `query --json`/`get_flight_details`-style detail flags.

### Infrastructure and cost

Runs on your local machine. No VPS, no domain, no Docker, no hosted infrastructure.

| Component | Spec | Monthly cost |
|-----------|------|-------------|
| Local machine | Your laptop/desktop (needs ~2GB RAM for Playwright) | $0 |
| SQLite | Just a file — zero setup | $0 |
| **Total** | | **$0/month** |

## Operational notes

- **Data validation** is already implemented in `core/models.py`: IATA codes, date ranges, cabin types, miles bounds (1-500K), taxes. Invalid data rejected before DB.
- **Error handling** is already implemented in the scraper: HTTP 403/429/redirect detection, session recovery, circuit breaker, exponential backoff.
- **Recovery:** SQLite file at `~/.searchaero/data.db` — back up by copying. Scrape interruptions are safe (every route upserted immediately). `--skip-scanned` resumes where you left off.
- **Legal risk:** United ToS prohibits automated access. Low risk for personal use. Public repo contains framework only; scraper implementations are `.gitignored`.

## What's already built (scraper foundation)

The scraping pipeline is proven and production-ready:

- **API contract** — United's calendar endpoint reverse-engineered and documented (`docs/api-contract/`)
- **Hybrid scraper** — curl_cffi + Playwright cookie farm. Handles Cloudflare TLS fingerprinting and Akamai `_abck` cookies. SMS MFA with automated code entry (`scripts/experiments/`)
- **CLI entry point** — `cli.py` with argparse subparsers, `pyproject.toml` for `pip install -e .`. `searchaero setup` runs diagnostics (DB, Playwright, credentials). `searchaero search` runs the scraper in-process for single-route and batch modes (CookieFarm → HybridScraper → scrape_route via `_scrape_route_live()` helper), delegates to orchestrate.py for parallel (`--workers > 1`). `searchaero query` reads stored availability from SQLite and prints Rich-formatted summary/detail tables or JSON; supports `--from`/`--to` date range filtering, `--cabin` cabin class filtering (economy/business/first with group expansion), `--sort` (date/miles/cabin), `--csv` export, `--history` for price history with sparklines, `--fields` for JSON field selection, `--meta` for JSON type hints, `--refresh` for auto-scrape on stale/missing data, and `--ttl HOURS` (default 12) for configurable staleness threshold. `searchaero status` shows DB stats, record counts, route coverage, date range, freshness, and scrape job history. `searchaero alert` manages price alerts: `add` creates alerts with route/cabin/miles/date filters, `list` shows active (or all with `--all`), `remove` deletes by ID, `check` evaluates against current availability with content-hash deduplication and auto-expiry. `searchaero schedule` manages Windows Task Scheduler tasks: `add` (60-min minimum interval, generates `.bat` + schtasks), `list`, `remove`, `enable`/`disable` (backoff state), `status` (wake timers, failure counts). `searchaero watch` manages watched routes with Discord webhook notifications: `add` creates watches with route/cabin/miles/date/interval filters, `list` shows active (or all with `--all`), `remove` deletes by ID, `check` runs the full scrape→evaluate→notify pipeline, `run` starts a foreground daemon loop, `setup` configures Discord webhook URL. `searchaero schema [command]` returns JSON introspection for agent discovery. `--db-path`, `--json`, and `--meta` global flags work across all subcommands
- **Data path** — Database schema, upsert with ON CONFLICT, row-level validation, `query_availability` with date, date range (`date_from`/`date_to`), and cabin list filters, `get_scrape_stats` and `get_job_stats` for aggregate reporting, `query_history`, `get_history_stats`, and `get_price_trend` for price history and sparkline data, `get_route_freshness` for per-route TTL/staleness checks (`core/db.py`, `core/models.py`). `availability_history` table with INSERT/UPDATE triggers for automatic price change tracking. `alerts` table with CRUD functions, match evaluation, content-hash deduplication, and auto-expiry. `watches` table with CRUD, due-watch queries, interval scheduling, and notification tracking. 504 tests passing (unit + L1 data-path integration + L2 CLI integration + feature tests). SQLite with WAL mode, zero-setup
- **Terminal visualization** — Rich-powered colored tables, inline Unicode sparklines (`▁▂▃▄▅▆▇█`) for price trends in history views, auto-TTY detection (Rich when terminal, plain/JSON when piped). `core/output.py` with `sparkline()`, `should_use_json()`, `print_table()`, `print_error()`, `build_meta()`. All `_print_*` functions in `cli.py` use Rich. `--json` and `--csv` output unchanged (backward compatible)
- **Agent discoverability** — `searchaero schema [command]` returns JSON describing all commands, parameters (type, required, choices, defaults), output fields, and usage examples. `--meta` flag adds `_meta` block with field type hints to JSON output. `--fields` flag on `query --json` for field selection (reduces agent token consumption). Structured error JSON with `error`, `message`, `suggestion` keys. `core/schema.py` with `COMMAND_SCHEMAS` dict covering all 13 commands. (Agents consume this via the CLI directly — `searchaero schema` + `--json`. The MCP server that once also exposed these as typed tools was removed in `917a514`.)
- **Scheduling** — Consolidated master schedule architecture: `schedule add` accumulates route groups into a single Windows Task Scheduler task. One `.bat`, one browser session, one schtasks task regardless of route group count. `core/scheduler.py` provides timing constants (`MAX_ROUTES=10`, `BUFFER_MINUTES=45`, `LOGIN_OVERHEAD_MINUTES=3`, `PER_ROUTE_MINUTES=2`), estimation functions (`estimate_scrape_minutes()`, `compute_min_interval()`), and route group helpers (`add_route_group()`, `remove_route_group()`). CLI validates total routes ≤ 10 and interval ≥ estimated scrape time + 45-min buffer (rounded to 15-min increments). `schedule list` shows per-group details with estimated scrape time and buffer margin. `schedule remove <group>` removes a single group; `schedule remove master` tears down everything. `scheduled_scrape.py --schedule-name master` loads route groups from registry and runs one `searchaero search` per group sequentially. `schedule enable/disable` manages per-schedule backoff state (`~/.searchaero/scrape_state.json`): 3 consecutive failures auto-disable with Discord alert. Metadata in `~/.searchaero/schedules.json`, generated files in `~/.searchaero/schedules/`. `core/scheduler.py`. **Production validated (2026-05-25):** 16 consecutive successful autonomous scrapes (YYZ-WUH, PC waking from sleep). Akamai login block after ~5 hours at 15-min intervals; 60-min minimum now enforced
- **MFA file handoff** — `--mfa-file` flag on `search` and `query --refresh` switches from `input()` to filesystem polling for SMS codes. Scraper writes `~/.searchaero/mfa_request` (JSON with timestamp), polls `~/.searchaero/mfa_response` for the code (2s interval, 300s timeout), cleans up both files. Enables non-interactive MFA from any external process. `_prompt_sms_file()`, `_get_mfa_prompt()` in `cli.py`. 5 tests in `TestMFAFileHandoff`. Without `--mfa-file`, behavior unchanged (`input()`)
- **Burn-in validated** — 15 routes, 180/180 windows (100%), 16,386 solutions, 0 errors, 0 burns. Ephemeral browser profiles eliminate stale cookie poisoning. Single-route live test: YYZ-LAX 12/12 windows, 1,398 results, 0 errors (2026-04-09)
- **Parallel orchestrator** — `scripts/orchestrate.py` splits routes across N workers with status file monitoring, burn-based worker termination, and `--skip-scanned` resume

## Implementation plan

Build the `searchaero` CLI as the primary interface. Each step gates the next.

| Step | What | Why |
|------|------|-----|
| **1** | **~~Migrate `core/db.py` from PostgreSQL to SQLite.~~** Done. `core/db.py` uses sqlite3 (stdlib), WAL mode, `~/.searchaero/data.db`. All callers updated (`--db-path`). Tests rewritten for in-memory SQLite. 58/58 passing. | ~~Eliminates Docker dependency. SQLite is zero-setup.~~ |
| **2** | **~~CLI skeleton + `setup` command.~~** Done. `cli.py` with argparse subparsers and `pyproject.toml` (`searchaero = "cli:main"`). `searchaero setup` creates SQLite DB + schema, checks Playwright install, checks `.env` credentials, prints diagnostic report. Supports `--db-path` override and `--json` output. 8 CLI tests passing. | ~~The entry point must exist before any subcommand.~~ |
| **3** | **~~`search` command.~~** Done. `searchaero search YYZ LAX` (single), `searchaero search --file routes.txt` (batch), `searchaero search --file routes.txt --workers 3` (parallel). Single-route and batch modes call `scrape_route()` in-process (CookieFarm → HybridScraper → scrape_route → DB). Parallel mode delegates to `orchestrate.py` via subprocess (each worker needs its own browser). `--json` returns structured results (route/found/stored/rejected/errors). IATA validation, auto-uppercase, file existence checks. Crash detection with automatic browser restart and retry. 21 CLI tests passing. | ~~Core write path. Merges 3 scripts into one command.~~ |
| **4** | **~~`query` command.~~** Done. `searchaero query YYZ LAX` reads SQLite, prints summary table (one row per date, lowest saver miles per cabin group). `--date 2026-05-01` shows detail view (every record for that date). `--json` outputs raw JSON array. `query_availability(conn, origin, dest, date=None)` in `core/db.py`. Route validation, auto-uppercase, date format validation. 48 tests passing. | ~~Core read path. Users see results without running a web server.~~ |
| **5** | **~~`status` command.~~** Done. `searchaero status` prints formatted report: DB path/size, record count, route coverage, date range, latest scrape, scrape job stats (completed/failed/total). `--json` outputs structured JSON. Handles missing DB ("No database found") and empty DB ("No data yet") gracefully. `get_job_stats(conn)` in `core/db.py`. 57 tests passing. | ~~Users need to know what data they have.~~ |
| **6** | **~~Query filters + export.~~** Done. `--from`/`--to` date range, `--cabin` filter (economy/business/first with group expansion), `--csv` export, `--sort` (date/miles/cabin). Mutually exclusive validation (`--date` vs `--from`/`--to`, `--csv` vs `--json`). `query_availability` extended with `date_from`, `date_to`, `cabin` SQL filters. 76 tests passing. | ~~Narrow 337 days of data to a travel window.~~ |
| **7** | **~~Price history.~~** Done. `availability_history` table with INSERT/UPDATE SQLite triggers — automatic price change tracking with zero scraper modifications. `--history` flag on query: route-level summary (lowest/highest/current per cabin) without `--date`, chronological timeline with `--date`. Composes with `--cabin`, `--json`, `--csv`, `--sort`. `query_history` and `get_history_stats` db functions. 94 tests passing. | ~~Historical context for "is this a good price?"~~ |
| **8** | **~~Alerts.~~** Done. `searchaero alert add/list/remove/check` subcommands. `alerts` table in SQLite with route, cabin, max_miles, date range, and notification tracking. `alert check` evaluates all active alerts against current availability, deduplicates via SHA-256 content hashing (`last_notified_hash`), auto-expires past alerts. `--json` across all subcommands. 7 db functions, 6 CLI functions. 129 tests passing. | ~~Passive monitoring — get notified when saver fares appear.~~ |
| **9** | **~~E2E scraper→CLI tests.~~** Done. `tests/test_e2e.py` — 16 E2E tests with `FakeScraper` exercising `scrape_route()` → real SQLite → CLI read-path. Covers happy path, error handling, circuit breaker, crash detection, scrape→query/status/alert/history round-trips, date edge cases. 250 tests passing. | ~~Closes the write-path integration gap — no automated test exercised `scrape_route()` before.~~ |
| **10** | **~~Schedule, visualization, agent hints.~~** Done. `searchaero schedule add/list/remove/status` with Windows Task Scheduler management (schtasks XML patching, powercfg wake timers, .bat generation, schedules.json registry). Rich-powered colored tables with inline Unicode sparklines for price trends. `searchaero schema [command]` for runtime introspection, `--meta` for field type hints, `--fields` for JSON field selection, structured error JSON. `core/output.py`, `core/schema.py`, `core/scheduler.py`. 282 tests passing (49 new). | ~~CLI needs scheduling, visual output, and agent discoverability.~~ |
| **11** | **~~In-process scraper integration.~~** Done. Refactored CLI `search` to call `scrape_route()` in-process instead of shelling out via `subprocess.run()`. Single-route and batch modes now use CookieFarm/HybridScraper directly — gives CLI control over output formatting, error handling, and structured JSON. Parallel mode (`--workers > 1`) still delegates to `orchestrate.py` (needs independent browser instances per worker). Removed `_search_single`, `_search_batch`, `_run_script`, `SCRAPE_PY`, `BURN_IN_PY`. Added `verbose` parameter to `scrape_route()` for quiet mode. `tests/test_cli_full.py` with 39 comprehensive tests covering every CLI command. 336 tests passing. | ~~CLI needs direct scraper control for structured output and proper error handling.~~ |
| **12** | **~~Fix login detection.~~** Done. Rewrote `_is_logged_in()` with inverted detection: visible "Sign in" button as negative signal (fast exit for anonymous/fresh profiles), user-specific DOM content as positive signal. Fixed `_enter_mfa_code()` to navigate to homepage after MFA submission (United SPA doesn't redirect). Replaced fixed 3s wait with `wait_for_selector` for SPA auth state. Fully automated SMS MFA login verified: YYZ-LAX 12/12 windows, 1,398 results, 0 errors. 336 tests passing. | ~~False positive login detection caused immediate cookie burns on fresh profiles.~~ |
| **13** | **~~Install `searchaero` on PATH.~~** Done. Fixed `pyproject.toml` build backend (`setuptools.build_meta`), added explicit package discovery (`py-modules`, `packages`). `pip install -e .` installs `searchaero` entry point. | ~~Agents need to call `searchaero`, not `python cli.py`.~~ |
| **14** | **~~Shared parser + --json flag fix.~~** Done. Fixed `--json` flag position bug: refactored to `shared_parser` pattern so `--json`, `--meta`, `--db-path` work after any subcommand (not just before). Updated ~120 test invocations. 336 tests passing. | ~~`--json` only worked before the subcommand, breaking agent usage.~~ |
| **14b** | **~~MCP server.~~** Done. `mcp_server.py` using FastMCP (`mcp.server.fastmcp`) with 14 `@mcp.tool()` functions: `query_flights` (summary-only), `get_flight_details` (paginated rows), `get_price_trend` (time series), `find_deals` (cross-route deal discovery), `flight_status`, `add_alert`, `check_alerts`, `add_watch`, `list_watches`, `remove_watch`, `check_watches`, `search_route`, `submit_mfa`, `stop_session`. Summary/detail split pattern: `query_flights` returns ~150-300 tokens (no raw rows); `get_flight_details` provides paginated rows on demand (default 15, max 50). `get_price_trend` returns per-date cheapest miles for graphing with `from_date`/`to_date` date range filters. `find_deals` uses server-side SQL aggregation (`find_deals_query` in `core/db.py`) to find below-average pricing across all routes. Watch tools (`add_watch`, `list_watches`, `remove_watch`, `check_watches`) mirror CLI watchlist functionality with ntfy push notifications. FastMCP `instructions` provides tool selection decision flow. `ToolAnnotations` on all tools (`readOnlyHint`, `openWorldHint`). Read tools call `core/db.py` directly. Write tools use persistent in-process CookieFarm session (MFA once, browser reused across scrapes). Registered in `.mcp.json` for project-scoped auto-discovery. `searchaero-mcp` console script entry point. `mcp[cli]>=1.20` dependency. 48 MCP tool tests in `tests/test_mcp.py`. | ~~Agents discover searchaero through typed MCP tool schemas, not agent-specific config files.~~ |
| **15** | **~~DB as cache with TTL.~~** Done. `get_route_freshness()` in `core/db.py` checks per-route staleness (MAX scraped_at vs configurable TTL). `--refresh` flag on `query` auto-scrapes if data is stale or missing, then returns fresh results. `--ttl HOURS` (default 12) configures staleness threshold. `_scrape_route_live()` extracted as reusable helper for both `search` and `query --refresh`. `--json --meta` output includes `_freshness` block (`latest_scraped_at`, `age_hours`, `is_stale`, `ttl_hours`, `refreshed`). Backward compatible — plain `query` still returns cached data instantly. `build_freshness()` in `core/output.py`. Schema and CLAUDE.md updated for agent discovery. 358 tests passing (13 new). | ~~Prevents agents from confidently reporting fares that no longer exist.~~ |
| **15b** | **~~MFA-aware MCP server.~~** Done. Upgraded `mcp_server.py` so agents run scrapes conversationally via structured tool contract. `search_route` runs CookieFarm in-process with persistent session (`_session` dict: farm, scraper, logged_in). Cold start: background thread runs `_ensure_session()` + `scrape_route()`, polls `~/.searchaero/mfa_request`, returns `{"status": "mfa_required"}` if MFA detected. Warm session: `scrape_route()` runs directly (no MFA, no thread). `submit_mfa(code)` writes code to `~/.searchaero/mfa_response`, joins scrape thread, returns results. `stop_session()` shuts down browser. `atexit` handler auto-cleans on server shutdown. `_active_scrape` dict tracks one in-flight scrape (thread, route_key, result, error). `subprocess` import removed. 8 tests in `TestSearchRouteMFA` + 2 in `TestMCPMetadata`. | ~~Closes the natural-language scrape loop.~~ |
| **16** | **~~Live agent loop test (round 1).~~** Done (2026-04-10). First test revealed agent bypassed all MCP tools and used Bash with raw Python/SQL imports. Root causes: (1) `query_flights` returned flat JSON identical to Bash+SQL — no differentiation, (2) tool descriptions said what tools do, not when to use them, (3) each `search_route` spawned a fresh subprocess — no session reuse. Fix shipped same day: enriched `query_flights` with `_summary`/`_display_hint`/`_format_suggestions`, added FastMCP `instructions` with decision flow, added `ToolAnnotations`, replaced subprocess with persistent in-process CookieFarm, added `stop_session` tool. Re-test confirmed agent used `query_flights` → `search_route` → `submit_mfa` → `query_flights` correctly. Identified token waste: final `query_flights` returned ~10.4k tokens (91 rows) when agent only needed the ~150-token summary. Led to step 16b. | ~~Features that aren't tested from the agent's perspective will have invisible UX bugs.~~ |
| **16b** | **~~Token-efficient toolkit.~~** Done (2026-04-10). Refactored `query_flights` to summary-only (~150-300 tokens, no raw rows). Added 3 new MCP tools: `get_flight_details` (paginated rows, default 15, max 50, sort by cheapest), `get_price_trend` (per-date cheapest miles time series for graphing, with `from_date`/`to_date` date range filters), `find_deals` (server-side cross-route deal discovery via SQL CTEs in `find_deals_query()` in `core/db.py`). Updated FastMCP `instructions` with new tool selection flow. Tool count: 7 → 10. 35 MCP tests (was 22). 389 tests passing. | Token waste from `query_flights` returning full row arrays (~10.4k tokens) when agents only needed the summary (~150 tokens). Summary/detail split prevents context window blowup. |
| **16c** | **Live re-test (token-efficient toolkit).** Repeat step 16 test protocol with the summary/detail split in place. Verify: (1) agent uses `query_flights` and gets ~150-300 token summary, not ~10.4k, (2) agent calls `get_flight_details` only when user asks for a table, (3) `get_price_trend` and `find_deals` work when prompted, (4) multi-turn conversation stays well under context limits. | Confirm the token reduction works in practice from the agent's perspective. |
| **16f** | **~~Remove email auth — MP# only login.~~** Done (2026-04-11). Deleted `gmail_mfa.py` entirely. Rewrote `_auto_login()` in `cookie_farm.py` to enter MP# directly — no email attempt, no Akamai 428 detection, no Gmail IMAP recovery fallback. Removed `UNITED_EMAIL`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` env vars from all files (`cli.py`, `debug_login.py`, `orchestrate.py`, `.env.sample`). `_load_credentials()` reads only `UNITED_MP_NUMBER` + `UNITED_PASSWORD`. Deleted deprecated specs (`email-recovery-login.md`, `unified-login-with-recovery-fallback.md`). Rewrote `TestAutoLoginRecoveryFallback` as `TestAutoLoginMPOnly`. 392 tests passing. | Email-first login was a liability: Akamai blocked email login frequently, MP# login never gets blocked. The entire email→428→fallback→Gmail IMAP pipeline was unnecessary complexity. |
| **16g** | **~~MCP server simplification (fastmcp migration).~~ OBSOLETE — not done, never will be.** This planned `fastmcp` v3 migration was overtaken by the decision to remove the MCP server entirely (`917a514`, replaced by CLI + `/flights` skill). There is no `mcp_server.py` to migrate. Kept for history. | The MCP server was removed rather than simplified — the CLI + skill made it redundant. |
| **17** | **~~Watchlist + Discord notifications.~~** Done (2026-04-11, migrated ntfy→Discord 2026-05-26). `searchaero watch add/list/remove/check/run/setup` subcommands. `watches` table in SQLite with route, cabin, max_miles, date range, check interval, and notification tracking. `watch check` implements full pipeline: find due watches → check freshness → scrape stale routes via `burn_in.py --one-shot` → evaluate conditions via `check_alert_matches` → content-hash dedup → send Discord webhook notifications. `watch run` starts foreground daemon loop. `core/notify.py` handles Discord webhooks via stdlib `urllib.request` (zero new dependencies). Config in `~/.searchaero/config.json` with env var override (`SEARCHAERO_DISCORD_WEBHOOK_URL`). `core/watchlist.py` orchestrates the check pipeline with `parse_interval()` for human-friendly intervals (hourly, 6h, 12h, daily). 4 MCP tools (`add_watch`, `list_watches`, `remove_watch`, `check_watches`). 474 tests passing (56 new across test_notify, test_watchlist, test_cli, test_mcp). | Closes the notification loop — watches combine route monitoring, scheduled scraping, and push notifications into a fully automated pipeline. |
| **17b** | **~~Agent-mediated notification delivery.~~** Done (2026-04-13, simplified 2026-05-26). Decoupled MCP notification delivery so searchaero returns structured match data with pre-formatted messages, and the calling agent delivers via whatever channel it has (Gmail MCP, Slack, etc.). `_format_notification()` helper produces `{title, body}` dicts. `check_watches` includes `notification` block and `discord_sent` per result. CLI path uses Discord webhooks directly (`core/notify.py` with `send_discord()`). MCP `instructions` guide agents to deliver via their own tools. 474 tests passing. | Searchaero finds deals; the agent routes delivery. Discord for headless runs, agent-mediated for interactive sessions. |
| **18** | **~~MFA responder script.~~** Done (2026-05-05). `scripts/mfa_responder.py` — standalone companion script (202 lines) that watches `~/.searchaero/mfa_request` and answers it by reading the MFA verification code from Gmail via IMAP. Adapted from mintapi's `get_email_code()` pattern. Guards: `@united.com` sender filter, email age check (180s), newest-first + limit 3, delete-after-read. Communicates exclusively through existing file protocol (`mfa_request`/`mfa_response`). Uses existing env vars (`SEARCHAERO_GMAIL_SENDER`, `SEARCHAERO_GMAIL_APP_PASSWORD`). Only stdlib (`imaplib`, `email`) + existing `python-dotenv` dependency — no new deps. Not imported by any searchaero module. Enables fully unattended cron-based scraping. | Closes Phase 1 Step 1: automated MFA without human or LLM in the loop. Prerequisite for cron-based scheduled scraping. |
| **18b** | **~~MFA code extraction fix + email MFA flow.~~** Done (2026-05-08). Two bugs prevented autonomous email MFA: (1) `extract_code_from_email()` — naive `\b(\d{6})\b` regex matched CSS hex colors (#000000) in United's HTML emails. Fixed with `_strip_html_to_text()` (html.parser, skips `<style>`/`<script>`) + 3-tier extraction: subject line → contextual anchor search near "verification code" → fallback with same-digit rejection. 7 tests in `tests/test_mfa_responder.py`. (2) `_select_mfa_method()` in `cookie_farm.py` — United's MFA defaults to SMS; switching to email requires clicking "try a different way" → selecting Email → clicking Continue. Rewrote to handle this navigation flow. **E2E validated (2026-05-08):** YYZ-LAX (2x) and YYZ-WUH all completed with fully autonomous email MFA pipeline: MP# login → email MFA triggered → responder reads Gmail via IMAP → contextual match extracts code → file handoff → login completes → 12/12 windows scraped. | The MFA responder existed but couldn't extract codes from real United emails (CSS noise) and the browser couldn't switch to email MFA. These two fixes complete the autonomous pipeline. |
| **18c** | **~~Scheduled scrape wrapper script.~~** Done (2026-05-10, eval+email added 2026-05-17, claude CLI eval 2026-05-25). `scripts/scheduled_scrape.py` — orchestrates the autonomous pipeline: clean stale MFA files → start `mfa_responder.py` → run `searchaero search` → evaluate watches via `claude` CLI (`core/eval_watches.py`) → send Discord notifications → kill responder. CLI: `--routes`, `--delay`, `--dry-run`, `--db-path`, `--env-file`, `--no-eval`, `--register-scheduler`. Watch conditions in `~/.searchaero/watches.yaml` (natural language, evaluated by `claude -p --model haiku`). Template fallback if CLI unavailable. No API key needed — uses existing Claude Code auth. `--register-scheduler` prints `schtasks /create` for Windows Task Scheduler. `scripts/scheduled_scrape.bat` for Task Scheduler launcher. 18 tests in `tests/test_scheduled_scrape.py` + 12 in `tests/test_eval_watches.py`. | Closes Phase 1 Step 2: fully autonomous scrape → evaluate → notify pipeline. Single process, triggered by Windows Task Scheduler. |
| **18d** | **~~Scheduled scrape v2 — Claude CLI eval.~~** Done (2026-05-18, unified 2026-05-25). Watch evaluation uses `claude` CLI throughout — both the inline `eval_watches.py` path (called by `scheduled_scrape.py` when `--no-eval` is off) and the `.bat` two-step path (`claude -p < eval_prompt.txt`). `eval_watches.py` calls `subprocess.run(["claude", "-p", prompt, "--model", "haiku"])` instead of the Anthropic API — no `ANTHROPIC_API_KEY`, no `anthropic` pip dependency. Template fallback if `claude` CLI not found or times out. JSONL logs include `eval_method` field. `/flights` skill updated with Autonomous Mode section. | Unified on `claude` CLI for all eval paths. Zero extra credentials — uses existing Claude Code auth. |
| **19** | **~~Scrape guardrails + watch notifications.~~** Done (2026-05-26). Three enhancements to the scheduled scraping pipeline: (1) **60-min minimum interval** — `MIN_INTERVAL_MINUTES = 60` in `core/scheduler.py`, validated in `cli.py` and `register_task()`. Prevents Akamai rate limiting from aggressive intervals. Default changed from 15 to 60. (2) **Backoff state machine** — `~/.searchaero/scrape_state.json` tracks consecutive failures per schedule. After 3 consecutive failures, auto-disables the schedule and sends Discord alert. `scheduled_scrape.py` reads state at start (exits immediately if disabled), resets on success, increments on failure. `--schedule-name` arg links runs to their schedule. (3) **Eval ON by default + notification history** — Flipped eval default so `.bat` launchers include watch evaluation unless `--no-eval` specified. `run_eval_and_notify()` appends to `~/.searchaero/logs/watch_notifications.jsonl` after each Discord notification — no hash-based dedup, every match fires. CLI `schedule enable/disable` commands manage backoff state. `schedule status` shows failure counts and disabled state. Also: `scrape_route()` now supports `--months`, `--from`, `--to` date filtering; `core/notify.py` simplified to Discord-only (removed ntfy + SMTP). | Closes the operational gaps from first production run: rate limiting prevention, automatic failure recovery, and always-on watch evaluation. |

## Testing strategy

### Pipeline layers

```
United API  →  parse response  →  validate  →  upsert to SQLite  →  query/alert
  (network)     (united_api.py)   (models.py)    (db.py)             (db.py / cli.py)
```

### Current test coverage (507 tests)

| Layer | Test file | Tests | What's real | What's faked |
|-------|-----------|-------|-------------|--------------|
| Parse | `test_parser.py` | 8 | Parser logic | API response (synthetic JSON) |
| Validate | `test_models.py` | 22 | All validation rules | Nothing |
| Store/Query/Alerts/Freshness | `test_db.py` | 45 | Real in-memory SQLite, triggers, upserts, TTL freshness | Nothing |
| Web API | `test_api.py` | 17 | Endpoint logic | DB mocked entirely |
| Scraper state | `test_hybrid_scraper.py` | 5 | State machine | CookieFarm mocked |
| CLI dispatch | `test_cli.py` | 108 | Arg parsing, search dispatch, query freshness, MFA file handoff, watch commands | CookieFarm/HybridScraper mocked (search), db mocked (query), temp dir for MFA files |
| CLI comprehensive | `test_cli_full.py` | 50 | Every CLI command incl. query --refresh/--ttl | CookieFarm/HybridScraper mocked (search), real temp SQLite (query/status/alert) |
| **L1 Integration** | **`test_integration.py`** | **11** | **Full pipeline: parse→validate→upsert→query→history→alerts** | **API response (synthetic JSON)** |
| **L2 CLI Integration** | **`test_cli_integration.py`** | **34** | **CLI commands against real temp SQLite incl. freshness metadata** | **Nothing (no mocks)** |
| **E2E Scraper→CLI** | **`test_e2e.py`** | **16** | **`scrape_route()` → real SQLite → CLI query/status/alert/history** | **`HybridScraper` (FakeScraper returns synthetic API responses)** |
| Output | `test_output.py` | 20 | Sparkline rendering, auto-TTY, build_meta, build_freshness, print_error, print_table | Nothing (pure unit) |
| Schema | `test_schema.py` | 14 | Schema introspection, CLI schema command, --fields, --meta | Nothing |
| Schedule | `test_schedule.py` | 15 | Cron parsing, CLI schedule command | APScheduler mocked |
| Notify | `test_notify.py` | 20 | Config load/save, send_discord HTTP POST, notify_watch_matches embed formatting, env var override, webhook failure | `urllib.request.urlopen` mocked, temp dir for config files |
| Watchlist | `test_watchlist.py` | 9 | parse_interval, _compute_match_hash, check_watches pipeline | subprocess.run mocked, core.notify mocked, real temp SQLite |
| MFA responder | `test_mfa_responder.py` | 7 | Code extraction from subject/body/HTML, CSS hex color rejection, anchor text search | Nothing (pure unit) |
| Scheduled scrape | `test_scheduled_scrape.py` | 18 | CLI args, subprocess lifecycle, JSONL logging, dry-run, Discord notify, eval step integration | subprocess mocked, core.notify mocked |
| Eval watches | `test_eval_watches.py` | 12 | YAML loading, DB query, claude CLI call, template fallback, run_eval_and_notify flow | subprocess mocked, DB mocked, send_discord mocked |
| ~~MCP server~~ (removed) | ~~`test_mcp.py`~~ | — | The MCP server and its 55 tests were removed with `mcp_server.py` in `917a514`. The logic those tools wrapped (`core/db.py` queries, scrape pipeline) is still covered by the data-path, CLI, and E2E suites above. | — |

Unit tests cover each layer in isolation. L1 integration proves the data-path layers compose. L2 CLI integration proves CLI commands work end-to-end against real databases. E2E tests prove the full scraper write-path composes correctly with the CLI read-path.

### End-to-end test plan

Three levels, each building on the last. No test at any level hits United's real servers.

**Level 1: Data path integration** (`tests/test_integration.py`) — **Done.**

11 integration tests across 5 test classes, stitching parse → validate → store → query → history → alerts with synthetic API data and real in-memory SQLite:

- `TestParseToValidate` (2 tests) — parser output validates successfully for all cabin types; unknown cabins rejected
- `TestParseToStore` (2 tests) — full pipeline (2 dates × 3 cabins = 6 solutions) through parse→validate→upsert→query; cabin/date/date_from filters verified
- `TestHistoryIntegration` (3 tests) — INSERT trigger creates history on first upsert; UPDATE trigger tracks price changes (13000→15000 miles); unchanged prices produce no duplicate history
- `TestAlertIntegration` (3 tests) — alert matching on pipeline data; cabin-filtered alerts; notification hash round-trip stability
- `TestAwardTypeCoexistence` (1 test) — Saver and Standard award types coexist as separate rows for same cabin/date

Catches: field name mismatches between layers, SQL type errors, trigger failures, alert dedup hash drift.

**Level 2: CLI integration** (`tests/test_cli_integration.py`) — **Done.**

32 CLI integration tests across 7 test classes. Pre-seeds a real temp SQLite file, then runs CLI commands via `main(["--db-path", ...])` with no mocked DB:

- `TestSetupIntegration` (2 tests) — schema creation with `--db-path`, JSON output validation
- `TestQueryIntegration` (5 tests) — summary table, detail view with taxes, JSON (7 records), no-results, CSV with header/row validation
- `TestQueryFiltersIntegration` (7 tests) — cabin expansion (economy→3 rows, business→2), date range, date_from, combined cabin+date, sort by miles/cabin
- `TestQueryHistoryIntegration` (5 tests) — route summary text/JSON with lowest/highest/observations, date timeline, cabin filter on history
- `TestStatusIntegration` (4 tests) — text/JSON output with counts, missing DB, empty DB
- `TestAlertIntegration` (7 tests) — add/list, cabin+date filters, check finds matches, cabin filter on check, hash dedup, remove, remove nonexistent
- `TestPriceChangeCLI` (2 tests) — price drop triggers alert refire via changed hash, history reflects both observations

Catches: CLI arg parsing regressions, `--db-path` forwarding, output formatting, cabin filter expansion, sort logic, alert dedup hash drift through CLI.

**Level 2.5: E2E Scraper→CLI round-trip** (`tests/test_e2e.py`) — **Done.**

16 E2E tests across 6 test classes, bridging the write-path gap between L1 (data-path from parsed JSON) and L2 (CLI on pre-seeded DB). Uses a `FakeScraper` that returns synthetic API responses — `scrape_route()` runs for real with a temp SQLite DB, then CLI commands verify the stored data:

- `TestScrapeRouteIntegration` (3 tests) — `scrape_route()` stores all 12 windows (36 solutions), records scrape_jobs, returns correct totals with custom responses
- `TestScrapeRouteErrors` (3 tests) — failed windows record failed jobs, circuit breaker aborts after 3 consecutive burns, mixed success/failure counts
- `TestCrashDetection` (3 tests) — `_scrape_with_crash_detection()` identifies browser crash keywords, ignores partial failures, ignores non-browser errors
- `TestScrapeToCliRoundTrip` (3 tests) — full pipeline: scrape → CLI `query`/`status`/`alert check` via `main(["--db-path", ...])`
- `TestScrapeHistoryRoundTrip` (2 tests) — price change tracked in history through full pipeline, alert re-fires on price drop
- `TestScrapeDateEdgeCases` (2 tests) — past dates and far-future dates rejected by validator during scrape

Catches: `scrape_route()` orchestration bugs, circuit breaker logic, crash detection, scrape_job recording, write-path → read-path composition failures.

**Level 3: Scraper smoke test** (manual gate, hits United servers)

The only level that makes real HTTP requests. Requires a live MileagePlus session, Playwright, and network access. Too flaky/slow for CI — run manually before releases:

```bash
searchaero search YYZ LAX --db-path /tmp/test.db
searchaero query YYZ LAX --db-path /tmp/test.db
```

The existing burn-in infrastructure (`burn_in.py --one-shot`) is this test. The 15-route, 180/180-window, 0-error burn-in result documented above serves as the E2E validation gate. Additionally, `searchaero search YYZ LAX --delay 7` completed 12/12 windows (1,398 results, 0 errors) with fully automated SMS MFA login on 2026-04-09.

### What each level catches

| Bug class | L1 | L2 | E2E | L3 |
|-----------|----|----|-----|-----|
| Field name mismatch between parse/validate | Yes | | | |
| SQL type errors (str vs int) | Yes | | | |
| Trigger not firing on upsert | Yes | | Yes | |
| Alert dedup hash drift | Yes | | | |
| `scrape_route()` orchestration broken | | | Yes | |
| Circuit breaker logic broken | | | Yes | |
| Crash detection false positive/negative | | | Yes | |
| Scrape job recording incorrect | | | Yes | |
| Write-path → read-path composition failure | | | Yes | |
| CLI arg parsing regression | | Yes | | |
| `--db-path` not forwarded correctly | | Yes | | |
| Output formatting broken | | Yes | | |
| United changed their API shape | | | | Yes |
| Cookie/auth session expired | | | | Yes |
| Validation rejecting real API data | | | | Yes |
