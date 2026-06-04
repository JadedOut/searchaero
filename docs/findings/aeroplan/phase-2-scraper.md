# Aeroplan Phase 2 — Scraper → Store → Alerts: Findings + Live Runbook

**Purpose.** Phase 0 proved the transport (intercept `air-calendars` JSON, no SigV4)
and measured the ~30–40 min session TTL; Phase 1 automated the Gigya login + 2FA behind
a reusable `AeroplanSession`. Phase 2 turns those into an actual **scraper**: navigate
each 5-day `air-calendars` window on a logged-in session, parse the cheapest-economy
miles + taxes per day, and **store the rows in the existing United availability table —
tagged by program** so the existing alert/watch read layer sees them with **zero new
query pipeline**.

This doc is two things: (1) the findings/architecture record (house style: TL;DR banner,
tables, honest caveats), and (2) **the account-safe live runbook the USER executes** to
gate correctness and end-to-end behavior. Agents built and offline-tested the code; the
**live run is the user's gate** (it touches the real authenticated account — Claude
cannot drive it).

---

## TL;DR — Status: ✅ DONE — built, offline-tested, and **live-verified 2026-06-03**.

**Phase 2 delivers** the navigate-per-5-day `air-calendars` scraper → program-tagged
**shared** availability store → the existing alert/watch read layer, exposed through the
CLI as `searchaero search --program aeroplan` / `searchaero query --program aeroplan`.

| Question | Result |
|---|---|
| Transport for the scraper? | ✅ **Navigate-per-5-day window**, intercept `air-calendars` (Phase 0's proven eager-capture pattern). URL-flex widening and in-page SigV4 replay are both **dead** (Phase 0 tests A + B). |
| Data scope (MVP)? | ✅ **air-calendars only** — cheapest economy miles + taxes **per day**. No seats/quota (deferred). |
| Where does it store? | ✅ **Option A — shared `availability` table + a `program` column** on the unique key. United and Aeroplan rows live side by side; one read layer. |
| Read / alert path? | ✅ **Reused unchanged.** `searchaero query` and the alert/watch matcher take an optional `program=`; rows carry the real per-row program. |
| Live-validated? | ✅ **Yes — 2026-06-03** (see *Live verification result* below). Slice A correctness gate passed; one fresh-login window-1 gap + one cosmetic crash noted as follow-ups. |

**Locked decisions (this phase):**

- **MVP = air-calendars only** — cheapest-economy **miles + taxes per day**; **no
  seats/quota** (the `air-bounds`/`polldapi` flight list is explicitly deferred —
  see *Deferred follow-ups*).
- **Storage = Option A** — one shared availability table + a `program` column on the
  unique key (the alternative, a separate Aeroplan DB, was rejected: it would fork the
  read/alert pipeline).

---

## Live verification result — 2026-06-03 (Slice A: PASS)

First live run on the real account, agent-operated keyboard with the user supplying
the SMS code (human in the loop), single route, capped to 3 windows.

- **Command:** `searchaero search --program aeroplan YYZ LAX --from 2026-06-03 --to 2026-06-15 --mfa-method sms`
- **Login:** automated Gigya login + SMS 2FA via the `~/.searchaero/aeroplan_sms_code`
  drop-file protocol → `Aeroplan login confirmed` ~65 s after launch. Arkose passive
  (warm profile).
- **Result:** `10 found, 10 stored, 0 rejected, 1 error, expired=False`. 10 rows
  persisted with `program='aeroplan'` for YYZ→LAX, **2026-06-06 … 2026-06-15**.
- **Correctness:** baseline economy = **12,500 miles / CA$170.59** — matches the Phase-0
  eyeball-confirmed value (`convertedMiles.base=12500`, `totalTaxes≈17060`). Higher days
  (30,800 / 22,100) reflect real day-of-week pricing. ⇒ **parse is correct on live data.**

**Two follow-ups surfaced (neither lost data; both minor) — ✅ both FIXED 2026-06-03:**

1. **Window-1 fresh-login gap (lost the earliest ~3 days).** The first window's
   `page.goto` was interrupted by the post-login OIDC consent redirect
   (`…/clogin/pages/proxy?mode=afterConsent…`) — the documented "fresh login bleeds
   through the consent redirect" race. The scraper's pre-window URL poll didn't help
   because the redirect hadn't started yet when it checked; it fires *during* window 1's
   navigation. **Fix (shipped):** `cli.py::_aeroplan_warmup(session)` does a throwaway
   navigate to the AC home + redirect-chain settle *after* login confirms and *before*
   the scraper runs, so the consent hop completes on a throwaway page. Test-safe (bails
   instantly when `page.url` isn't a real string, e.g. under mocks).
2. **Cosmetic Unicode crash → exit 1 after data was committed.** The success-tip line
   prints a `→` (U+2192) which the Windows console (cp1252) can't encode, raising
   `UnicodeEncodeError` *after* all 10 rows were already stored. (Rich box-drawing tables
   in `query` would crash the same way under redirected Windows stdout.) **Fix (shipped):**
   `cli.py::main()` reconfigures `sys.stdout`/`sys.stderr` to UTF-8 at entry. Verified:
   `PYTHONIOENCODING=cp1252 … query … --table-view programs` now exits 0 and renders.

### Slice B — end-to-end alert (✅ PASS 2026-06-03)

Ran on the real DB against the 10 stored Aeroplan rows: created an alert
`YYZ-LAX ≤13,000 miles` (threshold below United's 15,000 economy, so only Aeroplan's
12,500 fares can match), ran `searchaero alert check`, and confirmed **8 matched fares
each tagged `program=Aeroplan`** in the human output (and `program:'aeroplan'` in the
JSON/`check_alert_matches` layer). United rows correctly excluded ⇒ program-agnostic
matching works and the program column distinguishes them. The matched-row human output
gained a `Program` column (`cli.py::_alert_check`). Temp alert removed afterward.

---

## Architecture — split front-end, shared back-end

Two independent front-ends produce normalized award rows into **one** table; everything
downstream (query, alerts, watches, presentation) reads that one table, optionally
filtered by `program`.

```
            ┌──────────────────────── UNITED front-end ─────────────────────────┐
            │  core/cookie_farm.py  →  core/hybrid_scraper.py / core/united_api  │
            │  (warm cookies; intercept United award JSON)                       │
            └────────────────────────────────┬───────────────────────────────────┘
                                              │  AwardResult(program='united')
                                              ▼
                               ┌─────────────────────────────┐
                               │   core/db.py  availability   │
                               │  UNIQUE(program, origin,     │
                               │  destination, date, cabin,   │   ← single shared store
                               │  award_type)                 │
                               └──────────────┬──────────────┘
                                              ▲  AwardResult(program='aeroplan')
            ┌─────────────────────────────────┴───────────────────────────────────┐
            │  core/aeroplan_session.py  (logged-in headed session, ~30–40m TTL)   │
            │      └─ core/aeroplan_scraper.py  (navigate-per-5-day window)        │
            │             └─ core/aeroplan_api.py  (URL build + air-calendars parse)│
            └─────────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
        searchaero query --program …   │   alert/watch matcher (program-aware)
        core/presentation.format_programs_table (real per-row program)
```

### New / changed files

| File | Role | New/changed |
|---|---|---|
| `core/aeroplan_api.py` | Pure transport/parse — no browser. `build_availability_url`, `window_dates` (5-day stepping), `parse_calendar_response` (cheapest-economy miles + taxes/day), `redact_card_numbers`, `extract_sent_flexibility`. | **NEW** |
| `core/aeroplan_scraper.py` | `scrape_route_aeroplan(origin, dest, session, conn, *, delay, from_date, to_date, months, max_windows, step_days, verbose, progress_cb, capture_timeout)` — navigate-per-5-day-window, intercept `air-calendars`, parse → `validate_aeroplan_fare` → `db.upsert_availability(program='aeroplan')` → `db.record_scrape_job`. Session-expiry guard surfaces `expired=True` on login-redirect/401/403. | **NEW** |
| `core/aeroplan_session.py` | Reusable logged-in `AeroplanSession` (from Phase 1) consumed by the scraper. | Phase 1 (listed for completeness) |
| `core/models.py` | `validate_aeroplan_fare(...)`; `AwardResult.program`. | changed |
| `core/db.py` | `program` column on the unique key `(program, origin, destination, date, cabin, award_type)`; idempotent `ensure_program_column()` migration backfills existing rows to `'united'`; all read queries accept optional `program=`. | changed |
| `cli.py` | `searchaero search --program {united,aeroplan}` (aeroplan = single-route, headed, MFA via email by default); `searchaero query --program {united,aeroplan}` (omit = all programs). | changed |
| `core/presentation.py` | `format_programs_table` shows the real per-row program. | changed |

---

## Why navigate-per-5-day is the only path

This is **not** a fresh design choice — Phase 0 closed off every alternative empirically:

- **URL `flexibility` injection is DEAD** (Phase 0 test A). Injecting
  `&flexibility=10&flex=10&calendarFlexibility=10` was ignored; the SPA always sends
  `flexibility: 2` and returns 5 days. The URL cannot widen the window.
- **In-page SigV4 replay is DEAD** (Phase 0 test B). Re-firing `air-calendars` from the
  booted page returns **403 even for a byte-identical replay** (`credentials:'omit'`
  reaches the gateway but can't reproduce a valid signature; `credentials:'include'` is
  CORS-blocked). There is no path to a self-issued 200.
- **The SPA fixes 5 days per call** (`flexibility: 2` = ±2 days, a 5-day window centered
  on the requested date). So one window = one full navigate. **Navigate-per-window is the
  sole viable transport.**

### Capture discipline (carried verbatim from Phase 0 — the scraper obeys all of it)

- The availability page **never reaches `networkidle`** — continuous tracking/heartbeat
  beacons (bttrack, GA, adentifi) keep the network busy. Do **not** gate capture on page
  idle.
- The award XHRs **trickle in over ~17 s**; `air-calendars` arrives **last** (~17 s after
  navigate). Capture **must** wait on the specific response, not on load.
- Use `page.expect_response(...)` and **read the body eagerly the instant it arrives**
  (before the next navigation evicts it). **Never call `.json()` inside the sync
  response handler** — that triggers `Network.getResponseBody: No resource…` body
  eviction. Stash the raw body, parse after.
- After a **fresh** login, the first navigate can be interrupted by the OIDC consent
  redirect tail — do a throwaway warm-up navigate (or wait for the app domain) before the
  first real capture. (`AeroplanSession` already lands logged-in; relevant only on a
  brand-new login mid-batch.)

---

## Two documented assumptions needing a human eye

These two normalization choices are **judgment calls baked into the MVP**. They are
correct enough to ship and to gate against the live UI, but a human should confirm they
match intent — flagged here explicitly so they are not silently inherited.

### Assumption 1 — `award_type` / `cabin` mapping

The `air-calendars` price strip is **economy-only** and exposes a per-day
`fareFamilyCode` (e.g. `STANDARD`; `dictionaries.fareFamilyWithServices` maps it to
`commercialFareFamily: RWDECO`, `cabin: eco`). The MVP normalizes every strip row to:

- `cabin = 'economy'`
- `award_type = 'Standard'`

**Flag for review:** if the **raw `fareFamilyCode`** (STANDARD / and any future
variants) should be preserved on the row instead of being collapsed to a fixed
`award_type='Standard'`, that is a one-line change in the parse/normalize step. The MVP
chose the fixed mapping for clean cross-program comparability with United; preserving the
raw code is the documented alternative.

### Assumption 2 — taxes stored **as-is in CAD cents** (no currency column)

`air-calendars` reports `totalTaxes` in **cents CAD**. Phase 0 confirmed the value
on-screen: **`totalTaxes = 17060` cents = CA$170.60** (i.e. 17060 ÷ 100 = 170.60 — the UI
rounds the *display* to "CA$171", but the stored ground-truth cents are **170.60
dollars**). The MVP stores the cents value **unconverted**, in the same integer-cents
column United uses.

**Flag for review — cross-program currency mismatch:** United stores **USD cents**;
Aeroplan stores **CAD cents**, in the *same* column, with **no currency column** to
distinguish them. This is a **documented limitation, not a bug** — the MVP does **not**
convert currencies. Any future cross-program "cheapest taxes" comparison must treat
Aeroplan taxes as CAD and United as USD. Adding a `currency` column is the clean fix and
is deferred.

---

## Account-safe live runbook (THE deliverable — the USER runs this)

**Discipline carried from Phase 0/1 — applies to every step below:**

- **HEADED ONLY.** No headless (Aeroplan = single-route, headed `AeroplanSession`; the
  session manager refuses headless).
- **Single account, single route per run, capped windows.** `--max-windows` bounds the
  navigates; keep it to a handful for Slice A.
- **Warm, already-logged-in session** (SMS path is fine for these gates — email-2FA
  delivery selection is still parked, see TODO-1).
- **Human in the loop for ALL live runs.** No agent runs these.
- **Never park or transfer points.** Searches only. A freeze is recoverable; a transfer
  is not.
- **~30–40 min session TTL bounds one login's batch.** If the scraper surfaces
  `expired` mid-batch, simply re-run — re-auth is `AeroplanSession`'s job, not the
  scraper's. Keep each batch comfortably inside one ~30 min window.

The user **records GO/NO-GO + the numbers** for each slice. These are **human eyeball
gates — no code assertions** decide correctness.

### Slice A — correctness gate (eyeball the numbers)

**Goal:** confirm the scraped miles + taxes match the live aircanada.com Aeroplan
availability calendar, to the **point** (miles) and to the **CA$** (taxes).

1. Warm session: be **already logged in** to Aeroplan in the headed session (SMS path
   fine).
2. Scrape a **few windows only** for a **single route**, near-future dates:

   ```
   .venv\Scripts\python.exe cli.py search --program aeroplan YYZ LAX ^
     --from 2026-08-13 --to 2026-08-27 --max-windows 3
   ```

   (`--from 2026-08-13 --to 2026-08-27` ≈ three 5-day windows: 08-13…08-17, 08-18…08-22,
   08-23…08-27. Adjust dates to near-future; **keep `--max-windows` small**.)

3. In a real browser, open the **same route + dates** on aircanada.com's Aeroplan
   availability calendar (logged in).
4. **For each returned day, eyeball:**
   - **Miles** — must match **to the point** (e.g. scraped `12500` = on-screen "12.5K").
   - **Taxes** — must match **to the CA$**, remembering **cents → dollars** (scraped
     `17060` cents = **CA$170.60**; the site may *display* "CA$171" rounded — confirm the
     dollar value, 170.60, not the rounded label).
5. **GO** = every eyeballed day matches (miles to the point, taxes to the dollar).
   **NO-GO** = any mismatch → record which day, scraped value, and on-screen value.

> Note on routing: some days the cheapest routing is via a **different airport** (Phase 0
> saw YYZ→ONT instead of LAX when cheaper). That is expected — the strip returns the
> cheapest economy bound for the day. Eyeball against the **calendar strip price**, not a
> specific flight.

### Slice B — end-to-end (store → query → alert carries `program=aeroplan`)

**Goal:** confirm the full path stores program-tagged rows and the existing read/alert
layer surfaces them as `program=aeroplan`.

1. **Full scrape** (same warm session, single route, capped windows — reuse Slice A's run
   if rows already landed):

   ```
   .venv\Scripts\python.exe cli.py search --program aeroplan YYZ LAX ^
     --from 2026-08-13 --to 2026-08-27 --max-windows 3
   ```

2. **Confirm rows stored, tagged aeroplan:**

   ```
   .venv\Scripts\python.exe cli.py query --program aeroplan YYZ LAX
   ```

   Expect rows for the scraped days, each showing **program = aeroplan**. (Run
   `query` **without** `--program` to see United + Aeroplan side by side and confirm the
   per-row program column is real, not hard-coded.)

3. **Add an alert/watch** on the route (use the project's existing watch command; example
   shape):

   ```
   .venv\Scripts\python.exe cli.py watch add YYZ LAX --program aeroplan
   ```

   (Use whatever the live `cli.py watch --help` / alert command is; the point is the watch
   matches on the same shared table.)

4. **Confirm a matched-row output carries `program=aeroplan`** — the matcher reads the
   shared table, so a matched Aeroplan row must surface with **program=aeroplan** in the
   alert/watch output.
5. **GO** = rows stored with `program=aeroplan`, `query --program aeroplan` returns them,
   and the watch match output carries `program=aeroplan`. **NO-GO** = rows missing,
   mis-tagged, or the matcher drops/ mislabels the program.

---

## Deferred / parked follow-ups (NOT Phase-2 blockers)

- **Seats / quota.** The per-flight `quota` ("3 seats left") lives in the
  `air-bounds` / `polldapi` flight list, **not** in `air-calendars`. The MVP is
  **calendar-only (cheapest economy miles + taxes/day)**; seats/quota is **explicitly
  deferred**. A clean live capture of the large `polldapi` flight-list body was also still
  outstanding from Phase 0.
- **Unattended re-auth loop.** Both are parked in
  [`aeroplan-todo.md`](./aeroplan-todo.md) and are **Phase-3 (fully-unattended)
  prerequisites, not blockers** for this keyboard-driven, human-in-the-loop MVP:
  - **TODO-1** — live verification of email-2FA selection + the Gmail-IMAP responder
    (built, never exercised live; the 2026-06-03 run used SMS).
  - **TODO-2** — cold-profile / fresh-IP Arkose re-verification (all passing Arkose
    results so far are on a warmed profile).
- **Currency column.** Adding a `currency` column to disambiguate USD (United) vs CAD
  (Aeroplan) taxes — see Assumption 2.

---

## Related

- [`phase-0-transport-spike.md`](./phase-0-transport-spike.md) — transport, session
  lifetime (~30–40 min), calendar strip (5 days/call), URL-flex (dead), SigV4 replay
  (dead), `air-calendars` schema.
- [`phase-1-login-automation.md`](./phase-1-login-automation.md) — scripted Gigya login +
  2FA, Arkose passive (warmed profile), `AeroplanSession`.
- [`aeroplan-todo.md`](./aeroplan-todo.md) — open gates (TODO-1 email responder, TODO-2
  cold-profile Arkose).
