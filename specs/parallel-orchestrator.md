# Plan: Parallel Worker Orchestrator

## Task Description
Build an orchestrator script that launches N parallel burn-in workers, each with its own United account credentials, covering different route slices. The orchestrator queries the database to find unscanned routes, splits them across workers, and manages the parallel processes. This enables scraping all ~2,000 Canada routes from a single machine with 3 workers.

## Objective
1. A master routes file listing all Canada↔US route pairs that United serves
2. An orchestrator script (`scripts/orchestrate.py`) that:
   - Queries the DB for routes not yet scanned today
   - Splits remaining routes across N workers
   - Launches N parallel `burn_in.py` processes with per-worker credentials
   - Handles Ctrl+C graceful shutdown of all workers
   - Prints a summary when all workers finish
3. Per-worker credential support via `.env.worker1`, `.env.worker2`, etc.
4. CookieFarm accepts a custom env file path so each worker loads its own credentials

## Problem Statement
One worker takes ~2.3 min/route. At ~2,000 routes, a single worker needs ~77 hours for a full sweep — over 3 days. Daily data freshness requires parallelism. Running 3 workers from the same machine and IP is safe (looks like a household of 3 people searching flights), but requires per-worker credential isolation and an orchestrator to coordinate route assignments.

## Solution Approach

**Route source**: A master routes file (`routes/canada_us_all.txt`) with all Canada↔US pairs. For now, manually curated — start with the 15 test routes, expand later as we discover which routes United actually serves.

**Work queue**: The orchestrator queries `scrape_jobs` for routes that have a `completed` job today. Any route NOT in that set is "unscanned" and needs work. Routes are split evenly across workers. Each worker gets a temporary routes file with its assigned slice.

**Credentials**: Each worker has its own `.env.workerN` file in `scripts/experiments/`. The orchestrator passes `--env-file` to `burn_in.py`, which passes it to `CookieFarm`. CookieFarm's `_load_credentials()` loads from the specified file instead of the default `.env`.

**Process management**: The orchestrator uses `subprocess.Popen` to launch N `burn_in.py` processes. It traps SIGINT/Ctrl+C and forwards termination to all child processes. When all workers exit, it prints an aggregate summary by reading their JSONL log files.

## Relevant Files

- `scripts/orchestrate.py` — **NEW**. The orchestrator script.
- `scripts/experiments/cookie_farm.py` — Add `env_file` parameter to `__init__` and `_load_credentials()`.
- `scripts/burn_in.py` — Add `--env-file` CLI flag, pass to CookieFarm.
- `core/db.py` — Add `get_scanned_routes_today()` query function.
- `routes/canada_us_all.txt` — **NEW**. Master routes file (start with the 15 test routes, expand later).

### New Files
- `scripts/orchestrate.py` — Orchestrator that splits routes and launches parallel workers
- `routes/canada_us_all.txt` — Master routes file for all Canada↔US pairs
- `scripts/experiments/.env.worker1`, `.env.worker2`, `.env.worker3` — Per-worker credential files (user creates these manually with their own account details)

## Implementation Phases

### Phase 1: Foundation
Add `env_file` support to CookieFarm and burn_in.py. Add `get_scanned_routes_today()` to db.py. Create master routes file.

### Phase 2: Core Implementation
Build the orchestrator: route splitting, subprocess launching, signal handling, summary output.

### Phase 3: Integration & Polish
Validate end-to-end with 3 workers on the 15 test routes. Verify each worker loads its own credentials, gets its own route slice, and writes to the shared DB.

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
  - Name: plumber
  - Role: Add env_file support to CookieFarm, burn_in.py, and add DB query to db.py
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: orchestrator-builder
  - Role: Build the orchestrate.py script
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: validator
  - Role: Verify all changes are correct by reading modified files
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Add env_file support to CookieFarm
- **Task ID**: env-file-cookiefarm
- **Depends On**: none
- **Assigned To**: plumber
- **Agent Type**: general-purpose
- **Parallel**: false
- In `cookie_farm.py`, add `env_file=None` parameter to `__init__()`:
  ```python
  def __init__(self, user_data_dir=None, headless=False, ephemeral=True, env_file=None):
  ```
- Pass `env_file` to `_load_credentials()`:
  ```python
  self._load_credentials(env_file)
  ```
- Update `_load_credentials()` to accept an optional path:
  ```python
  def _load_credentials(self, env_file=None):
      """Load login and Gmail credentials from .env file."""
      script_dir = Path(__file__).parent.resolve()
      load_dotenv(env_file or (script_dir / ".env"))

      self._united_email = os.getenv("UNITED_EMAIL", "").strip()
      self._united_password = os.getenv("UNITED_PASSWORD", "").strip()
      self._gmail_address = os.getenv("GMAIL_ADDRESS", "").strip()
      self._gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
  ```
- IMPORTANT: `load_dotenv` with a specific file sets env vars globally. To avoid workers clobbering each other's env vars, use `load_dotenv(override=True)` so each worker loads its own file cleanly. However since workers are separate processes (not threads), this is already safe — each subprocess has its own env.

### 2. Add --env-file flag to burn_in.py
- **Task ID**: env-file-burnin
- **Depends On**: env-file-cookiefarm
- **Assigned To**: plumber
- **Agent Type**: general-purpose
- **Parallel**: false
- Add `--env-file` argument to `build_parser()`:
  ```python
  parser.add_argument(
      "--env-file",
      type=str,
      default=None,
      help="Path to .env file with United credentials (default: scripts/experiments/.env)",
  )
  ```
- Pass it to CookieFarm constructor:
  ```python
  farm = CookieFarm(user_data_dir=profile_dir, headless=args.headless,
                    ephemeral=not args.persist_profile, env_file=args.env_file)
  ```
- Add to startup banner:
  ```python
  if args.env_file:
      print(f"Credentials:       {args.env_file}")
  ```

### 3. Add get_scanned_routes_today() to db.py
- **Task ID**: db-scanned-routes
- **Depends On**: none
- **Assigned To**: plumber
- **Agent Type**: general-purpose
- **Parallel**: true (can run alongside step 1)
- Add a function to `core/db.py`:
  ```python
  def get_scanned_routes_today(conn: psycopg.Connection) -> set[tuple[str, str]]:
      """Return set of (origin, destination) pairs that have at least one
      completed scrape_job with started_at today (UTC).

      Used by the orchestrator to skip routes already scanned in the current sweep.
      """
      sql = """
          SELECT DISTINCT origin, destination
          FROM scrape_jobs
          WHERE status = 'completed'
            AND started_at >= CURRENT_DATE
      """
      with conn.cursor() as cur:
          cur.execute(sql)
          return {(row[0], row[1]) for row in cur.fetchall()}
  ```

### 4. Create master routes file
- **Task ID**: master-routes
- **Depends On**: none
- **Assigned To**: plumber
- **Agent Type**: general-purpose
- **Parallel**: true (can run alongside steps 1-3)
- Create `routes/canada_us_all.txt` — for now, copy the 15 test routes from `routes/canada_test.txt`. Add a comment header noting this will be expanded later:
  ```
  # All Canada <-> US routes with United service
  # Start with test routes, expand as we discover valid pairs
  # Format: ORIGIN DEST (3-letter IATA codes, space-separated)
  YYZ LAX
  YYZ SFO
  ... (all 15 from canada_test.txt)
  ```

### 5. Build the orchestrator script
- **Task ID**: build-orchestrator
- **Depends On**: env-file-burnin, db-scanned-routes, master-routes
- **Assigned To**: orchestrator-builder
- **Agent Type**: general-purpose
- **Parallel**: false
- Create `scripts/orchestrate.py` with this behavior:

**CLI interface:**
```bash
python scripts/orchestrate.py \
  --routes-file routes/canada_us_all.txt \
  --workers 3 \
  --duration 60 \
  --delay 3.0
```

**Arguments:**
- `--routes-file` (required): Master routes file
- `--workers` (default 3): Number of parallel workers
- `--duration` (default 120): Minutes per worker
- `--delay` (default 3.0): Delay between API calls (passed to burn_in.py)
- `--headless`: Run browsers headless
- `--create-schema`: Create/update DB schema (only first worker needs this)
- `--database-url`: Override DB connection
- `--skip-scanned`: Query DB and skip routes already scanned today (default True)

**Core logic:**
1. Load all routes from the routes file
2. If `--skip-scanned`: connect to DB, call `get_scanned_routes_today()`, remove those from the list
3. Print how many routes total, how many already scanned, how many remaining
4. Split remaining routes evenly across N workers (round-robin or chunk). Write each worker's routes to a temp file (`tempfile.NamedTemporaryFile` with delete=False, suffix `.txt`)
5. For each worker, build a `burn_in.py` command:
   ```python
   cmd = [
       sys.executable, "scripts/burn_in.py",
       "--routes-file", worker_routes_file,
       "--worker-id", str(worker_id),
       "--duration", str(args.duration),
       "--delay", str(args.delay),
       "--env-file", f"scripts/experiments/.env.worker{worker_id}",
   ]
   if args.headless:
       cmd.append("--headless")
   if worker_id == 1 and args.create_schema:
       cmd.append("--create-schema")
   ```
6. Launch all workers with `subprocess.Popen`, store the process objects
7. Print a banner showing each worker's ID, route count, and env file
8. Wait for all processes (`process.wait()` in a loop, or use `concurrent.futures`)
9. On `KeyboardInterrupt` (Ctrl+C): send SIGTERM/terminate to all child processes, wait briefly, then force kill any survivors
10. Clean up temp route files
11. Print summary: read each worker's JSONL log file (pattern: `logs/burn_in_wN_*.jsonl`), aggregate totals

**Important details:**
- The orchestrator does NOT import cookie_farm or hybrid_scraper. It only launches burn_in.py as subprocesses. This keeps each worker fully isolated in its own process with its own env.
- Each worker gets `--worker-id N` which already gives it a unique browser profile directory and log file name.
- The orchestrator should print each worker's stdout with a prefix like `[W1]`, `[W2]`, `[W3]` so the user can distinguish output. Use threading to read from each subprocess's stdout in real-time.

### 6. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-orchestrator
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Read `cookie_farm.py` and verify: `env_file` parameter in `__init__`, passed to `_load_credentials()`, `_load_credentials()` uses the custom path
- Read `burn_in.py` and verify: `--env-file` flag exists, passed to CookieFarm constructor, shown in banner
- Read `core/db.py` and verify: `get_scanned_routes_today()` exists, queries `scrape_jobs` for today's completed jobs
- Read `scripts/orchestrate.py` and verify:
  - Loads routes from file
  - Queries DB for already-scanned routes
  - Splits routes across workers
  - Launches N burn_in.py subprocesses with correct flags
  - Handles Ctrl+C / signal forwarding
  - Cleans up temp files
- Read `routes/canada_us_all.txt` and verify it contains the 15 test routes

## Acceptance Criteria
- `CookieFarm(env_file="path/to/.env.worker1")` loads credentials from that specific file
- `burn_in.py --env-file path` passes the env file to CookieFarm
- `get_scanned_routes_today()` returns set of (origin, dest) pairs scanned today
- `scripts/orchestrate.py --routes-file routes/canada_us_all.txt --workers 3` launches 3 parallel burn_in.py processes
- Each worker gets a different subset of routes (no overlap, no gaps)
- Each worker uses its own `.env.workerN` credentials file
- Ctrl+C in the orchestrator kills all child processes
- Temp route files are cleaned up on exit
- Worker output is prefixed with `[W1]`, `[W2]`, `[W3]` for readability

## Validation Commands
```bash
# Verify env_file parameter in CookieFarm
grep "env_file" C:/Users/jiami/local_workspace/seataero/scripts/experiments/cookie_farm.py

# Verify --env-file flag in burn_in.py
grep "env.file" C:/Users/jiami/local_workspace/seataero/scripts/burn_in.py

# Verify get_scanned_routes_today in db.py
grep "get_scanned_routes_today" C:/Users/jiami/local_workspace/seataero/core/db.py

# Verify orchestrate.py exists and has key components
grep "subprocess.Popen" C:/Users/jiami/local_workspace/seataero/scripts/orchestrate.py
grep "burn_in.py" C:/Users/jiami/local_workspace/seataero/scripts/orchestrate.py
grep "env-file" C:/Users/jiami/local_workspace/seataero/scripts/orchestrate.py

# Verify master routes file
wc -l C:/Users/jiami/local_workspace/seataero/routes/canada_us_all.txt

# Dry run (will fail at login but validates CLI parsing)
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe scripts/orchestrate.py --routes-file routes/canada_us_all.txt --workers 3 --help
```

## Notes
- Workers are separate OS processes (not threads). Each has its own Python interpreter, env vars, and memory. No shared state except the PostgreSQL database (which handles concurrent writes via upserts).
- The user must create `.env.worker1`, `.env.worker2`, `.env.worker3` manually in `scripts/experiments/` with different MileagePlus credentials before running. The orchestrator should check these exist and fail fast with a helpful message if missing.
- For the initial test with 15 routes, using the same account in all 3 env files is fine. For production with 2,000 routes, separate accounts are strongly recommended.
- The `--skip-scanned` feature means the orchestrator is idempotent: if a run crashes halfway, restarting it skips already-completed routes and picks up where it left off.
- The master routes file (`routes/canada_us_all.txt`) starts with the 15 test routes. Expanding to all ~2,000 Canada↔US pairs is a separate task (likely involves querying United's route map or iterating through known airport pairs).
