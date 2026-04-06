# Plan: Phase 1 Feature Expansion & Pipeline Fixes

## Task Description
Two things in one plan: (1) Fix the pipeline issues discovered in the ultra-low delay experiment (backoff state carryover, session budget calibration), run a 5-hour endurance test to validate the fixes, and (2) add the missing features to the Phase 1 plan in `docs/project-brief.md` — specifically new steps inserted after Step 6 (scale to all Canada routes) and before Step 7 (Telegram alerts). The missing features come from a gap analysis against the real Seats.aero product.

## Objective
1. A scraper pipeline that runs reliably at 0.5s delay with proper session budget management and clean backoff state between cycles
2. A validated 5-hour endurance test proving the fixes work
3. An updated project brief with concrete new Phase 1 steps for: calendar view, date range search, sorting, booking deeplinks, price history tracking, and data export

## Problem Statement
**Pipeline issues**: The 60-minute endurance test revealed two bugs: (a) `_backoff_seconds` and `_consecutive_burns` in `HybridScraper` are never reset between cycles or after full session resets in `burn_in.py`, causing stale exponential backoff to penalize clean routes after recovery; (b) `--session-budget 9999` effectively disables session resets, allowing cumulative IP-level request counting to trigger Akamai cookie burns after ~140+ requests. A session budget of ~30 with proactive resets should prevent this.

**Missing features**: Comparing our tool against Seats.aero's feature set, the most impactful gaps that are achievable within Phase 1 (United-only, Canada routes) are: calendar view, date range search, result sorting, booking deeplinks to united.com, price history/trend tracking, and CSV data export. These should be added as Phase 1 steps between "scale to all Canada routes" and "alerts + Telegram."

## Solution Approach

### Pipeline Fixes (Code Changes)

**Fix 1: Backoff state reset** — Add a `reset_backoff()` method to `HybridScraper` that resets `_consecutive_burns` to 0 and `_backoff_seconds` to `_BASE_BACKOFF`. Call it from `burn_in.py` in two places: (a) after the 5-minute circuit breaker reset (line ~427), and (b) between cycles (after cycle summary, before inter-cycle delay).

**Fix 2: Session budget default** — Change `--session-budget` default from 40 to 30 in `burn_in.py` argparse. This is more conservative — at 0.5s delay, 30 requests takes ~45 seconds, then a clean session reset prevents cumulative counting. The 10-minute session pause (`_SESSION_PAUSE_SECONDS`) should also be reduced to 60 seconds — at 0.5s delay, 10 minutes of idle is wasteful; 60 seconds is enough for Akamai to "forget" the session.

**Fix 3: Session pause duration** — Make `_SESSION_PAUSE_SECONDS` configurable via a new `--session-pause` CLI flag (default 60s). This replaces the hardcoded 600s (10 minutes) that was designed for conservative 12s-delay settings.

### Endurance Test (5 hours)
Run with: `--delay 0.5 --refresh-interval 2 --session-budget 30 --session-pause 60 --route-delay 10 --duration 300`

Expected: at 0.5s delay + refresh every 2 calls + session reset every 30 requests + 60s pause + 10s route delay, each route takes ~90s scrape + 10s route delay = ~100s. With 15 routes per cycle + 60s session pause every ~2.5 routes, a cycle takes ~25-30 minutes. In 5 hours: ~10 full cycles, ~150 route scrapes, ~1,800 API calls.

### Project Brief Updates
Insert new steps between current Step 6 and Step 7. Renumber accordingly:

- **Step 6a: Calendar view** — Month-view grid showing price heatmap per cabin class. Green=saver available, yellow=standard only, gray=no availability. Click a date to see detail modal.
- **Step 6b: Date range search + sorting** — Date picker in search form (start/end date filter). Column sorting in results table (by date, miles, cabin). Server-side support for date range filtering.
- **Step 6c: Booking deeplinks** — Each result row gets a "Book on United" link that opens United's award search pre-filled with origin, destination, and date.
- **Step 6d: Price history** — New `availability_history` table that snapshots price changes over time instead of just upserting latest. Line chart showing miles cost trend per route/cabin over days/weeks.
- **Step 6e: Data export** — CSV download button on search results page. Export current filtered results.

## Relevant Files
Use these files to complete the task:

- `scripts/experiments/hybrid_scraper.py` — Add `reset_backoff()` method, make `_SESSION_PAUSE_SECONDS` configurable
- `scripts/burn_in.py` — Call `reset_backoff()` after circuit breaker recovery and between cycles; add `--session-pause` flag; change `--session-budget` default to 30
- `docs/project-brief.md` — Insert new Phase 1 steps (6a-6e) between Steps 6 and 7, renumber Step 7→8, Step 8→9
- `docs/findings/ultra-low-delay-experiment.md` — Append 5-hour endurance test results section

### New Files
- None (all changes are to existing files)

## Implementation Phases

### Phase 1: Foundation (Pipeline Fixes)
1. Add `reset_backoff()` method to `HybridScraper`
2. Make `_SESSION_PAUSE_SECONDS` a constructor parameter with CLI flag
3. Change `--session-budget` default from 40 to 30
4. Call `reset_backoff()` in `burn_in.py` after circuit breaker recovery and between cycles
5. Run existing tests to verify no regressions

### Phase 2: Core Implementation (5-Hour Endurance Test)
1. Run the 5-hour burn-in with fixed parameters from a cold start (no scraping for 30+ minutes prior)
2. Monitor for: cookie burns, circuit breaks, session budget hits, backoff resets
3. Compare results against baseline and previous experiments
4. Document results in `docs/findings/ultra-low-delay-experiment.md`

### Phase 3: Integration & Polish (Project Brief Update)
1. Read current Phase 1 plan steps in `docs/project-brief.md`
2. Insert new steps 6a-6e between Step 6 and Step 7
3. Renumber existing Step 7 (Alerts/Telegram) → Step 8, Step 8 (US expansion) → Step 9
4. Write concise descriptions for each new step matching the existing format (What | Why | Time)
5. Update "What we give away for free" section to mention calendar view and price history

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
  - Name: pipeline-fixer
  - Role: Implement the three pipeline fixes (reset_backoff method, session-pause flag, session-budget default) and ensure tests pass
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: endurance-runner
  - Role: Run the 5-hour endurance test with fixed parameters, capture results, analyze and document findings
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: brief-updater
  - Role: Update docs/project-brief.md with new Phase 1 steps (6a-6e) and renumber existing steps
  - Agent Type: general-purpose
  - Resume: false

- Builder
  - Name: validator
  - Role: Verify pipeline fixes are correct, tests pass, endurance test log exists, project brief has new steps
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Implement Pipeline Fixes
- **Task ID**: pipeline-fixes
- **Depends On**: none
- **Assigned To**: pipeline-fixer
- **Agent Type**: general-purpose
- **Parallel**: false
- Add a `reset_backoff()` method to `HybridScraper` in `scripts/experiments/hybrid_scraper.py`:
  ```python
  def reset_backoff(self):
      """Reset backoff state (call between cycles or after full session resets)."""
      self._consecutive_burns = 0
      self._backoff_seconds = self._BASE_BACKOFF
  ```
- Make `_SESSION_PAUSE_SECONDS` a constructor parameter instead of a class constant. Add `session_pause: int = 60` to `__init__`. Default to 60 seconds (down from hardcoded 600).
- In `burn_in.py`:
  - Add `--session-pause` argparse flag (default 60, help="Seconds to pause on session budget reset")
  - Pass `session_pause=args.session_pause` to HybridScraper constructor
  - Change `--session-budget` default from 40 to 30
  - After the circuit breaker full reset (line ~427, after `scraper.start()`), add `scraper.reset_backoff()`
  - Between cycles (after cycle summary print, before inter-cycle delay), add `scraper.reset_backoff()`
- Run tests: `C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v`

### 2. Run 5-Hour Endurance Test
- **Task ID**: endurance-test
- **Depends On**: pipeline-fixes
- **Assigned To**: endurance-runner
- **Agent Type**: general-purpose
- **Parallel**: false
- Verify PostgreSQL is running (`docker compose ps`)
- Run the burn-in with a 6-hour timeout (21600000ms):
  ```bash
  cd C:/Users/jiami/local_workspace/seataero
  C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py \
    --routes-file routes/canada_test.txt \
    --duration 300 \
    --delay 0.5 \
    --refresh-interval 2 \
    --session-budget 30 \
    --session-pause 60 \
    --route-delay 10 \
    --create-schema
  ```
- Capture the full summary output and JSONL log file path
- If it fails to start, troubleshoot and retry once

### 3. Analyze Endurance Results
- **Task ID**: analyze-endurance
- **Depends On**: endurance-test
- **Assigned To**: endurance-runner
- **Agent Type**: general-purpose
- **Parallel**: false
- Read the experiment JSONL log
- Run `scripts/analyze_burn_in.py` on the new log
- Calculate: success rate, cookie burns, circuit breaks, HTTP 429s, avg solutions/route, session budget hits, avg route duration
- Compare per-cycle metrics: check if success rate degrades over time (early cycles vs late cycles)
- Compare against all three prior runs (baseline 12s, 10-min 0.5s, 60-min 0.5s)
- Determine outcome: success (>95% over full 5 hours), partial (80-95%), failure (<80%)

### 4. Document Endurance Results
- **Task ID**: document-endurance
- **Depends On**: analyze-endurance
- **Assigned To**: endurance-runner
- **Agent Type**: general-purpose
- **Parallel**: false
- Append a new "5-Hour Endurance Test" section to `docs/findings/ultra-low-delay-experiment.md` with:
  - Parameters (including new session-budget=30, session-pause=60)
  - Full metrics table (4-way comparison: baseline, 10-min, 60-min, 5-hour)
  - Per-cycle breakdown (success rate per cycle to show degradation trend)
  - Error analysis (types, routes affected)
  - Session budget reset count and behavior
  - Conclusion and updated recommendation

### 5. Update Project Brief
- **Task ID**: update-brief
- **Depends On**: analyze-endurance
- **Assigned To**: brief-updater
- **Agent Type**: general-purpose
- **Parallel**: true (can run alongside document-endurance)
- Read `docs/project-brief.md` lines 365-381 (Phase 1 plan table)
- Insert new rows between Step 6 and current Step 7, using the same table format:
  - **6a** | Calendar view: month grid with price heatmap per cabin. Green=saver, yellow=standard, gray=none. Click date for detail modal. | Most-requested visual feature gap vs Seats.aero. Makes browsing 337 days of data practical. | 3-5 days |
  - **6b** | Date range search + result sorting: date picker in search form, column sorting (date, miles, cabin) in results table. Server-side date range filtering in /api/search. | Currently shows all 337 days unsorted. Users need to narrow by travel window and compare prices. | 2-3 days |
  - **6c** | Booking deeplinks: "Book on United" button per result row. Opens united.com/ual/en/us/flight-search/book-a-flight/results/awd?f=ORIGIN&t=DEST&d=DATE&tt=1&at=1&sc=7&px=1&taxng=1&newHP=True&clm=7&st=bestmatches&fare498 pre-filled with route and date. | Zero-friction path from finding availability to booking it. Core UX gap — without this, users must manually re-search on united.com. | 1 day |
  - **6d** | Price history tracking: new `availability_history` table that logs price snapshots over time (INSERT instead of just upsert). Line chart in UI showing miles cost trend per route/cabin. | Seats.aero Pro charges for this. Lets users see "is this price good?" by showing historical range. Enables future price-drop alerts. | 3-5 days |
  - **6e** | CSV data export: download button on search results page. Exports currently filtered results as CSV (date, cabin, miles, taxes, direct, last_seen). | Power users want data in spreadsheets. Trivial to build, high utility. | 1 day |
- Renumber current Step 7 (Alerts/Telegram) → Step 7, Step 8 (US expansion) → Step 8 (keep same numbers since 6a-6e are sub-steps of 6)
- Also update Step 6 to reference the validated timing settings from the endurance test (if successful)

### 6. Validate Everything
- **Task ID**: validate-all
- **Depends On**: pipeline-fixes, endurance-test, analyze-endurance, document-endurance, update-brief
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `HybridScraper` has `reset_backoff()` method
- Verify `burn_in.py` has `--session-pause` flag and calls `reset_backoff()` in the right places
- Verify `--session-budget` default is 30 in `burn_in.py` argparse
- Run tests: `C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v`
- Verify endurance test JSONL log exists in `logs/` and has entries
- Verify `docs/findings/ultra-low-delay-experiment.md` has a "5-Hour Endurance Test" section
- Verify `docs/project-brief.md` has steps 6a-6e in the Phase 1 plan table
- Verify no other production files were modified beyond the specified changes

## Acceptance Criteria
- `HybridScraper.reset_backoff()` method exists and is called from `burn_in.py` after circuit breaker recovery and between cycles
- `--session-pause` CLI flag exists in `burn_in.py` with default 60
- `--session-budget` default is 30 in `burn_in.py`
- 5-hour endurance test completes and JSONL log exists in `logs/`
- `docs/findings/ultra-low-delay-experiment.md` contains 5-hour endurance test results with 4-way comparison table
- `docs/project-brief.md` Phase 1 plan contains steps 6a through 6e (calendar view, date range + sorting, booking deeplinks, price history, data export)
- All 77+ existing tests pass

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# 1. Verify reset_backoff method exists
grep "def reset_backoff" C:/Users/jiami/local_workspace/seataero/scripts/experiments/hybrid_scraper.py && echo "OK" || echo "MISSING"

# 2. Verify session-pause flag exists
grep "session.pause" C:/Users/jiami/local_workspace/seataero/scripts/burn_in.py && echo "OK" || echo "MISSING"

# 3. Verify session-budget default is 30
grep "session.budget.*default.*30" C:/Users/jiami/local_workspace/seataero/scripts/burn_in.py && echo "OK" || echo "MISSING"

# 4. Verify reset_backoff is called in burn_in.py
grep "reset_backoff" C:/Users/jiami/local_workspace/seataero/scripts/burn_in.py && echo "OK" || echo "MISSING"

# 5. Verify endurance test section in findings doc
grep "5-Hour" C:/Users/jiami/local_workspace/seataero/docs/findings/ultra-low-delay-experiment.md && echo "OK" || echo "MISSING"

# 6. Verify new steps in project brief
grep -E "6[a-e]" C:/Users/jiami/local_workspace/seataero/docs/project-brief.md && echo "OK" || echo "MISSING"

# 7. Run existing tests
cd C:/Users/jiami/local_workspace/seataero && C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v

# 8. Check new JSONL log has entries
ls -la C:/Users/jiami/local_workspace/seataero/logs/burn_in_*.jsonl | tail -1
```

## Notes
- The 5-hour endurance test will take ~5.5 hours with overhead. Run in background.
- The session-pause reduction from 600s to 60s is aggressive but justified: at 0.5s delay, the old 10-minute pause was 85% of wall-clock time per session budget hit. The 60-minute endurance test showed that session resets (scraper stop/start with fresh cookies) are fast and reliable.
- The project brief updates are descriptions of FUTURE work, not implementation. Steps 6a-6e describe what to build; they don't build it.
- If the 5-hour endurance test fails (>5% error rate), the session-budget may need further tuning. The document-endurance task should include a recommendation for next steps regardless of outcome.
- The brief-updater task can run in parallel with the endurance results documentation since it only needs the outcome category (success/partial/failure), not the full analysis.
