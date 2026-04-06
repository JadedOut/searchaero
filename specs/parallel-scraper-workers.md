# Plan: Parallel Scraper Workers (3 Concurrent Processes)

## Task Description
Add parallel execution support to the seataero scraper so that 3 independent worker processes can scrape different route chunks simultaneously on a single machine. Each worker gets its own Playwright browser profile, cookie farm, and curl_cffi session, all sharing the same PostgreSQL database. A wrapper script orchestrates launching, monitoring, and aggregating results from all workers.

## Objective
Reduce total scrape time by ~3x (from ~44h to ~15h for a full route sweep) by running 3 workers concurrently from one machine/IP, with zero code changes to the core scraping logic.

## Problem Statement
The current `burn_in.py` is single-threaded: one browser, one cookie farm, one scraper, processing routes serially. With the aggressive timing settings (delay=1s, refresh_interval=3), a single worker takes ~75s per route. For large route lists, this is too slow for daily refreshes. The machine's CPU and network are idle 90% of the time (I/O-bound), so running multiple workers in parallel is the natural scaling path.

The key constraint: each worker needs its own Playwright browser profile directory (Chrome locks the profile). The CookieFarm already accepts `user_data_dir` as a constructor parameter, but `burn_in.py` doesn't expose it as a CLI flag. A wrapper script is needed to split routes, launch workers, and aggregate results.

## Solution Approach
1. Add `--worker-id` flag to `burn_in.py` that sets a unique browser profile path (`scripts/experiments/.browser-profile-{worker_id}/`)
2. Create `scripts/parallel_burn_in.py` that splits a routes file into N chunks and launches N `burn_in.py` subprocesses
3. Each worker writes to its own JSONL log file (already handled — timestamped filenames)
4. The wrapper monitors all workers, handles Ctrl+C graceful shutdown, and prints an aggregated summary
5. First-time setup: a `--setup-profiles` mode that launches each browser profile sequentially for login/MFA before running the parallel scrape

## Relevant Files
Use these files to complete the task:

- `scripts/burn_in.py` — Main runner. Needs `--worker-id` flag that passes `user_data_dir` to CookieFarm. This is the only change to existing code.
- `scripts/experiments/cookie_farm.py` — Already accepts `user_data_dir` in constructor (line 37). No changes needed.
- `scripts/experiments/hybrid_scraper.py` — No changes needed. Each worker creates its own instance.
- `core/db.py` — Uses `ON CONFLICT` for upserts (line 116). Safe for concurrent writes. No changes needed.
- `scripts/analyze_burn_in.py` — Already accepts multiple JSONL files via glob. No changes needed.
- `routes/canada_test.txt` — Test routes for validation.

### New Files
- `scripts/parallel_burn_in.py` — Wrapper script that splits routes, launches N workers, monitors progress, aggregates results.

## Implementation Phases

### Phase 1: Foundation
Add `--worker-id` to `burn_in.py`. This is a 2-line change:
1. Add the CLI argument (string, default None)
2. Pass it to `CookieFarm(user_data_dir=...)` if set

When `--worker-id` is provided, the browser profile path becomes:
```
scripts/experiments/.browser-profile-{worker_id}/
```

When not provided, behavior is unchanged (uses default `.browser-profile/`).

The `--create-schema` flag should only be used by one worker (or run once before launching). The wrapper handles this.

### Phase 2: Core Implementation
Create `scripts/parallel_burn_in.py` with these responsibilities:

**Route splitting:**
```python
def split_routes(routes_file: str, n_workers: int) -> list[list[str]]:
    """Split routes round-robin across N workers.

    Round-robin (not contiguous chunks) ensures each worker gets a mix
    of airports, spreading the load across different Akamai origin
    fingerprints.
    """
```
Round-robin is better than contiguous chunks because it avoids one worker hitting the same origin airport repeatedly (e.g., all YYZ routes), which could look suspicious to Akamai.

Each chunk is written to a temp file: `routes/_chunk_{worker_id}.txt`

**Worker launch:**
```python
def launch_worker(worker_id: int, routes_chunk_file: str, args) -> subprocess.Popen:
    """Launch a burn_in.py subprocess with worker-specific settings."""
    cmd = [
        sys.executable, "scripts/burn_in.py",
        "--routes-file", routes_chunk_file,
        "--worker-id", str(worker_id),
        "--duration", str(args.duration),
        "--delay", str(args.delay),
        "--refresh-interval", str(args.refresh_interval),
        "--session-budget", str(args.session_budget),
        "--route-delay", str(args.route_delay),
    ]
    # Only first worker creates schema
    if worker_id == 1:
        cmd.append("--create-schema")
    return subprocess.Popen(cmd, ...)
```

**Staggered start:** Workers launch with a 10-second stagger between each to avoid all 3 browsers navigating to united.com simultaneously (Akamai rate-limiting risk on login).

**Monitoring:** The wrapper polls worker processes and tails their JSONL logs to show a live dashboard:
```
Worker 1: ██████████░░  5/8 routes  | 60/96 windows OK | 0 errors
Worker 2: ████████░░░░  4/7 routes  | 48/84 windows OK | 0 errors
Worker 3: ████████░░░░  4/7 routes  | 48/84 windows OK | 0 errors
```

**Graceful shutdown:** On Ctrl+C, send SIGINT to all worker subprocesses (they already handle KeyboardInterrupt) and wait for them to finish their current route.

**Profile setup mode:** `--setup-profiles` launches each browser profile one at a time (not parallel) to allow MFA login:
```bash
python scripts/parallel_burn_in.py --setup-profiles --workers 3
# Opens browser 1 → waits for login → closes
# Opens browser 2 → waits for login → closes
# Opens browser 3 → waits for login → closes
```
This only needs to happen once. After that, "Already logged in" kicks in for each profile.

### Phase 3: Integration & Polish
**Aggregated summary:** After all workers finish, parse all JSONL logs from the run and print a combined report:
```
============================================================
Parallel Burn-In Complete (3 workers)
============================================================
  Total routes scraped:  15
  Total windows:         180/180 (100.0%)
  Solutions found:       18,500
  Total errors:          0
  Wall-clock time:       5.2 minutes
  Throughput:            2.88 routes/min (vs 0.80 single-threaded)
  Log files:
    Worker 1: logs/burn_in_20260403_050000.jsonl (5 routes)
    Worker 2: logs/burn_in_20260403_050010.jsonl (5 routes)
    Worker 3: logs/burn_in_20260403_050020.jsonl (5 routes)
============================================================
```

**Cleanup:** Remove temp route chunk files after the run completes.

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
  - Name: burn-in-plumber
  - Role: Add --worker-id flag to burn_in.py and create the parallel_burn_in.py wrapper script
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: profile-bootstrapper
  - Role: Implement --setup-profiles mode and test that multiple browser profiles can log in
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: validator
  - Role: Verify the parallel runner works with 3 workers on the test routes, validate results, run existing tests
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Add --worker-id Flag to burn_in.py
- **Task ID**: add-worker-id
- **Depends On**: none
- **Assigned To**: burn-in-plumber
- **Agent Type**: general-purpose
- **Parallel**: false
- Add `--worker-id` CLI argument to `build_parser()` in `scripts/burn_in.py` (type=str, default=None, help="Worker ID for parallel runs — sets a unique browser profile")
- In `main()`, when `args.worker_id` is set, compute `profile_dir = os.path.join(os.path.dirname(__file__), "experiments", f".browser-profile-{args.worker_id}")` and pass it to `CookieFarm(user_data_dir=profile_dir, headless=args.headless)`
- When `args.worker_id` is not set, behavior is unchanged (use default profile)
- Include worker_id in the banner output and in the JSONL log filename: `burn_in_w{worker_id}_{timestamp}.jsonl` (makes it easy to identify which worker produced which log)
- Verify the change with a quick read of the file

### 2. Create parallel_burn_in.py Wrapper Script
- **Task ID**: create-wrapper
- **Depends On**: add-worker-id
- **Assigned To**: burn-in-plumber
- **Agent Type**: general-purpose
- **Parallel**: false
- Create `scripts/parallel_burn_in.py` with the following components:
  - **CLI arguments**: `--routes-file` (required), `--workers` (default 3), `--duration`, `--delay` (default 1), `--refresh-interval` (default 3), `--session-budget` (default 9999), `--route-delay` (default 5), `--setup-profiles`, `--create-schema`, `--stagger` (default 10, seconds between worker launches)
  - **`split_routes_round_robin()`**: Read routes file, distribute round-robin across N workers, write temp chunk files to `routes/_chunk_{i}.txt`
  - **`launch_worker()`**: Build the `burn_in.py` command line with `--worker-id`, spawn as `subprocess.Popen`, redirect stdout/stderr to per-worker log files in `logs/worker_{id}_stdout.log`
  - **`monitor_workers()`**: Poll worker processes every 5 seconds, tail their JSONL logs, print a live status line for each worker showing routes completed and errors
  - **`aggregate_results()`**: After all workers exit, parse all JSONL logs from this run, print combined summary table
  - **Ctrl+C handler**: On SIGINT, send SIGINT to all worker processes, wait up to 30s for graceful shutdown, then SIGTERM
  - **Cleanup**: Remove temp `routes/_chunk_*.txt` files after run completes
- Only the first worker should use `--create-schema` (if the user passed `--create-schema`)
- Workers launch with `--stagger` second gaps (default 10s) to avoid simultaneous browser startups

### 3. Implement Profile Setup Mode
- **Task ID**: setup-profiles
- **Depends On**: add-worker-id
- **Assigned To**: profile-bootstrapper
- **Agent Type**: general-purpose
- **Parallel**: true (can run alongside create-wrapper)
- Add `--setup-profiles` mode to `scripts/parallel_burn_in.py` (or as a standalone section — coordinate with burn-in-plumber)
- When `--setup-profiles` is passed:
  1. For each worker_id in range(1, workers+1):
     - Compute profile path `scripts/experiments/.browser-profile-{worker_id}/`
     - Create a CookieFarm with that profile path
     - Call `farm.start()` and `farm.ensure_logged_in()`
     - Print status: "Worker {id} profile ready"
     - Call `farm.stop()`
  2. Do NOT launch parallel scrape — just set up profiles and exit
  3. Print: "All profiles set up. Run without --setup-profiles to start scraping."
- This is sequential (one browser at a time) because MFA emails arrive one at a time

### 4. Test Parallel Run with 3 Workers
- **Task ID**: test-parallel
- **Depends On**: create-wrapper, setup-profiles
- **Assigned To**: burn-in-plumber
- **Agent Type**: general-purpose
- **Parallel**: false
- Ensure all 3 browser profiles are logged in (run setup-profiles if needed, or verify existing profiles)
- Run a short parallel test (3 minutes):
  ```bash
  cd C:/Users/jiami/local_workspace/seataero
  scripts/experiments/.venv/Scripts/python.exe scripts/parallel_burn_in.py \
    --routes-file routes/canada_test.txt \
    --workers 3 --duration 3 --delay 1 --refresh-interval 3 \
    --session-budget 9999 --route-delay 5 --create-schema
  ```
- Verify: all 3 workers start, each gets ~5 routes, no crashes, no profile lock conflicts
- Verify: aggregated summary shows combined results from all workers
- Verify: JSONL log files contain worker_id in filename and valid entries

### 5. Validate All Changes
- **Task ID**: validate-all
- **Depends On**: test-parallel
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `burn_in.py` has `--worker-id` flag and passes it to CookieFarm correctly
- Verify `scripts/parallel_burn_in.py` exists with all required components: route splitting, worker launch, monitoring, aggregation, Ctrl+C handling, cleanup
- Verify running without `--worker-id` still uses the default profile (backwards compatible)
- Run existing tests: `C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v`
- Confirm all tests pass
- Check that no other production files were modified

## Acceptance Criteria
- `burn_in.py` accepts `--worker-id` and creates a unique browser profile per worker
- `burn_in.py` without `--worker-id` behaves identically to before (backwards compatible)
- `scripts/parallel_burn_in.py` exists and can launch N workers in parallel
- Route splitting is round-robin (not contiguous) to spread load across airports
- Workers launch with configurable stagger delay
- `--setup-profiles` mode sets up browser profiles sequentially for MFA login
- Ctrl+C gracefully shuts down all workers
- Aggregated summary prints combined results after all workers finish
- Temp route chunk files are cleaned up after the run
- Existing tests still pass
- No changes to `cookie_farm.py`, `hybrid_scraper.py`, `scrape.py`, or `core/db.py`

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# 1. Verify --worker-id flag exists
cd C:/Users/jiami/local_workspace/seataero
scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py --help | grep worker-id

# 2. Verify parallel_burn_in.py exists
test -f scripts/parallel_burn_in.py && echo "EXISTS" || echo "MISSING"

# 3. Verify --setup-profiles flag exists
scripts/experiments/.venv/Scripts/python.exe scripts/parallel_burn_in.py --help | grep setup-profiles

# 4. Run existing tests
scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v

# 5. Verify no changes to core files (should show no diff)
git diff scripts/experiments/cookie_farm.py  # only the 2s wait change from earlier
git diff scripts/experiments/hybrid_scraper.py  # should be clean
git diff core/db.py  # should be clean
```

## Notes
- **Browser profile disk usage:** Each Chrome profile is ~50-100MB. 3 profiles = ~300MB. Negligible.
- **MFA rate limiting:** United may throttle MFA emails if you request 3 in rapid succession during setup. The sequential setup mode handles this — wait for each login to complete before starting the next.
- **Session expiry:** If a worker's session expires mid-run, it already handles recovery via `farm.ensure_logged_in()` in the existing burn_in loop. No special parallel handling needed.
- **Database contention:** PostgreSQL handles concurrent inserts from 3 connections easily. The `ON CONFLICT` clause in `db.py` ensures no duplicate rows. Each connection uses autocommit=False with per-route commits, so lock contention is minimal.
- **Log file separation:** Each worker writes to its own JSONL file (timestamped + worker_id). The existing `analyze_burn_in.py` already supports multiple file inputs via glob, so analysis works out of the box.
- **Scaling beyond 3:** The wrapper supports any `--workers N`, but recommend staying at 2-3 for a single residential IP. Beyond that, add proxies or VPS.
- **The cookie_farm.py 2s wait change** from the earlier experiment is already in place and should be kept — it saves ~16s per route per worker.
