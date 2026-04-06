# Plan: Fix Scraper Resilience — HTTP/2 Stream Failures After Initial Requests

## Task Description
The burn-in test (Phase 3B, ~12 hours) revealed that the scraper dies after the first ~8 successful requests. Every subsequent request fails with `curl: (92) HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR (err 2)`. Cookie refresh + session reset doesn't recover it. The scraper burned through 900 windows with only 8 successes (0.9% success rate), spending ~12 hours retrying a permanently blocked connection pattern.

## Objective
Make the scraper resilient enough to sustain multi-hour burn-in runs (Phase 3B: 1 hour, Phase 3C: 48 hours) with >80% window success rate across 15 routes. The scraper should stay under Akamai's behavioral detection threshold proactively, and recover intelligently when detection does occur.

## Problem Statement
**Root cause: behavioral volume detection, not IP reputation.**

Evidence: Phase 3A succeeded at 48/48 windows (100%) from the same IP. If the IP were flagged, request 1 would fail. Instead, the first ~8 requests succeed before Akamai's behavioral model flags the traffic pattern. This means:

- The IP is fine (not datacenter-blacklisted)
- curl_cffi's TLS/HTTP/2 fingerprint passes
- Cookie farm's _abck cookies are valid
- **The problem is sustained request volume/timing that crosses Akamai's automated-traffic threshold**

Once flagged, Akamai sends HTTP/2 RST_STREAM (error 92) — its high-confidence bot response. The current code's 30-second cooldown + cookie refresh doesn't help because the behavioral flag persists at the IP level for minutes to hours.

**Four compounding code issues make this worse:**

1. **No session budget** — The scraper has no concept of "I've made N requests, I should proactively pause." It just keeps going until blocked, then it's too late.

2. **Proactive refresh doesn't reset HTTP/2 connection** (`hybrid_scraper.py:175-178`). Cookies refresh every 2 calls but the same HTTP/2 connection is reused, making behavioral correlation easier for Akamai.

3. **Fixed 30-second cooldown is too short** (`hybrid_scraper.py:191`). After behavioral detection triggers, 30 seconds isn't enough for the flag to expire.

4. **No circuit breaker**. After detection triggers, the scraper burns through all remaining windows and routes on a dead session for hours.

**Why Phase 3A (10 min) passed but Phase 3B failed:** Phase 3A made ~48 requests at ~4.8 req/min over 10 minutes — under the behavioral detection threshold. The longer run sustained this rate for hours, crossing the threshold after ~8 requests into the sustained phase.

## Solution Approach
The primary defense is **staying under the detection threshold proactively** rather than trying to recover after being caught. Five changes, ordered by impact:

1. **Session budget with proactive long pause** (PRIMARY FIX) — After every N requests (default 40), proactively pause for 10-15 minutes and fully reset the session (new HTTP/2 connection + fresh cookies + fresh bearer token), even if nothing has failed yet. This keeps each "burst" of activity under Akamai's behavioral detection window. Phase 3A proved 48 requests works — so budgeting 40 per session gives margin.

2. **Slower default timing** — Increase default inter-request delay from 7s to 12s, and add longer inter-route pauses (90s between routes). This reduces request density within each session budget window.

3. **Request timing jitter** — Randomize delays (±4s on the 12s base, so 8-16s) to break the clockwork pattern that behavioral models flag.

4. **Always reset HTTP/2 session on proactive refresh** — One-line fix. Ensures every cookie refresh also creates a fresh TCP connection, reducing behavioral correlation across requests.

5. **Circuit breaker + escalating backoff** (SAFETY NET) — If detection still triggers despite the budget, detect it quickly and stop wasting requests. Escalating backoff (30s → 60s → 120s → 300s), circuit breaker after 3 consecutive burns, and full session reset on circuit break. This is the fallback, not the primary defense.

**Throughput math:** With 15 routes × 12 windows = 180 requests per cycle, budget of 40 requests per session, 12s average delay, and 10-minute pauses between sessions:
- ~8 minutes of active scraping per session (40 × 12s)
- ~10 minutes pause between sessions
- ~18 minutes per session cycle
- ~5 sessions per cycle = ~90 minutes per full cycle
- That's ~2,880 windows/day — more than enough for monitoring 15 routes.

## Relevant Files
Use these files to complete the task:

- `scripts/experiments/hybrid_scraper.py` — Core fix location. Session budget tracking, proactive session reset (line 175-178), cooldown escalation (line 191), backoff state.
- `scrape.py` — `scrape_route()` function (line 34-118). Needs jitter, circuit breaker, and session-budget awareness (pause mid-route if budget exhausted).
- `scripts/burn_in.py` — Burn-in orchestration (line 283-441). Needs circuit breaker handling, updated default CLI args (delay, cycle-delay), and inter-route pause.
- `scripts/experiments/cookie_farm.py` — No changes needed, but referenced for understanding refresh flow.
- `scripts/experiments/united_api.py` — No changes needed, but referenced for understanding request building.

## Implementation Phases

### Phase 1: Foundation
- Add session budget state and proactive pause logic to `HybridScraper`
- Add escalating backoff state to `HybridScraper`
- Change proactive refresh to always reset the HTTP/2 session

### Phase 2: Core Implementation
- Implement session budget pause in `fetch_calendar` — when budget exhausted, do a long pause + full reset before proceeding
- Implement escalating backoff in the cookie burn recovery path
- Add request jitter to `scrape_route()` delay
- Add circuit breaker to `scrape_route()` — return early after consecutive failures
- Add circuit breaker handling and inter-route pause to `_run_burn_in()`
- Update CLI defaults (delay: 12s, cycle-delay: 120s)

### Phase 3: Integration & Polish
- Verify the fix with a short burn-in test (~10 min, 4 routes)
- Ensure existing tests still pass
- Test that session budget pause and circuit breaker trigger correctly (observable in console output)

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
  - Name: scraper-resilience-builder
  - Role: Implement all resilience fixes across hybrid_scraper.py, scrape.py, and burn_in.py
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: validator
  - Role: Validate all changes work correctly and existing tests still pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Add Session Budget + Escalating Backoff + Proactive Session Reset to HybridScraper
- **Task ID**: fix-hybrid-scraper
- **Depends On**: none
- **Assigned To**: scraper-resilience-builder
- **Agent Type**: general-purpose
- **Parallel**: false
- In `scripts/experiments/hybrid_scraper.py`, make these changes:
  - **Session budget state in `__init__`:** Add `self._session_budget: int = session_budget` parameter (default 40). Add `self._requests_this_session: int = 0` counter. Add constants: `_DEFAULT_SESSION_BUDGET = 40`, `_SESSION_PAUSE_SECONDS = 600.0` (10 minutes).
  - **Session budget check in `fetch_calendar` (before the proactive refresh block):** If `self._requests_this_session >= self._session_budget`, print a message like `"Session budget reached ({N} requests), pausing {M}min for session reset..."`. Sleep for `_SESSION_PAUSE_SECONDS`. Then call `self._refresh(reset_session=True)` to get fresh cookies + fresh HTTP/2 connection. Reset `self._requests_this_session = 0`. This is the PRIMARY defense — proactively pause before Akamai flags the pattern.
  - **Increment session counter:** After each request (successful or not), increment `self._requests_this_session`.
  - **Proactive session reset (line 175-178):** Change `self._refresh()` to `self._refresh(reset_session=True)` so every proactive cookie refresh also creates a fresh HTTP/2 connection.
  - **Add backoff state to `__init__`:** Add `self._consecutive_burns: int = 0` and `self._backoff_seconds: float = 30.0`. Add constants: `_BASE_BACKOFF = 30.0`, `_MAX_BACKOFF = 300.0`, `_BACKOFF_MULTIPLIER = 2.0`.
  - **Escalating backoff in `fetch_calendar` (line 189-203):** Replace the fixed `time.sleep(30)` with `time.sleep(self._backoff_seconds)`. After a burn is detected, increment `self._consecutive_burns` and multiply `self._backoff_seconds` by `_BACKOFF_MULTIPLIER` (capped at `_MAX_BACKOFF`). Print the current backoff duration so the user can see what's happening.
  - **Reset backoff on success:** After a successful request, reset `self._consecutive_burns = 0` and `self._backoff_seconds = self._BASE_BACKOFF`.
  - **Expose state properties:** Add properties `consecutive_burns` (returns `self._consecutive_burns`) and `requests_this_session` (returns `self._requests_this_session`) so callers can inspect scraper health.
  - **Add `session_budget` parameter to constructor:** So it can be configured via CLI: `HybridScraper(farm, refresh_interval=2, session_budget=40)`.

### 2. Add Request Jitter, Inter-Route Pause, and Circuit Breaker to scrape_route
- **Task ID**: fix-scrape-route
- **Depends On**: fix-hybrid-scraper
- **Assigned To**: scraper-resilience-builder
- **Agent Type**: general-purpose
- **Parallel**: false
- In `scrape.py`, modify `scrape_route()`:
  - **Request jitter (line 110-111):** Replace `time.sleep(delay)` with `jittered = max(3.0, delay + random.uniform(-4.0, 4.0))` then `time.sleep(jittered)`. With a 12s base delay this gives 8-16s range. Add `import random` at the top.
  - **Circuit breaker:** After each failed window, check `scraper.consecutive_burns`. If `consecutive_burns >= 3`, print a "Circuit breaker triggered — 3 consecutive burns, aborting route" message and break out of the window loop early. Return the totals with an additional key `"circuit_break": True` so the burn-in loop knows to pause. Use 3 (not 5) since with session budgeting, any consecutive burns indicate we've hit a detection edge.
  - **Ensure backward compatibility:** The `circuit_break` key defaults to `False` in the return dict. Callers that don't check it are unaffected.

### 3. Add Circuit Breaker Handling + Inter-Route Pause + Updated Defaults to Burn-In Loop
- **Task ID**: fix-burn-in
- **Depends On**: fix-scrape-route
- **Assigned To**: scraper-resilience-builder
- **Agent Type**: general-purpose
- **Parallel**: false
- In `scripts/burn_in.py`, make these changes:
  - **Update CLI defaults:** Change `--delay` default from `7.0` to `12.0`. Change `--cycle-delay` default from `60` to `120`. Add new `--session-budget` arg (int, default 40, help: "Proactively pause and reset session after N requests"). Add new `--route-delay` arg (int, default 90, help: "Seconds to pause between routes").
  - **Pass session_budget to HybridScraper:** `HybridScraper(farm, refresh_interval=args.refresh_interval, session_budget=args.session_budget)`.
  - **Inter-route pause:** After each route completes in `_run_burn_in()`, add `time.sleep(args.route_delay)` before starting the next route. Print a message like `"Pausing {N}s between routes..."`. This spreads request activity over more time.
  - **Circuit breaker handling:** After each route, check if `totals.get("circuit_break")` is True. If so:
    - Print `"Circuit breaker: scraper blocked, pausing 5 minutes for full reset..."`.
    - Sleep for 300 seconds (5 minutes).
    - Do a full session reset: `scraper.stop()` → `farm.refresh_cookies()` → `scraper.start()`.
    - Print `"Session fully reset, resuming..."`.
  - **Consecutive circuit break abort:** Track consecutive circuit breaks. If 2 routes in a row trigger the circuit breaker, abort the current cycle entirely with a message like `"2 consecutive circuit breaks — aborting cycle, waiting for next cycle..."`. Jump to the inter-cycle delay. This prevents burning 15 × 5 minutes on a completely dead session.
  - **Log new fields in JSONL record:** Add `"circuit_break": True/False` and `"requests_this_session": scraper.requests_this_session` to the JSONL record.

### 4. Validate All Changes
- **Task ID**: validate-all
- **Depends On**: fix-hybrid-scraper, fix-scrape-route, fix-burn-in
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run existing test suite: `scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v`
- Verify `scrape.py --help` still works
- Verify `scripts/burn_in.py --help` still works — confirm new args appear (`--session-budget`, `--route-delay`) and updated defaults (`--delay 12.0`, `--cycle-delay 120`)
- Read through all changed files to verify:
  - Session budget is tracked and triggers a 10-minute pause + full session reset after 40 requests
  - Session counter resets after budget pause
  - Proactive refresh now passes `reset_session=True`
  - Backoff state is initialized in `__init__` and reset on success
  - Backoff escalates correctly (30 → 60 → 120 → 240 → 300 cap)
  - Circuit breaker threshold is 3 consecutive burns
  - Jitter range is ±4s with 3s floor
  - Inter-route delay defaults to 90s
  - JSONL records include `circuit_break` and `requests_this_session` fields
  - New CLI args (`--session-budget`, `--route-delay`) are wired through correctly
  - `scrape.py` standalone (non-burn-in) usage is unaffected — session budget defaults to 40 in the constructor, jitter applies, but no route-delay logic needed

## Acceptance Criteria
- Session budget pauses scraper for 10 minutes and resets session after every 40 requests (configurable via `--session-budget`)
- Proactive refresh always resets HTTP/2 session (not just cookies)
- Backoff escalates from 30s to 300s max on consecutive cookie burns
- Backoff resets to 30s after any successful request
- `scrape_route()` breaks early after 3 consecutive burns (circuit breaker)
- `burn_in.py` pauses 5 minutes and fully resets session on circuit break
- `burn_in.py` aborts cycle after 2 consecutive route-level circuit breaks
- Default inter-request delay is 12s with ±4s jitter (floor 3s)
- Default inter-route pause is 90s
- Default inter-cycle delay is 120s
- JSONL log records include `circuit_break` and `requests_this_session` fields
- New CLI args: `--session-budget` (default 40), `--route-delay` (default 90)
- All existing tests pass
- `scrape.py` standalone usage is unaffected (works with defaults)

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# Existing tests still pass
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v

# CLI help still works with new args and updated defaults
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe scrape.py --help
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe scripts/burn_in.py --help

# Verify session budget logic exists
grep -n "session_budget" scripts/experiments/hybrid_scraper.py

# Verify proactive refresh uses reset_session=True
grep -n "reset_session" scripts/experiments/hybrid_scraper.py

# Verify circuit_break in scrape_route return
grep -n "circuit_break" scrape.py

# Verify circuit breaker handling in burn_in
grep -n "circuit_break" scripts/burn_in.py

# Verify jitter in delay
grep -n "random" scrape.py

# Verify route-delay arg
grep -n "route.delay\|route_delay" scripts/burn_in.py
```

## Notes

### Research-Backed Design Rationale

This plan is informed by research into Akamai Bot Manager's detection mechanisms (see sources below). Key findings that shaped the approach:

1. **Behavioral detection, not IP reputation, is the primary blocker.** Phase 3A's 100% success from the same IP proves the IP isn't flagged. Akamai's behavioral model flags the sustained request pattern after ~50 requests.

2. **Session budgeting is the industry-standard "free" approach.** Proxy rotation is the gold standard for high-volume scraping, but for low-volume monitoring (15 routes), staying under the detection threshold via pacing is sufficient and costs nothing.

3. **HTTP/2 RST_STREAM (error 92) is Akamai's high-confidence bot response.** The curl_cffi maintainer confirmed on GitHub #627 that this is intentional server-side blocking. Once triggered, the IP+session combination is burned for minutes to hours — backoff helps but rotation is better.

4. **Connection reuse aids behavioral correlation.** Akamai can correlate all requests on the same HTTP/2 connection. Resetting connections periodically (which we now do on every proactive refresh) limits this correlation window.

5. **Clockwork timing is a strong bot signal.** Fixed 7-second delays between all requests is an unmistakable automated pattern. Jitter (±4s) breaks this.

### Tuning Guide

If Phase 3B still shows <80% success after this fix, try these adjustments in order:
- **Reduce session budget** from 40 to 25 (more frequent pauses)
- **Increase session pause** from 10 min to 15 min
- **Increase base delay** from 12s to 20s
- **Increase route delay** from 90s to 180s

If success rate is >90%, you can cautiously increase throughput:
- **Increase session budget** from 40 to 60
- **Decrease session pause** from 10 min to 7 min
- **Decrease base delay** from 12s to 8s

### Future: Proxy Rotation (Not In This Plan)

If the session-budgeting approach proves insufficient (e.g., Akamai tightens detection), residential proxy rotation is the next step. This would involve: proxy pool management, cookie-per-proxy binding, and profile-based session rotation. Budget: ~$1-5/GB for residential proxies. This is not needed now — the current approach should work for 15-route monitoring.

### Sources
- curl_cffi GitHub #627: maintainer confirms HTTP/2 INTERNAL_ERROR is server-side blocking
- Akamai TechDocs: Bot Manager response actions (deny, tarpit, challenge, connection reset)
- Akamai Black Hat EU 2017: HTTP/2 passive fingerprinting
- The Web Scraping Club: Akamai _abck cookie lifecycle, bypassing strategies
- Scrapfly: Akamai bypass techniques (TLS fingerprint, behavioral, IP reputation layers)
