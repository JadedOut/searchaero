# Plan: Intelligent Orchestrator with Worker Status Reporting

## Task Description
Upgrade the orchestrator and burn_in worker to be intelligent: workers exit after completing their assigned routes (instead of looping forever), report live status via status files, and the orchestrator actively monitors workers and kills them if they hit a burn threshold. Workers continue writing directly to the main DB (no temp DBs — see rationale below).

## Objective
1. `burn_in.py` runs in **one-shot mode** by default when given a finite route list — scrapes all assigned routes once, then exits cleanly
2. Each worker writes a **status file** (`logs/worker_{id}_status.json`) updated after every route, containing progress metrics and burn count
3. The orchestrator runs a **monitor thread** that polls status files and terminates any worker that hits the burn threshold (default: 10 burns)
4. The orchestrator waits until **all workers stop** (naturally or by burn-kill), then prints a final summary
5. Workers continue to write directly to the shared PostgreSQL database

## Problem Statement
Currently, `burn_in.py` is a continuous loop — it cycles through routes until `--duration` expires, making it poorly suited for orchestrated one-shot sweeps. The orchestrator is a dumb fire-and-forget launcher: it has no visibility into worker health and no way to kill a struggling worker. If a worker gets cookie-burned repeatedly, it wastes time in backoff loops instead of being put down.

## Solution Approach

### One-shot mode for burn_in.py
Add a `--one-shot` flag to `burn_in.py`. When set, the worker exits after completing one pass through its route list instead of looping. The orchestrator always passes this flag. Exit codes convey outcome: 0 = all routes completed, 1 = killed by burn threshold, 2 = error.

### Status file IPC
Each worker writes a JSON status file after every route completion. The file is atomically written (write to temp, rename) to avoid partial reads. The orchestrator reads these files periodically. This approach is:
- Cross-platform (no Unix signals needed, works on Windows)
- Zero dependencies (just the filesystem)
- Non-invasive (workers just write a file, no sockets or pipes)

Status file schema:
```json
{
  "worker_id": 1,
  "status": "running",
  "routes_total": 8,
  "routes_completed": 3,
  "current_route": "YYZ-LAX",
  "windows_ok": 36,
  "windows_failed": 0,
  "solutions_found": 120,
  "solutions_stored": 115,
  "total_burns": 2,
  "circuit_breaks": 0,
  "updated_at": "2026-04-04T12:34:56"
}
```

### Orchestrator monitor thread
A background thread in the orchestrator polls worker status files every 15 seconds. If any worker's `total_burns >= burn_threshold`, the orchestrator terminates that worker's subprocess. The main thread uses `proc.wait()` per worker and detects when all have stopped.

### Why NOT temp DBs

The user asked whether workers should write to temp databases with orchestrator-level validation before merging. **No — direct writes to the shared DB is the correct design.** Here's why:

1. **Data is already validated at the source.** `models.validate_solution()` validates every field — IATA codes, date ranges, cabin types, award types, mile bounds, tax amounts. Invalid data is rejected before it ever reaches `db.upsert_availability()`. A second validation layer at merge time is redundant.

2. **Upserts are idempotent.** The `ON CONFLICT DO UPDATE` pattern means writing the same data twice is harmless. Two workers scraping overlapping routes (e.g., after a crash/restart) won't corrupt anything — the later write wins with fresher data.

3. **Crash resilience.** With direct writes, every completed route is immediately persisted. If the orchestrator crashes, all data collected so far is safe. With temp DBs, a crash before merge = **total data loss** for that run. This is the killer argument.

4. **Massive complexity for zero benefit.** Temp DBs require: spinning up SQLite files or temp Postgres schemas per worker, duplicating the schema definition, writing merge/copy logic, handling upsert conflicts during merge, cleanup on crashes. The orchestrator would need to understand the data schema, breaking the current clean separation where the orchestrator only manages processes.

5. **PostgreSQL handles concurrency natively.** Two workers writing to the same table with upserts is a solved problem at the database level. That's literally what ACID transactions and conflict resolution clauses are for.

**Bottom line:** The validation boundary is at `validate_solution()`, not at the DB. Adding a temp-DB staging layer moves the safety check to the wrong place and introduces a catastrophic failure mode (data loss on orchestrator crash).

## Relevant Files

- `scripts/burn_in.py` — Add `--one-shot` flag, status file writing, total burn tracking, clean exit on completion
- `scripts/orchestrate.py` — Add monitor thread, burn threshold flag, status-based worker management, wait-for-all-workers logic
- `scripts/experiments/hybrid_scraper.py` — Read-only reference for understanding `consecutive_burns` property (no changes needed)
- `scrape.py` — Read-only reference for understanding `circuit_break` return value (no changes needed)
- `core/db.py` — No changes needed
- `core/models.py` — Read-only reference for understanding validation layer (no changes needed)

### New Files
- None. All changes are to existing files.

## Implementation Phases

### Phase 1: Foundation
Add `--one-shot` flag and status file writing to `burn_in.py`. These are self-contained changes that don't affect the orchestrator yet.

### Phase 2: Core Implementation
Update the orchestrator with a monitor thread, burn threshold management, and proper wait-for-all-workers logic.

### Phase 3: Integration & Polish
Validate end-to-end: orchestrator launches workers in one-shot mode, monitors their status files, kills burned workers, waits for completion, and prints an accurate summary.

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
  - Name: worker-upgrader
  - Role: Add one-shot mode and status file writing to burn_in.py
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: orchestrator-upgrader
  - Role: Add monitor thread, burn threshold, and intelligent wait logic to orchestrate.py
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

### 1. Add --one-shot flag to burn_in.py
- **Task ID**: one-shot-flag
- **Depends On**: none
- **Assigned To**: worker-upgrader
- **Agent Type**: general-purpose
- **Parallel**: false
- Add `--one-shot` argument to `build_parser()`:
  ```python
  parser.add_argument(
      "--one-shot",
      action="store_true",
      help="Exit after completing one pass through all routes (no cycling)",
  )
  ```
- In `_run_burn_in()`, after the inner `for orig, dest in routes:` loop completes (line 477), add a check before the cycle-continuation logic:
  ```python
  # In one-shot mode, exit after completing all routes once
  if args.one_shot:
      print("\nOne-shot mode: all routes completed.")
      break
  ```
  This goes right after `total_errors += cycle_errors` (line 486) and before the duration check on line 489. When `--one-shot` is set, the outer `while True` loop breaks after one full pass.

### 2. Add total burn tracking to burn_in.py
- **Task ID**: burn-tracking
- **Depends On**: one-shot-flag
- **Assigned To**: worker-upgrader
- **Agent Type**: general-purpose
- **Parallel**: false
- Add a `total_burns` counter alongside the existing aggregate counters in `_run_burn_in()` (around line 336):
  ```python
  total_burns = 0
  ```
- After each route, when a circuit break is detected (line 455: `if totals.get("circuit_break"):`), increment:
  ```python
  total_burns += 1
  ```
  Note: `circuit_break` is True when `scraper.consecutive_burns >= 3`, meaning the scraper got blocked 3+ times in a row on that route. Each such event counts as one "burn" from the orchestrator's perspective.
- Add `total_burns` to the JSONL record so it appears in the log:
  ```python
  "total_burns_cumulative": total_burns,
  ```
- Add `--burn-limit` argument to `build_parser()`:
  ```python
  parser.add_argument(
      "--burn-limit",
      type=int,
      default=10,
      help="Exit if total circuit breaks reach this limit (default: 10)",
  )
  ```
- After incrementing `total_burns`, check the limit:
  ```python
  if total_burns >= args.burn_limit:
      print(f"\n  BURN LIMIT REACHED ({total_burns}/{args.burn_limit}) — shutting down worker")
      break  # breaks the inner for loop
  ```
  And after the inner loop, if burn limit is reached, break the outer loop too:
  ```python
  if total_burns >= args.burn_limit:
      break
  ```

### 3. Add status file writing to burn_in.py
- **Task ID**: status-file-writing
- **Depends On**: burn-tracking
- **Assigned To**: worker-upgrader
- **Agent Type**: general-purpose
- **Parallel**: false
- Add a helper function near the top of burn_in.py (after imports):
  ```python
  def _write_status_file(worker_id, status_data, log_dir="logs"):
      """Atomically write worker status to a JSON file.

      Writes to a temp file first, then renames to avoid partial reads
      by the orchestrator.
      """
      if not worker_id:
          return
      status_path = os.path.join(log_dir, f"worker_{worker_id}_status.json")
      tmp_path = status_path + ".tmp"
      try:
          with open(tmp_path, "w") as f:
              json.dump(status_data, f, indent=2)
          # Atomic rename (on Windows, need to remove target first)
          if os.path.exists(status_path):
              os.replace(tmp_path, status_path)
          else:
              os.rename(tmp_path, status_path)
      except OSError:
          pass
  ```
- In `_run_burn_in()`, after each route's JSONL record is written (around line 418), write the status file:
  ```python
  _write_status_file(args.worker_id, {
      "worker_id": args.worker_id,
      "status": "running",
      "routes_total": len(routes),
      "routes_completed": total_routes_scraped + cycle_routes_scraped,
      "current_route": route_label,
      "windows_ok": total_windows_ok + cycle_windows_ok,
      "windows_failed": total_windows_failed + cycle_windows_failed,
      "solutions_found": total_found + cycle_found,
      "solutions_stored": total_stored + cycle_stored,
      "total_burns": total_burns,
      "updated_at": datetime.now().isoformat(),
  })
  ```
  Note: use `total_* + cycle_*` because aggregate counters aren't updated until end of cycle.
- At the end of `_run_burn_in()` (after the summary is printed), write a final status:
  ```python
  exit_reason = "completed"
  if total_burns >= args.burn_limit:
      exit_reason = "burn_limit"
  _write_status_file(args.worker_id, {
      "worker_id": args.worker_id,
      "status": exit_reason,
      "routes_total": len(routes),
      "routes_completed": total_routes_scraped,
      "current_route": None,
      "windows_ok": total_windows_ok,
      "windows_failed": total_windows_failed,
      "solutions_found": total_found,
      "solutions_stored": total_stored,
      "total_burns": total_burns,
      "updated_at": datetime.now().isoformat(),
  })
  ```
- Pass `args` to `_run_burn_in()` — it already receives `args` as the first parameter, so this is already available.

### 4. Update orchestrator to pass --one-shot and --burn-limit
- **Task ID**: orchestrator-flags
- **Depends On**: status-file-writing
- **Assigned To**: orchestrator-upgrader
- **Agent Type**: general-purpose
- **Parallel**: false
- In `build_worker_cmd()`, always append `--one-shot`:
  ```python
  cmd.append("--one-shot")
  ```
- Add `--burn-limit` to orchestrator's `build_parser()`:
  ```python
  parser.add_argument(
      "--burn-limit",
      type=int,
      default=10,
      help="Kill worker after this many circuit breaks (default: 10)",
  )
  ```
- Forward `--burn-limit` in `build_worker_cmd()`:
  ```python
  cmd.extend(["--burn-limit", str(args.burn_limit)])
  ```

### 5. Add monitor thread to orchestrator
- **Task ID**: monitor-thread
- **Depends On**: orchestrator-flags
- **Assigned To**: orchestrator-upgrader
- **Agent Type**: general-purpose
- **Parallel**: false
- Add a `monitor_workers()` function that runs in a background thread:
  ```python
  def monitor_workers(processes, burn_limit, poll_interval=15):
      """Poll worker status files and terminate burned-out workers.

      Runs as a daemon thread. Checks each worker's status file every
      poll_interval seconds. If total_burns >= burn_limit, terminates
      that worker's subprocess.

      Args:
          processes: list of (worker_id, subprocess.Popen) tuples.
          burn_limit: Maximum allowed burns before termination.
          poll_interval: Seconds between status checks.
      """
      while True:
          time.sleep(poll_interval)
          all_done = True
          for worker_id, proc in processes:
              if proc.poll() is not None:
                  continue  # Already exited
              all_done = False
              status_path = os.path.join("logs", f"worker_{worker_id}_status.json")
              try:
                  with open(status_path) as f:
                      status = json.load(f)
              except (OSError, json.JSONDecodeError):
                  continue

              burns = status.get("total_burns", 0)
              routes_done = status.get("routes_completed", 0)
              routes_total = status.get("routes_total", "?")
              with _print_lock:
                  print(f"  [Monitor] W{worker_id}: {routes_done}/{routes_total} routes, {burns} burns")

              if burns >= burn_limit:
                  with _print_lock:
                      print(f"  [Monitor] W{worker_id}: BURN LIMIT ({burns}/{burn_limit}) — terminating")
                  try:
                      proc.terminate()
                  except OSError:
                      pass
          if all_done:
              break
  ```
- Launch the monitor thread in `main()` right after launching all workers and printing the banner:
  ```python
  monitor = threading.Thread(
      target=monitor_workers,
      args=(processes, args.burn_limit),
      daemon=True,
  )
  monitor.start()
  ```

### 6. Update orchestrator wait logic
- **Task ID**: wait-logic
- **Depends On**: monitor-thread
- **Assigned To**: orchestrator-upgrader
- **Agent Type**: general-purpose
- **Parallel**: false
- Replace the current sequential `proc.wait()` loop with a proper wait-for-all that doesn't block on the first worker:
  ```python
  # Wait for all workers to finish (either naturally or via monitor kill)
  try:
      while True:
          all_done = True
          for worker_id, proc in processes:
              if proc.poll() is None:
                  all_done = False
              elif not hasattr(proc, '_announced'):
                  proc._announced = True
                  with _print_lock:
                      print(f"\n>>> Worker {worker_id} exited with code {proc.returncode}")
          if all_done:
              break
          time.sleep(2)
  except KeyboardInterrupt:
      # ... existing Ctrl+C handling ...
  ```
  The current code does `proc.wait()` sequentially — if worker 1 takes 2 hours and worker 2 finishes in 30 min, you don't see worker 2's exit until worker 1 also finishes. The new polling loop detects exits as they happen.
- Print a final status summary by reading each worker's final status file:
  ```python
  # Print final worker status from status files
  for worker_id, proc in processes:
      status_path = os.path.join("logs", f"worker_{worker_id}_status.json")
      try:
          with open(status_path) as f:
              status = json.load(f)
          exit_status = status.get("status", "unknown")
          routes_done = status.get("routes_completed", 0)
          routes_total = status.get("routes_total", 0)
          burns = status.get("total_burns", 0)
          print(f"  Worker {worker_id}: {exit_status} — {routes_done}/{routes_total} routes, {burns} burns")
      except (OSError, json.JSONDecodeError):
          print(f"  Worker {worker_id}: no status file (exit code {proc.returncode})")
  ```

### 7. Clean up status files
- **Task ID**: status-cleanup
- **Depends On**: wait-logic
- **Assigned To**: orchestrator-upgrader
- **Agent Type**: general-purpose
- **Parallel**: false
- In the orchestrator's `finally` block (where temp route files are cleaned up), also clean up status files:
  ```python
  # Cleanup status files
  for i in range(actual_workers):
      worker_id = i + 1
      for suffix in ["_status.json", "_status.json.tmp"]:
          path = os.path.join("logs", f"worker_{worker_id}{suffix}")
          try:
              os.unlink(path)
          except OSError:
              pass
  ```

### 8. Validate all changes
- **Task ID**: validate-all
- **Depends On**: status-cleanup
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Read `burn_in.py` and verify:
  - `--one-shot` flag exists and breaks the outer loop after one cycle
  - `--burn-limit` flag exists with default 10
  - `total_burns` counter is incremented on circuit breaks
  - Worker exits when burn limit reached
  - `_write_status_file()` function exists and uses atomic write pattern
  - Status file written after each route with correct fields
  - Final status file written with exit reason
- Read `orchestrate.py` and verify:
  - `--burn-limit` flag exists and is forwarded to workers
  - `--one-shot` is always passed to workers
  - `monitor_workers()` function exists, runs as daemon thread
  - Monitor reads status files, prints progress, terminates burned workers
  - Wait loop polls all workers instead of blocking sequentially
  - Status files cleaned up in finally block
- Run syntax checks on both files

## Acceptance Criteria
- `burn_in.py --one-shot` exits after completing one pass through routes (exit code 0)
- `burn_in.py` exits when total circuit breaks reach `--burn-limit` (default 10)
- Worker writes `logs/worker_{id}_status.json` after each route with progress/burn metrics
- Orchestrator's monitor thread reads status files every 15s and prints progress
- Orchestrator terminates a worker whose burns >= threshold
- Orchestrator waits for all workers to finish (no sequential blocking)
- Worker stdout still streamed with `[WN]` prefix (existing behavior preserved)
- Status files and temp route files cleaned up on exit
- All existing functionality (JSONL logging, DB writes, session recovery) preserved

## Validation Commands
```bash
# Verify one-shot flag
grep "one.shot" C:/Users/jiami/local_workspace/seataero/scripts/burn_in.py

# Verify burn-limit flag in burn_in.py
grep "burn.limit" C:/Users/jiami/local_workspace/seataero/scripts/burn_in.py

# Verify status file writing
grep "_write_status_file" C:/Users/jiami/local_workspace/seataero/scripts/burn_in.py

# Verify monitor thread in orchestrator
grep "monitor_workers" C:/Users/jiami/local_workspace/seataero/scripts/orchestrate.py

# Verify orchestrator passes --one-shot
grep "one-shot" C:/Users/jiami/local_workspace/seataero/scripts/orchestrate.py

# Verify burn-limit in orchestrator
grep "burn.limit" C:/Users/jiami/local_workspace/seataero/scripts/orchestrate.py

# Syntax checks
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -c "import py_compile; py_compile.compile('scripts/burn_in.py', doraise=True); py_compile.compile('scripts/orchestrate.py', doraise=True); print('OK')"

# CLI help checks
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py --help
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe scripts/orchestrate.py --help
```

## Notes
- A "burn" = a circuit break event (3+ consecutive cookie burns on a single route). This is exposed via `scraper.consecutive_burns >= 3` in `scrape_route()`. We track the cumulative count across all routes in a run.
- The burn limit is a dual mechanism: the worker self-exits (via `--burn-limit`) AND the orchestrator monitors and kills (via the monitor thread). Belt and suspenders — if the worker's self-check misses a burn (e.g., between routes), the orchestrator catches it.
- Status files use `os.replace()` for atomic writes on Windows. This prevents the orchestrator from reading a half-written file.
- The orchestrator does NOT delete status files before launching workers. If stale status files exist from a previous run, the monitor thread will read them but they won't trigger kills unless burns are already above threshold (which they might be from the previous run). To be safe, the orchestrator should delete existing status files at startup before launching workers.
- The `--one-shot` flag doesn't remove the `--duration` limit — both apply. A worker stops on whichever comes first: all routes done (one-shot) or time expired (duration). This is intentional — duration is a safety valve.
