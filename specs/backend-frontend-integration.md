# Plan: Backend-Frontend Integration

## Task Description
Wire the redesigned Seataero frontend to the existing FastAPI backend so that all UI components display real data from the PostgreSQL database. The frontend was recently redesigned with a rich aviation-ledger style UI (stats cards, filter bar, results table, detail modal) but several components still use hardcoded placeholders. The backend already has endpoints (`/api/search`, `/api/search/detail`, `/api/health`) but is missing a stats endpoint and doesn't surface all available DB columns.

## Objective
When complete:
1. Stats cards on the home page show **live data** from the database (route count, latest scrape time, data integrity)
2. The results table displays real availability with **correct badge styling** (green for saver/direct, orange for connecting)
3. The "direct flights only" filter **actually works** against real `direct` column data
4. The frontend **gracefully handles** the backend being offline (no crash, clear messaging)
5. A single `npm run dev` / `uvicorn` workflow runs both services with proper CORS

## Problem Statement
The frontend redesign introduced rich UI components (stats cards, filter bar with direct-flights toggle, availability badges with green/orange distinction) that rely on data the backend doesn't yet provide. Specifically:
- Stats cards are hardcoded (42 MS, 1,204 PTRS, 99.9%)
- The `direct` column exists in the DB but is never queried or returned by the API
- `scraped_at` is computed as `MAX()` in search but the per-row granularity is lost
- No `/api/stats` endpoint exists for the home page metrics
- The frontend has no health-check awareness — it just shows "Failed to load results" with no recovery path

## Solution Approach
- **Add `/api/stats` endpoint** — returns live metrics from `get_scrape_stats()` already in `core/db.py`
- **Add `direct` to search results** — include `BOOL_OR(direct)` in the search query aggregation so the frontend knows if any availability on a date includes a direct flight
- **Add `award_type` to search results** — expose whether the minimum-miles offering is Saver or Standard, so badges can differentiate
- **Update frontend stats cards** — fetch from `/api/stats` on mount, show loading skeleton, fallback to placeholder on error
- **Update frontend badges** — pass real `direct` value from API; green glow for saver/direct, orange for standard/connecting
- **Wire direct-flights filter** — filter results client-side using the new `direct` field
- **Add health-check polling** — ping `/api/health` on the home page to show backend connection status in the stats cards
- **Keep API changes backward-compatible** — new fields are additive, no breaking changes

## Relevant Files
Use these files to complete the task:

**Backend (modify):**
- `web/api.py` — Add `/api/stats` endpoint; add `direct` and `award_type` fields to `/api/search` response; add `scraped_at` to `/api/search/detail` response
- `core/db.py` — `get_scrape_stats()` already exists and returns what we need; no changes needed

**Frontend (modify):**
- `web/frontend/lib/api.ts` — Add `StatsResponse` type, `getStats()` function; add `direct` field to `SearchResult`; add `scraped_at` to `Offering`
- `web/frontend/components/stats-cards.tsx` — Fetch live stats from `/api/stats`, add loading state, handle errors
- `web/frontend/components/availability-badge.tsx` — Use `direct` prop for green/orange distinction; use `awardType` for saver vs standard styling
- `web/frontend/components/results-table.tsx` — Pass `direct` from search results to badges; display award type info
- `web/frontend/components/filter-bar.tsx` — No structural changes needed (already has directOnly prop wired)
- `web/frontend/app/search/page.tsx` — Wire `direct` field into filter logic; pass to ResultsTable
- `web/frontend/app/page.tsx` — Minor: pass health status down to StatsCards if needed

**Tests (modify/create):**
- `tests/test_api.py` — Add tests for `/api/stats` endpoint and new fields in `/api/search`

### New Files
- None — all changes are to existing files

## Implementation Phases

### Phase 1: Backend API Enhancements
- Add `/api/stats` endpoint returning `{ total_rows, routes_covered, latest_scrape, date_range_start, date_range_end }`
- Modify `/api/search` SQL to include `BOOL_OR(direct)` per date and `MIN(award_type)` for each cabin's minimum-miles row
- Modify `/api/search/detail` to include `scraped_at` in each offering
- Add response model documentation

### Phase 2: Frontend Data Wiring
- Update TypeScript types in `lib/api.ts` to match new API shapes
- Make `StatsCards` a client component that fetches `/api/stats` on mount
- Update `AvailabilityBadge` to use `direct` and `awardType` props
- Update `ResultsTable` to pass new fields through to badges
- Wire `directOnly` filter to use real `direct` data in search page

### Phase 3: Resilience & Polish
- Add loading skeletons to stats cards
- Handle `/api/stats` failure gracefully (show "—" or placeholder values)
- Handle `/api/search` failure with retry button
- Add health-check ping on home page to show backend status
- Write API tests for new endpoint and modified responses

## Team Orchestration

- You operate as the team lead and orchestrate the team to execute the plan.
- You're responsible for deploying the right team members with the right context to execute the plan.
- IMPORTANT: You NEVER operate directly on the codebase. You use `Task` and `Task*` tools to deploy team members to do the building, validating, testing, deploying, and other tasks.

### Team Members

- Builder
  - Name: api-builder
  - Role: Add /api/stats endpoint; enhance /api/search and /api/search/detail responses with direct, award_type, scraped_at fields
  - Agent Type: backend-architect
  - Resume: true

- Builder
  - Name: frontend-wiring
  - Role: Update TypeScript types, stats cards, availability badges, results table, and search page to consume new API fields
  - Agent Type: frontend-architect
  - Resume: true

- Builder
  - Name: resilience-builder
  - Role: Add loading states, error handling, health-check, retry logic, and graceful degradation
  - Agent Type: frontend-architect
  - Resume: true

- Builder
  - Name: test-builder
  - Role: Write API tests for /api/stats and verify new fields in /api/search responses
  - Agent Type: backend-architect
  - Resume: true

- Builder
  - Name: validator
  - Role: Run build, type check, API tests, and verify end-to-end with Docker + backend + frontend
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

### 1. Add /api/stats Endpoint
- **Task ID**: add-stats-endpoint
- **Depends On**: none
- **Assigned To**: api-builder
- **Agent Type**: backend-architect
- **Parallel**: true (can run in parallel with task 2)
- Add a new `GET /api/stats` endpoint to `web/api.py`
- Call `get_scrape_stats(conn)` from `core/db.py` (already implemented)
- Return JSON: `{ "total_rows": int, "routes_covered": int, "latest_scrape": ISO string | null, "date_range_start": ISO string | null, "date_range_end": ISO string | null }`
- Handle the case where the DB is empty (all values null/0)
- Serialize `datetime` and `date` objects to ISO strings

### 2. Enhance /api/search Response with Direct and Award Type
- **Task ID**: enhance-search-response
- **Depends On**: none
- **Assigned To**: api-builder
- **Agent Type**: backend-architect
- **Parallel**: true (can run in parallel with task 1)
- Modify the SQL query in the `/api/search` endpoint to include:
  - `BOOL_OR(direct) FILTER (WHERE cabin = 'economy')` as `economy_direct` (and same for each cabin group)
  - `MIN(award_type) FILTER (WHERE cabin = 'economy' AND miles = economy_miles_subquery)` — or simpler: just include `BOOL_OR(direct)` per cabin group
- For simplicity, add per-cabin `direct` as a boolean to each `CabinAvailability` object: `{ "miles": 13000, "taxes_cents": 6851, "direct": true }`
- The SQL should aggregate `direct` per cabin group per date: `BOOL_OR(CASE WHEN cabin IN ('economy') THEN direct END) as economy_direct`
- Update the response builder to include the `direct` field in each cabin object
- Note: The `direct` column may be NULL for existing rows (scraper doesn't populate it yet). Treat NULL as unknown — return `null` in JSON.

### 3. Enhance /api/search/detail Response
- **Task ID**: enhance-detail-response
- **Depends On**: none
- **Assigned To**: api-builder
- **Agent Type**: backend-architect
- **Parallel**: true (can run in parallel with tasks 1 and 2)
- The detail endpoint already queries `scraped_at` but doesn't include it in the response
- Add `scraped_at` (ISO string) to each offering in the response
- Add `direct` (boolean | null) to each offering — query it from the DB row

### 4. Update Frontend TypeScript Types
- **Task ID**: update-frontend-types
- **Depends On**: add-stats-endpoint, enhance-search-response, enhance-detail-response
- **Assigned To**: frontend-wiring
- **Agent Type**: frontend-architect
- **Parallel**: false
- In `web/frontend/lib/api.ts`:
  - Add `direct: boolean | null` to `CabinAvailability` interface
  - Add `scraped_at: string` to `Offering` interface
  - Add `direct: boolean | null` to `Offering` interface
  - Add new interface:
    ```typescript
    export interface StatsResponse {
      total_rows: number;
      routes_covered: number;
      latest_scrape: string | null;
      date_range_start: string | null;
      date_range_end: string | null;
    }
    ```
  - Add new function:
    ```typescript
    export async function getStats(): Promise<StatsResponse> {
      const response = await fetch(`${API_BASE}/api/stats`);
      if (!response.ok) throw new Error(`Stats failed: ${response.status}`);
      return response.json();
    }
    ```

### 5. Wire Stats Cards to Live API Data
- **Task ID**: wire-stats-cards
- **Depends On**: update-frontend-types
- **Assigned To**: frontend-wiring
- **Agent Type**: frontend-architect
- **Parallel**: true (can run in parallel with tasks 6 and 7)
- Convert `web/frontend/components/stats-cards.tsx` from server component to client component ("use client")
- Add `useState` + `useEffect` to fetch from `/api/stats` on mount
- Loading state: show animated pulse/skeleton placeholders for the metric values
- Error state: show "—" for values, no error banner (silent degradation)
- Success state: map API response to display:
  - **Network Latency** → Replace with "Latest Scrape" showing relative time of `latest_scrape` (e.g., "2h ago"). Keep Activity icon. Remove "LIVE" badge if no data.
  - **Cached Routes** → Show `routes_covered` as the number. Keep "PTRS" → change unit to "ROUTES".
  - **Data Integrity** → Show `total_rows` as the count. Change unit to "ROWS". Keep ShieldCheck icon.
- Use `formatRelativeTime()` from `lib/utils.ts` for the latest scrape timestamp

### 6. Wire Availability Badges with Direct Field
- **Task ID**: wire-badges
- **Depends On**: update-frontend-types
- **Assigned To**: frontend-wiring
- **Agent Type**: frontend-architect
- **Parallel**: true (can run in parallel with tasks 5 and 7)
- In `web/frontend/components/availability-badge.tsx`:
  - The component already handles `direct` prop — green for direct/null, orange for `direct === false`
  - No changes needed to the component itself — it already works correctly
- In `web/frontend/components/results-table.tsx`:
  - Pass `direct={result.economy?.direct ?? null}` (and same for each cabin) to `AvailabilityBadge`
  - Currently passes `direct={null}` — update to use real data from the search result
- Verify the badge renders correctly: green when `direct` is `true` or `null`, orange when `direct` is `false`

### 7. Wire Direct Flights Filter
- **Task ID**: wire-direct-filter
- **Depends On**: update-frontend-types
- **Assigned To**: frontend-wiring
- **Agent Type**: frontend-architect
- **Parallel**: true (can run in parallel with tasks 5 and 6)
- In `web/frontend/app/search/page.tsx`:
  - Update the client-side filter logic to handle the `directOnly` toggle
  - When `directOnly` is true, filter results to only show rows where at least one cabin has `direct === true`
  - Logic: `if (directOnly) { const hasDirectCabin = [r.economy, r.premium, r.business, r.first].some(c => c?.direct === true); if (!hasDirectCabin) return false; }`
  - When `directOnly` is false, show all results (current behavior)

### 8. Add Loading States and Error Recovery
- **Task ID**: add-resilience
- **Depends On**: wire-stats-cards, wire-badges, wire-direct-filter
- **Assigned To**: resilience-builder
- **Agent Type**: frontend-architect
- **Parallel**: false
- In `web/frontend/app/search/page.tsx`:
  - Add a "Retry" button to the error state that re-triggers the search
  - Show a more specific error message: "Could not connect to the backend. Make sure the API server is running on port 8000."
- In `web/frontend/components/stats-cards.tsx`:
  - Ensure loading skeleton has proper animation (`animate-pulse` on placeholder values)
  - Show "Offline" indicator if stats fetch fails (subtle, not alarming)
- In `web/frontend/components/results-table.tsx`:
  - Add loading skeleton rows while data is being fetched (3-5 placeholder rows with pulse animation)

### 9. Write API Tests
- **Task ID**: write-api-tests
- **Depends On**: add-stats-endpoint, enhance-search-response, enhance-detail-response
- **Assigned To**: test-builder
- **Agent Type**: backend-architect
- **Parallel**: true (can run in parallel with frontend tasks)
- Create or update `tests/test_api.py`:
  - Test `GET /api/stats` returns correct shape with `total_rows`, `routes_covered`, `latest_scrape`, `date_range_start`, `date_range_end`
  - Test `GET /api/stats` returns zeros/nulls when DB is empty
  - Test `GET /api/search` returns `direct` field in cabin objects (can be null)
  - Test `GET /api/search/detail` returns `scraped_at` and `direct` in offerings
- Use the existing test patterns from `tests/test_db.py` and `tests/test_models.py`
- Tests should use a test database or mock the DB connection
- Run with: `scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v`

### 10. Validate Everything
- **Task ID**: validate-all
- **Depends On**: add-resilience, write-api-tests
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run frontend build: `cd web/frontend && npm run build` — no errors
- Run type check: `cd web/frontend && npx tsc --noEmit` — no errors
- Run Python tests: `scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v`
- Start Docker PostgreSQL: `docker compose up -d`
- Start backend: `scripts/experiments/.venv/Scripts/python.exe -m uvicorn web.api:app --port 8000`
- Verify `GET /api/health` returns `{"status": "ok"}`
- Verify `GET /api/stats` returns valid JSON (may be zeros if no data)
- Verify `GET /api/search?origin=YYZ&destination=JFK` returns results array with `direct` field in cabin objects
- Start frontend: `cd web/frontend && npm run dev`
- Verify home page stats cards show live data (or "—" if no DB data)
- Verify search results page renders with filter bar, table, and badges
- Verify "direct flights only" toggle filters results when applicable

## Acceptance Criteria
- [ ] `GET /api/stats` returns `{ total_rows, routes_covered, latest_scrape, date_range_start, date_range_end }`
- [ ] `GET /api/search` cabin objects include `direct: boolean | null` field
- [ ] `GET /api/search/detail` offerings include `scraped_at` and `direct` fields
- [ ] Stats cards on home page fetch from `/api/stats` and display live data
- [ ] Stats cards show loading skeleton while fetching and "—" on error
- [ ] Availability badges use `direct` field: green for direct/null, orange for connecting
- [ ] "Direct flights only" toggle filters results based on real `direct` data
- [ ] Search results error state includes retry button with helpful message
- [ ] `npm run build` passes with no TypeScript errors
- [ ] Python API tests pass for new/modified endpoints
- [ ] Frontend works gracefully when backend is offline (no crash, clear messaging)

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# Frontend build check
cd C:/Users/jiami/local_workspace/seataero/web/frontend && npm run build

# Frontend type check
cd C:/Users/jiami/local_workspace/seataero/web/frontend && npx tsc --noEmit

# Python tests
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v

# Start backend and test endpoints
docker compose -f C:/Users/jiami/local_workspace/seataero/docker-compose.yml up -d
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m uvicorn web.api:app --port 8000 &

# Test health endpoint
curl http://localhost:8000/api/health

# Test stats endpoint
curl http://localhost:8000/api/stats

# Test search endpoint (check for direct field)
curl "http://localhost:8000/api/search?origin=YYZ&destination=JFK"

# Test detail endpoint (check for scraped_at and direct)
curl "http://localhost:8000/api/search/detail?origin=YYZ&destination=JFK&date=2026-05-01"
```

## Notes
- The `direct` column in the DB is currently NULL for most rows since the scraper doesn't populate it yet. The frontend must handle `null` gracefully (treat as "unknown" — use green badge, same as direct).
- The `flights` JSONB column has richer data (flight numbers, stops, times) but parsing it is out of scope for this plan. A future iteration could add flight-level detail.
- The `seats` column is also unused — could be surfaced later for "X seats remaining" indicators.
- The stats endpoint uses `get_scrape_stats()` which runs 4 separate COUNT/MAX/MIN queries. For a small dataset this is fine. If the table grows to millions of rows, consider caching or materialized views.
- CORS is already configured for `http://localhost:3000` in the FastAPI app, so no CORS changes needed.
- The `date_range_start` and `date_range_end` from stats could be shown on the home page as "Coverage: May 2026 — Mar 2027" but this is optional polish.
