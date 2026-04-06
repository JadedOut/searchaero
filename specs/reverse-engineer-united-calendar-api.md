# Plan: Reverse-Engineer United Calendar API

## Task Description
Step 0 of Phase 1: Reverse-engineer United's award calendar API using browser DevTools. Capture the exact URL, headers, cookies, request body, and full response JSON schema. Document error formats. Save HAR files as ground-truth artifacts. This is the foundational research step — every downstream decision (parsing, storage schema, scrape volume math, concurrency model) depends on knowing exactly what the API returns.

The critical open question from the project brief: **Does the calendar endpoint return only the cheapest option per day, or all available options across cabin classes? Does it include flight-level details (flight numbers, times, connections) or just daily price summaries?** This plan answers that question definitively.

## Objective
Produce a complete, verified API contract document for United's award calendar endpoint, including:
1. Exact endpoint URL(s) and HTTP method(s)
2. Full request structure (headers, cookies, body/params)
3. Full response JSON schema with annotated field meanings
4. Confirmation of what data is returned (cabins, flight details, price-only, etc.)
5. Authentication flow documentation (login → session → API access)
6. Error response catalog (403, 429, session expiry, malformed, etc.)
7. Saved HAR files as reproducible evidenceok, t
8. A determination of whether a second per-date request is needed for flight details

## Problem Statement
The entire project architecture hinges on assumptions about United's calendar API that have never been empirically verified. The project brief flags this explicitly: "Does the calendar endpoint return only the cheapest option per day, or all available options across cabin classes?" If the calendar endpoint returns price-summary-only data, a second request per date is needed for flight details, which doubles or triples scrape volume and fundamentally changes the architecture. This must be resolved before any code is written.

## Solution Approach
Use Chrome DevTools (Network tab) while manually performing award searches on united.com with a logged-in MileagePlus account. Capture all network traffic as HAR files. Systematically vary search parameters (routes, dates, cabin classes, one-way vs round-trip) to map the full API surface. Document everything in a structured API contract file that becomes the single source of truth for all subsequent implementation.

### Reference: Scraperly Assessment (https://scraperly.com/scrape/united-airlines)
- **Difficulty: 2/5 (Easy)** — light Cloudflare, basic rate limiting
- **Anti-bot**: Cloudflare JS challenges, TLS fingerprinting, behavioral analysis
- **Recommended tool**: curl_cffi for TLS fingerprint impersonation (primary), Playwright as fallback
- **Proxies**: Datacenter sufficient ($1-5/GB), no residential needed
- **Rate limits**: ~1-5 req/min per IP before blocks, 10-30 min cooldown, space 5-10s apart
- **TLS**: curl_cffi impersonating Chrome achieves ~85% success rate through Cloudflare
- **Session**: Maintain cookie chains across requests; preserve tracking cookies

## Relevant Files
- `docs/project-brief.md` — Project brief with context on the calendar API, scrape volume math, and the critical assumption to verify (lines 87-89)

### New Files
- `docs/api-contract/united-calendar-api.md` — The primary deliverable: full API contract documentation
- `docs/api-contract/united-auth-flow.md` — Authentication flow documentation (login, session, cookies)
- `docs/api-contract/error-catalog.md` — Catalog of error responses with handling recommendations
- `docs/api-contract/har-captures/README.md` — Index of saved HAR files with descriptions
- `docs/api-contract/har-captures/*.har` — Raw HAR files (gitignored — contain session tokens)
- `docs/api-contract/sample-responses/calendar-response.json` — Sanitized sample calendar API response
- `docs/api-contract/sample-responses/detail-response.json` — Sanitized sample detail API response (if a second endpoint exists)
- `.gitignore` — Updated to exclude HAR files and any files containing credentials/tokens

## Implementation Phases

### Phase 1: Foundation — Environment & Account Setup
Set up the research environment: a MileagePlus account, Chrome with DevTools ready, a directory structure for captures, and the .gitignore rules to prevent credential leaks.

### Phase 2: Core Implementation — API Capture & Analysis
Perform systematic award searches in Chrome while capturing all network traffic. Vary parameters methodically: different routes, date ranges, cabin classes, one-way vs round-trip, direct vs connecting. Analyze every request/response. Identify the calendar endpoint, any secondary detail endpoints, and the authentication cookie chain.

### Phase 3: Integration & Polish — Documentation & Verification
Synthesize all captures into a clean API contract document. Cross-reference calendar data against what united.com actually displays to confirm nothing is missing. Answer every open question from the project brief. Produce the final deliverable that unblocks Phase 1 Steps 1-3.

## Team Orchestration

- You operate as the team lead and orchestrate the team to execute the plan.
- You're responsible for deploying the right team members with the right context to execute the plan.
- IMPORTANT: You NEVER operate directly on the codebase. You use `Task` and `Task*` tools to deploy team members to do the building, validating, testing, deploying, and other tasks.

### Team Members

- Builder
  - Name: researcher
  - Role: Perform browser-based API research using Chrome DevTools, capture HAR files, and analyze API requests/responses. This is the primary hands-on researcher who interacts with united.com.
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: doc-writer
  - Role: Take raw research findings (HAR analyses, captured requests/responses, field mappings) and produce clean, structured API contract documentation files.
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: validator
  - Role: Verify that all documentation is complete, all open questions from the project brief are answered, sample responses match what's documented, and no credentials/tokens leaked into tracked files.
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Setup Research Environment
- **Task ID**: setup-environment
- **Depends On**: none
- **Assigned To**: researcher
- **Agent Type**: general-purpose
- **Parallel**: false
- Create directory structure: `docs/api-contract/`, `docs/api-contract/har-captures/`, `docs/api-contract/sample-responses/`
- Update `.gitignore` to exclude `*.har`, `docs/api-contract/har-captures/`, and any files that could contain session tokens or credentials
- Create `docs/api-contract/har-captures/README.md` with instructions on how to capture HAR files and naming conventions
- Verify Chrome is available and DevTools Network tab can export HAR

### 2. Capture Authentication Flow
- **Task ID**: capture-auth-flow
- **Depends On**: setup-environment
- **Assigned To**: researcher
- **Agent Type**: general-purpose
- **Parallel**: false
- Open Chrome DevTools Network tab, enable "Preserve log"
- Navigate to united.com and initiate MileagePlus login
- Capture the full authentication sequence: login page load → credential POST → any redirects → session cookie set
- Document every cookie set during auth (name, domain, path, expiry, httpOnly, secure flags)
- Identify which cookies are required for authenticated API access vs analytics/tracking
- Save the auth flow as `docs/api-contract/har-captures/auth-flow.har`
- Note: Do NOT save actual credentials in any tracked file. Sanitize before committing.

### 3. Capture Calendar API — Base Case
- **Task ID**: capture-calendar-base
- **Depends On**: capture-auth-flow
- **Assigned To**: researcher
- **Agent Type**: general-purpose
- **Parallel**: false
- With an authenticated session, navigate to United's award search
- Search for a well-known route: YYZ → LAX, one-way, economy, 1 passenger, flexible dates (calendar view)
- In DevTools Network tab, identify the XHR/Fetch request that returns calendar pricing data
- For that request, capture and document:
  - Full URL (including query parameters)
  - HTTP method (GET/POST)
  - All request headers (especially Authorization, Cookie, X-* custom headers, User-Agent)
  - Request body (if POST) — exact JSON structure
  - Response status code
  - Response headers (especially caching, rate-limit headers)
  - **Full response JSON** — every field, nested object, and array
- Save as `docs/api-contract/har-captures/calendar-yyz-lax-economy.har`
- Save a sanitized copy of the response JSON as `docs/api-contract/sample-responses/calendar-response.json`

### 4. Capture Calendar API — Parameter Variations
- **Task ID**: capture-calendar-variations
- **Depends On**: capture-calendar-base
- **Assigned To**: researcher
- **Agent Type**: general-purpose
- **Parallel**: false
- Systematically vary search parameters and capture each response. For each variation, note what changed in the response:
  - **Different cabin class**: Same route (YYZ-LAX), but select Business, then First. Does the calendar endpoint return data for all cabins in one response, or only the selected cabin?
  - **Different route**: Try a domestic US route (e.g., SFO → JFK) and a short-haul (e.g., ORD → DTW). Do response structures differ?
  - **Round-trip vs one-way**: Does the calendar view change API endpoints or just add return-date fields?
  - **Different date ranges**: Click forward to months 6+ out (into the 330-337 day window). Same endpoint?
  - **Direct flights only filter**: If United's UI has a "nonstop only" toggle, does it change the API request or filter client-side?
- Save each variation as a separate HAR file with descriptive naming (e.g., `calendar-yyz-lax-business.har`, `calendar-sfo-jfk-economy.har`)
- The critical question to answer: **Is there a single calendar endpoint that returns ALL cabin data per day, or separate requests per cabin?**

### 5. Capture Flight Detail Endpoint (If Exists)
- **Task ID**: capture-detail-endpoint
- **Depends On**: capture-calendar-base
- **Assigned To**: researcher
- **Agent Type**: general-purpose
- **Parallel**: true (can run alongside task 4)
- Click on a specific date in the calendar view to see individual flight results
- Capture the network request that loads flight-level details (flight numbers, departure/arrival times, connections, fare classes, seats remaining)
- Document the full request/response structure as done in task 3
- Determine the relationship between the calendar endpoint and the detail endpoint:
  - Does the calendar response contain flight details, or only daily price summaries?
  - If separate: what parameters link them (date? route? session state?)
  - If the calendar already contains everything: confirm by cross-referencing with the flight list UI
- Save as `docs/api-contract/har-captures/flight-detail-yyz-lax-apr21.har`
- Save sanitized response as `docs/api-contract/sample-responses/detail-response.json`
- **This task directly answers the critical assumption from the project brief (line 87-89)**

### 6. Capture Error Responses
- **Task ID**: capture-errors
- **Depends On**: capture-calendar-base
- **Assigned To**: researcher
- **Agent Type**: general-purpose
- **Parallel**: true (can run alongside tasks 4-5)
- Deliberately trigger error conditions and capture the responses:
  - **Expired session**: Wait for session to expire (or manually clear auth cookies) and retry a calendar request. Capture the redirect/error response.
  - **Invalid route**: Search for a nonsensical route (e.g., airport code that doesn't exist). What does the API return?
  - **Past date**: Search for a date that has already passed.
  - **No availability**: Search a route/date unlikely to have award availability.
- For each error, document: HTTP status code, response body, any error codes or messages, and whether the response is JSON or HTML (redirect to login page).
- Save as `docs/api-contract/har-captures/error-*.har`

### 7. Document API Contract
- **Task ID**: document-api-contract
- **Depends On**: capture-calendar-base, capture-calendar-variations, capture-detail-endpoint, capture-errors
- **Assigned To**: doc-writer
- **Agent Type**: general-purpose
- **Parallel**: false
- Synthesize all captured data into `docs/api-contract/united-calendar-api.md` containing:
  - **Endpoint reference**: URL, method, parameters, headers (with which are required vs optional)
  - **Request schema**: Exact JSON body or query parameter structure with field types and allowed values
  - **Response schema**: Full JSON schema with field descriptions, types, nullability, and example values
  - **Calendar vs Detail relationship**: Clear explanation of what each endpoint returns and whether one or two requests are needed per route/date
  - **Data coverage per request**: Exactly how many days of data one calendar request returns (verify the "~30 days" assumption from the brief)
  - **Cabin class behavior**: Whether all cabins are in one response or separate requests per cabin
  - **Scrape volume implications**: Updated math based on actual API behavior (requests needed for full coverage)
- Create `docs/api-contract/united-auth-flow.md` containing:
  - Step-by-step authentication sequence
  - Required cookies and their lifetimes
  - Session expiry detection method
  - Re-authentication flow
- Create `docs/api-contract/error-catalog.md` containing:
  - Each error type with status code, response format, and recommended handling action
  - Maps to the error handling taxonomy in the project brief (line 309-319)

### 8. Answer Critical Architecture Questions
- **Task ID**: answer-architecture-questions
- **Depends On**: document-api-contract
- **Assigned To**: doc-writer
- **Agent Type**: general-purpose
- **Parallel**: false
- Add a dedicated "Architecture Impact" section to `docs/api-contract/united-calendar-api.md` that explicitly answers:
  1. **Calendar data scope**: Does one request return all cabins or just the selected one? (Impacts: requests per route multiplied by cabin count)
  2. **Flight detail availability**: Does the calendar include flight numbers, times, connections? Or is that a separate endpoint? (Impacts: whether a second request per date is needed, which 2-3x scrape volume)
  3. **Days per response**: Exactly how many days does one calendar request cover? (Impacts: the "12 requests per route for 337 days" assumption)
  4. **Seat count**: Does the response include seats remaining? (Impacts: alert matching and data model)
  5. **Fare class codes**: Are X/XN, IN, O fare class codes visible in the response? (Impacts: saver detection)
  6. **Direct vs connecting**: Is nonstop/connecting indicated in the calendar response or only the detail response? (Impacts: whether the `direct` boolean in the DB can be populated from calendar data alone)
  7. **Revised scrape volume**: Based on all findings, recalculate the daily request count. Does the 261K/day estimate from the brief hold, or does it need revision?
- Each answer must cite the specific HAR file / response field that proves it

### 9. Validate Completeness
- **Task ID**: validate-all
- **Depends On**: answer-architecture-questions
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all deliverables exist and are complete:
  - `docs/api-contract/united-calendar-api.md` exists, has endpoint reference, request/response schemas, and architecture impact section with all 7 questions answered
  - `docs/api-contract/united-auth-flow.md` exists, documents the full auth sequence and session lifecycle
  - `docs/api-contract/error-catalog.md` exists, covers at least: 403, 429, session expiry, invalid route, no availability
  - `docs/api-contract/sample-responses/calendar-response.json` exists and is valid JSON
  - `docs/api-contract/har-captures/README.md` exists with index of captures
  - `.gitignore` excludes `*.har` files and credential-bearing content
  - No actual credentials, session tokens, or MileagePlus numbers appear in any tracked file
- Verify internal consistency:
  - Schema documentation matches the actual sample response JSON
  - Scrape volume math in architecture impact section is consistent with the endpoint behavior documented
  - Error catalog covers all error types discovered during capture
- Report any gaps or inconsistencies

## Acceptance Criteria
- [ ] Calendar API endpoint fully documented (URL, method, headers, request body, response schema)
- [ ] Response JSON schema annotated with field meanings and types
- [ ] **Critical question answered**: Calendar returns [all cabins / single cabin] per request — with evidence
- [ ] **Critical question answered**: Calendar returns [flight details / price summary only] — with evidence
- [ ] **Critical question answered**: One calendar request covers [N] days — with evidence
- [ ] Authentication flow documented (login → cookies → session lifecycle)
- [ ] Error response catalog with at least 4 error types documented
- [ ] At least 3 HAR files saved (base case, variation, error)
- [ ] At least 1 sanitized sample response JSON saved
- [ ] Architecture impact section answers all 7 questions with cited evidence
- [ ] Revised scrape volume estimate calculated from empirical data
- [ ] No credentials or session tokens in any git-tracked file
- [ ] .gitignore updated to protect sensitive captures

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# Verify directory structure exists
ls -la docs/api-contract/
ls -la docs/api-contract/har-captures/
ls -la docs/api-contract/sample-responses/

# Verify key documents exist and have content
wc -l docs/api-contract/united-calendar-api.md    # Should be substantial (100+ lines)
wc -l docs/api-contract/united-auth-flow.md        # Should be substantial (50+ lines)
wc -l docs/api-contract/error-catalog.md            # Should be substantial (30+ lines)

# Verify sample response is valid JSON
python -m json.tool docs/api-contract/sample-responses/calendar-response.json > /dev/null

# Verify .gitignore protects sensitive files
grep -q "*.har" .gitignore && echo "HAR files excluded" || echo "WARNING: HAR files not excluded"

# Verify no credentials leaked in tracked files
git diff --cached --name-only | xargs grep -l -i "mileageplus\|password\|session\|token\|cookie" 2>/dev/null && echo "WARNING: possible credential leak" || echo "No credential leaks detected"

# Verify the critical architecture questions are answered
grep -c "Architecture Impact" docs/api-contract/united-calendar-api.md   # Should be >= 1
```

## Notes
- **This is a research/documentation task, not a coding task.** The deliverable is documentation, not software. No scraper code is written in this step.
- **Browser interaction required.** The researcher must interact with united.com in a real Chrome browser with DevTools. This cannot be fully automated — it requires a human (or browser automation agent) to log in, perform searches, and inspect network traffic.
- **MileagePlus account prerequisite.** A free MileagePlus account must exist before this work begins. Account creation is free at united.com. If the user doesn't have one, that's the very first action.
- **HAR files contain secrets.** HAR captures include session cookies and potentially login credentials. They must NEVER be committed to git. The .gitignore update in task 1 is a safety-critical prerequisite.
- **Scraperly reference confirms feasibility.** United is rated 2/5 difficulty with standard Cloudflare protection. Datacenter proxies suffice. curl_cffi is the recommended primary tool. This step is about understanding the API contract, not about bypassing protections (that's Step 2).
- **Time estimate.** 1-2 days of focused work. Most time is spent in the browser performing systematic searches and analyzing responses, not writing code.
- **Dependency.** Steps 1-9 of Phase 1 all depend on the output of this step. Do not proceed to any subsequent step until this plan's acceptance criteria are fully met.
