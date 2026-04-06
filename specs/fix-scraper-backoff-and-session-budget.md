# Plan: Fix Scraper Backoff State Reset and Session Budget Calibration

## Task Description
Fix two related bugs in the hybrid scraper that caused cascading failures during the 60-minute endurance test:

1. **Backoff state carryover**: `_consecutive_burns` and `_backoff_seconds` in `HybridScraper` persist across `stop()`/`start()` cycles and circuit break recoveries, causing stale escalated penalties on subsequent clean routes.
2. **Session budget miscalibration**: The default session budget (40) is reasonable, but the endurance tests used `--session-budget 9999` which disabled proactive resets entirely. The 60-min test showed cookie burns starting at ~60+ cumulative requests across sessions, indicating the budget should be lowered to 30 with a proper reset.

## Objective
After this plan is complete:
- `HybridScraper.start()` resets ALL mutable state including backoff counters
- A new `reset_backoff()` method exists for targeted resets without full stop/start
- The burn-in runner calls `reset_backoff()` after circuit break recovery
- Default session budget is lowered from 40 to 30
- Unit tests cover all reset behaviors
- Existing tests still pass

## Problem Statement
During the 60-minute endurance test (log: `logs/burn_in_20260403_122135.jsonl`), YVR routes triggered cookie burns after ~144 cumulative API calls (84 from a prior 10-min test + 60 from the 60-min test). After the circuit breaker fired and the burn-in runner did `scraper.stop()` / `farm.refresh_cookies()` / `scraper.start()`, the scraper resumed with:

- `_consecutive_burns` still at 3+ (from the prior circuit break), so the NEXT single burn immediately re-triggers the circuit breaker
- `_backoff_seconds` still at 300s (max), so even a single retry waits 5 minutes instead of the base 30s
- `_requests_this_session` correctly reset to 0, but this is insufficient

The `start()` method (hybrid_scraper.py:82-91) only resets `_calls_since_refresh` and `_requests_this_session`. It does NOT reset `_consecutive_burns` or `_backoff_seconds`. This is a straightforward omission — `start()` should restore all mutable state to initial values.

## Solution Approach
1. Fix `HybridScraper.start()` to reset `_consecutive_burns` and `_backoff_seconds` to their initial values
2. Add an explicit `reset_backoff()` method for use cases where a full stop/start is overkill (e.g., between routes in the burn-in runner)
3. Lower `_DEFAULT_SESSION_BUDGET` from 40 to 30 based on empirical evidence (burns at ~60 cumulative requests = safety margin at 30)
4. Update `burn_in.py` circuit break recovery to call `reset_backoff()` after restarting the scraper
5. Add unit tests for the reset behaviors

## Relevant Files
Use these files to complete the task:

- **`scripts/experiments/hybrid_scraper.py`** — Contains `HybridScraper` class. The `start()` method (line 82) needs to reset `_consecutive_burns` and `_backoff_seconds`. New `reset_backoff()` method goes here. `_DEFAULT_SESSION_BUDGET` (line 51) needs to change from 40 to 30.
- **`scripts/burn_in.py`** — Contains the circuit break recovery logic (lines 417-429). After `scraper.start()` on line 425, needs to call `scraper.reset_backoff()` (or rely on `start()` doing it).
- **`scrape.py`** — Contains `scrape_route()` with circuit breaker check at line 111 (`scraper.consecutive_burns >= 3`). No changes needed here — the fix upstream in `start()` handles this.
- **`tests/test_models.py`**, **`tests/test_db.py`**, **`tests/test_parser.py`**, **`tests/test_api.py`** — Existing tests. Must still pass after changes.

### New Files
- **`tests/test_hybrid_scraper.py`** — New unit tests for `HybridScraper` reset behaviors, backoff escalation, and session budget logic.

## Implementation Phases

### Phase 1: Foundation
Fix the `HybridScraper` class to properly reset all mutable state.

### Phase 2: Core Implementation
Update `burn_in.py` to use the improved reset, lower the session budget default, and write tests.

### Phase 3: Integration & Polish
Run all tests, verify no regressions.

## Team Orchestration

- You operate as the team lead and orchestrate the team to execute the plan.
- You're responsible for deploying the right team members with the right context to execute the plan.
- IMPORTANT: You NEVER operate directly on the codebase. You use `Task` and `Task*` tools to deploy team members to do the building, validating, testing, deploying, and other tasks.

### Team Members

- Builder
  - Name: scraper-fixer
  - Role: Implement the backoff reset fix in HybridScraper, update burn_in.py, lower session budget default
  - Agent Type: builder
  - Resume: true

- Builder
  - Name: test-writer
  - Role: Write unit tests for HybridScraper reset behaviors
  - Agent Type: builder
  - Resume: true

- Builder
  - Name: validator
  - Role: Run all tests, verify no regressions, confirm acceptance criteria
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Fix HybridScraper.start() to reset all mutable state
- **Task ID**: fix-start-reset
- **Depends On**: none
- **Assigned To**: scraper-fixer
- **Agent Type**: builder
- **Parallel**: false
- In `scripts/experiments/hybrid_scraper.py`, add two lines to the `start()` method (after line 89) to reset `_consecutive_burns = 0` and `_backoff_seconds = self._BASE_BACKOFF`
- This ensures `stop()`/`start()` cycles always restore fresh state

### 2. Add reset_backoff() method to HybridScraper
- **Task ID**: add-reset-backoff
- **Depends On**: fix-start-reset
- **Assigned To**: scraper-fixer
- **Agent Type**: builder
- **Parallel**: false
- Add a public `reset_backoff()` method to `HybridScraper` that resets `_consecutive_burns = 0` and `_backoff_seconds = self._BASE_BACKOFF`
- This is for cases where we want to clear backoff state without a full stop/start cycle (e.g., between routes after circuit break recovery)

### 3. Lower default session budget from 40 to 30
- **Task ID**: lower-session-budget
- **Depends On**: none
- **Assigned To**: scraper-fixer
- **Agent Type**: builder
- **Parallel**: true (can run alongside fix-start-reset)
- In `scripts/experiments/hybrid_scraper.py`, change `_DEFAULT_SESSION_BUDGET = 40` to `_DEFAULT_SESSION_BUDGET = 30`
- In `scripts/experiments/hybrid_scraper.py`, change the `__init__` default `session_budget: int = 40` to `session_budget: int = 30`
- In `scripts/burn_in.py`, change the `--session-budget` argparse default from `40` to `30`

### 4. Update burn_in.py circuit break recovery
- **Task ID**: update-burn-in-recovery
- **Depends On**: add-reset-backoff
- **Assigned To**: scraper-fixer
- **Agent Type**: builder
- **Parallel**: false
- In `scripts/burn_in.py` lines 417-429 (circuit break handling), after `scraper.start()` on line 425, the backoff state is now reset by `start()` itself, so no additional call is needed. However, add a log line confirming the reset: `print("  Backoff state reset (consecutive_burns=0, backoff=30s)")`
- Also in the cycle-end session recovery (lines 456-464), after `scraper.start()` on line 463, add a similar confirmation log

### 5. Write unit tests for HybridScraper reset behaviors
- **Task ID**: write-reset-tests
- **Depends On**: add-reset-backoff, lower-session-budget
- **Assigned To**: test-writer
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/test_hybrid_scraper.py` with tests:
  - `test_start_resets_backoff_state`: Manually set `_consecutive_burns` and `_backoff_seconds` to non-default values, call `start()`, assert they are reset. Use a mock `CookieFarm` that returns dummy cookie/token strings.
  - `test_reset_backoff_clears_counters`: Call `reset_backoff()`, assert `_consecutive_burns == 0` and `_backoff_seconds == 30.0`
  - `test_start_resets_request_counters`: Verify `_calls_since_refresh` and `_requests_this_session` are also reset (existing behavior, regression test)
  - `test_default_session_budget_is_30`: Instantiate with default args, assert `_session_budget == 30`
- Use `unittest.mock.MagicMock` for the `CookieFarm` dependency — mock `get_cookies()` to return `"a=b"`, `get_bearer_token()` to return `"token123"`, `refresh_cookies()` to return `None`

### 6. Run all tests and validate
- **Task ID**: validate-all
- **Depends On**: write-reset-tests, update-burn-in-recovery
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run: `C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v`
- Verify all existing tests still pass
- Verify new tests in `test_hybrid_scraper.py` pass
- Read `scripts/experiments/hybrid_scraper.py` and confirm `start()` resets `_consecutive_burns` and `_backoff_seconds`
- Read `scripts/burn_in.py` and confirm circuit break recovery has reset confirmation logging
- Confirm `_DEFAULT_SESSION_BUDGET` is 30

## Acceptance Criteria
- [ ] `HybridScraper.start()` resets `_consecutive_burns` to 0 and `_backoff_seconds` to `_BASE_BACKOFF` (30.0)
- [ ] `HybridScraper.reset_backoff()` method exists and resets both counters
- [ ] `_DEFAULT_SESSION_BUDGET` is 30 (was 40)
- [ ] `__init__` default for `session_budget` parameter is 30 (was 40)
- [ ] `burn_in.py` `--session-budget` argparse default is 30 (was 40)
- [ ] `burn_in.py` circuit break recovery logs the backoff reset
- [ ] New `tests/test_hybrid_scraper.py` exists with 4+ tests covering reset behaviors
- [ ] All existing tests pass (`test_models.py`, `test_db.py`, `test_parser.py`, `test_api.py`)
- [ ] All new tests pass

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# Run all tests
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v

# Verify session budget default changed
grep -n "_DEFAULT_SESSION_BUDGET" scripts/experiments/hybrid_scraper.py
grep -n "session_budget" scripts/experiments/hybrid_scraper.py | head -5
grep -n "session-budget" scripts/burn_in.py

# Verify start() resets backoff
grep -A5 "def start" scripts/experiments/hybrid_scraper.py

# Verify reset_backoff() exists
grep -A5 "def reset_backoff" scripts/experiments/hybrid_scraper.py

# Verify circuit break recovery logging
grep -A3 "Session fully reset" scripts/burn_in.py
```

## Notes
- The `scrape_route()` circuit breaker in `scrape.py` (line 111) checks `scraper.consecutive_burns >= 3`. This does NOT need changing — once `start()` properly resets the counter, the circuit breaker will behave correctly after recovery.
- The session budget of 30 is conservative. The endurance test showed burns at ~60+ cumulative requests. With proactive resets every 30 requests (including a full session recreate + cookie refresh), each "session window" stays well under the threshold. This can be tuned later with a calibration test.
- After this fix, the next step is a session-budget-calibration burn-in test (60 min, `--session-budget 30`, cold start) to validate the fix empirically.
