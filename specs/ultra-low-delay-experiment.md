# Plan: Ultra-Low Delay Experiment (0.5s inter-request delay)

## Task Description
Test whether the scraper can operate with 0.5-second inter-request delays — 14-24x faster than current production settings (7-12s). This is a pure empirical test: no production code changes, just running the existing burn-in infrastructure with ultra-aggressive timing flags and documenting what happens. The hypothesis is that the inter-request delay is overly conservative and Akamai primarily uses cookie-count-based detection, not timing-based.

## Objective
Determine empirically whether 0.5s inter-request delay causes increased cookie burns, rate limiting, or detection. If it works, a full 2,000-route daily sweep drops from ~7 hours (single worker) to ~30-45 minutes — eliminating the need for multiple parallel workers entirely.

## Problem Statement
The current scraper uses 7-12s inter-request delays because "a real user spends time reading results." But we've never actually tested lower values against Akamai/United. The 7-12s figure was chosen defensively, not empirically. At Phase 1b scale (20,000 routes), this conservative delay is the dominant throughput bottleneck — it accounts for ~85% of wall-clock time per request.

Two things we already know:
1. The aggressive-timing experiment spec (`specs/aggressive-timing-experiment.md`) proposed testing `--delay 1 --refresh-interval 3` but may not have been run yet.
2. The jitter floor fix in `scrape.py` (line 117) was already applied — proportional jitter now works for sub-second delays.

This experiment isolates the delay variable by keeping refresh-interval at the proven value of 2 (well under the ~3-4 burn threshold) and only changing the inter-request delay.

## Solution Approach
Run the existing burn-in infrastructure with `--delay 0.5` and compare against the baseline burn-in logs. No code changes required — the CLI flags already support this. The session budget must be set very high (9999) because at 0.5s delay, the default 40-request budget would trigger a 10-minute pause after just ~60 seconds of scraping.

Key design decision: **isolate one variable.** We change ONLY the delay. Refresh interval stays at 2. This way, if it fails, we know it's the delay speed, not the cookie refresh frequency.

## Relevant Files
Use these files to complete the task:

- `scrape.py` — Contains the proportional jitter logic (line 117). Already supports sub-second delays. **No changes needed.**
- `scripts/burn_in.py` — Burn-in runner. Already supports `--delay`, `--refresh-interval`, `--session-budget`, `--route-delay`, `--duration` flags. **No changes needed.**
- `scripts/experiments/hybrid_scraper.py` — HybridScraper class. `refresh_interval` and `session_budget` are constructor params. **No changes needed.**
- `scripts/analyze_burn_in.py` — Existing analysis script for JSONL log comparison.
- `routes/canada_test.txt` — 15 Canada test routes. Use as-is.
- `logs/burn_in_20260403_013516.jsonl` — Baseline data (Step 5 burn-in, refresh_interval=2, delay=12s, 99.5% success). If this file doesn't exist, use whatever the most recent baseline log is.

### New Files
- `docs/findings/ultra-low-delay-experiment.md` — Document results regardless of outcome.

## Implementation Phases

### Phase 1: Foundation
Verify preconditions before running:
1. Confirm jitter floor in `scrape.py` already uses proportional logic (not hardcoded `max(3.0, ...)`)
2. Confirm PostgreSQL is running (`docker compose ps`)
3. Confirm cookie farm can start and authenticate
4. Identify the baseline JSONL log for comparison

No code changes in this phase. If the jitter floor hasn't been fixed yet (still has `max(3.0, ...)`), apply the fix from the aggressive-timing-experiment spec first:
```python
# Before (broken for sub-second delays):
jittered = max(3.0, delay + random.uniform(-4.0, 4.0))

# After (proportional):
jitter_range = max(0.5, delay * 0.3)
jittered = max(delay * 0.5, delay + random.uniform(-jitter_range, jitter_range))
```

### Phase 2: Core Implementation
Run the burn-in with ultra-low delay:
```bash
cd C:/Users/jiami/local_workspace/seataero
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py \
  --routes-file routes/canada_test.txt \
  --duration 10 \
  --delay 0.5 \
  --refresh-interval 2 \
  --session-budget 9999 \
  --route-delay 10 \
  --create-schema
```

Parameter rationale:
- `--delay 0.5` — 0.5 seconds between API calls (down from 12). With proportional jitter: actual delays range 0.25-1.0s.
- `--refresh-interval 2` — Keep at proven safe value. We're testing delay speed, not cookie refresh frequency.
- `--session-budget 9999` — Effectively disable session budget pauses. At 0.5s delay, the default budget of 40 would hit in ~60 seconds, then idle for 10 minutes — consuming the entire test window.
- `--route-delay 10` — Reduce inter-route pause from 90s to 10s. Still gives some breathing room between routes but doesn't burn the whole test on idle time.
- `--duration 10` — 10-minute test.

Expected throughput in 10 minutes:
- Per batch of 2 calls: 2 × (~0.5s delay + ~0.3s call) + ~2s refresh = ~3.6s for 2 calls
- Per route (12 windows): 6 batches × 3.6s = ~21.6s + 10s route pause = ~32s/route
- In 600s: ~15-18 routes = 180-216 API calls
- vs. baseline: ~16 calls in same window at old settings (12s delay)
- **Throughput increase: ~12-14x over baseline**

### Phase 3: Integration & Polish
Analyze results by comparing against baseline:

| Metric | Baseline (Step 5) | Experiment (0.5s) |
|--------|-------------------|-------------------|
| Inter-request delay | 12s | 0.5s |
| Refresh interval | 2 calls | 2 calls |
| Session budget | 40 | disabled |
| Success rate | 99.5% (191/192) | ? |
| Cookie burns | 0 | ? |
| Circuit breaks | 0 | ? |
| Solutions/minute | ~148 | ? |
| Avg route duration | ~3-5 min | ? |
| 429 rate limits | 0 | ? |

Document findings in `docs/findings/ultra-low-delay-experiment.md` with one of three outcomes:
1. **Success (>95% windows OK, 0 circuit breaks, 0 rate limits)** — 0.5s delay is viable for production. Massive throughput win.
2. **Partial (80-95% OK)** — 0.5s is too fast, but lower-than-12s delays may work. Follow up with 2s and 5s tests.
3. **Failure (<80% OK, or rate limits/circuit breaks)** — Timing-based detection is real. Current 7-12s delay is necessary.

Watch specifically for these failure signatures:
- **HTTP 429 (rate limit)**: United/Akamai is explicitly rate-limiting. The delay matters.
- **Spike in cookie burns**: Akamai may use timing + count together. Fast calls may burn cookies faster.
- **HTTP 403 (Cloudflare)**: IP-level blocking triggered by request velocity.
- **Silent data degradation**: 200 OK but fewer solutions per route. Compare solutions_found against baseline.

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
  - Name: experiment-runner
  - Role: Verify preconditions (jitter floor, PostgreSQL, baseline log), run the 10-minute burn-in experiment, and capture the JSONL log file path
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: results-analyst
  - Role: Analyze the experiment JSONL log against baseline, calculate metrics, determine outcome, and write the findings document
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: validator
  - Role: Verify no production code was modified, findings document exists with correct comparison data, and existing tests still pass
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Verify Preconditions
- **Task ID**: verify-preconditions
- **Depends On**: none
- **Assigned To**: experiment-runner
- **Agent Type**: general-purpose
- **Parallel**: false
- Read `scrape.py` line ~117 and confirm the jitter floor uses proportional logic (`max(delay * 0.5, ...)`) not the hardcoded `max(3.0, ...)`. If not fixed, apply the proportional jitter fix (this is the ONLY allowed code change).
- Run `docker compose ps` to confirm PostgreSQL is running. If not, run `docker compose up -d` and wait for it to be healthy.
- Identify the most recent baseline JSONL log in `logs/` for later comparison. Record its filename.
- Verify `routes/canada_test.txt` exists and has routes.

### 2. Run 10-Minute Ultra-Low Delay Burn-In
- **Task ID**: run-experiment
- **Depends On**: verify-preconditions
- **Assigned To**: experiment-runner
- **Agent Type**: general-purpose
- **Parallel**: false
- Run the burn-in command shown in Phase 2 above. Use a 15-minute timeout to account for startup time.
- Run this in the background. The burn-in will take ~10 minutes plus startup/shutdown overhead.
- If the burn-in fails to start (cookie farm auth failure, DB connection error), troubleshoot and retry once.
- After completion, record the output JSONL log file path (e.g., `logs/burn_in_20260403_XXXXXX.jsonl`).
- Capture the summary output printed at the end of the burn-in (success rate, total routes, etc.).

### 3. Analyze Results vs. Baseline
- **Task ID**: analyze-results
- **Depends On**: run-experiment
- **Assigned To**: results-analyst
- **Agent Type**: general-purpose
- **Parallel**: false
- Read the experiment JSONL log and the baseline JSONL log.
- Run `scripts/analyze_burn_in.py` on both logs if available.
- Calculate and compare: success rate (windows_ok / total_windows), cookie burn count, circuit break count, HTTP 429 count, average solutions_found per route, average route duration, total API calls completed.
- Look for **silent degradation**: if success rate is similar but solutions_found per route drops significantly, that indicates the API is returning incomplete data under load.
- Determine outcome: success (>95%), partial (80-95%), or failure (<80%).

### 4. Document Findings
- **Task ID**: document-findings
- **Depends On**: analyze-results
- **Assigned To**: results-analyst
- **Agent Type**: general-purpose
- **Parallel**: false
- Create `docs/findings/ultra-low-delay-experiment.md` with:
  - Experiment date and parameters
  - Full results table comparing baseline vs. experiment (all metrics from Phase 3)
  - Raw data: total API calls, total duration, throughput (calls/minute)
  - Error breakdown by type (cookie burn, rate limit, timeout, etc.)
  - Conclusion: which outcome category (success/partial/failure)
  - Recommendation: what delay to use going forward, and what follow-up experiments (if any) are needed
- Do NOT modify any production code or the project brief based on results. This is an experiment doc only.

### 5. Validate Experiment Integrity
- **Task ID**: validate-all
- **Depends On**: verify-preconditions, run-experiment, analyze-results, document-findings
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify NO production code was modified except the jitter floor fix (if it was needed). `hybrid_scraper.py`, `burn_in.py`, `united_api.py`, `cookie_farm.py` must be unchanged.
- Verify `docs/findings/ultra-low-delay-experiment.md` exists and contains a comparison table with both baseline and experiment data.
- Run existing tests: `C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v`
- Confirm all tests pass.
- Verify the experiment JSONL log exists in `logs/` and has entries.

## Acceptance Criteria
- A 10-minute burn-in completes with `--delay 0.5 --refresh-interval 2 --session-budget 9999` settings
- The experiment JSONL log exists in `logs/` with route-level entries
- Results are compared quantitatively against the Step 5 baseline (or most recent baseline)
- `docs/findings/ultra-low-delay-experiment.md` exists with experiment results, comparison table, and a clear recommendation
- No production code is modified (except the jitter floor fix if it hadn't been applied yet)
- Existing tests still pass

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# 1. Verify jitter floor is proportional (should NOT match)
grep "max(3.0" C:/Users/jiami/local_workspace/seataero/scrape.py && echo "BROKEN" || echo "OK"

# 2. Verify findings doc exists
test -f C:/Users/jiami/local_workspace/seataero/docs/findings/ultra-low-delay-experiment.md && echo "EXISTS" || echo "MISSING"

# 3. Run existing tests
cd C:/Users/jiami/local_workspace/seataero && C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v

# 4. Check new JSONL log has entries
ls -la C:/Users/jiami/local_workspace/seataero/logs/burn_in_*.jsonl | tail -1
```

## Notes
- **This is a zero-code-change experiment.** The entire test uses existing CLI flags. The only possible code change is the jitter floor fix, which may already be applied.
- **Session budget is disabled intentionally.** At 0.5s delay, the default 40-request budget would idle for 10 minutes after just 60 seconds of scraping. If the experiment succeeds, determining a safe session budget at high speed becomes a follow-up task.
- **If the experiment succeeds, do NOT immediately adopt 0.5s for production.** A 10-minute test with 15 routes is not proof of long-term viability. The next step would be a 60-minute test, then a 24-hour test. Akamai detection can be delayed — a session that works for 10 minutes may get flagged after 2 hours.
- **Abort heuristic:** If the burn-in output shows 2+ consecutive circuit breaks in the first 2 minutes, the experiment has failed. The circuit breaker logic in `burn_in.py` already handles this — it pauses 5 minutes and resets after 2 consecutive circuit breaks.
- **Previous experiment:** The `specs/aggressive-timing-experiment.md` planned a test at `--delay 1 --refresh-interval 3`. This experiment is more aggressive on delay (0.5s vs 1s) but more conservative on refresh interval (2 vs 3). Different variable isolation.
