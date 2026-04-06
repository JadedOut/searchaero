<!-- USAGE RULES
This document describes the project's high-level direction, scope, and technical strategy.

WHEN TO READ THIS FILE:
- When you need to understand the project's goals, scope, or strategic direction
- When making architectural decisions that depend on project vision
- When evaluating whether a proposed feature is in or out of scope
- When you need context on why certain technical choices were made

WHEN NOT TO READ THIS FILE:
- During routine implementation tasks where the direction is already clear
- When debugging or fixing bugs (use the code and logs instead)
- When writing tests or doing code reviews
- When the current task context already contains the needed information

This is a reference document, not a working document.
Sections marked with ⚠️ are critique annotations added during architecture review.
-->

# Project brief: free, open-source United award flight search tool

## What this project is

A free, open-source alternative to the paid features of Seats.aero, scoped to United MileagePlus award flights within the US and Canada. The tool scrapes United's award search API on a schedule, caches the results, and lets anyone search availability and set alerts without paying $10/month.

## Background: what is Seats.aero and why does it matter

Seats.aero is a metasearch engine for airline award flights. It lets you find where to redeem airline miles/points across 24+ frequent flyer programs simultaneously, instead of searching each airline's website one at a time. It doesn't book flights; it surfaces availability so you can book on the airline's site.

Founded in June 2022 by Ian Carroll (security researcher, ex-Dropbox/Robinhood) as a personal side project. Originally called "awards.pnr" and promoted on Reddit. Now at $7M ARR, 500K monthly active users, 100K+ paid members, with Chris Lopinto (former ExpertFlyer co-founder) as CEO. Completely bootstrapped, zero outside funding.

### How Seats.aero works

Automated bots continuously scrape airline loyalty program websites, sending HTTP requests to their search pages and parsing the results. Scraped data is stored in a massive cache (reportedly Amazon Aurora/PostgreSQL, 1B+ rows). When you search on Seats.aero, you're querying their cache, not the airline. Results load instantly but can be minutes-to-hours stale. Each result shows a "Last Seen" timestamp.

### What Seats.aero charges for

The free tier gives you: search within a 30-day window, up to 5 alerts, email notifications.

Pro ($9.99/month or $99.99/year) unlocks: full year (365 days) search window, unlimited alerts, SMS alerts, live real-time search, advanced filters (direct flights only, fare class viewer, minimum seats, max fees), and API access.

The most valuable Pro feature is the extended date range. Award seats, especially in premium cabins, get released 330-360 days before departure and often get snatched within days. A 30-day free window misses the entire planning horizon for most international trips.

### Why Seats.aero is hard to replicate at full scale

- 24 programs, each requiring a separate reverse-engineered scraper
- Airlines actively fight scrapers (Air Canada sued Seats.aero in October 2023; the case is ongoing)
- Both United and Aeroplan now require login to search awards (as of late 2025 / early 2025 respectively)
- Scrapers break constantly as airlines update sites and bot detection
- Infrastructure costs at scale: proxies, database, compute for 70K+ routes
- The scraping code is the moat; it's proprietary and constantly maintained

Competitors like AwardFares, Point.me, Roame, and PointsYeah all struggle to keep their scrapers working. AwardFares currently can't show Aeroplan data at all due to Air Canada's recent changes.

## Our project: what we're building

A United-only award search tool starting with Canada routes, expanding to US later. Free, open source (framework public, scraper implementations private), self-hostable.

### Scope

**Phase 1 (current): Canada routes only**
- One airline program: United MileagePlus
- Geographic coverage: Routes where at least one endpoint is a Canadian airport (9 airports: YYZ, YVR, YUL, YYC, YOW, YEG, YWG, YHZ, YQB)
- Estimated meaningful route pairs: ~1,000-2,000 (Canada↔US + Canada↔Canada, filtered to routes with United service)
- Scrape volume: ~24,000 requests/day (2,000 routes × 12 monthly windows) — achievable with 1 account, no proxies, single worker
- Refresh cadence: daily full sweep (low volume makes hourly feasible too)
- Date coverage: full 337 days (United's maximum award booking window)

**Phase 1b (after Canada is stable): Expand to US domestic**
- Add US domestic routes (241 destinations), bringing total to ~20,000 meaningful route pairs
- Scrape volume increases to ~240,000 requests/day — requires account pool, proxy strategy, tiered scheduling
- All infrastructure from Phase 1 carries over; only scaling changes needed

### What we give away for free that Seats.aero charges for

- Full 337-day search window (no 30-day cap)
- Unlimited alerts (no 5-alert cap)
- All advanced filters (direct flights, cabin class, min seats, max miles)
- No account required to search
- Hourly cache freshness on monitored routes
- Price history and trend charts (Seats.aero Pro feature)
- Calendar heatmap view
- CSV data export
- Booking deeplinks to united.com

### What we can't match

- Multi-program search (Seats.aero searches 24 programs; we only search United)
- International coverage beyond US/Canada
- Live real-time search (our cache is hourly; Seats.aero Pro does on-demand live queries)

## Technical approach

### Scraping United

As of 2026, United is rated 2/5 difficulty for scraping by Scraperly (https://scraperly.com/scrape/united-airlines). They use standard Cloudflare protection. Datacenter proxies are sufficient; residential proxies are not required.

**Key discovery:** United's award calendar view returns an entire month of lowest-price availability per API call. One request for YYZ-LAX returns ~30 days of pricing data (miles cost + taxes per day). This means covering 337 days for one route requires only ~12 requests (337 / 30), not 337 individual date searches.

**⚠️ Critical assumption to verify:** Does the calendar endpoint return only the cheapest option per day, or all available options across cabin classes? Does it include flight-level details (flight numbers, times, connections) or just daily price summaries? If it's price-summary-only, a second request per date is needed for flight details, which doubles or triples scrape volume and changes the entire architecture. This must be verified by inspecting the actual API response before any implementation.

**Login requirement:** As of late 2025, United requires MileagePlus login to view award pricing. This was explicitly done to block third-party search tools. The scraper needs to maintain authenticated sessions.

**Session management:** Use Playwright with persistent browser contexts to save login state between runs. Sessions stay alive for hours; the hourly scrape cadence naturally keeps them warm. Re-authentication is only needed when sessions expire (roughly once per day).

**Anti-bot evasion:** United uses dual-layer bot protection: Cloudflare (TLS fingerprinting at the edge) and Akamai Bot Manager (JavaScript sensor cookies). curl_cffi with Chrome TLS impersonation handles Cloudflare, but Akamai requires a real browser to generate and maintain `_abck` cookies. The proven approach is a hybrid architecture: Playwright runs in the background as a "cookie farm" keeping Akamai cookies fresh, while curl_cffi makes the actual API calls using those cookies. See `docs/findings/curl-cffi-feasibility.md` and `docs/findings/hybrid-architecture.md`.

**Account pool:** 10-20 MileagePlus accounts (free to create) distributing searches. Each account does ~350-700 searches per hour, which looks like an active but not suspicious user.

**⚠️ Account tolerance is the #1 unknown.** The per-account rate limit has never been empirically tested. If United locks accounts at 50 requests/hour instead of 350-700, the project needs 400+ accounts and is likely infeasible. This number must be determined experimentally before committing to any architecture. See "Account lifecycle management" below.

### Account lifecycle management 
The account pool is not a config value — it's a first-class subsystem that needs:

- **Health monitoring per account**: track success rate, captcha frequency, login failures, response latency
- **Automatic rotation and quarantine**: when an account shows signs of flagging (rising captcha rate, slower responses), pull it from the pool and rest it
- **Account-to-proxy affinity**: United will correlate account identity with IP. Switching an account across proxy IPs is a detection signal. Each account should have a sticky proxy assignment.
- **Session lifecycle**: define what happens when a session expires mid-scrape, how sessions are distributed across workers, re-authentication flow, concurrent access control
- **Credential storage**: encrypted at rest, never plaintext in config files. `config.example.json` shows the format; real credentials go in environment variables or an encrypted secrets file
- **Detection signals**: beyond just HTTP errors — monitor for degraded results (e.g., fewer results returned, higher prices than expected, redirect to login page)

### Scrape volume math

**Verified**: The calendar endpoint (`/api/flight/FetchAwardCalendar`) returns 30 days of pricing per request, covering ALL cabin classes (economy, business, first, premium economy) and both saver/standard award types in a single response. See `docs/api-contract/united-calendar-api.md` for full API contract.

**Phase 1 — Canada only (~2,000 routes):**

- 2,000 routes × 12 monthly windows = 24,000 requests for a full year sweep
- One full sweep per day: ~0.3 requests/second sustained
- Single worker completes in ~2 hours (with 5-10s delays between requests)
- No proxies needed, 1 MileagePlus account sufficient
- Can run on a laptop or free VPS

**Phase 1b — Full US+Canada (~20,000 routes):**

- 20,000 routes x 12 monthly windows = 240,000 requests for a full year sweep
- One full sweep per day: ~2.8 requests/second sustained
- With 5 concurrent workers: completes in ~13 hours
- Hourly refresh on 500 alert routes x 12 months = 6,000 requests/hour = 1.7/second

**⚠️ Concurrency model (underspecified, deferred to Phase 1b):** "5 concurrent workers" could mean threads, asyncio coroutines, multiprocessing, or Celery workers. Each has different implications for session management, database connection pooling, error isolation, and resource consumption. Playwright contexts are memory-heavy: 5 concurrent browsers consume 2-4 GB RAM. The choice should be deferred until empirical testing reveals the real constraints. Not needed for Phase 1 Canada-only scope.

Phase 1b tiered approach (when scaling to US):

| Tier | What | Routes | Frequency | Daily searches |
|------|------|--------|-----------|----------------|
| Alert routes | Routes with active user alerts | 500 | Every 2 hours | ~72,000 |
| Hot routes | Popular hub-to-hub, days 1-30 | 2,000 | Every 6 hours | ~32,000 |
| Rolling edge | All routes, day 330-337 | 20,000 | Daily | ~20,000 |
| Full sweep | Everything else | 20,000 | Weekly | ~137,000 |
| **Total** | | | | **~261,000/day** |

> **Math correction:** Alert tier = 500 routes x 12 monthly windows = 6,000 requests/cycle x 12 cycles/day (every 2 hours) = 72,000/day. The original figure of 288,000 was incorrect (would imply every 30 minutes). Total revised from ~477K to ~261K/day.

### Data storage

PostgreSQL from day one (not SQLite — concurrent writes from multiple workers will hit WAL contention on SQLite immediately). Same VPS.

```sql
CREATE TABLE availability (
    id SERIAL PRIMARY KEY,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    date DATE NOT NULL,
    cabin TEXT NOT NULL,
    miles INTEGER NOT NULL,
    taxes_cents INTEGER,
    seats INTEGER,
    direct BOOLEAN,
    flights JSONB,
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(origin, destination, date, cabin, direct)
);

-- Primary search pattern
CREATE INDEX idx_route_date_cabin ON availability(origin, destination, date, cabin);
-- For pruning and freshness checks
CREATE INDEX idx_scraped ON availability(scraped_at);
-- For alert matching (cabin + miles threshold queries)
CREATE INDEX idx_alert_match ON availability(origin, destination, cabin, miles);

-- Alerts
CREATE TABLE alerts (
    id SERIAL PRIMARY KEY,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    date_start DATE,
    date_end DATE,
    cabin TEXT,
    max_miles INTEGER,
    notify_channel TEXT NOT NULL,       -- 'telegram' or 'email'
    notify_target TEXT NOT NULL,        -- chat_id or email address
    last_notified_at TIMESTAMPTZ,      -- for deduplication
    last_notified_hash TEXT,           -- hash of last notification content
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at DATE                    -- auto-expire past-date alerts
);

-- Scrape job tracking
CREATE TABLE scrape_jobs (
    id SERIAL PRIMARY KEY,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    tier TEXT NOT NULL,                -- 'alert', 'hot', 'edge', 'sweep'
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/running/done/failed
    account_id TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error TEXT,
    UNIQUE(origin, destination, tier, started_at)
);
```

**Upsert strategy:** Use `INSERT ... ON CONFLICT (origin, destination, date, cabin, direct) DO UPDATE` to avoid duplicate rows. Without this, every scrape cycle creates redundant rows — millions per day before pruning.

**JSONB trade-off:** The `flights JSONB` column stores flight details (flight numbers, times, connections) but cannot be efficiently filtered in SQL. If alerts need to filter on "nonstop only" or "departing after 6pm," this becomes expensive. Consider a separate `flight_segments` table if query patterns demand it. For MVP, JSONB is acceptable.

**Pruning strategy:** Delete rows where a newer scrape exists for the same route/date/cabin, not a blanket time-based TTL. If the scraper goes down for 48 hours, a time-based TTL would wipe the entire database. Schedule `VACUUM` after bulk deletes — PostgreSQL's MVCC creates dead tuples that must be cleaned up, especially on a small VPS where autovacuum competes with write throughput.

**Storage estimate:** At ~261K writes/day, ~200-500 bytes/row = ~50-130 MB/day of raw inserts. With indexes and JSONB overhead, budget 200-300 MB/day. A 40 GB disk fills in ~4-5 months without pruning. Monitor disk usage and set up alerts.

### Alert system

After each scrape cycle, compare new results against saved alert criteria. Notify via Telegram Bot API (free, instant) or email. Matching is a simple database query: "any new availability on this route, in this cabin, at or below this miles threshold, since last notification?"

**Deduplication:** Without dedup, users get re-notified every 2 hours for unchanged availability. The `alerts` table tracks `last_notified_at` and `last_notified_hash` (hash of the matching availability data). Only notify when the hash changes (new availability appeared, price dropped, or seats changed).

**Alert evaluation timing:** "After each scrape cycle" is vague when there are 4 tiers with different cadences. Alert evaluation should run per-route immediately after that route is scraped, not as a batch after all routes complete. This gives the fastest notification latency for alert-tier routes.

**Rate limiting outbound notifications:** If a user creates a broad alert that matches hundreds of results, batch them into a single digest message rather than spamming individual notifications. Cap at ~1 notification per alert per evaluation cycle.

**Alert lifecycle:** Auto-expire alerts where all dates have passed. Cap alerts per user to prevent database bloat (e.g., 50 per notify_target).

**Notification reliability:** Queue failed notifications for retry. If Telegram rate-limits the bot (~30 messages/second), back off and retry. Log all notification attempts for debugging.

### Frontend

A simple web app. No signup required. Search bar with origin, destination, date range, cabin class. Results page showing availability with miles cost, seats remaining, last-checked timestamp, and a link to book on united.com. Alert form where you enter Telegram handle or email.

### Open-source architecture

```
award-scraper/
  core/
    db.py              # PostgreSQL/SQLite schema, queries, pruning
    scheduler.py       # cron/APScheduler, tiered priority logic
    alerts.py          # matching engine + Telegram/email notifications
    models.py          # AwardResult dataclass, route priority tiers
  scrapers/
    base.py            # abstract AwardScraper interface
    example.py         # documented skeleton with comments
    # actual implementations are .gitignored (private)
  web/
    app.py             # Flask/FastAPI search API
    templates/         # minimal HTML frontend
  config.example.json  # route list, account credentials template
  scrape.py            # main entry point
  README.md
```

The framework is public. Scraper implementations are private (publishing them accelerates the arms race with airline bot detection). The README includes a guide on reverse-engineering airline APIs using Chrome DevTools so people can write their own scraper plugins.

**⚠️ Open-source tension:** If scraper implementations are `.gitignored`, users cannot actually run the tool without reverse-engineering the API themselves. The project is an open-source framework with a proprietary plugin, not a usable open-source product. This is a deliberate trade-off — making the scraper public lets United trivially target it — but should be explicitly communicated. Options: (a) accept the framework-only model, (b) distribute scraper implementations via a separate private channel, (c) encrypted blobs with a shared key in a community Discord.

### Infrastructure and cost

| Component | Spec | Monthly cost |
|-----------|------|-------------|
| VPS | Hetzner CX32+ (4 vCPU, 8GB RAM minimum — CX22 at 4GB cannot run PostgreSQL + workers + web server, and Playwright needs 8GB+) | $7-38 |
| Proxies | 5-10 rotating datacenter IPs (may not be needed at low volume) | $0-15 |
| Database | PostgreSQL on same VPS | $0 |
| Notifications | Telegram Bot API | $0 |
| Domain | Optional | $0-1 |
| **Total** | | **$7-54/month** |

For personal use at 50 routes: $0 (run on your laptop or free Oracle Cloud VM with PostgreSQL).
For full US+Canada coverage: $15-40/month.

### Proxy strategy: start free, scale only if needed

United's Cloudflare protection is light (2/5 difficulty). At personal-use volume (50-100 routes/hour), proxies may not be necessary at all. The approach is to start with the cheapest option and only escalate if you get blocked.

**Tier 0: No proxy ($0).** Run the scraper from your home IP, a university network, or a free Oracle Cloud VM. At low request volume with human-like delays between searches, United's Cloudflare likely won't flag you. Try this first for at least a week before spending anything.

**Tier 1: Webshare free tier ($0).** If your IP gets rate-limited, Webshare offers 10 rotating datacenter proxies for free, forever, with unmetered bandwidth. US IPs included. This is enough for personal-scale scraping. Trustpilot rated 4.7/5.

**Tier 2: Oxylabs free tier ($0).** 5 free datacenter proxy IPs upon registration (no credit card required), US-located, 5GB/month bandwidth cap. At ~50-100KB per request, that's 50,000-100,000 requests/month, plenty for this project.

**Tier 3: GitHub Student credits ($0 with student status).** The GitHub Student Developer Pack includes $200 DigitalOcean credits, $100 Azure credits, and more. Spin up 2-3 small VMs in different US regions and use them as your own proxy pool. No proxy service needed; each VM has its own datacenter IP with unlimited bandwidth. Effectively free for the duration of the credits.

**Tier 4: Webshare paid ($3/month).** 100 shared datacenter IPs with 250GB bandwidth. Only needed if you're scaling to full US+Canada coverage (20,000 routes) and the free tiers aren't enough.

For a university student, Tier 0-3 should cover the project indefinitely at $0/month. Paid proxies are a last resort for scale, not a starting requirement.

## What Seats.aero data looks like vs United's actual data

Verified with manual testing: Seats.aero's cached data closely matches United's live results. Example from March 31, 2026:

- **United's calendar view** for YYZ-LAX: shows 13,000 miles + $68.51 taxes for most dates in April-May 2026. Some dates show 16.7k, 21.3k (dynamic pricing when saver inventory is sold out).
- **Seats.aero's cached data** for the same route: "2026-04-21, 20 hours ago, United, YYZ-LAX, 13,000 pts" which matches the 13k saver price on United's calendar.

The saver price (13k in this case) is the floor. Higher prices on specific dates mean saver seats are gone and United is showing the next tier up. Detecting and alerting on saver availability is the core value of the tool.

## Award availability basics (for context)

Airlines release award seats 330-360 days before departure. United's window is 337 days. When those seats first appear, premium cabin saver fares are available. They get booked quickly. Monitoring the "rolling edge" (day 330-337) for new availability is the highest-value use case.

Fare classes to know for United:
- X/XN = saver economy
- IN = saver business
- O = saver first

These are the fare classes that show the lowest miles prices and that partner programs (like Aeroplan) can also book.

## Key resources

- **Scraperly United guide**: https://scraperly.com/scrape/united-airlines (difficulty rating, anti-bot details, proxy recommendations, tool suggestions)
- **Scrapfly Academy**: https://scrapfly.io/academy (free structured course on web scraping fundamentals, reverse engineering, DevTools usage)
- **Playwright docs - Authentication**: https://playwright.dev/docs/auth (persistent contexts, session management)
- **curl_cffi**: https://github.com/yifeikong/curl_cffi (browser TLS fingerprint impersonation for direct HTTP)
- **mitmproxy**: https://mitmproxy.org (intercepting proxy for understanding auth flows)

## Operational concerns
### This is an adversarial systems problem, not a data engineering problem

The data storage and web serving components are straightforward. The scraping reliability system — maintaining stable access to United's API against an actively hostile counterparty — is where this project will succeed or fail. Account management, detection evasion, and failure recovery deserve as much design attention as the data pipeline.

### Error handling taxonomy

~261K daily requests will produce thousands of errors. Each needs different handling:

| Error | Meaning | Action |
|-------|---------|--------|
| HTTP 403 | Cloudflare blocked | Rotate proxy, retry with backoff |
| HTTP 429 | Rate limited | Back off this account/proxy for 15-60 min |
| Captcha page | Bot detection triggered | Quarantine account, escalate to Playwright |
| Redirect to login | Session expired | Re-authenticate, retry |
| Malformed JSON | API changed or partial response | Log raw response, skip route, alert operator |
| Timeout | Network issue or slow response | Retry once, then skip |
| Unexpected price (negative, 0, >500K) | Parsing bug or data anomaly | Reject row, log for investigation |

### Data validation

Scraped data is notoriously inconsistent. The parser must reject anomalous data rather than inserting garbage. A parsing bug that shows 5,000 miles for business class saver will trigger every alert and destroy user trust. Validate: miles > 0 and < reasonable max, dates within expected range, cabin is a known value, origin/destination are valid IATA codes.

### Monitoring and observability

For a system making ~261K requests/day, you need at minimum:
- Scrape success/failure rates per tier, per account, per proxy
- Average response time per request
- Database size, connection pool utilization, dead tuple count
- Alert notification success/failure rates
- Account health metrics (captcha rate, error rate per account)
- Disk usage alerts (critical for a small VPS)

Without these, you won't know the system is degrading until users report it.

### Recovery and deployment

- **Deployment:** Docker Compose with containers for PostgreSQL, scraper, scheduler, and web server. Minimum viable process management.
- **Backup:** Daily `pg_dump` to object storage. Alert configs and user data are the irreplaceable part; scraped availability is ephemeral.
- **Graceful shutdown:** If the scraper crashes mid-sweep, it should resume from where it left off using the `scrape_jobs` table, not restart the entire sweep.
- **Disk full:** PostgreSQL refuses writes when disk is full, corrupting in-flight transactions. Set up disk usage monitoring with alerts at 80% and 90%.

### Legal risk (acknowledged for Aeroplan but missing for United)

United's Terms of Service prohibit automated access. While legal risk for a free personal tool is low, it is not zero. United can send cease-and-desist letters. The `.gitignored` scraper implementations help here — the public repo contains a framework, not a United-specific scraping tool. This should be explicitly communicated in the README.

## Phase 1 plan

Validation-first. Each step gates the next — if any step reveals a blocking problem, pivot before wasting time on things that depend on it. The UI and alerts have zero risk; the scraping system has nearly all the risk. Defer all UI/alert work until the scraper has run successfully at 500+ routes for at least one week.

| Step | What | Why | Time |
|------|------|-----|------|
| **0** | ✅ DONE. Reverse-engineer the calendar API via DevTools. Capture exact URL, headers, cookies, request body, response JSON schema, error formats. Save HAR files. | Everything downstream depends on this API contract. Verified: calendar returns all cabins in one response, 30 days per request. See `docs/api-contract/`. | 1-2 days |
| **~~1~~** | ~~Create 2-3 MileagePlus accounts. Hit the calendar endpoint at increasing rates.~~ **SKIPPED for now.** | At Canada-only scale (~24K req/day, ~300-400 req/hr from one account), rate limits are unlikely to be an issue. One account with human-like delays (5-10s between requests) should be fine. If we get locked out, we'll deal with it then — no point over-engineering for a problem that may not exist at this volume. Rate limit testing becomes relevant when scaling to US in Phase 1b. | — |
| **1** | ✅ DONE. Build hybrid scraper: curl_cffi for API calls + Playwright cookie farm in background. Playwright maintains Akamai cookies (`_abck`) that burn every ~3-4 requests. curl_cffi pulls fresh cookies before each batch. Manual login via Playwright on first run (Gmail MFA). | curl_cffi alone fails — Akamai burns cookies after ~3-4 calls (see `docs/findings/curl-cffi-feasibility.md`). Pure Playwright works but is 10x slower. Hybrid gets curl_cffi speed with Playwright cookie freshness. See `docs/findings/hybrid-architecture.md`. | 2-3 days |
| **2** | ✅ DONE. Build minimal data path: PostgreSQL schema (with upsert), single-threaded scraper for 1 route, parser with validation, storage. Verify data matches united.com manually. | Validates API contract + parsing + storage end-to-end. No concurrency, no scheduling, no alerts. 922 records stored, 60 tests passing (models, db, parser). | 3-5 days |
| **3** | ✅ DONE. Burn-in test: 10-minute supervised run, then 1-hour run. Observe: session expiry timing, error patterns, rate limit behavior, response consistency across different routes and dates. 10 min: 4 routes, 48/48 windows (100%), 5,057 solutions, 0 errors. Data verified against united.com. 1 hour: stable, no issues. | Surfaces real-world constraints that the scaling model depends on. The supervised runs catch infrastructure issues (Playwright crashes, login failures) before committing to longer runs. | 2 days (mostly waiting) |
| **4** | ✅ DONE. Web UI: Next.js + shadcn/ui frontend with FastAPI backend. Dark theme, seats.aero-style layout. Home page with origin/destination search, results page with availability table (Date, Last Seen, Program, Departs, Arrives, Economy, Premium, Business, First). Green badges for available, gray for not available. Info icon per row shows all offerings for that date. | Users need to visualize scraped data. Pull UI work forward since the data pipeline is proven. | 1 week |
| **5** | ✅ DONE. **Latest run (2026-04-04): 100% success rate.** Ephemeral browser profile fix eliminated stale Akamai cookie poisoning. 15 routes, 180/180 windows OK (100%), 16,386 solutions found/stored, 0 rejected, 0 errors, 0 burns, 0 circuit breaks, 0 session expiries. 35 min scrape time. Default delay changed to 3s for sustained reliability. Key changes: fresh temp browser profile per session (no persistent `.browser-profile`), non-invasive cookie-only login polling, manual MFA handoff. Log: `logs/burn_in_20260404_161633.jsonl`. Previous run (2026-04-03): 99.5% (191/192 windows, 1 transient curl error). | Validates long-running stability before scaling to all Canada routes. 100% success rate across 15 routes confirms ephemeral profile approach is production-ready. | 1 day |
| **6** | Scale to all Canada routes (~2,000). Run for 1+ week. Measure scrape success rate, data freshness, disk usage, PostgreSQL performance. **Validated aggressive timing settings** from Step 5b experiment: `refresh_interval=3, delay=1s, session_budget=9999` (100% success, 0 errors, 0 burns across 84 requests/7 routes -- see `docs/findings/aggressive-timing-experiment.md`). Use these as the default for the scale-up; they cut per-route duration by 60-70% vs conservative settings. | Prove the core loop works at Canada scale before building anything on top of it. No concurrency model or account pool needed at this volume — single worker, single account. | 1-2 weeks |
| **6a** | Calendar view: month-view grid with price heatmap per cabin class. Green = saver available, yellow = standard only, gray = no availability. Click a date cell to see detail modal. Replaces scrolling through 337 rows. | Most impactful visual gap vs Seats.aero. Makes browsing a full year of data practical. The toggle placeholder already exists in the filter bar. | 3-5 days |
| **6b** | Date range search + result sorting: functional date picker in search form (start/end date). Column sorting in results table (by date, miles, cabin). Server-side date range filter on /api/search endpoint. | Currently shows all 337 days in fixed date order. Users need to narrow by travel window and sort by price to find deals. | 2-3 days |
| **6c** | Booking deeplinks: "Book on United" button on each result row/detail modal. Opens united.com award search pre-filled with origin, destination, date, and cabin. | Zero-friction path from finding availability to booking. Without this, users must manually re-search on united.com. Core UX gap. | 1 day |
| **6d** | Price history tracking: new `availability_history` table that logs price snapshots over time (INSERT, not just upsert). Line chart in UI showing miles cost trend per route/cabin over days/weeks. | Seats.aero charges $10/mo for this. Shows "is this a good price?" via historical context. Enables future price-drop alerts. | 3-5 days |
| **6e** | CSV data export: download button on search results page. Exports currently visible/filtered results as CSV (date, cabin, miles, taxes, direct, last_seen). | Power users want data in spreadsheets for analysis. Trivial to implement, high utility. | 1 day |
| **7** | Alerts and Telegram notifications. | Low risk, straightforward once data pipeline is proven. | 3-5 days |
| **8** | Gradually expand toward full US+Canada coverage. | Only after prior steps are stable. | Ongoing |

## Phase 2: Aeroplan (harder, separate phase)

Air Canada's Aeroplan is the next target after United is stable. It's more valuable (better partner pricing, fixed zone rates on partner flights) but significantly harder to scrape.

### Why Aeroplan matters

Aeroplan is one of the most valuable loyalty programs for award travel. It's a Star Alliance member with 40+ partner airlines, and critically:

- **Fixed zone pricing on partner flights**: A YYZ-FRA flight on Lufthansa or SWISS costs a predictable 60,000-70,000 points in business class based on distance, regardless of demand. This is often far cheaper than booking through the partner airline's own program.
- **Dynamic pricing on Air Canada/United/Emirates flights**: Prices fluctuate with demand, ranging from reasonable (10.7K economy) to absurd (178.4K for a domestic connection).
- **Transfer partner for every major US credit card program**: Chase, Amex, Capital One, Citi, Bilt all transfer 1:1 to Aeroplan. Anyone with transferable points can use it.
- **No fuel surcharges on partner awards**: Booking a Lufthansa flight through Aeroplan avoids the hundreds of dollars in surcharges Lufthansa's own program charges.

The key insight: you almost always want to book partner flights (not Air Canada metal) through Aeroplan, because those get fixed zone rates. Air Canada's own flights get dynamic pricing that can be 2-5x higher for the same distance.

### Verified data accuracy

Manually verified on March 31, 2026, for YYZ-SFO on April 21, 2026:

**Seats.aero cached data**: "2026-04-21, 23 hours ago, Aeroplan, YYZ-SFO, 10,700 pts economy, 25,400 pts business"

**Air Canada's live search results** (same route, same date, booked with Aeroplan points):

| Flight | Route | Economy | Premium Econ | Business |
|--------|-------|---------|-------------|----------|
| AC nonstop 08:15 | YYZ-SFO | 10.7K + CA$125 | -- | 25.4K + CA$125 |
| AC nonstop 10:45 | YYZ-SFO | 16.1K + CA$125 | -- | 178.4K + CA$125 |
| AC nonstop 18:50 | YYZ-SFO | 12.5K + CA$125 | -- | 111.6K + CA$125 |
| UA nonstop 06:30 | YYZ-SFO | 15K + CA$164 | -- | -- |
| UA via ORD 12:05 | YYZ-SFO | 11.6K + CA$171 | -- | 25.9K + CA$171 |
| UA via SAN 09:30 | YYZ-SFO | 12.5K + CA$171 | -- | 25K + CA$171 |

Seats.aero's 10,700 pts matches the lowest economy fare (AC nonstop 08:15). Seats.aero's 25,400 pts matches the lowest business fare (same flight). The data is accurate; Seats.aero captures the cheapest available option per cabin class.

Notice the massive price variation: business class on the same route, same day ranges from 25.4K (saver on the 08:15) to 178.4K (dynamic on the 10:45). The 25.4K is the deal. Monitoring for these saver prices and alerting when they appear is the core value.

### How Aeroplan's search works

The search returns all flights for a given route and date, across Air Canada, Air Canada Express, Air Canada Rouge, and partner airlines (United, Lufthansa, SWISS, etc.). Each flight shows:

- Points cost per cabin class (economy, premium economy, business)
- Taxes/fees in CAD
- Seats remaining per cabin
- Operating airline
- Connection details (stops, layover duration, airports)

The calendar bar at the top shows the lowest economy fare per day across a ~5-day window (e.g., Sun Apr 19: 22.8K, Mon Apr 20: 11.6K, Tue Apr 21: 10.7K). This is comparable to United's monthly calendar view, though Aeroplan's shows fewer days per view.

A single search for YYZ-SFO returned 49 flights, including Air Canada nonstops, United-operated connections through ORD/EWR/IAH/DEN, and Air Canada connections through YVR/YUL. The data is rich: origin, destination, departure time, arrival time, duration, stops, connecting airports, operating airline, points cost per cabin, seats remaining.

### Challenges specific to Aeroplan scraping

**Login + 2FA required**: As of March 2025, Air Canada requires Aeroplan login to search award availability. Every login triggers a 2FA code sent to email or SMS. The scraper needs to handle automated 2FA (IMAP email reading or Twilio SMS). Sessions stay alive for hours once authenticated, so the 2FA flow should only trigger once per day if the scraper runs hourly.

**Active legal hostility**: Air Canada sued Seats.aero in October 2023 (US District Court of Delaware) for scraping Aeroplan data. The judge denied the preliminary injunction in March 2024, but the case continues. Air Canada uses CFAA, trademark, and trespass-to-chattels claims. For personal-scale scraping (50 routes/hour from a residential IP), legal risk is effectively zero, but worth noting.

**Akamai bot detection**: Air Canada uses Akamai Bot Manager, which is harder to bypass than United's Cloudflare. Akamai injects JavaScript challenges that generate sensor data tokens. Direct HTTP requests with curl_cffi may not be sufficient; Playwright with stealth patches is the likely required approach.

**Website instability**: Air Canada's site is notoriously unreliable. Account creation frequently fails with generic error messages. The search itself can be slow and return inconsistent results. The scraper needs robust error handling, retries with exponential backoff, and tolerance for partial failures.

**No Scraperly guide**: Unlike United (which has a full Scraperly guide at https://scraperly.com/scrape/united-airlines), Air Canada has no Scraperly difficulty rating or scraping recipe. The reverse-engineering must be done from scratch using DevTools and mitmproxy.

### Aeroplan scraper approach

**Authentication flow**:
1. Navigate to aircanada.com, click sign in
2. POST credentials (Aeroplan number + password)
3. Receive 2FA prompt, code sent to email
4. Read code from email via IMAP (programmatically)
5. Submit 2FA code
6. Session cookies set; save to persistent Playwright context

**Search flow**:
1. Navigate to the award search page with "Book with Aeroplan points" toggled
2. Enter origin, destination, date, passengers
3. Submit search
4. Wait for results to load (Aeroplan uses a polling/async pattern; results don't appear instantly)
5. Intercept the API response JSON via Playwright's network interception, or parse the rendered DOM
6. Extract: flights, points per cabin, taxes, seats remaining, operating airline, connections

**Data model** (extends the United schema):

```sql
-- Same availability table works; the 'program' column distinguishes United vs Aeroplan
-- Additional fields for Aeroplan:
--   operating_airline TEXT (e.g., 'AC', 'UA', 'LH', 'LX')
--   taxes_cad INTEGER (Aeroplan shows taxes in CAD, not USD)
--   connections JSONB (stop airports and layover durations)
```

**Session persistence**: Use Playwright `launch_persistent_context` with a dedicated user data directory for Aeroplan. The hourly scrape cycle keeps the session alive. Re-authenticate with 2FA only when the session expires (detected by redirect to login page).

**Rate limiting**: Keep volume conservative. Air Canada is aggressive about blocking scrapers. For personal use, 50-100 route searches per hour with 5-10 second delays between requests. No proxy needed at this volume from a residential IP.

### Aeroplan phase plan

1. Get Aeroplan account working (mobile app or phone call if website fails)
2. Open DevTools, capture the full auth flow (login + 2FA) and search flow network requests
3. Build Playwright scraper with persistent context and IMAP-based 2FA handling
4. Get one route working end-to-end: YYZ-SFO, parse results, store in database
5. Expand to 10-50 personal routes alongside the United scraper
6. Add Aeroplan results to the same web UI and alert system

The framework from United (database, alerts, scheduling, web UI) carries over unchanged. Only the scraper plugin is new.
