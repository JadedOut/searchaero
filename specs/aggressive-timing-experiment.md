# Plan: Aggressive Timing Experiment (3 calls / 1s delay)

## Task Description
Test whether the scraper can operate at 3x the current throughput by increasing the cookie refresh interval from 2 to 3 API calls and reducing the inter-call delay from 7-12 seconds to 1 second. Run a 10-minute burn-in with these aggressive settings across the 15 Canada test routes and compare results against the baseline (Step 5 burn-in: 99.5% success at refresh_interval=2, delay=12s).

## Objective
Determine empirically whether 3 API calls per cookie refresh cycle with 1-second delays triggers Akamai cookie burns or other detection. This directly impacts scaling feasibility — if it works, a full Canada sweep (~24,000 calls) drops from ~60 hours to ~20 hours single-threaded.

## Problem Statement
The current scraper refreshes cookies every 2 calls with 7-12s delays. Akamai burns cookies after ~3-4 uses. We've never tested whether the burn threshold is call-count-only or also time-sensitive. At current pace, a full 2,000-route sweep takes ~60 hours — too slow for daily refreshes. If 3 calls at 1s delay works, throughput triples.

## Solution Approach
1. Fix the hardcoded 3-second jitter floor in `scrape.py` that prevents testing delays below 3s
2. Run the existing burn-in infrastructure with `--refresh-interval 3 --delay 1 --session-budget 9999 --duration 10`
3. Analyze success rate, cookie burn frequency, and error patterns vs. baseline

**Key risk:** We're testing right at the known ~3-4 call burn threshold. If 3 calls burns consistently, the experiment fails fast and we revert — no harm done.

## Relevant Files
Use these files to complete the task:

- `scrape.py` — Contains the jitter floor (`max(3.0, ...)` on line 117) that must be changed to allow 1-second delays. This is the ONLY production code change.
- `scripts/burn_in.py` — Burn-in runner. Already supports `--delay`, `--refresh-interval`, `--session-budget`, `--duration` flags. No changes needed.
- `scripts/experiments/hybrid_scraper.py` — The `HybridScraper` class. `refresh_interval` is already a constructor parameter. No changes needed.
- `routes/canada_test.txt` — 15 test routes. Use as-is.
- `logs/burn_in_20260403_013516.jsonl` — Baseline data (Step 5 results) for comparison.
- `scripts/analyze_burn_in.py` — Existing analysis script for JSONL logs.

### New Files
- `docs/findings/aggressive-timing-experiment.md` — Document results, regardless of success or failure.

## Implementation Phases

### Phase 1: Foundation
Fix the jitter floor in `scrape.py` line 117. The current code:
```python
jittered = max(3.0, delay + random.uniform(-4.0, 4.0))
```
Must become proportional to the configured delay:
```python
jitter_range = max(0.5, delay * 0.3)
jittered = max(delay * 0.5, delay + random.uniform(-jitter_range, jitter_range))
```
This preserves jitter behavior at normal delays (7s → ~5.2-8.8s range) while allowing 1s delays to actually work (~0.5-1.3s range). The floor is half the configured delay instead of hardcoded 3.0.

### Phase 2: Core Implementation
Run the burn-in with aggressive settings:
```bash
cd C:/Users/jiami/local_workspace/seataero
scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py \
  --routes-file routes/canada_test.txt \
  --duration 10 \
  --delay 1 \
  --refresh-interval 3 \
  --session-budget 9999 \
  --route-delay 5 \
  --create-schema
```

Parameter rationale:
- `--delay 1` — 1 second between API calls (down from 12)
- `--refresh-interval 3` — Refresh cookies every 3 calls (up from 2, right at burn threshold)
- `--session-budget 9999` — Effectively disable session budget pauses (the 10-min pause every 40 requests would eat the entire test window)
- `--route-delay 5` — Reduce inter-route pause from 90s to 5s (we want maximum calls in 10 minutes)
- `--duration 10` — 10-minute test

Expected throughput in 10 minutes:
- Per batch: 3 calls × (~1s delay + ~0.3s call) + ~5s refresh = ~9s for 3 calls
- In 600s: ~200 API calls = ~16 routes worth of windows
- vs. baseline: ~16 calls in same window at old settings

### Phase 3: Integration & Polish
Analyze results by comparing against baseline:

| Metric | Baseline (Step 5) | Experiment |
|--------|-------------------|------------|
| Refresh interval | 2 calls | 3 calls |
| Delay | 12s | 1s |
| Success rate | 99.5% (191/192) | ? |
| Cookie burns | 0 | ? |
| Circuit breaks | 0 | ? |
| Solutions/minute | ~148 | ? |

Document findings in `docs/findings/aggressive-timing-experiment.md` with one of three outcomes:
1. **Success (>95% windows OK, 0 circuit breaks)** — Adopt new settings, proceed to Step 6 with them
2. **Partial (80-95% OK)** — Try intermediate settings (refresh_interval=2, delay=3s)
3. **Failure (<80% OK or circuit breaks)** — Revert to proven settings, document why

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
  - Name: scraper-tuner
  - Role: Fix the jitter floor in scrape.py (single line change), then run the burn-in experiment and capture results
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: results-analyst
  - Role: Analyze the experiment JSONL log, compare against baseline, and write the findings document
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: validator
  - Role: Verify the jitter fix doesn't break existing behavior and that findings are documented
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Fix Jitter Floor in scrape.py
- **Task ID**: fix-jitter-floor
- **Depends On**: none
- **Assigned To**: scraper-tuner
- **Agent Type**: general-purpose
- **Parallel**: false
- Edit `scrape.py` line 117: replace `jittered = max(3.0, delay + random.uniform(-4.0, 4.0))` with proportional jitter: `jitter_range = max(0.5, delay * 0.3); jittered = max(delay * 0.5, delay + random.uniform(-jitter_range, jitter_range))`
- Verify the change with a quick read of the file to confirm correctness
- Do NOT change any other code — the `HybridScraper`, `burn_in.py`, and `cookie_farm.py` remain untouched

### 2. Run 10-Minute Aggressive Burn-In
- **Task ID**: run-experiment
- **Depends On**: fix-jitter-floor
- **Assigned To**: scraper-tuner
- **Agent Type**: general-purpose
- **Parallel**: false
- Ensure PostgreSQL is running (`docker compose ps`)
- Run: `cd C:/Users/jiami/local_workspace/seataero && C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py --routes-file routes/canada_test.txt --duration 10 --delay 1 --refresh-interval 3 --session-budget 9999 --route-delay 5 --create-schema`
- Run this as a background task with a 15-minute timeout
- Monitor: check the JSONL log file for entries appearing, watch for circuit breaks or mass failures in the first 1-2 minutes

### 3. Analyze Results vs. Baseline
- **Task ID**: analyze-results
- **Depends On**: run-experiment
- **Assigned To**: results-analyst
- **Agent Type**: general-purpose
- **Parallel**: false
- Read the new JSONL log from `logs/burn_in_*.jsonl` (most recent file)
- Compare against baseline at `logs/burn_in_20260403_013516.jsonl`
- Calculate: success rate, cookie burns, circuit breaks, solutions/minute, avg route duration
- Determine outcome: success (>95%), partial (80-95%), or failure (<80%)

### 4. Document Findings
- **Task ID**: document-findings
- **Depends On**: analyze-results
- **Assigned To**: results-analyst
- **Agent Type**: general-purpose
- **Parallel**: false
- Create `docs/findings/aggressive-timing-experiment.md` with:
  - Experiment parameters
  - Results table comparing baseline vs. experiment
  - Conclusion and recommendation for Step 6 settings
- Update `docs/project-brief.md` only if the experiment succeeds — add a note to Step 6 about validated settings

### 5. Validate Changes
- **Task ID**: validate-all
- **Depends On**: fix-jitter-floor, document-findings
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `scrape.py` jitter change is correct and proportional (doesn't break normal 7s+ delays)
- Verify the findings document exists and contains comparison data
- Run existing tests: `C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v`
- Confirm tests still pass

## Acceptance Criteria
- The jitter floor in `scrape.py` is proportional to the configured delay (no hardcoded 3.0s floor)
- A 10-minute burn-in completes with `--delay 1 --refresh-interval 3` settings
- Results are compared quantitatively against the Step 5 baseline
- `docs/findings/aggressive-timing-experiment.md` exists with experiment results and a clear recommendation
- Existing tests still pass
- No other production code is modified (only `scrape.py` jitter line)

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# 1. Verify jitter fix (should NOT contain "max(3.0")
grep "max(3.0" C:/Users/jiami/local_workspace/seataero/scrape.py

# 2. Verify findings doc exists
test -f C:/Users/jiami/local_workspace/seataero/docs/findings/aggressive-timing-experiment.md && echo "EXISTS" || echo "MISSING"

# 3. Run existing tests
cd C:/Users/jiami/local_workspace/seataero && C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v

# 4. Check new JSONL log has entries
wc -l C:/Users/jiami/local_workspace/seataero/logs/burn_in_2026040*.jsonl
```

## Notes
- **Session budget disabled for this test.** The default 40-request budget with 10-min pause would consume the entire test window. If the experiment succeeds, we'll need to determine a new session budget for production (or whether one is needed at all).
- **Cookie burn threshold is the key unknown.** We've observed ~3-4 calls in Experiment 3 (curl-cffi-feasibility.md), but that was with static cookies and no proactive refresh. The hybrid architecture's continuous refresh may change the dynamics.
- **If burns spike in the first 2 minutes, abort early.** No point running 10 minutes if the first cycle shows consistent failures. The circuit breaker (3 consecutive burns) will handle this automatically.
- **Rollback is trivial.** If the experiment fails, the only code change is one line in `scrape.py`. All other settings are CLI flags.
