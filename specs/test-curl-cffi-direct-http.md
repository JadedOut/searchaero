# Plan: Test curl_cffi Direct HTTP Path

## Task Description
Phase 1, Step 1: Test whether curl_cffi can make authenticated API calls to United's FetchAwardCalendar endpoint and get valid responses through Cloudflare. This is the critical feasibility test for the scraper's data-fetching layer.

**Authentication approach (decided):** Manual. The user logs into united.com in Chrome, grabs the bearer token from DevTools, and pastes it into a `.env` file. United requires Gmail MFA on login, which makes automated auth complex. For Phase 1 Canada scale (single account, one daily sweep taking ~2 hours), manual token acquisition once per day is acceptable and avoids the MFA problem entirely. Automated auth (Playwright + IMAP MFA) is deferred to a future step if/when daily manual login becomes burdensome.

The test focuses solely on the API call layer:
1. **Cloudflare bypass**: Can curl_cffi's Chrome TLS fingerprint impersonation pass Cloudflare's bot detection?
2. **API access**: Can curl_cffi make authenticated POST requests to FetchAwardCalendar with a manually-obtained bearer token and get valid JSON responses?
3. **Minimum requirements**: What headers/cookies are actually required for a successful call?
4. **Reliability at scraping pace**: Does curl_cffi sustain a 90%+ success rate over multiple calls with 7-second delays?

## Objective
Produce a working proof-of-concept script that fetches United award calendar data via curl_cffi using a manually-provided bearer token. Determine whether curl_cffi is viable for the API call layer (the 24,000 requests/day part), independent of how the token is obtained.

The deliverable is:
- A working, runnable experiment script (`test_curl_cffi.py`)
- A shared API utility module (`united_api.py`) reusable by the future production scraper
- A findings document recording what works, what doesn't, and the confirmed architecture

## Problem Statement
The project brief recommends curl_cffi as the primary HTTP tool (per Scraperly: "curl_cffi with Chrome TLS fingerprint impersonation handles Cloudflare, ~85% success rate"). This has never been empirically tested against United's specific Cloudflare configuration. If curl_cffi can't pass Cloudflare, the scraper needs Playwright for every API call (heavier, ~10x more resource usage). This step answers that question definitively.

Separately, the auth flow doc incorrectly states "No 2FA required (unlike Aeroplan)." United now requires Gmail-based MFA on login. This makes automated auth a separate, harder problem. By decoupling auth (manual) from API calls (curl_cffi), we can test the API layer in isolation and defer the MFA challenge.

## Solution Approach

Run three incremental experiments using a manually-obtained bearer token:

1. **Experiment 1 — Single API call**: Use curl_cffi with a manual bearer token to call FetchAwardCalendar for one route. Tests Cloudflare bypass + API access.
2. **Experiment 2 — Minimum viable request**: Strip headers/cookies to find the minimum required for a successful call. Simplifies the production scraper.
3. **Experiment 3 — Rate & reliability**: Make 10 successive calls at scraping pace (7s delays, varied routes). Tests sustained reliability.

Each experiment has clear pass/fail criteria. The user must manually obtain a fresh bearer token before running.

### Manual Token Acquisition Workflow (Phase 1 production)
This is how the scraper will work day-to-day during Phase 1:
1. User opens Chrome, navigates to united.com, logs in (enters email, password, Gmail MFA code)
2. User opens DevTools (F12) → Network tab
3. User navigates to an award search page (any route)
4. User finds any request to `/api/flight/*`, clicks it, copies the `x-authorization-api` header value
5. User pastes the token into `scripts/experiments/.env` as `UNITED_BEARER_TOKEN=bearer DAAAA...`
6. User runs the scraper — it completes the daily sweep in ~2 hours using curl_cffi
7. Next day, repeat from step 1

This is acceptable at Canada scale because:
- One login per day, ~60 seconds of manual effort
- Token lasts hours — easily covers a 2-hour sweep of 2,000 routes
- Zero dependency on Playwright, IMAP, or any auth automation
- The scraper itself is a pure Python script with only `curl_cffi` as a dependency

## Relevant Files

Use these files to complete the task:

- `docs/api-contract/united-calendar-api.md` — Full API contract for FetchAwardCalendar (endpoint URL, request body structure, headers, response schema). Primary reference for constructing API requests.
- `docs/api-contract/united-auth-flow.md` — Authentication flow documentation (bearer token format, required headers, session lifecycle). Note: this doc's "No 2FA" claim is incorrect and should be corrected.
- `docs/api-contract/error-catalog.md` — Error response catalog (403, 429, 401 detection and handling). Use to classify failures during testing.
- `docs/api-contract/sample-responses/calendar-response.json` — Known-good response to validate against.
- `docs/project-brief.md` — Project context, Scraperly reference (lines 92-102), curl_cffi recommendation.

### New Files
- `scripts/experiments/test_curl_cffi.py` — Main experiment script. Runs Experiments 1-3 (manual token → curl_cffi API calls).
- `scripts/experiments/united_api.py` — Shared utility module with request templates, header builders, and response validators. Designed for reuse by the production scraper in Step 2.
- `scripts/experiments/requirements.txt` — Python dependencies (curl_cffi + python-dotenv only, no Playwright).
- `scripts/experiments/.env.example` — Placeholder showing the bearer token field.
- `scripts/experiments/README.md` — Instructions for obtaining a token and running experiments.
- `docs/findings/curl-cffi-feasibility.md` — Final findings document with architecture confirmation.

## Implementation Phases

### Phase 1: Foundation — Environment & Utilities
Set up Python environment with curl_cffi (no Playwright needed). Create the experiment directory structure. Build the shared `united_api.py` utility module with request templates from the API contract.

### Phase 2: Core Implementation — Experiments 1-3
Run the three experiments in order. Log all raw responses (status codes, headers, body snippets) for analysis. Compare responses against the known-good sample response.

### Phase 3: Integration & Polish — Findings & Correction
Document findings. Correct the auth flow doc's MFA claim. Produce the findings document that confirms the architecture for Step 2.

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
  - Name: env-setup
  - Role: Set up Python environment, install dependencies (curl_cffi only, no Playwright), create directory structure, build the shared `united_api.py` utility module with request templates from the API contract.
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: experimenter
  - Role: Build and implement the experiment script (`test_curl_cffi.py`) with all three experiments. The user will provide the bearer token manually — the script reads it from `.env`. This builder writes the code; the user runs it and reports results.
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: doc-writer
  - Role: Analyze experiment results (once the user reports them), produce the findings document, and correct the auth flow doc's MFA claim.
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: validator
  - Role: Validate that all scripts are syntactically correct, request templates match the API contract, no credentials are in tracked files, and the findings document is complete.
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Set Up Experiment Environment
- **Task ID**: setup-experiment-env
- **Depends On**: none
- **Assigned To**: env-setup
- **Agent Type**: general-purpose
- **Parallel**: false
- Create directory `scripts/experiments/`
- Create `scripts/experiments/requirements.txt` with minimal dependencies:
  ```
  curl_cffi>=0.7.0
  python-dotenv>=1.0.0
  ```
  No Playwright — auth is manual for Phase 1.
- Create `scripts/experiments/.env.example` with:
  ```
  # Bearer token from Chrome DevTools.
  # How to get this:
  #   1. Log into united.com in Chrome (you'll need to complete Gmail MFA)
  #   2. Open DevTools (F12) → Network tab
  #   3. Navigate to any award search (e.g., search YYZ to LAX with "Book with miles" checked)
  #   4. Find any request to /api/flight/FetchAwardCalendar or /api/flight/FetchFlights
  #   5. Click the request → Headers tab → copy the full "x-authorization-api" header value
  #   6. Paste below (include the "bearer " prefix)
  #
  # Token expires after several hours. Get a fresh one each day before running.
  UNITED_BEARER_TOKEN=bearer DAAAA...paste_your_token_here
  ```
- Update `.gitignore` to exclude:
  - `scripts/experiments/.env`
  - `scripts/experiments/*.log`
  - `scripts/experiments/__pycache__/`
- Create shared utility module `scripts/experiments/united_api.py` containing:
  - The FetchAwardCalendar request body template (from `docs/api-contract/united-calendar-api.md` lines 66-114)
  - The required headers template (from `docs/api-contract/united-auth-flow.md` lines 109-133)
  - A function `build_calendar_request(origin, destination, depart_date)` that returns a complete request body dict
  - A function `build_headers(bearer_token)` that returns the full headers dict with all required and recommended headers
  - A function `validate_response(response)` that checks HTTP status, Content-Type, parses JSON, verifies `data.Status == 1`, and classifies errors per the error catalog (403 → Cloudflare, 429 → rate limit, 401 → token expired, HTML → session expired)
  - A function `parse_calendar_solutions(response_json)` that extracts day/cabin/miles/taxes from the response following the schema in `docs/api-contract/united-calendar-api.md` lines 214-270
  - Constants: `CALENDAR_URL = "https://www.united.com/api/flight/FetchAwardCalendar"`
  - CabinType mapping dict from `docs/api-contract/united-calendar-api.md` lines 655-663
- Create `scripts/experiments/README.md` with:
  - Purpose: "Empirical testing of curl_cffi against United's award search API"
  - Prerequisites: Python 3.10+, a MileagePlus account
  - Step-by-step instructions for obtaining a bearer token manually (with screenshots description)
  - How to set up `.env` from `.env.example`
  - How to install dependencies: `pip install -r requirements.txt`
  - How to run: `python test_curl_cffi.py`
  - What to expect: PASS/FAIL verdicts for each experiment
  - Note about token expiry: "Tokens last several hours. Get a fresh one if you see 401 errors."

### 2. Build Experiment Script — All Three Experiments
- **Task ID**: build-experiments
- **Depends On**: setup-experiment-env
- **Assigned To**: experimenter
- **Agent Type**: general-purpose
- **Parallel**: false
- Create `scripts/experiments/test_curl_cffi.py` implementing all three experiments in a single script with a CLI interface:
  - `python test_curl_cffi.py` — runs all experiments sequentially
  - `python test_curl_cffi.py --experiment 1` — runs only Experiment 1
  - `python test_curl_cffi.py --experiment 2` — runs only Experiment 2
  - `python test_curl_cffi.py --experiment 3` — runs only Experiment 3

- **Experiment 1 — Single API Call (Cloudflare + API test)**:
  - Load bearer token from `.env` via `python-dotenv`
  - If token is missing or is the placeholder, print the manual token instructions from `.env.example` and exit
  - Create a `curl_cffi.requests.Session` with `impersonate="chrome131"` (match Chrome version from captured headers)
  - Build a FetchAwardCalendar POST request for YYZ → LAX, departure date 30 days from today, using `united_api.build_calendar_request()` and `united_api.build_headers()`
  - Send the request
  - Log: HTTP status, key response headers (`content-type`, `cf-ray`, any `cf-*` or rate-limit headers), response body first 500 chars
  - Run `united_api.validate_response()` to classify the result
  - If valid JSON with `data.Status == 1`: parse Solutions with `united_api.parse_calendar_solutions()`, print count of days with data, sample of cabin/miles values
  - Print clear PASS/FAIL verdict:
    - PASS: "curl_cffi successfully fetched award calendar data through Cloudflare. {N} days with pricing data returned."
    - FAIL: "curl_cffi failed. Status: {code}. Error type: {classification}. Details: {snippet}"
  - If FAIL with 403: suggest trying a different impersonation target and print available targets from `curl_cffi`

- **Experiment 2 — Minimum Viable Request**:
  - Only runs if Experiment 1 passed
  - **Test A — Bearer token only (no cookies)**: Create a fresh `curl_cffi.requests.Session` (no cookie jar from previous requests), send the same request with full headers but explicitly no cookies. Record pass/fail.
  - **Test B — Minimal headers**: Send request with ONLY `x-authorization-api`, `Content-Type`, `User-Agent`, and `Accept` headers (drop all `sec-*`, `Origin`, `Referer`). Record pass/fail.
  - **Test C — Bad token confirmation**: Send request with `x-authorization-api: bearer INVALID_TOKEN_12345`. Confirm we get a non-200 or error response. This validates our error detection works.
  - Print summary:
    ```
    Experiment 2 — Minimum Viable Request
    ──────────────────────────────────────
    Test A (no cookies):      PASS/FAIL (status: NNN)
    Test B (minimal headers): PASS/FAIL (status: NNN)
    Test C (bad token):       PASS/FAIL (got expected error: yes/no)

    Minimum required: [bearer token only / bearer + sec-* headers / bearer + cookies + sec-* headers]
    ```

- **Experiment 3 — Rate & Reliability**:
  - Only runs if Experiment 1 passed
  - Use the minimum viable request configuration determined by Experiment 2 (or full headers if Experiment 2 wasn't run)
  - Make 10 successive FetchAwardCalendar calls with 7-second delays between each
  - Routes list (Canada airports to vary the requests):
    ```python
    ROUTES = [
        ("YYZ", "LAX"), ("YYZ", "SFO"), ("YYZ", "ORD"),
        ("YVR", "LAX"), ("YUL", "JFK"), ("YYZ", "DEN"),
        ("YYC", "SEA"), ("YOW", "EWR"), ("YYZ", "IAH"),
        ("YVR", "SFO"),
    ]
    ```
  - Departure dates: stagger across next 12 months (30 days out, 60, 90, ... 300)
  - For each call, record: call number, route, HTTP status, response time in ms, valid JSON (yes/no), solutions count, any error classification, cf-ray header
  - Print a summary table:
    ```
    Experiment 3 — Rate & Reliability (10 calls, 7s delay)
    ──────────────────────────────────────────────────────
    #  | Route     | Status | Time(ms) | Valid | Solutions | Notes
    1  | YYZ-LAX   | 200    | 342      | YES   | 156       |
    2  | YYZ-SFO   | 200    | 289      | YES   | 148       |
    ...
    10 | YVR-SFO   | 200    | 315      | YES   | 132       |

    Summary:
    - Success rate: 10/10 (100%)
    - Avg response time: 305ms
    - Failures: none
    - 403 (Cloudflare): 0
    - 429 (Rate limit): 0
    - 401 (Token expired): 0

    VERDICT: PASS — curl_cffi is reliable at 7s intervals
    ```
  - **Pass criteria**: ≥90% success rate, no 403s, no 429s
  - **Fail criteria**: Any 403 (Cloudflare blocking), any 429 (rate limit), >20% failure rate
  - If a 403 occurs mid-run: log the cf-ray, try one more call with a different impersonation target, note whether the version change helped

- **Final summary**: After all experiments, print an overall verdict:
  ```
  ════════════════════════════════════════
  OVERALL RESULTS
  ════════════════════════════════════════
  Experiment 1 (Single call):       PASS/FAIL
  Experiment 2 (Minimum request):   PASS/FAIL
  Experiment 3 (Rate & reliability): PASS/FAIL

  Architecture: curl_cffi with manual bearer token [CONFIRMED/NOT VIABLE]

  Next step: [Build the production scraper / Fall back to Playwright for API calls]
  ════════════════════════════════════════
  ```

### 3. Correct Auth Flow Documentation
- **Task ID**: correct-auth-doc
- **Depends On**: setup-experiment-env
- **Assigned To**: doc-writer
- **Agent Type**: general-purpose
- **Parallel**: true (independent of experiments)
- Update `docs/api-contract/united-auth-flow.md`:
  - Correct the "No 2FA required" claim in the Overview section. Change to: "Gmail-based MFA required. After entering email + password, United sends a verification code to the registered email. This must be completed manually or via IMAP automation."
  - Add a new section "### Phase 1 Authentication Strategy" documenting the manual token workflow:
    1. User logs into united.com in Chrome (completes Gmail MFA manually)
    2. User copies bearer token from DevTools Network tab
    3. Token is pasted into `.env` for the scraper to use
    4. Token expires after several hours — one manual login per day covers a full Canada sweep
    5. Future: automate via Playwright + IMAP email reading when scaling beyond daily manual effort
  - Add a note under "### curl_cffi Consideration" that pure curl_cffi auth is not feasible due to Gmail MFA — Playwright or manual browser login is required for token acquisition
  - Keep all existing content about token format, headers, session lifecycle, etc. — that's still accurate

### 4. Document Findings & Architecture Confirmation
- **Task ID**: document-findings
- **Depends On**: build-experiments, correct-auth-doc
- **Assigned To**: doc-writer
- **Agent Type**: general-purpose
- **Parallel**: false
- **Note**: This task runs AFTER the user has executed the experiments and reported results. The doc-writer uses the experiment output to produce the findings document. If the experiments haven't been run yet, create the document as a template with placeholder results.
- Create `docs/findings/` directory if it doesn't exist
- Create `docs/findings/curl-cffi-feasibility.md` documenting:
  - **Summary**: One-paragraph verdict on curl_cffi viability for API calls
  - **Authentication Decision**: Manual token acquisition via Chrome DevTools. United requires Gmail MFA, making automated login complex. At Canada scale (1 account, 1 daily sweep, ~2 hours), manual login once per day is acceptable (~60 seconds of human effort). Automated auth (Playwright + IMAP) deferred to Phase 1b or when manual effort becomes a bottleneck.
  - **Experiment Results**: Table summarizing each experiment's outcome
    ```
    | Experiment | Description | Result | Notes |
    |---|---|---|---|
    | 1 | Single curl_cffi API call | PASS/FAIL | ... |
    | 2 | Minimum viable request | ... | Minimum: ... |
    | 3 | Rate & reliability (10 calls) | ... | Success rate: ...% |
    ```
  - **Architecture Confirmation**: Based on results, confirm:
    - **API calls**: curl_cffi with Chrome TLS impersonation (or Playwright if curl_cffi fails)
    - **Authentication**: Manual — user logs in via Chrome, copies bearer token to `.env`
    - **Token lifecycle**: Token obtained once per day, lasts hours, covers a full 2-hour sweep
    - **No Playwright dependency**: The production scraper only needs `curl_cffi` and `python-dotenv`
  - **Minimum Viable Request**: Document the minimum headers/cookies needed
  - **Cloudflare Observations**: TLS fingerprint behavior, impersonation version that works
  - **Rate Limit Observations**: Observed behavior at 7s delays
  - **Implications for Phase 1 Production Scraper**:
    - Dependencies: curl_cffi, python-dotenv (minimal)
    - Resource usage: negligible (no browser, no headless rendering)
    - Can run on user's laptop — no VPS needed for Canada scale
    - Daily workflow: ~60 seconds manual login + start scraper
  - **Open Questions / Future Work**:
    - Token expiry timing (exact hours) — to be measured during Step 3 burn-in
    - Automated auth via Playwright + IMAP — deferred to when manual becomes burdensome
    - Whether token can survive IP changes (relevant if running from different networks)

### 5. Validate All Results
- **Task ID**: validate-all
- **Depends On**: document-findings
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all scripts exist and are syntactically valid:
  - `scripts/experiments/test_curl_cffi.py` — exists, imports curl_cffi, has all 3 experiments, has CLI argument parsing
  - `scripts/experiments/united_api.py` — exists, has `build_calendar_request()`, `build_headers()`, `validate_response()`, `parse_calendar_solutions()`, `CALENDAR_URL` constant, CabinType mapping
  - `scripts/experiments/requirements.txt` — exists, lists curl_cffi and python-dotenv (NOT playwright)
  - `scripts/experiments/.env.example` — exists, has `UNITED_BEARER_TOKEN` placeholder with manual instructions
  - `scripts/experiments/README.md` — exists, has step-by-step token acquisition instructions
- Verify the API request templates in `united_api.py` match the documented API contract:
  - Request body structure matches `docs/api-contract/united-calendar-api.md` (fields: SearchTypeSelection, Trips, AwardTravel, CalendarLengthOfStay, etc.)
  - Headers match `docs/api-contract/united-auth-flow.md` (x-authorization-api, Content-Type, User-Agent, sec-* headers)
  - Error classification matches `docs/api-contract/error-catalog.md` (403, 429, 401, 302, HTML response handling)
- Verify auth flow doc was corrected:
  - `docs/api-contract/united-auth-flow.md` no longer claims "No 2FA required"
  - Contains Phase 1 manual auth strategy section
- Verify findings document:
  - `docs/findings/curl-cffi-feasibility.md` exists, has all required sections
  - "Authentication Decision" section documents manual approach with Gmail MFA context
  - Architecture decision is clearly stated
- Verify security:
  - `.gitignore` excludes `.env`, `*.log`, `__pycache__/` in experiments directory
  - No actual tokens or credentials in any tracked file
  - `.env.example` contains only placeholder values
- Report any gaps or inconsistencies

## Acceptance Criteria
- [ ] `scripts/experiments/test_curl_cffi.py` exists with Experiments 1-3 and CLI interface
- [ ] `scripts/experiments/united_api.py` exists with request builders and validators matching the API contract
- [ ] `scripts/experiments/requirements.txt` lists curl_cffi and python-dotenv only (no Playwright)
- [ ] `scripts/experiments/.env.example` has manual bearer token instructions including Gmail MFA context
- [ ] `scripts/experiments/README.md` has clear step-by-step manual token acquisition instructions
- [ ] `docs/api-contract/united-auth-flow.md` corrected: Gmail MFA documented, Phase 1 manual auth strategy added
- [ ] `docs/findings/curl-cffi-feasibility.md` exists with authentication decision, experiment results (or template), and architecture confirmation
- [ ] No credentials, tokens, or session data in any git-tracked file
- [ ] `.gitignore` updated to protect experiment artifacts
- [ ] All scripts pass `python -m py_compile` syntax check

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# Verify directory structure
ls -la scripts/experiments/

# Verify key files exist
test -f scripts/experiments/test_curl_cffi.py && echo "PASS: test_curl_cffi.py exists" || echo "FAIL"
test -f scripts/experiments/united_api.py && echo "PASS: united_api.py exists" || echo "FAIL"
test -f scripts/experiments/requirements.txt && echo "PASS: requirements.txt exists" || echo "FAIL"
test -f scripts/experiments/.env.example && echo "PASS: .env.example exists" || echo "FAIL"
test -f scripts/experiments/README.md && echo "PASS: README.md exists" || echo "FAIL"
test -f docs/findings/curl-cffi-feasibility.md && echo "PASS: findings doc exists" || echo "FAIL"

# Verify Python syntax
python -m py_compile scripts/experiments/test_curl_cffi.py && echo "PASS: syntax OK" || echo "FAIL"
python -m py_compile scripts/experiments/united_api.py && echo "PASS: syntax OK" || echo "FAIL"

# Verify NO playwright in requirements
grep -q "playwright" scripts/experiments/requirements.txt && echo "FAIL: playwright should not be listed" || echo "PASS: no playwright dependency"

# Verify curl_cffi IS in requirements
grep -q "curl_cffi" scripts/experiments/requirements.txt && echo "PASS: curl_cffi listed" || echo "FAIL"

# Verify .gitignore protections
grep -q ".env" .gitignore && echo "PASS: .env excluded" || echo "FAIL"

# Verify no credentials in tracked files
git diff --name-only | xargs grep -l -i "bearer DAAAA\|mileageplus.*@\|password" 2>/dev/null && echo "WARNING: possible credential leak" || echo "PASS: no credential leaks"

# Verify API request template matches contract
grep -q "FetchAwardCalendar" scripts/experiments/united_api.py && echo "PASS: correct endpoint" || echo "FAIL"
grep -q "x-authorization-api" scripts/experiments/united_api.py && echo "PASS: correct auth header" || echo "FAIL"
grep -q "CalendarLengthOfStay" scripts/experiments/united_api.py && echo "PASS: correct body field" || echo "FAIL"

# Verify auth flow doc was corrected (MFA documented)
grep -qi "MFA\|mfa\|multi-factor\|verification code" docs/api-contract/united-auth-flow.md && echo "PASS: MFA documented" || echo "FAIL: MFA not mentioned"
grep -qi "no 2FA required" docs/api-contract/united-auth-flow.md && echo "FAIL: old 2FA claim still present" || echo "PASS: old claim removed"

# Verify findings document has required sections
grep -q "Authentication Decision" docs/findings/curl-cffi-feasibility.md && echo "PASS: has auth decision" || echo "FAIL"
grep -q "Minimum Viable Request" docs/findings/curl-cffi-feasibility.md && echo "PASS: has minimum request" || echo "FAIL"
```

## Notes
- **This plan does NOT include Playwright**. Auth is manual for Phase 1. The only Python dependencies are `curl_cffi` and `python-dotenv`. This keeps the scraper as lightweight as possible.
- **User must provide bearer token before experiments can run**. The experiment scripts are built by the team, but the user must log into Chrome, complete Gmail MFA, and copy the token before execution. Scripts should detect a missing/placeholder token and print clear instructions.
- **Gmail MFA makes automated auth a separate problem**. Options for future automation include Playwright + IMAP email reading (like the Aeroplan approach in the project brief) or Playwright + manual MFA with persistent session reuse. This is explicitly out of scope for this step.
- **Bearer token expiry**: Tokens expire after hours (exact duration unknown). If the user obtains a token and waits too long, they may need a fresh one. The script detects 401/302 responses and prints "Token expired — get a fresh one from Chrome DevTools."
- **curl_cffi version matters**: The Chrome impersonation target must match an available version. As of 2026, `chrome131` should work. If not, the script falls back to the latest available target and prints available options.
- **Experiment order is critical**: If Experiment 1 fails (Cloudflare blocks curl_cffi entirely), Experiments 2-3 are skipped and the script prints a clear verdict: "curl_cffi is not viable — fall back to Playwright for API calls."
- **This step does NOT build the production scraper**. It only tests feasibility. The production scraper is built in Phase 1, Step 2.
- **Fallback is not failure**: Even if curl_cffi doesn't work, the project is still viable. Playwright can make API calls directly. The $7 VPS handles it at Canada scale.
