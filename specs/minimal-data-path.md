# Plan: Minimal Data Path — PostgreSQL + Scrape + Validate + Store

## Task Description
Phase 1, Step 2: Build the minimal end-to-end data pipeline. PostgreSQL schema (with upsert), single-threaded scraper for 1 route across all 12 monthly windows, response parser with row-level validation, and database storage. Verify stored data matches united.com manually. No concurrency, no scheduling, no alerts — just proving the data path works.

## Objective
A single command (`python scrape.py --route YYZ LAX`) scrapes 12 monthly calendar windows, validates every row, upserts results into PostgreSQL, and prints a verification report that can be cross-checked against united.com.

## Problem Statement
Steps 0 and 1 proved the API contract and built the hybrid scraper (curl_cffi + Playwright cookie farm). But the scraper currently only prints results to stdout — there's no database, no validation, and no way to verify data accuracy over time. Step 2 closes that gap by connecting the scraper to persistent storage with data integrity guarantees.

## Solution Approach

**Architecture: Scrape → Parse → Validate → Upsert → Verify**

```
hybrid_scraper.fetch_calendar()
         │
         ▼
united_api.parse_calendar_solutions()    ← existing parser
         │
         ▼
models.validate_solution()               ← NEW: row-level validation
         │
         ▼
db.upsert_availability()                 ← NEW: INSERT ... ON CONFLICT DO UPDATE
         │
         ▼
db.get_route_summary()                   ← NEW: verification query
```

- **PostgreSQL** via Docker Compose (one command to start, no manual DB setup)
- **psycopg** (v3) for database access — modern, supports binary protocol, sync API
- **Adapted schema** from project brief, adjusted for calendar-only data (no seat counts, no direct/connecting, no flight details — those come from FetchFlights in a future step)
- **Upsert** via `INSERT ... ON CONFLICT DO UPDATE` to avoid duplicate rows
- **Data validation** rejects anomalous rows (zero miles, negative prices, unknown cabins, out-of-range dates) per the error catalog

### Schema Design (Calendar-Only)

The project brief's schema assumes FetchFlights data (seats, direct, flights JSONB). Since step 2 uses calendar-only data, the schema is adapted:

```sql
CREATE TABLE availability (
    id SERIAL PRIMARY KEY,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    date DATE NOT NULL,
    cabin TEXT NOT NULL,           -- economy, premium_economy, business, business_pure, first, first_pure
    award_type TEXT NOT NULL,      -- Saver, Standard
    miles INTEGER NOT NULL,
    taxes_cents INTEGER,           -- USD taxes in cents (e.g., 6851 = $68.51)
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Nullable: populated later by FetchFlights enrichment (step 3+)
    seats INTEGER,
    direct BOOLEAN,
    flights JSONB,
    UNIQUE(origin, destination, date, cabin, award_type)
);
```

Key differences from the brief:
- Added `award_type` to the unique constraint (Saver vs Standard are distinct data points)
- `direct` removed from unique constraint (not available from calendar; nullable for future enrichment)
- `taxes_cents` stores taxes as integer cents to avoid floating-point issues
- `seats`, `direct`, `flights` are nullable — will be populated when FetchFlights is added

### Validation Rules (from error-catalog.md)

| Field | Rule | Action on Violation |
|---|---|---|
| miles | > 0 AND < 500,000 | Reject row |
| taxes_cents | >= 0 AND < 100,000 ($1,000) | Reject if < 0; flag if > 100,000 |
| date | Not in past AND <= today + 337 days | Skip row |
| cabin | In known CABIN_TYPE_MAP values | Reject row, log unknown type |
| award_type | "Saver" or "Standard" | Reject row, log unknown type |
| origin/destination | 3 uppercase letters | Reject row |

## Relevant Files

### Existing Files (Read/Import)
- `scripts/experiments/hybrid_scraper.py` — The scraper that fetches calendar data. `scrape.py` will import `HybridScraper` and `CookieFarm` from here.
- `scripts/experiments/cookie_farm.py` — Playwright cookie farm for fresh Akamai cookies.
- `scripts/experiments/united_api.py` — Request building, response validation, calendar parsing. The `parse_calendar_solutions()` function returns the raw parsed data that feeds into validation.
- `scripts/experiments/gmail_mfa.py` — MFA code retrieval (used by cookie_farm).
- `docs/api-contract/united-calendar-api.md` — API contract. Defines the response schema, CabinType mapping, and empirical data.
- `docs/api-contract/error-catalog.md` — Error classification and data anomaly rules. Drives the validation logic.
- `docs/project-brief.md` — Project brief with the original schema design and step 2 description.
- `.gitignore` — Must be updated to exclude new sensitive files.

### New Files
- `docker-compose.yml` — PostgreSQL container definition. One `docker compose up -d` to start.
- `requirements.txt` — Project-level Python dependencies (psycopg, curl_cffi, playwright, python-dotenv).
- `core/__init__.py` — Package init.
- `core/db.py` — Database connection, schema creation, upsert, query functions, pruning.
- `core/models.py` — `AwardResult` dataclass, `validate_solution()` function, IATA code validation.
- `scrape.py` — Main entry point. Ties together hybrid_scraper + parser + validation + storage.
- `scripts/verify_data.py` — Verification script that queries the DB and formats a comparison table for manual cross-checking against united.com.

## Implementation Phases

### Phase 1: Foundation
- Docker Compose for PostgreSQL
- `core/models.py` with `AwardResult` dataclass and validation
- `core/db.py` with connection management, schema creation, upsert

### Phase 2: Core Implementation
- `scrape.py` main entry point: scrape 1 route across 12 monthly windows, validate, store
- `requirements.txt` with all dependencies
- Update `.gitignore` for new artifacts

### Phase 3: Integration & Polish
- `scripts/verify_data.py` for manual verification
- End-to-end test: scrape YYZ-LAX, query DB, compare with united.com
- Update project brief step 2 status

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
  - Name: infra-builder
  - Role: Create Docker Compose, requirements.txt, and .gitignore updates. Foundation infrastructure that other tasks depend on.
  - Agent Type: builder
  - Resume: true

- Builder
  - Name: core-builder
  - Role: Build `core/models.py` (AwardResult dataclass, validation) and `core/db.py` (connection, schema, upsert, queries). The data layer.
  - Agent Type: backend-architect
  - Resume: true

- Builder
  - Name: pipeline-builder
  - Role: Build `scrape.py` (main entry point) and `scripts/verify_data.py` (verification). Integrates the hybrid scraper with the new data layer.
  - Agent Type: builder
  - Resume: true

- Builder
  - Name: validator
  - Role: Validate all files exist, Python syntax is correct, schema creates cleanly, imports resolve, no credentials in tracked files, and the end-to-end flow is logically sound.
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Create Docker Compose and Infrastructure
- **Task ID**: setup-infra
- **Depends On**: none
- **Assigned To**: infra-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `docker-compose.yml` at project root with:
  - PostgreSQL 16 service named `db`
  - Port: `5432:5432`
  - Database: `seataero`
  - User/password from environment with defaults: `POSTGRES_USER=seataero`, `POSTGRES_PASSWORD=seataero_dev`, `POSTGRES_DB=seataero`
  - Volume: `pgdata:/var/lib/postgresql/data` for persistence
  - Healthcheck: `pg_isready -U seataero`
- Create `requirements.txt` at project root with:
  ```
  psycopg[binary]>=3.1
  curl_cffi>=0.7
  playwright>=1.40
  python-dotenv>=1.0
  ```
- Update `.gitignore` to add:
  ```
  # Python
  __pycache__/
  *.pyc
  .venv/
  venv/

  # Database
  pgdata/
  ```
- Create `core/__init__.py` (empty file)
- Create `.env.example` at project root with:
  ```
  # PostgreSQL connection
  DATABASE_URL=postgresql://seataero:seataero_dev@localhost:5432/seataero

  # United credentials (see scripts/experiments/.env for scraper credentials)
  ```

### 2. Build Data Models and Validation
- **Task ID**: build-models
- **Depends On**: setup-infra
- **Assigned To**: core-builder
- **Agent Type**: backend-architect
- **Parallel**: false
- Create `core/models.py` with:
  - `VALID_CABINS` set: `{"economy", "premium_economy", "business", "business_pure", "first", "first_pure"}`
  - `VALID_AWARD_TYPES` set: `{"Saver", "Standard"}`
  - `CANADIAN_AIRPORTS` list: `["YYZ", "YVR", "YUL", "YYC", "YOW", "YEG", "YWG", "YHZ", "YQB"]` (Phase 1 airports from the project brief)
  - A `@dataclass` `AwardResult` with fields:
    - `origin: str`
    - `destination: str`
    - `date: datetime.date`
    - `cabin: str`
    - `award_type: str`
    - `miles: int`
    - `taxes_cents: int`
    - `scraped_at: datetime.datetime` (defaults to `datetime.now(timezone.utc)`)
  - A `validate_solution(raw: dict, origin: str, destination: str) -> tuple[AwardResult | None, str | None]` function:
    - Takes a single parsed solution dict from `united_api.parse_calendar_solutions()` plus the route origin/destination
    - Converts `date` from MM/DD/YYYY string to `datetime.date`
    - Converts `taxes_usd` float to `taxes_cents` integer (multiply by 100, round)
    - Converts `miles` float to integer
    - Applies validation rules (see Validation Rules table above)
    - Returns `(AwardResult, None)` on success, `(None, "reason string")` on rejection
  - A `validate_iata_code(code: str) -> bool` helper: 3 uppercase ASCII letters
  - Unit tests are NOT needed in this step — validation will be tested end-to-end by scraping a real route
- Read `scripts/experiments/united_api.py` to understand the output format of `parse_calendar_solutions()` — specifically the dict keys: `date`, `cabin`, `cabin_raw`, `award_type`, `miles`, `taxes_usd`
- Read `docs/api-contract/error-catalog.md` for the data anomaly rules

### 3. Build Database Layer
- **Task ID**: build-db
- **Depends On**: build-models
- **Assigned To**: core-builder
- **Agent Type**: backend-architect
- **Parallel**: false
- Create `core/db.py` with:
  - `get_connection(database_url: str = None) -> psycopg.Connection`:
    - Reads `DATABASE_URL` from environment (via `os.getenv`) if not provided
    - Default: `postgresql://seataero:seataero_dev@localhost:5432/seataero`
    - Returns a psycopg connection with `autocommit=False`
  - `create_schema(conn: psycopg.Connection)`:
    - Creates the `availability` table if not exists (using the schema from this plan)
    - Creates indexes:
      - `idx_route_date_cabin ON availability(origin, destination, date, cabin)` — primary search pattern
      - `idx_scraped ON availability(scraped_at)` — for pruning and freshness checks
      - `idx_alert_match ON availability(origin, destination, cabin, miles)` — for future alert matching
    - Creates the `scrape_jobs` table if not exists:
      ```sql
      CREATE TABLE IF NOT EXISTS scrape_jobs (
          id SERIAL PRIMARY KEY,
          origin TEXT NOT NULL,
          destination TEXT NOT NULL,
          month_start DATE NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          started_at TIMESTAMPTZ,
          completed_at TIMESTAMPTZ,
          solutions_found INTEGER DEFAULT 0,
          solutions_stored INTEGER DEFAULT 0,
          solutions_rejected INTEGER DEFAULT 0,
          error TEXT,
          UNIQUE(origin, destination, month_start, started_at)
      );
      ```
    - Uses `IF NOT EXISTS` for idempotency
    - Commits the transaction
  - `upsert_availability(conn, results: list[AwardResult]) -> int`:
    - Takes a list of validated `AwardResult` objects
    - Uses `INSERT ... ON CONFLICT (origin, destination, date, cabin, award_type) DO UPDATE SET miles = EXCLUDED.miles, taxes_cents = EXCLUDED.taxes_cents, scraped_at = EXCLUDED.scraped_at`
    - Uses `executemany` with `psycopg.sql` for parameterized queries
    - Returns the number of rows upserted
    - Commits the transaction
  - `record_scrape_job(conn, origin, destination, month_start, status, solutions_found=0, solutions_stored=0, solutions_rejected=0, error=None)`:
    - Inserts a row into `scrape_jobs`
    - Used to track what was scraped and when
  - `get_route_summary(conn, origin, destination) -> list[dict]`:
    - Queries `SELECT date, cabin, award_type, miles, taxes_cents, scraped_at FROM availability WHERE origin = %s AND destination = %s ORDER BY date, cabin, award_type`
    - Returns list of dicts for the verification report
  - `get_scrape_stats(conn) -> dict`:
    - Returns aggregate stats: total rows, routes covered, latest scrape time, date range
- Read the schema design in this plan and `docs/project-brief.md` for the original schema intent
- Read `core/models.py` (built in previous task) to understand the `AwardResult` dataclass

### 4. Build Scrape Pipeline Entry Point
- **Task ID**: build-pipeline
- **Depends On**: build-db
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scrape.py` at project root with:
  - Adds `scripts/experiments` to `sys.path` so it can import `CookieFarm`, `HybridScraper`, and `united_api`
  - Imports from `core.db` and `core.models`
  - A `scrape_route(origin, destination, conn, scraper)` function that:
    1. Generates 12 departure dates spaced 30 days apart starting from today (to cover ~360 days)
    2. For each monthly window:
       a. Calls `scraper.fetch_calendar(origin, destination, depart_date)`
       b. If successful, parses with `united_api.parse_calendar_solutions()`
       c. Validates each solution with `models.validate_solution()`
       d. Upserts valid results with `db.upsert_availability()`
       e. Records the scrape job with `db.record_scrape_job()`
       f. Prints progress: `"Window 3/12 (2026-06-01): 28 solutions, 26 stored, 2 rejected"`
    3. Sleeps `delay` seconds between windows
    4. Returns aggregate stats (total solutions found/stored/rejected/errors)
  - CLI via `argparse`:
    - `python scrape.py --route YYZ LAX` — scrape one route (all 12 windows)
    - `--headless` flag for cookie farm
    - `--delay` (default 7.0 seconds between API calls)
    - `--refresh-interval` (default 2, for cookie refresh)
    - `--database-url` (override, default from env)
    - `--create-schema` flag to create/update the DB schema before scraping
  - The `main()` function:
    1. Parse args
    2. Connect to PostgreSQL
    3. If `--create-schema`: call `db.create_schema(conn)`
    4. Start cookie farm and hybrid scraper
    5. Call `scrape_route()` for the specified route
    6. Print final summary with totals
    7. Clean up (close scraper, farm, DB connection)
  - Error handling:
    - If a single window fails, log the error and continue to the next window (don't abort the whole route)
    - If DB connection fails, print a clear error message: "Cannot connect to PostgreSQL. Run `docker compose up -d` first."
    - If cookie farm can't start, print clear error and exit

### 5. Build Verification Script
- **Task ID**: build-verify
- **Depends On**: build-db
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true (can run alongside build-pipeline since it only depends on build-db)
- Create `scripts/verify_data.py` with:
  - Imports from `core.db` and `core.models`
  - Adds project root to `sys.path` so core imports work
  - A `print_route_report(conn, origin, destination)` function that:
    1. Queries all availability for the route via `db.get_route_summary()`
    2. Prints a formatted table grouped by date:
       ```
       ═══════════════════════════════════════════════════════════════
       Verification Report: YYZ → LAX
       Data as of: 2026-04-01 15:30 UTC
       Total records: 342
       Date range: 2026-04-02 to 2027-03-01
       ═══════════════════════════════════════════════════════════════

       Date        | Cabin              | Type     | Miles   | Tax ($) | Scraped
       ------------|--------------------| ---------|---------|---------|------------------
       2026-04-02  | economy            | Saver    | 12,900  | 68.51   | 2026-04-01 15:28
       2026-04-02  | economy            | Standard | 22,500  | 68.51   | 2026-04-01 15:28
       2026-04-02  | business           | Saver    | 30,000  | 68.51   | 2026-04-01 15:28
       ...
       ```
    3. Prints a per-cabin summary:
       ```
       Cabin Summary:
         economy:         120 records (Saver: 80, Standard: 40)
         business:        90 records  (Saver: 30, Standard: 60)
         first:           42 records  (Saver: 8, Standard: 34)
       ```
    4. Prints a quick-check section with 5 sample rows to manually verify against united.com:
       ```
       ═══════════════════════════════════════════════════════════════
       Manual Verification Checklist
       ═══════════════════════════════════════════════════════════════
       Check these against united.com award calendar:

       1. YYZ → LAX, 2026-04-15, economy Saver: 12,900 miles + $68.51
          → Go to united.com, search YYZ-LAX one-way award on Apr 15
          → Verify economy calendar shows 12,900 miles

       2. YYZ → LAX, 2026-05-10, business Saver: 30,000 miles + $68.51
          → Same search for May 10, check business column
       ...
       ```
  - CLI: `python scripts/verify_data.py --route YYZ LAX`
  - Also accepts `--database-url` override
  - Also includes a `--stats` flag that prints `db.get_scrape_stats()`

### 6. Validate All Results
- **Task ID**: validate-all
- **Depends On**: build-pipeline, build-verify
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all new files exist:
  - `docker-compose.yml` — has PostgreSQL 16 service with healthcheck
  - `requirements.txt` — has psycopg, curl_cffi, playwright, python-dotenv
  - `core/__init__.py` — exists
  - `core/models.py` — has `AwardResult` dataclass, `validate_solution()`, `VALID_CABINS`, `VALID_AWARD_TYPES`
  - `core/db.py` — has `get_connection()`, `create_schema()`, `upsert_availability()`, `record_scrape_job()`, `get_route_summary()`, `get_scrape_stats()`
  - `scrape.py` — has CLI with `--route`, `--create-schema`, `--headless`, `--delay`
  - `scripts/verify_data.py` — has `print_route_report()`, CLI with `--route`
- Verify Python syntax on all new files: `python -m py_compile core/models.py core/db.py scrape.py scripts/verify_data.py`
- Verify imports resolve:
  - `core.models` imports cleanly
  - `core.db` imports cleanly (psycopg must be installed)
  - `scrape.py` can import from both `core.*` and `scripts.experiments.*`
- Verify schema correctness:
  - `availability` table has `UNIQUE(origin, destination, date, cabin, award_type)`
  - Upsert uses `ON CONFLICT ... DO UPDATE`
  - Indexes match the project brief's recommendations
  - `scrape_jobs` table exists for job tracking
- Verify validation logic:
  - `validate_solution()` rejects miles <= 0
  - `validate_solution()` rejects miles > 500,000
  - `validate_solution()` rejects unknown cabin types
  - `validate_solution()` rejects dates in the past
  - `validate_solution()` rejects invalid IATA codes
- Verify data flow:
  - `scrape.py` calls `hybrid_scraper.fetch_calendar()` → `united_api.parse_calendar_solutions()` → `models.validate_solution()` → `db.upsert_availability()`
  - Results are committed to PostgreSQL, not just printed
- Verify no credentials in tracked files:
  - No passwords, tokens, or API keys in any new file
  - `.env.example` has placeholder values only
  - `docker-compose.yml` uses default dev credentials (acceptable for local dev)
- Verify `.gitignore` covers:
  - `__pycache__/`
  - `pgdata/`
  - `.env` files (already covered)
- Report any gaps or issues

## Acceptance Criteria
- [ ] `docker-compose.yml` exists and defines PostgreSQL 16 with healthcheck
- [ ] `requirements.txt` lists psycopg, curl_cffi, playwright, python-dotenv
- [ ] `core/models.py` has `AwardResult` dataclass with all fields and `validate_solution()` implementing all validation rules
- [ ] `core/db.py` has `create_schema()` creating both tables with proper indexes, `upsert_availability()` with ON CONFLICT, and query functions
- [ ] `scrape.py` works as CLI: `python scrape.py --route YYZ LAX --create-schema` scrapes 12 windows and stores results
- [ ] `scripts/verify_data.py` queries the DB and prints a formatted verification report
- [ ] Upsert is idempotent: running `scrape.py` twice for the same route updates (not duplicates) rows
- [ ] Validation rejects zero-mile rows, negative prices, unknown cabins, past dates
- [ ] Schema uses `UNIQUE(origin, destination, date, cabin, award_type)` constraint
- [ ] `scrape_jobs` table tracks each window's scrape status
- [ ] No credentials in tracked files
- [ ] All Python files pass `python -m py_compile`

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# Verify new files exist
test -f docker-compose.yml && echo "PASS: docker-compose.yml" || echo "FAIL"
test -f requirements.txt && echo "PASS: requirements.txt" || echo "FAIL"
test -f core/__init__.py && echo "PASS: core/__init__.py" || echo "FAIL"
test -f core/models.py && echo "PASS: core/models.py" || echo "FAIL"
test -f core/db.py && echo "PASS: core/db.py" || echo "FAIL"
test -f scrape.py && echo "PASS: scrape.py" || echo "FAIL"
test -f scripts/verify_data.py && echo "PASS: scripts/verify_data.py" || echo "FAIL"
test -f .env.example && echo "PASS: .env.example" || echo "FAIL"

# Verify Python syntax
python -m py_compile core/models.py && echo "PASS: models.py syntax" || echo "FAIL"
python -m py_compile core/db.py && echo "PASS: db.py syntax" || echo "FAIL"
python -m py_compile scrape.py && echo "PASS: scrape.py syntax" || echo "FAIL"
python -m py_compile scripts/verify_data.py && echo "PASS: verify_data.py syntax" || echo "FAIL"

# Verify core/models.py has required components
grep -q "class AwardResult" core/models.py && echo "PASS: AwardResult class" || echo "FAIL"
grep -q "def validate_solution" core/models.py && echo "PASS: validate_solution" || echo "FAIL"
grep -q "VALID_CABINS" core/models.py && echo "PASS: VALID_CABINS" || echo "FAIL"
grep -q "VALID_AWARD_TYPES" core/models.py && echo "PASS: VALID_AWARD_TYPES" || echo "FAIL"

# Verify core/db.py has required components
grep -q "def create_schema" core/db.py && echo "PASS: create_schema" || echo "FAIL"
grep -q "def upsert_availability" core/db.py && echo "PASS: upsert_availability" || echo "FAIL"
grep -q "ON CONFLICT" core/db.py && echo "PASS: upsert logic" || echo "FAIL"
grep -q "def get_route_summary" core/db.py && echo "PASS: get_route_summary" || echo "FAIL"
grep -q "def record_scrape_job" core/db.py && echo "PASS: record_scrape_job" || echo "FAIL"
grep -q "scrape_jobs" core/db.py && echo "PASS: scrape_jobs table" || echo "FAIL"

# Verify scrape.py has required components
grep -q "hybrid_scraper\|HybridScraper" scrape.py && echo "PASS: imports HybridScraper" || echo "FAIL"
grep -q "cookie_farm\|CookieFarm" scrape.py && echo "PASS: imports CookieFarm" || echo "FAIL"
grep -q "upsert_availability" scrape.py && echo "PASS: calls upsert" || echo "FAIL"
grep -q "validate_solution" scrape.py && echo "PASS: calls validate" || echo "FAIL"
grep -q "\-\-route" scrape.py && echo "PASS: --route CLI arg" || echo "FAIL"
grep -q "\-\-create-schema" scrape.py && echo "PASS: --create-schema CLI arg" || echo "FAIL"

# Verify docker-compose.yml
grep -q "postgres" docker-compose.yml && echo "PASS: PostgreSQL in compose" || echo "FAIL"
grep -q "5432" docker-compose.yml && echo "PASS: port 5432" || echo "FAIL"
grep -q "healthcheck" docker-compose.yml && echo "PASS: healthcheck" || echo "FAIL"

# Verify no credentials in tracked files
grep -rn "DAAAA\|seataero_dev" core/ scrape.py scripts/verify_data.py 2>/dev/null | grep -v "example\|default\|placeholder\|\.env" && echo "WARNING: possible credentials" || echo "PASS: no credentials in code"

# Verify .gitignore updates
grep -q "__pycache__" .gitignore && echo "PASS: __pycache__ excluded" || echo "FAIL"
grep -q "pgdata" .gitignore && echo "PASS: pgdata excluded" || echo "FAIL"
```

## Notes
- **PostgreSQL must be running** before `scrape.py` can work. The `docker compose up -d` command starts it. The script should print a clear error if the DB is unreachable.
- **Scraper credentials** remain in `scripts/experiments/.env` (already gitignored). The new `.env.example` only has the DATABASE_URL template.
- **12 windows per route**: Starting from today, each window is 30 days. Dates: today, today+30, today+60, ..., today+330. This covers the full 337-day United booking window with overlap (360 days total, but the overlap ensures no gaps).
- **Taxes in cents**: The API returns taxes as a float (e.g., 68.51). Store as integer cents (6851) to avoid floating-point comparison issues. This matches the project brief's `taxes_cents` column.
- **Calendar date format**: The API returns dates in `MM/DD/YYYY` format (e.g., "04/01/2026"). The parser converts to `datetime.date` before validation and storage.
- **Idempotent schema creation**: `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` make it safe to run `--create-schema` repeatedly.
- **Step 3 dependency**: Step 3 (48-hour continuous run) will reuse `scrape.py` with a loop. The scrape_jobs table enables resume-from-where-you-left-off if the script crashes mid-sweep.
- **No concurrency**: Step 2 is explicitly single-threaded. One route, one worker, one database connection. Concurrency comes in step 4 (scale to all Canada routes).
