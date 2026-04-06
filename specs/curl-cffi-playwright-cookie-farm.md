# Plan: curl_cffi + Playwright Cookie Farm

## Task Description
Redesign Phase 1, Step 1 to use a hybrid architecture: **curl_cffi for API calls** + **Playwright running in the background as a cookie farm**. The curl_cffi feasibility experiments revealed that Akamai burns `_abck` cookies after ~3-4 FetchAwardCalendar calls. A static cookie set from Chrome DevTools is not viable for sustained scraping. The solution is to keep Playwright alive in the background, continuously maintaining fresh Akamai cookies via its real browser JavaScript execution, and have curl_cffi pull fresh cookies from it before each batch of requests.

## Objective
Build a working scraper that combines curl_cffi's speed/efficiency for API calls with Playwright's ability to maintain valid Akamai cookies. The system must sustain a 90%+ success rate over hundreds of consecutive FetchAwardCalendar calls.

## Problem Statement
From `docs/findings/curl-cffi-feasibility.md`:
- curl_cffi passes Cloudflare TLS checks (not the problem)
- Akamai Bot Manager requires browser-executed JavaScript to generate and refresh `_abck`, `bm_sz`, `bm_sv` cookies
- Static cookies exported from Chrome DevTools work for ~3-4 calls, then Akamai invalidates them server-side
- Pure Playwright works (test_playwright.py confirmed it) but is ~10x slower per request and uses ~200-400 MB RAM per browser context

The core tension: curl_cffi is fast but can't maintain cookies. Playwright maintains cookies but is slow. The cookie farm approach gets both: Playwright's cookie freshness + curl_cffi's request speed.

## Solution Approach

**Architecture: Playwright Cookie Farm + curl_cffi API Caller**

```
┌─────────────────────────────────────────┐
│           Cookie Farm (Playwright)       │
│                                          │
│  Real Chrome browser running in bg       │
│  ├─ Akamai JS sensor executing          │
│  ├─ _abck cookie refreshing             │
│  ├─ Periodic keep-alive navigations      │
│  └─ Exports cookies on demand            │
│                                          │
│  Trigger: navigates to united.com page   │
│  every N requests or every M minutes     │
│  to force Akamai JS to refresh cookies   │
└──────────────┬──────────────────────────┘
               │ fresh cookies
               ▼
┌─────────────────────────────────────────┐
│         API Caller (curl_cffi)           │
│                                          │
│  Fast HTTP with Chrome TLS fingerprint   │
│  ├─ Pulls cookies from Playwright        │
│  ├─ Makes FetchAwardCalendar POST        │
│  ├─ Detects cookie burn (stream reset)   │
│  └─ Requests cookie refresh on burn      │
│                                          │
│  Rate: 7s between calls                  │
│  Expected: 3-4 calls per cookie set      │
│  Cookie refresh: every 2-3 calls         │
└─────────────────────────────────────────┘
```

**Cookie refresh strategy:**
- **Proactive**: Refresh cookies every 2 calls (before the ~3-4 burn threshold)
- **Reactive**: On stream reset / empty response, immediately refresh and retry
- **Keep-alive**: Playwright navigates to a United page every 5 minutes to keep Akamai JS sensor active, even between scrape batches

**How cookie extraction works:**
1. Playwright's browser context holds all cookies for `united.com`
2. Use `context.cookies("https://www.united.com")` to export current cookie values
3. Format as a `Cookie:` header string for curl_cffi
4. Key cookies: `_abck`, `bm_sz`, `bm_sv`, plus session cookies

**Login flow (unchanged from current):**
1. First run: Playwright opens headed Chrome, user logs in manually (Gmail MFA)
2. Session persists in Playwright's `user_data_dir` between runs
3. Subsequent runs: session is reused, no login needed until it expires

## Relevant Files

- `docs/findings/curl-cffi-feasibility.md` — Experiment results proving curl_cffi works with cookies but cookies burn after ~3-4 calls. This is the core constraint driving the cookie farm design.
- `docs/api-contract/united-calendar-api.md` — API contract for FetchAwardCalendar (request body, response schema)
- `docs/api-contract/united-auth-flow.md` — Auth flow, bearer token format, Akamai cookie details
- `docs/api-contract/error-catalog.md` — Error classification (stream reset = cookie burn, 401 = token expired, etc.)
- `scripts/experiments/united_api.py` — Existing shared utility module (request builders, validators, parsers). Reuse directly.
- `scripts/experiments/test_curl_cffi.py` — Previous curl_cffi experiments (reference for what works/fails)
- `scripts/experiments/test_playwright.py` — Previous Playwright experiments (reference for browser setup, login flow, `launch_persistent_context` pattern)

### New Files
- `scripts/experiments/cookie_farm.py` — The Playwright cookie farm module. Runs the browser, handles login, exports fresh cookies on demand.
- `scripts/experiments/hybrid_scraper.py` — The hybrid scraper script. Uses cookie_farm for cookies + curl_cffi for API calls.
- `scripts/experiments/test_hybrid.py` — Experiment script validating the hybrid approach (replaces the role of test_curl_cffi.py Experiment 3).
- `docs/findings/hybrid-architecture.md` — Updated findings document confirming the hybrid architecture.

## Implementation Phases

### Phase 1: Foundation — Cookie Farm Module
Build `cookie_farm.py`: a module that launches Playwright in the background, handles login, and exports cookies on demand. This is the core new component. It must:
- Launch a persistent Chrome context (reusing the `.browser-profile` from test_playwright.py)
- Detect login state and prompt for manual login if needed
- Export current Akamai cookies as a formatted string for curl_cffi
- Periodically refresh cookies by navigating to a United page (keep-alive)
- Run in a background thread so the main thread can make curl_cffi calls

### Phase 2: Core Implementation — Hybrid Scraper
Build `hybrid_scraper.py`: the main scraper that pulls cookies from the farm and makes API calls via curl_cffi. Key behaviors:
- Before each curl_cffi call (or every N calls), request fresh cookies from the farm
- Detect cookie burn (HTTP/2 stream reset, empty response) and trigger immediate refresh
- Use the existing `united_api.py` for request building and response validation
- Log cookie refresh events for debugging

### Phase 3: Integration & Polish — Validation Experiments
Build `test_hybrid.py`: run the same 10-route reliability test from Experiment 3, but with the cookie farm providing fresh cookies. Target: 90%+ success rate (vs the 30% without the farm). Document results in `docs/findings/hybrid-architecture.md`.

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
  - Name: cookie-farm-builder
  - Role: Build the `cookie_farm.py` module — Playwright background browser management, login detection, cookie export, keep-alive navigation, thread-safe cookie access.
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: hybrid-scraper-builder
  - Role: Build `hybrid_scraper.py` — integrates cookie_farm with curl_cffi API calls, implements proactive/reactive cookie refresh, logs all events.
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: experiment-builder
  - Role: Build `test_hybrid.py` — the validation experiment that runs the 10-route reliability test using the hybrid approach, and produce `docs/findings/hybrid-architecture.md`.
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: validator
  - Role: Validate all scripts are syntactically correct, cookie farm properly exports Akamai cookies, hybrid scraper correctly integrates both components, no credentials in tracked files.
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Build Cookie Farm Module
- **Task ID**: build-cookie-farm
- **Depends On**: none
- **Assigned To**: cookie-farm-builder
- **Agent Type**: general-purpose
- **Parallel**: false
- Create `scripts/experiments/cookie_farm.py` with:
  - A `CookieFarm` class that encapsulates all Playwright browser management
  - `__init__(self, user_data_dir, headless=False)`: Store config. Don't launch browser yet.
  - `start(self)`: Launch Playwright persistent context using the same pattern as `test_playwright.py` (`launch_persistent_context` with `channel="chrome"`, `--disable-blink-features=AutomationControlled`, `ignore_default_args=["--enable-automation"]`). Reuse the same `.browser-profile` directory.
  - `ensure_logged_in(self)`: Check login state using the same `check_logged_in()` logic from `test_playwright.py`. If not logged in and not headless, prompt for manual login using `wait_for_login()`. If headless and not logged in, raise an error.
  - `get_cookies(self) -> str`: Export current cookies from `self.context.cookies("https://www.united.com")`. Format as a `Cookie:` header string (e.g., `"_abck=abc123; bm_sz=xyz; bm_sv=...; ..."`). This is the primary interface used by curl_cffi.
  - `get_bearer_token(self) -> str`: Extract the bearer token from the browser. Use `page.evaluate()` to call the `/api/auth/anonymous-token` endpoint from within the browser (same pattern as `test_playwright.py`'s `fetch_calendar_via_api()`), or intercept it from a real API call. Return the full `"bearer DAAAA..."` string.
  - `refresh_cookies(self)`: Force a cookie refresh by navigating to a United page (`https://www.united.com/en/ca/`). This triggers Akamai's JS sensor to regenerate `_abck`. Wait 2-3 seconds for the sensor to complete. Call this proactively every 2 curl_cffi calls or reactively on cookie burn detection.
  - `stop(self)`: Close the browser context and Playwright instance.
  - Context manager support (`__enter__`/`__exit__`) for clean resource management.
  - All methods must be thread-safe (use `threading.Lock`) since the cookie farm runs in the background while curl_cffi calls happen on the main thread.
  - Print status messages: "Cookie farm started", "Login required", "Cookies refreshed", "Cookie farm stopped".
- Key design decisions:
  - The cookie farm does NOT make API calls itself — it only maintains the browser session and exports cookies/tokens.
  - Cookie export is a lightweight operation (reading from Playwright's cookie store, not a network call).
  - `refresh_cookies()` IS a network operation (page navigation) and takes 2-5 seconds. Budget this into the scraping pace.
  - The farm reuses the same `.browser-profile` directory as `test_playwright.py`, so an existing login session carries over.

### 2. Build Hybrid Scraper
- **Task ID**: build-hybrid-scraper
- **Depends On**: build-cookie-farm
- **Assigned To**: hybrid-scraper-builder
- **Agent Type**: general-purpose
- **Parallel**: false
- Create `scripts/experiments/hybrid_scraper.py` with:
  - Import `CookieFarm` from `cookie_farm.py` and `united_api` from `united_api.py`
  - Import `curl_cffi.requests.Session`
  - A `HybridScraper` class:
    - `__init__(self, cookie_farm: CookieFarm, refresh_interval: int = 2)`: Store the farm reference and how often to refresh cookies (default: every 2 calls, well under the ~3-4 burn threshold).
    - `fetch_calendar(self, origin, destination, depart_date) -> dict`: The main method. Steps:
      1. Check if cookies need refresh (call count >= `refresh_interval` since last refresh)
      2. If yes, call `self.farm.refresh_cookies()` then `self.farm.get_cookies()`
      3. If no, use cached cookies from last refresh
      4. Build request with `united_api.build_calendar_request()`
      5. Build headers with `united_api.build_headers(bearer_token, cookies)`
      6. Send POST via `curl_cffi.requests.Session.post()`
      7. Validate with `united_api.validate_response()`
      8. If stream reset or empty response (cookie burn detected):
         - Log "Cookie burn detected, refreshing..."
         - Call `self.farm.refresh_cookies()` + `self.farm.get_cookies()`
         - Retry once with fresh cookies
         - If retry fails, log and return failure
      9. Return result dict: `{success, status_code, data, elapsed_ms, error, cookie_refreshed}`
    - `scrape_routes(self, routes: list, delay: float = 7.0) -> list`: Batch scrape multiple routes with delays. For each route, call `fetch_calendar()`, sleep `delay` seconds between calls. Return list of results.
    - `start(self)`: Initialize curl_cffi Session with `impersonate="chrome136"`. Get initial cookies and bearer token from the farm.
    - `stop(self)`: Close curl_cffi session.
    - Context manager support.
  - A simple CLI:
    - `python hybrid_scraper.py --route YYZ LAX` — scrape one route
    - `python hybrid_scraper.py --routes-file routes.txt` — scrape from a file (one "ORIG DEST" per line)
    - `python hybrid_scraper.py --canada-test` — scrape the 10 test routes from ROUTES list
    - Prints results as a summary table (same format as test_curl_cffi.py Experiment 3)
  - Cookie burn detection logic:
    - HTTP/2 INTERNAL_ERROR stream reset → cookie burn
    - Empty response body → likely cookie burn
    - Response but `_abck` cookie marked invalid server-side → cookie burn
    - Distinguish from other errors: 401 = token expired (not cookie burn), 403 = Cloudflare (not cookie burn), 429 = rate limit (not cookie burn)
  - Logging: print each request's status, whether cookies were refreshed, response time, solutions count

### 3. Build Validation Experiment
- **Task ID**: build-experiment
- **Depends On**: build-hybrid-scraper
- **Assigned To**: experiment-builder
- **Agent Type**: general-purpose
- **Parallel**: false
- Create `scripts/experiments/test_hybrid.py` implementing:
  - **Experiment 1 — Single call**: Start cookie farm, get cookies, make one curl_cffi call. Validates the basic pipeline works.
  - **Experiment 2 — Cookie refresh cycle**: Make 6 calls, refreshing cookies every 2 calls. Validates proactive refresh prevents burns.
  - **Experiment 3 — Full reliability test**: The same 10-route test from test_curl_cffi.py Experiment 3, but with the cookie farm. Target: 90%+ success rate. This is the critical pass/fail test.
  - **Experiment 4 — Burn recovery**: Intentionally skip cookie refresh for 5+ calls to trigger a burn, then verify the reactive refresh + retry recovers.
  - CLI: `python test_hybrid.py` (all), `python test_hybrid.py --experiment N`
  - Summary table and PASS/FAIL verdicts matching the format from test_curl_cffi.py
  - Final verdict:
    ```
    ════════════════════════════════════════
    OVERALL RESULTS
    ════════════════════════════════════════
    Experiment 1 (Single call):          PASS/FAIL
    Experiment 2 (Cookie refresh cycle): PASS/FAIL
    Experiment 3 (Full reliability):     PASS/FAIL
    Experiment 4 (Burn recovery):        PASS/FAIL

    Architecture: curl_cffi + Playwright cookie farm [CONFIRMED/NOT VIABLE]

    Cookie refresh interval: every N calls
    Cookie farm overhead: ~X seconds per refresh
    Effective scrape rate: ~Y requests/minute

    Next step: [Build production scraper with cookie farm / Investigate alternatives]
    ════════════════════════════════════════
    ```

### 4. Update Project Brief Phase 1 Step 1
- **Task ID**: update-project-brief
- **Depends On**: build-experiment
- **Assigned To**: experiment-builder
- **Agent Type**: general-purpose
- **Parallel**: false
- Update `docs/project-brief.md` Phase 1 plan table, Step 1 row:
  - Change the "What" column from the current curl_cffi-only description to reflect the hybrid approach:
    - **What**: "Build hybrid scraper: curl_cffi for API calls + Playwright cookie farm in background. Playwright maintains Akamai cookies (`_abck`) that burn every ~3-4 requests. curl_cffi pulls fresh cookies before each batch. Manual login via Playwright on first run (Gmail MFA)."
    - **Why**: "curl_cffi alone fails — Akamai burns cookies after ~3-4 calls (see findings). Pure Playwright works but is 10x slower. Hybrid gets curl_cffi speed with Playwright cookie freshness."
  - Update `docs/project-brief.md` "Anti-bot evasion" paragraph (around line 102) to reflect the hybrid finding:
    - Remove the suggestion that curl_cffi alone handles Cloudflare
    - Note that Akamai Bot Manager (not Cloudflare) is the real barrier
    - Document the hybrid architecture as the proven approach
  - Update `docs/findings/curl-cffi-feasibility.md` "Architecture Decision" section:
    - Change from "Playwright Required" to "Hybrid: curl_cffi + Playwright Cookie Farm"
    - Add the cookie farm architecture diagram
    - Note that pure Playwright remains a fallback if the hybrid approach has issues

### 5. Update Findings Document
- **Task ID**: update-findings
- **Depends On**: build-experiment
- **Assigned To**: experiment-builder
- **Agent Type**: general-purpose
- **Parallel**: true (can run alongside update-project-brief)
- Create `docs/findings/hybrid-architecture.md` documenting:
  - **Summary**: One-paragraph verdict on the hybrid approach
  - **Architecture**: curl_cffi for API calls + Playwright cookie farm for Akamai cookies
  - **Cookie Lifecycle**: How `_abck` cookies are generated, how often they burn, how the farm refreshes them
  - **Experiment Results**: Table from test_hybrid.py
  - **Performance Comparison**:
    | Approach | Success Rate | Avg Request Time | RAM Usage | Complexity |
    |---|---|---|---|---|
    | curl_cffi only (no cookies) | 0% | N/A | ~50 MB | Low |
    | curl_cffi + static cookies | ~30% (burns after 3-4) | ~300ms | ~50 MB | Low |
    | Pure Playwright | ~100% | ~3-5s | ~400 MB | Medium |
    | **Hybrid (cookie farm)** | **target 90%+** | **~300ms + refresh overhead** | **~450 MB** | **Medium** |
  - **Optimal Cookie Refresh Interval**: Based on experiment results — how often to refresh without wasting time
  - **Implications for Phase 1 Production Scraper**:
    - Dependencies: curl_cffi, playwright, python-dotenv
    - RAM: ~450 MB (Playwright browser + Python process)
    - Daily workflow: first run needs manual login; subsequent runs reuse session
    - Effective throughput: calculate based on API call time + cookie refresh overhead
    - At 2,000 Canada routes × 12 months = 24,000 calls, with 7s delays and cookie refreshes every 2 calls: estimate total sweep time
  - **Open Questions**:
    - How long does a single Playwright session last before requiring re-login?
    - Can the cookie farm run headless after initial headed login?
    - Does cookie refresh frequency need to adapt based on time of day or Akamai behavior changes?
    - Can multiple curl_cffi sessions share cookies from one farm, or does each need its own?

### 6. Validate All Results
- **Task ID**: validate-all
- **Depends On**: update-project-brief, update-findings
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all new scripts exist and are syntactically valid:
  - `scripts/experiments/cookie_farm.py` — has `CookieFarm` class with `start()`, `get_cookies()`, `get_bearer_token()`, `refresh_cookies()`, `stop()`, thread safety
  - `scripts/experiments/hybrid_scraper.py` — has `HybridScraper` class, imports both `CookieFarm` and `curl_cffi`, has cookie burn detection, proactive + reactive refresh logic
  - `scripts/experiments/test_hybrid.py` — has 4 experiments, CLI interface, PASS/FAIL verdicts
- Verify `cookie_farm.py` properly integrates with existing code:
  - Uses same `.browser-profile` directory as `test_playwright.py`
  - Uses same `launch_persistent_context` pattern (channel="chrome", anti-detection args)
  - Cookie export formats correctly for curl_cffi's `Cookie:` header
- Verify `hybrid_scraper.py`:
  - Uses `united_api.build_calendar_request()` and `united_api.build_headers()` from existing module
  - Uses `united_api.validate_response()` for error classification
  - Handles cookie burn (stream reset) distinctly from other errors (401, 403, 429)
  - Refresh interval is configurable and defaults to 2 (conservative, under the ~3-4 burn threshold)
- Verify project brief updates:
  - Phase 1 Step 1 description reflects hybrid architecture
  - "Anti-bot evasion" section no longer claims curl_cffi alone handles everything
  - Akamai Bot Manager is mentioned as the real barrier (not just Cloudflare)
- Verify findings documents:
  - `docs/findings/hybrid-architecture.md` exists with all required sections
  - `docs/findings/curl-cffi-feasibility.md` architecture section updated to reference hybrid approach
- Verify security:
  - No credentials in tracked files
  - `.browser-profile/` is in `.gitignore`
- Run `python -m py_compile` on all new Python files
- Report any gaps

## Acceptance Criteria
- [ ] `scripts/experiments/cookie_farm.py` exists with `CookieFarm` class — thread-safe cookie export, Playwright management, login handling, keep-alive refresh
- [ ] `scripts/experiments/hybrid_scraper.py` exists — integrates cookie farm with curl_cffi, proactive + reactive cookie refresh, cookie burn detection
- [ ] `scripts/experiments/test_hybrid.py` exists with 4 experiments and PASS/FAIL verdicts
- [ ] `docs/project-brief.md` Phase 1 Step 1 updated to reflect hybrid architecture
- [ ] `docs/project-brief.md` "Anti-bot evasion" section corrected (Akamai, not just Cloudflare)
- [ ] `docs/findings/hybrid-architecture.md` created with architecture decision, performance comparison, and experiment template
- [ ] `docs/findings/curl-cffi-feasibility.md` architecture section updated to reference hybrid approach
- [ ] All Python scripts pass `python -m py_compile`
- [ ] No credentials in tracked files; `.browser-profile/` in `.gitignore`
- [ ] Cookie farm reuses existing `.browser-profile` from test_playwright.py (no duplicate login needed)

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# Verify new files exist
test -f scripts/experiments/cookie_farm.py && echo "PASS: cookie_farm.py exists" || echo "FAIL"
test -f scripts/experiments/hybrid_scraper.py && echo "PASS: hybrid_scraper.py exists" || echo "FAIL"
test -f scripts/experiments/test_hybrid.py && echo "PASS: test_hybrid.py exists" || echo "FAIL"
test -f docs/findings/hybrid-architecture.md && echo "PASS: hybrid findings exists" || echo "FAIL"

# Verify Python syntax
python -m py_compile scripts/experiments/cookie_farm.py && echo "PASS: cookie_farm syntax OK" || echo "FAIL"
python -m py_compile scripts/experiments/hybrid_scraper.py && echo "PASS: hybrid_scraper syntax OK" || echo "FAIL"
python -m py_compile scripts/experiments/test_hybrid.py && echo "PASS: test_hybrid syntax OK" || echo "FAIL"

# Verify cookie_farm has required class and methods
grep -q "class CookieFarm" scripts/experiments/cookie_farm.py && echo "PASS: CookieFarm class" || echo "FAIL"
grep -q "def get_cookies" scripts/experiments/cookie_farm.py && echo "PASS: get_cookies method" || echo "FAIL"
grep -q "def refresh_cookies" scripts/experiments/cookie_farm.py && echo "PASS: refresh_cookies method" || echo "FAIL"
grep -q "def get_bearer_token" scripts/experiments/cookie_farm.py && echo "PASS: get_bearer_token method" || echo "FAIL"
grep -q "threading" scripts/experiments/cookie_farm.py && echo "PASS: thread safety" || echo "FAIL"

# Verify hybrid_scraper integrates both
grep -q "CookieFarm" scripts/experiments/hybrid_scraper.py && echo "PASS: imports CookieFarm" || echo "FAIL"
grep -q "curl_cffi" scripts/experiments/hybrid_scraper.py && echo "PASS: imports curl_cffi" || echo "FAIL"
grep -q "united_api" scripts/experiments/hybrid_scraper.py && echo "PASS: imports united_api" || echo "FAIL"
grep -q "refresh_cookies\|cookie.*burn\|stream.*reset" scripts/experiments/hybrid_scraper.py && echo "PASS: cookie refresh logic" || echo "FAIL"

# Verify project brief updated
grep -qi "cookie farm\|cookie.farm\|playwright.*background" docs/project-brief.md && echo "PASS: project brief updated" || echo "FAIL"
grep -qi "akamai" docs/project-brief.md && echo "PASS: Akamai mentioned in brief" || echo "FAIL"

# Verify .gitignore covers browser profile
grep -q "browser-profile" .gitignore && echo "PASS: .browser-profile excluded" || echo "FAIL"

# Verify no credentials
grep -r "DAAAA\|bearer.*[A-Z].*[A-Z].*[A-Z]" scripts/experiments/*.py 2>/dev/null | grep -v "placeholder\|example\|paste\|INVALID" && echo "WARNING: possible credentials" || echo "PASS: no credentials"
```

## Notes
- **Cookie refresh overhead**: Each `refresh_cookies()` call takes 2-5 seconds (Playwright page navigation). At a refresh interval of 2 (every 2 API calls), this adds ~1-2.5 seconds per API call on average. With 7-second delays between calls, effective pace is ~9-10 seconds per call. For 24,000 calls (Canada sweep), total time is ~60-67 hours if single-threaded. This is too slow for a daily sweep.
  - **Optimization**: Test whether cookies actually burn at exactly 3-4 calls or if this varies. If consistent, set refresh interval to 3 (just before burn). If variable, keep at 2 for safety.
  - **Optimization**: Test whether `context.cookies()` returns fresh-enough cookies without a full page navigation. If Akamai's JS sensor runs continuously (not just on page load), cookies may stay fresh without explicit refresh.
  - **Optimization**: If cookies stay valid for a time window (not just a call count), the farm can refresh on a timer rather than per-call, dramatically reducing overhead.
  - **Critical finding needed**: Is the cookie burn based on call count, time, or both? The experiments must answer this.
- **RAM budget**: Playwright Chrome context uses ~200-400 MB. curl_cffi is negligible. Total ~450 MB. A laptop handles this easily. The Hetzner CX22 (4 GB RAM) may be tight with PostgreSQL running too — the CX32 (8 GB) recommendation in the brief stands.
- **Fallback**: If the hybrid approach proves unreliable or the cookie refresh overhead is too high, pure Playwright (as proven in test_playwright.py) remains the fallback. It's slower but has near-100% reliability.
- **No new dependencies**: Both curl_cffi and playwright are already in the project. No new packages needed.
- **Session reuse**: The cookie farm uses the same `.browser-profile` directory as `test_playwright.py`. If the user already logged in during Playwright experiments, the session carries over — no re-login needed.
