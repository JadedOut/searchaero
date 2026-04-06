# Plan: Web UI for Award Flight Search

## Task Description
Build a simple web UI to visualize award flight availability data, modeled after seats.aero. The UI consists of two pages: a home page with a search bar and a results page showing a table of availability data. Uses Next.js with shadcn/ui for the frontend (dark theme with orange accents) and FastAPI for the backend API. Also update the project brief to insert this as Step 4 in the Phase 1 plan.

## Objective
A working two-page web application where users can enter origin/destination airport codes, search, and see a table of award availability sorted by date. Each row shows the lowest (representative) miles per cabin class with color-coded badges (green = available direct, orange = available with connection, gray = not available). An info icon per row opens a modal showing the full breakdown of all offerings for that date.

## Problem Statement
The project has a working scraper and database with real availability data but no way for users to view it. The project brief lists "Minimal web UI" as Step 6, but the user wants to pull this forward to Step 4 with a specific design: dark theme, seats.aero-style layout, shadcn/ui components, and a clean two-page flow (home search → results table).

## Solution Approach
- **Backend**: FastAPI (Python) with two endpoints — one for search results (pivoted by date/cabin) and one for per-date detail breakdown
- **Frontend**: Next.js with shadcn/ui, dark theme, orange accent color
- **Data flow**: User enters origin/destination on home page → GET request navigates to results page → results page calls FastAPI → FastAPI queries PostgreSQL availability table → returns pivoted data → rendered as a table with color-coded badges
- **Badge logic**: Green if available (direct or unknown), orange if available with connection, gray if not available
- **Important data limitation**: The calendar scraper does NOT populate the `direct` column in the database (the FetchAwardCalendar endpoint doesn't return connection info — see `docs/api-contract/united-calendar-api.md` section 6). Initially, ALL available badges will be **green** since `direct` is NULL. When flight detail scraping (FetchFlights) is added in a future step, the `direct` column will be populated and orange badges will activate automatically based on the existing badge logic (`direct === false` → orange).

## Relevant Files
Use these files to understand the existing codebase:

- `core/db.py` — Database connection and query functions. Contains `get_connection()`, `get_route_summary()`, `get_scrape_stats()`. The search API will use the same connection pattern.
- `core/models.py` — Data models with `VALID_CABINS = {"economy", "premium_economy", "business", "business_pure", "first", "first_pure"}` and `VALID_AWARD_TYPES = {"Saver", "Standard"}`.
- `docker-compose.yml` — PostgreSQL 16 container. Connection: `postgresql://seataero:seataero_dev@localhost:5432/seataero`.
- `docs/api-contract/united-calendar-api.md` — Documents what data the calendar scraper collects. Confirms: no direct/connection info, no flight numbers, no seat counts from calendar data.
- `docs/project-brief.md` — Project brief to be updated with new Step 4.
- `requirements.txt` — Python dependencies. FastAPI and uvicorn need to be added.

### New Files
- `web/__init__.py` — Package init
- `web/api.py` — FastAPI application with CORS, search and detail endpoints
- `web/frontend/` — Next.js project (full structure below)
- `web/frontend/app/layout.tsx` — Root layout with dark theme
- `web/frontend/app/page.tsx` — Home page with centered search form
- `web/frontend/app/search/page.tsx` — Results page with table
- `web/frontend/components/search-form.tsx` — Origin/Destination inputs + Search button
- `web/frontend/components/results-table.tsx` — Main availability table
- `web/frontend/components/availability-badge.tsx` — Green/orange/gray badge component
- `web/frontend/components/detail-modal.tsx` — Info icon detail popup showing all offerings
- `web/frontend/lib/api.ts` — API client functions
- `web/frontend/lib/utils.ts` — Utility functions (relative time formatting, miles formatting)

## Implementation Phases

### Phase 1: Foundation
- Update project brief to insert Step 4, renumber subsequent steps, remove old Step 6
- Set up FastAPI backend with CORS and database connection
- Initialize Next.js project with shadcn/ui, configure dark theme and orange accents
- Add `fastapi` and `uvicorn[standard]` to `requirements.txt`

### Phase 2: Core Implementation
- Build two FastAPI endpoints: `GET /api/search` (pivoted results) and `GET /api/search/detail` (per-date breakdown)
- Build Home page with centered search form (From, To, Search button)
- Build Results page with data table, badges, and info icon
- Build detail modal for per-date offering breakdown

### Phase 3: Integration & Polish
- Wire frontend API client to backend
- Add loading states, empty states, error handling
- Input validation (3-letter IATA codes, auto-uppercase)
- Verify end-to-end with real database data

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
  - Name: brief-updater
  - Role: Update the project brief document to insert Step 4 and renumber subsequent steps
  - Agent Type: builder
  - Resume: true

- Builder
  - Name: backend-builder
  - Role: Build the FastAPI backend with search and detail API endpoints, database queries, and CORS configuration
  - Agent Type: backend-architect
  - Resume: true

- Builder
  - Name: frontend-builder
  - Role: Build the entire Next.js frontend — project setup, shadcn/ui, dark theme, home page, results page, all components
  - Agent Type: frontend-architect
  - Resume: true

- Builder
  - Name: final-validator
  - Role: Validate all work meets acceptance criteria — build checks, API responses, UI rendering, theme consistency
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Update Project Brief
- **Task ID**: update-project-brief
- **Depends On**: none
- **Assigned To**: brief-updater
- **Agent Type**: builder
- **Parallel**: true
- Read `docs/project-brief.md` and locate the Phase 1 plan table (starts after `## Phase 1 plan`)
- Insert new Step 4 row between current Step 3 and Step 4:
  ```
  | **4** | Web UI: Next.js + shadcn/ui frontend with FastAPI backend. Dark theme, seats.aero-style layout. Home page with origin/destination search, results page with availability table (Date, Last Seen, Program, Departs, Arrives, Economy, Premium, Business, First). Green badges for available, gray for not available. Info icon per row shows all offerings for that date. | Users need to visualize scraped data. Pull UI work forward since the data pipeline is proven. | 1 week |
  ```
- Renumber current Step 4 (Scale to all Canada routes) → Step 5
- Renumber current Step 5 (Alerts and Telegram) → Step 6
- Remove current Step 6 (Minimal web UI) — it is replaced by the new Step 4
- Renumber current Step 9 (Expand to US+Canada) → Step 7

### 2. Set Up FastAPI Backend
- **Task ID**: setup-backend
- **Depends On**: none
- **Assigned To**: backend-builder
- **Agent Type**: backend-architect
- **Parallel**: true (can run alongside frontend setup and brief update)
- Add `fastapi` and `uvicorn[standard]` to `requirements.txt`
- Create `web/__init__.py` (empty)
- Create `web/api.py` with:
  - FastAPI app with CORS middleware (allow origin `http://localhost:3000`)
  - Reuse connection pattern from `core/db.py` (`get_connection()`)
  - **`GET /api/search`** endpoint:
    - Query params: `origin` (str, required), `destination` (str, required)
    - Validate: both must be 3 uppercase letters (regex `^[A-Z]{3}$`), return 400 if invalid
    - SQL query to pivot availability by date and cabin:
      ```sql
      SELECT date,
             MAX(scraped_at) as last_seen,
             MIN(CASE WHEN cabin IN ('economy') THEN miles END) as economy_miles,
             MIN(CASE WHEN cabin IN ('economy') THEN taxes_cents END) as economy_taxes,
             MIN(CASE WHEN cabin IN ('premium_economy') THEN miles END) as premium_miles,
             MIN(CASE WHEN cabin IN ('premium_economy') THEN taxes_cents END) as premium_taxes,
             MIN(CASE WHEN cabin IN ('business', 'business_pure') THEN miles END) as business_miles,
             MIN(CASE WHEN cabin IN ('business', 'business_pure') THEN taxes_cents END) as business_taxes,
             MIN(CASE WHEN cabin IN ('first', 'first_pure') THEN miles END) as first_miles,
             MIN(CASE WHEN cabin IN ('first', 'first_pure') THEN taxes_cents END) as first_taxes
      FROM availability
      WHERE origin = %(origin)s AND destination = %(destination)s
        AND date >= CURRENT_DATE
      GROUP BY date
      ORDER BY date ASC
      ```
    - Response JSON:
      ```json
      {
        "origin": "YYZ",
        "destination": "JFK",
        "results": [
          {
            "date": "2026-04-09",
            "last_seen": "2026-04-04T12:00:00Z",
            "program": "United",
            "origin": "YYZ",
            "destination": "JFK",
            "economy": {"miles": 17500, "taxes_cents": 6851},
            "premium": {"miles": 100000, "taxes_cents": 6851},
            "business": {"miles": 45000, "taxes_cents": 6851},
            "first": null
          }
        ]
      }
      ```
    - If a cabin's miles is NULL, that cabin key should be `null` in the response
  - **`GET /api/search/detail`** endpoint:
    - Query params: `origin` (str), `destination` (str), `date` (str, YYYY-MM-DD format)
    - SQL query:
      ```sql
      SELECT cabin, award_type, miles, taxes_cents, scraped_at
      FROM availability
      WHERE origin = %(origin)s AND destination = %(destination)s AND date = %(date)s
      ORDER BY
        CASE cabin
          WHEN 'economy' THEN 1
          WHEN 'premium_economy' THEN 2
          WHEN 'business' THEN 3
          WHEN 'business_pure' THEN 4
          WHEN 'first' THEN 5
          WHEN 'first_pure' THEN 6
        END,
        miles ASC
      ```
    - Response JSON:
      ```json
      {
        "date": "2026-04-09",
        "origin": "YYZ",
        "destination": "JFK",
        "offerings": [
          {"cabin": "Economy", "award_type": "Saver", "miles": 7500, "taxes_cents": 8758},
          {"cabin": "Economy", "award_type": "Standard", "miles": 15000, "taxes_cents": 8758},
          {"cabin": "Business", "award_type": "Saver", "miles": 30000, "taxes_cents": 8758},
          {"cabin": "Business", "award_type": "Standard", "miles": 57900, "taxes_cents": 8758},
          {"cabin": "First", "award_type": "Saver", "miles": 30000, "taxes_cents": 8758}
        ]
      }
      ```
    - Map cabin names to display names: `economy` → "Economy", `premium_economy` → "Premium Economy", `business` / `business_pure` → "Business" / "Business (pure)", `first` / `first_pure` → "First" / "First (pure)"
  - **`GET /api/health`** endpoint: returns `{"status": "ok"}` for quick checks

### 3. Initialize Next.js Frontend
- **Task ID**: setup-frontend
- **Depends On**: none
- **Assigned To**: frontend-builder
- **Agent Type**: frontend-architect
- **Parallel**: true (can run alongside backend setup)
- Initialize Next.js in `web/frontend/`:
  ```bash
  cd C:/Users/jiami/local_workspace/seataero/web
  npx create-next-app@latest frontend --typescript --tailwind --eslint --app --src-dir=false --import-alias="@/*"
  ```
- Initialize shadcn/ui:
  ```bash
  cd web/frontend
  npx shadcn@latest init
  ```
  - Choose "New York" style, dark mode default
- Configure dark theme colors in `app/globals.css`:
  - Dark background: `hsl(0 0% 3.9%)` (near black, `#0a0a0a`)
  - Card/surface: `hsl(0 0% 7%)` (dark gray)
  - Primary (orange): `hsl(24.6 95% 53.1%)` (Tailwind orange-500, `#f97316`)
  - Primary foreground: white
  - Muted: `hsl(0 0% 14.9%)`
  - Border: `hsl(0 0% 14.9%)`
  - Keep destructive, success colors from defaults
- Add custom CSS variable for green badge: `--color-available: hsl(142.1 76.2% 36.3%)` (green-600)
- Add custom CSS variable for orange badge: `--color-connection: hsl(24.6 95% 53.1%)` (orange-500)
- Install required shadcn components:
  ```bash
  npx shadcn@latest add button input table dialog badge
  ```
- Set up the root layout (`app/layout.tsx`):
  - Apply `dark` class to `<html>` element
  - Set appropriate font (Inter or system font)
  - Add max-width container wrapper with horizontal padding (`max-w-7xl mx-auto px-4 sm:px-6 lg:px-8`)

### 4. Build Home Page
- **Task ID**: build-home-page
- **Depends On**: setup-frontend
- **Assigned To**: frontend-builder
- **Agent Type**: frontend-architect
- **Parallel**: false
- Create `components/search-form.tsx`:
  - Two text input fields side by side:
    - "From" — placeholder "Origin (e.g. YYZ)", auto-uppercase, max 3 chars
    - "To" — placeholder "Destination (e.g. JFK)", auto-uppercase, max 3 chars
  - Swap button (⇄ icon) between the two inputs to swap values
  - Orange "Search" button with search icon (magnifying glass)
  - Form behavior: on submit, validate both inputs are exactly 3 uppercase letters. If valid, navigate to `/search?origin=XXX&destination=YYY` using `router.push()`. This is a client-side navigation but the URL is a GET request pattern.
  - Use shadcn `Input` and `Button` components
- Style `app/page.tsx` (Home page):
  - Full viewport height, content centered vertically and horizontally
  - Title: "Seataero" or project name, large heading
  - Subtitle: brief description (e.g., "Search United award flight availability")
  - Search form card with subtle border/background on the dark theme
  - Max-width container with horizontal padding (same as seats.aero's buffered layout)
  - Clean, minimal, dark background

### 5. Build Results Page
- **Task ID**: build-results-page
- **Depends On**: setup-frontend, setup-backend
- **Assigned To**: frontend-builder
- **Agent Type**: frontend-architect
- **Parallel**: false
- Create `lib/api.ts`:
  - `const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"`
  - `async function searchAvailability(origin: string, destination: string)` → fetch from `/api/search`
  - `async function getDateDetail(origin: string, destination: string, date: string)` → fetch from `/api/search/detail`
  - Type definitions for API responses
- Create `lib/utils.ts`:
  - `formatRelativeTime(isoString: string): string` — converts ISO timestamp to relative time:
    - < 1 hour: "X minutes ago"
    - < 24 hours: "X hours ago"
    - < 30 days: "X days ago"
    - else: "X months ago"
  - `formatMiles(miles: number): string` — formats miles with commas and "pts" suffix: `17,500 pts`
  - `formatTaxes(taxesCents: number): string` — formats to dollars: `$68.51`
- Create `components/availability-badge.tsx`:
  - Props: `miles: number | null`, `taxesCents: number | null`, `direct: boolean | null`
  - Rendering logic:
    - If `miles` is null → gray badge showing "Not Available"
    - If `direct` is false → orange badge showing formatted miles (e.g., "17,500 pts")
    - Otherwise (direct is true or null/unknown) → green badge showing formatted miles
  - Styling: rounded corners (`rounded-md`), small padding, appropriate text size
  - Green background: `bg-green-600/90 text-white`
  - Orange background: `bg-orange-500/90 text-white`
  - Gray background: `bg-neutral-700 text-neutral-400`
- Create `components/results-table.tsx`:
  - Uses shadcn `Table` component
  - Column headers: Date, Last Seen, Program, Departs, Arrives, Economy, Premium, Business, First, (empty for info icon)
  - Header row has subtle bottom border
  - Each data row:
    - Date: formatted as `YYYY-MM-DD` (e.g., "2026-04-09")
    - Last Seen: relative time from `formatRelativeTime()`
    - Program: "United" (text, with United logo/icon if easily available, otherwise just text)
    - Departs: origin airport code (e.g., "YYZ")
    - Arrives: destination airport code (e.g., "JFK")
    - Economy/Premium/Business/First: `<AvailabilityBadge>` component
    - Info icon: clickable circle-info icon (ℹ), triggers detail modal
  - Alternating row backgrounds for readability on dark theme
  - Table is horizontally scrollable on mobile
- Create `components/detail-modal.tsx`:
  - Uses shadcn `Dialog` component
  - Triggered when user clicks info icon on a row
  - On open: fetches data from `/api/search/detail` for that date
  - Header: "Thu, Apr 9, 2026 · Seen 14 hours ago" + "YYZ → JFK"
  - Content: table/list of all offerings:
    - Columns: Cabin, Award Type, Miles, Taxes
    - Sorted by cabin order (Economy → Premium → Business → First), then by miles ascending
    - Each row shows: "Economy", "Saver", "7,500 miles", "+ $87.58"
  - Loading state while fetching
  - Close button (X) in top right
- Build `app/search/page.tsx` (Results page):
  - Read `origin` and `destination` from URL search params (`useSearchParams()`)
  - If params are missing, redirect to home page
  - Fetch data from API on mount using `useEffect`
  - Layout:
    - Search form at top (reuse `<SearchForm>` component, pre-filled with current origin/destination)
    - Route header below: "YYZ → JFK" in large text
    - Results count: "Showing X entries"
    - Results table
  - States:
    - Loading: spinner or skeleton
    - Empty: "No availability found for this route"
    - Error: "Failed to load results. Please try again."
  - Max-width container with horizontal padding (matching home page)

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: update-project-brief, setup-backend, build-home-page, build-results-page
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false

#### Command-Line Checks
- Verify `docs/project-brief.md` has correct step numbering (0-3 unchanged, 4 = Web UI, 5 = Scale, 6 = Alerts, 7 = Expand)
- Verify `web/api.py` exists and imports correctly
- Verify FastAPI app starts: `scripts/experiments/.venv/Scripts/uvicorn.exe web.api:app --port 8000`
- Verify API returns valid JSON: `curl http://localhost:8000/api/search?origin=YYZ&destination=JFK`
- Verify API validates input: `curl http://localhost:8000/api/search?origin=xx&destination=JFK` returns 400
- Verify `web/frontend/package.json` exists with Next.js and shadcn dependencies
- Verify Next.js builds: `cd web/frontend && npm run build`
- Verify existing Python tests still pass: `scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v`
- Check that no new files are created outside the expected locations (`web/`, `docs/project-brief.md`, `requirements.txt`)

#### Visual Verification via Chrome (claude-in-chrome MCP)
Use the `mcp__claude-in-chrome__*` browser tools to visually verify the running application. Both the FastAPI backend (port 8000) and Next.js dev server (port 3000) must be running first.

- **Home page** (`http://localhost:3000`):
  - Navigate to the page and take a screenshot
  - Verify dark theme is applied (dark background, not white)
  - Verify search form is visible and centered with From/To inputs and orange Search button
  - Verify horizontal padding/buffer on left and right sides
  - Verify the swap button (⇄) is present between the two inputs

- **Search flow**:
  - Enter "YYZ" in the From field and "JFK" (or "LAX") in the To field using `form_input` or `javascript_tool`
  - Click the Search button
  - Verify the URL changes to `/search?origin=YYZ&destination=JFK` (separate page, not same page)

- **Results page** (`http://localhost:3000/search?origin=YYZ&destination=LAX`):
  - Navigate directly or arrive via search
  - Take a screenshot
  - Verify table is visible with all expected columns: Date, Last Seen, Program, Departs, Arrives, Economy, Premium, Business, First
  - Verify results are sorted by date ascending (earliest date first)
  - Verify green badges appear for available cabins with miles displayed (e.g., "17,500 pts")
  - Verify gray "Not Available" badges appear for unavailable cabins
  - Verify "Last Seen" shows relative time (e.g., "5 days ago", not raw timestamps)
  - Verify dark theme is consistent (dark background, appropriate contrast)
  - Verify the search form is present at the top of the results page (pre-filled with current route)

- **Detail modal**:
  - Click the info icon (ℹ) on any row in the results table
  - Verify a dialog/modal opens showing all offerings for that date
  - Verify it shows cabin, award type, miles, and taxes for each offering
  - Verify the modal can be closed via the X button

- **Edge cases**:
  - Navigate to `/search?origin=XXX&destination=YYY` with a route that has no data
  - Verify a "No availability found" empty state is shown (not a blank page or error)

- **Record a GIF** (optional but recommended): Use `mcp__claude-in-chrome__gif_creator` to record the full search flow (home → enter route → search → results → click info icon → modal) for the user to review

## Acceptance Criteria
- Home page shows a clean search form with From, To inputs and orange Search button on dark background
- Searching navigates to `/search?origin=XXX&destination=YYY` via GET request (separate page from home)
- Results page shows a table with columns: Date, Last Seen, Program, Departs, Arrives, Economy, Premium, Business, First
- Results are sorted by date ascending (closest dates first, i.e., April 1, April 2, April 4, ...)
- Available cabins show green badges with formatted miles (e.g., "17,500 pts")
- Unavailable cabins show gray "Not Available" badges
- Last Seen column shows relative time (e.g., "5 days ago", "11 hours ago")
- Info icon (ℹ) on each row opens a dialog showing all offerings for that date (cabin, award type, miles, taxes)
- Business column merges `business` + `business_pure` (shows lower miles). First column merges `first` + `first_pure` similarly.
- Representative miles shown = MIN(miles) per cabin per date (the lowest/saver price)
- Dark theme throughout with orange accents (search button, primary actions)
- Layout has horizontal padding/buffer on left and right sides (max-width container, like seats.aero)
- FastAPI backend correctly queries PostgreSQL and returns pivoted availability data
- Both pages are separate routes (home page ≠ results page)
- Existing tests pass without regression

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# 1. Verify project brief was updated
grep -n "Step.*4.*Web UI\|Step.*5.*Scale\|Step.*6.*Alerts\|Step.*7.*Expand" docs/project-brief.md

# 2. Start PostgreSQL (if not running)
cd C:/Users/jiami/local_workspace/seataero && docker compose up -d

# 3. Install Python deps
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/pip.exe install fastapi "uvicorn[standard]"

# 4. Start FastAPI backend
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/uvicorn.exe web.api:app --reload --port 8000 &

# 5. Test API health
curl http://localhost:8000/api/health

# 6. Test search endpoint
curl "http://localhost:8000/api/search?origin=YYZ&destination=LAX"

# 7. Test detail endpoint
curl "http://localhost:8000/api/search/detail?origin=YYZ&destination=LAX&date=2026-04-09"

# 8. Test input validation
curl "http://localhost:8000/api/search?origin=xx&destination=JFK"

# 9. Install frontend deps and build
cd C:/Users/jiami/local_workspace/seataero/web/frontend && npm install && npm run build

# 10. Start frontend dev server
cd C:/Users/jiami/local_workspace/seataero/web/frontend && npm run dev &

# 11. Run existing tests
cd C:/Users/jiami/local_workspace/seataero
C:/Users/jiami/local_workspace/seataero/scripts/experiments/.venv/Scripts/python.exe -m pytest tests/ -v
```

## Notes
- **Direct vs. Connection badges**: The calendar scraper (`FetchAwardCalendar`) does NOT return connection info. The `direct` column in the availability table is NULL for all calendar-scraped data. Initially, all available badges will show as **green**. When the FetchFlights detail scraper is added (future work), the `direct` column will be populated and the badge component will automatically show **orange** for `direct === false`. No code changes needed — the badge logic already handles this.
- **Representative miles**: The MIN(miles) per cabin per date corresponds to what United shows as the headline calendar price (typically the Saver award). The info modal reveals all award types (Saver + Standard) with their individual prices.
- **Cabin merging**: `business` = "Business/First (lowest)" which may include mixed-cabin itineraries. `business_pure` = "Business/First (not mixed)" where all segments are same cabin. For the table, we show the lower of the two. The info modal shows both separately.
- **No departure date filter**: The search only takes origin/destination. Results show ALL future dates sorted chronologically. A date filter can be added later if the result set becomes too large.
- **CORS**: FastAPI must allow `http://localhost:3000` (Next.js dev server). In production, update to the actual domain.
- **Database connection**: The API uses the same connection string as the scraper (`postgresql://seataero:seataero_dev@localhost:5432/seataero`). For production, use the `DATABASE_URL` environment variable.
