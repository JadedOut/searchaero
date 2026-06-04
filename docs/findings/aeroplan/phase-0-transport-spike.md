# Aeroplan Phase 0 — Transport Spike: Findings + Runbook

**Purpose.** Phase 0 de-risks the core data-extraction mechanism *before* any real
Aeroplan scraper is built. It proves that a pre-authenticated Playwright persistent
context can navigate the Aeroplan availability URL and **intercept the award JSON
responses** (`air-calendars`, `reward/market-token`, `air-bounds`, `polldapi`) with
**zero SigV4 work** — we let the page's own SPA fire the signed calls and read the
responses — and it **measures the single gating number: session lifetime**. Together
with the per-endpoint cold-navigate firing result, these outcomes form the **Go /
No-Go gate for Phase 1** (login automation + Arkose).

---

## TL;DR — Result: ✅ GO for Phase 1

**Live run: 2026-06-01, real account (JIAMING, 7,747 pts), route YYZ→LAX 2026-08-15.**

| Question | Result |
|---|---|
| Transport works without SigV4? | ✅ **Yes.** Response interception captured real award JSON; a 22 KB `air-calendars.json` was saved and verified offline. No signer reproduced. |
| Cold navigate fires the full flow? | ✅ **Yes** — a bare navigate (no clicking) fires `market-token → air-bounds → polldapi → air-calendars`, all `200`. |
| Schema matches recon + live UI? | ✅ **Yes** — `convertedMiles.base = 12500`, `totalTaxes = 17060` (cents) = the on-screen **12.5K + CA$171**. |
| **Session lifetime?** | **≈ 32–38 min from login**, under active use. **Not** the feared 5–10 min idle logout; **not** multi-hour. Absolute TTL — activity did not extend it. |
| Expiry behavior | Navigate after expiry **redirects to the Gigya login screen-set** (`Kilo-RegistrationLogin`) ⇒ re-auth = full login + 2FA (+ Arkose risk). |

**What this means for Phase 1:** the **"log in once, search many"** architecture is
viable — but only within a **~30-minute window per login**, after which a full
re-authentication (2FA, possibly Arkose) is required. That ~30-min re-auth cadence is
the single most important operational input, and it is now measured.

---

## Run protocol / runbook

The live spike is a **manual, human-in-the-loop** session. **No agent runs this.**
Agents only build the instrument, run the offline `--dry-run`, and write this doc;
the live measurement is taken by the user, because it touches a real authenticated
Aeroplan account (Claude cannot enter credentials and must not drive the account
autonomously).

### Account-safety reminders (read before every run)

- **Real account is fine at this volume.** A handful of logins plus a few searches
  is indistinguishable from a normal user. A dedicated throwaway account is only
  provisioned later, *if* Phase 1 proves automated login clears Arkose.
- **Single route, single account.** Default `YYZ → LAX`, near-term date. The harness
  caps total navigations (`--max-navigations`, default 18) and floors the probe
  interval at 5 min.
- **Headed only.** The harness refuses `--headless`. Headless is non-viable against
  Air Canada's Akamai / bot stack (proven by recon and `core/cookie_farm.py`).
- **A freeze is recoverable.** **Never park or transfer points** as a "test"; only run
  searches.
- **No credentials are ever read or written** by the harness. Login + 2FA are done by
  the user, by hand, in the headed browser window.

### Prerequisites

- Repo root: `C:\Users\jiami\local_workspace\seataero-src`
- Python venv: `C:\Users\jiami\local_workspace\seataero-src\.venv` (Playwright 1.58.0)
- Real Aeroplan account credentials (entered manually by the user, never by an agent)

### Step 0 — Offline sanity check (no account, safe anytime)

```bash
cd C:\Users\jiami\local_workspace\seataero-src
.venv\Scripts\python.exe -c "import ast; ast.parse(open('scripts/experiments/aeroplan_transport_spike.py').read()); print('syntax OK')"
.venv\Scripts\python.exe scripts\experiments\aeroplan_transport_spike.py --help
.venv\Scripts\python.exe scripts\experiments\aeroplan_transport_spike.py --dry-run
.venv\Scripts\python.exe scripts\experiments\aeroplan_transport_spike.py --headless --dry-run   # must refuse, exit 2
```

### Step 1 — Live run (headed; manual login + 2FA, then lifetime probing)

```bash
cd C:\Users\jiami\local_workspace\seataero-src
.venv\Scripts\python.exe scripts\experiments\aeroplan_transport_spike.py ^
  --org YYZ --dest LAX --date 2026-08-15 --probe-interval 5 --max-duration 90 ^
  --out-dir logs\aeroplan_spike_run1
```

What happens:

1. A **headed Chrome** window opens against the persistent profile dir
   `scripts/experiments/.aeroplan-profile/`.
2. **If not already logged in:** the console prints manual-login instructions and
   **polls**. Complete the Aeroplan login + 2FA **by hand**. (The profile persists, so
   later runs reuse the session.)
3. The harness performs a **cold navigate** and reports which endpoints fired.
4. It **re-navigates every `--probe-interval` minutes**, logging each probe to
   `<out-dir>/probes.jsonl` and every aircanada/dapi response to
   `<out-dir>/network_debug.log`, until a `401`/`403`/login-redirect, or
   `--max-duration` elapses. On expiry it prints `TIME-TO-EXPIRY` and exits.
5. The first payload of each endpoint is saved to `<out-dir>/` with the Aeroplan card
   number and `userId` redacted.

> **⚠️ For a clean single-number lifetime measurement, run ONE uninterrupted pass
> from a *fresh* login to expiry.** The lifetime timer counts from process start; if
> you restart the harness and it *reuses* a still-live session, the printed number
> undercounts (this happened in the 2026-06-01 run — see Session lifetime below).

---

## Cold-navigate results

A bare cold navigate to the availability URL fires the **full** flow — no date-click
needed. Real endpoint URLs and observed arrival timing (cleanest capture, run "run2"):

| Endpoint              | Fired on cold navigate? | HTTP status | Real URL / arrival |
|-----------------------|-------------------------|-------------|--------------------|
| `reward/market-token` | ✅ yes                  | `200`       | `akamai-gw.dbaas.aircanada.com/loyalty/dapidynamicplus/1ASIUDALAC/v2/reward/market-token` — ~3 s; returns `{pollId}` |
| `air-bounds`          | ✅ yes                  | `200`       | `…/dapidynamicplus/1ASIUDALAC/v2/search/air-bounds` — ~6 s; returns `{pollId}` (async) |
| `polldapi`            | ✅ yes                  | `200`       | `akamai-gw.dbaas.aircanada.com/loyalty/polldapi` — polled ~2× over ~6–18 s |
| `air-calendars`       | ✅ yes                  | `200`       | `…/dapidynamic/1ASIUDALAC/v2/search/air-calendars` — **~17 s (arrives LAST)** |

**Key timing finding:** the award XHRs **trickle in over ~17 seconds**, with
`air-calendars` arriving last. A naive "read responses at page load / `networkidle`"
captures **nothing** — the page also never reaches `networkidle` (continuous
tracking/heartbeat beacons). Capture **must** wait on the response stash for the
specific endpoints (the harness uses a 35 s bounded wait, `--capture-timeout`).

> "Needed date-click?" — **No.** The fully-formed deep-link URL
> (`org0/dest0/departureDate0/tripType=O&ADT=1&…&marketCode=DOM&lang=en-CA`)
> auto-executes the search on navigate; `--with-date-click` was not required.

---

## Session lifetime

**Measured ≈ 32–38 minutes from login, under active use** (re-navigating every 5 min).

Timeline of the 2026-06-01 run:

| Time | Event |
|---|---|
| 17:02:13 | Fresh login confirmed (after an accidental tab-close mid-session) |
| 17:08 / 17:13 / 17:18 | Probes — award calls `200`, session alive |
| 17:23 / 17:28:43 / **17:34:26** | Still alive — `air-calendars`/`air-bounds`/`polldapi` all `200` in the network log (**last confirmed-alive: 17:34:26**) |
| **17:40:08** | Navigate → `login.aircanada.com/accounts.getScreenSets?screenSetIDs=Kilo-RegistrationLogin`; harness flags `session_expired` (logged-out DOM/cookie signal) |

- **Login → last-confirmed-alive:** 17:02 → 17:34 = **~32 min**
- **Login → expiry detected:** 17:02 → 17:40 = **~38 min** (true expiry is somewhere in
  the 17:34–17:40 window; resolution is ±5 min, the probe interval)
- **Nature:** the harness re-navigated every 5 min (continuous activity) and the
  session **still expired on schedule** → this is an **absolute session TTL (~30–40
  min)**, not a sliding idle timeout that activity refreshes.
- **Expiry signal:** redirect to the Gigya login screen-set (`Kilo-RegistrationLogin`)
  + logged-out cookie/DOM state. Re-auth therefore means a **full login + 2FA**, with
  Arkose exposure — exactly the Phase 1 risk.
- **Measurement caveat (honest):** the harness printed `~17.9 min`, but that counted
  from a *relaunch* at 17:22 that **reused** an already-live session — an undercount.
  The true login-to-expiry is the ~32–38 min above. The FlyerTalk "~5–10 min idle
  logout" anecdote is **not** what we observed; the real number is meaningfully longer
  but still bounded at roughly half an hour.
- **Raw logs:** `logs/aeroplan_spike_run5/probes.jsonl` (probe records) and
  `logs/aeroplan_spike_run5/network_debug.log` (per-response ground truth).

---

## Schema confirmation

Verified against the captured, redacted `air-calendars.json` (22 KB). Source of truth:
`docs/findings/aeroplan/auth-recon.md` §3.

| Field | Recon path | Endpoint | Resolves in payload? |
|-------|-----------|----------|----------------------|
| Miles | `prices.unitPrices[].milesConversion.convertedMiles.base` | `air-calendars` | ✅ **yes** — `12500` (= on-screen "12.5K") |
| Taxes (cents CAD) | `totalTaxes` | `air-calendars` | ✅ **yes** — `17060` (= on-screen "CA$171") |
| Top-level shape | `{ data[], meta, dictionaries }`, one entry/day | `air-calendars` | ✅ **yes** — 5 day-entries (Aug 13–17) |
| Seats left | `quota` | `air-bounds`/`polldapi` flight-list | ⚠️ **not captured** — see limitation #3; documented in recon §3 and visible in UI ("3 seats left", "4 seats left") |

- [x] Miles path confirmed — `12500`
- [x] Taxes path confirmed — `17060` cents
- [x] Top-level `data/meta/dictionaries` + per-day entries confirmed
- [ ] Seats `quota` path — **not yet captured in a saved payload** (Phase 2 follow-up)
- [x] Redaction working — no raw Aeroplan card number in saved payloads; `userId`
  (member number) redaction added after it was seen in a `polldapi` body

---

## Instrument notes & known limitations

The transport question is fully answered, but the **spike harness itself** has rough
edges that must be hardened before Phase 2 (where reliable per-search capture matters):

1. **`networkidle` is unusable on this page.** Continuous tracking/heartbeat beacons
   (bttrack, GA, adentifi) mean the page never goes network-idle. Capture is driven by
   a bounded wait on the response stash, not page idle. *(Fixed during the spike.)*
2. **Per-probe capture is racy on re-navigation.** The async `air-bounds → polldapi`
   flight-list poll and the slow `air-calendars` (~17 s) can land at the edge of the
   35 s window, so `probes.jsonl` `endpoint_status` sometimes shows `null` **even when
   the calls returned `200`**. The **`network_debug.log` is the reliable signal** and
   was used for the lifetime ground truth above. Hardening idea for Phase 2: gate on
   `page.expect_response(...)` per endpoint, or wait on a results-DOM signal.
3. **Flight-list `polldapi` not cleanly captured.** There are *two* `polldapi` calls —
   the small `market-token` resolution poll (~350 B) and the large `air-bounds`
   flight-list poll (~60 KB, with `airBoundGroups` + `quota`). The harness repeatedly
   saved the small one; the large one didn't complete within the window on
   re-navigation. The refined capture saves all polls + a `polldapi_flightlist.json`
   when `data.airBoundGroups` is present, but a clean live capture of it is still
   **outstanding** (Phase 2).
4. **A 22 KB-body redaction error once aborted the whole save loop** (only
   `market-token.json` saved). *(Fixed — per-endpoint saves are now isolated in
   try/except.)*
5. **Lifetime timer counts from process start**, so reusing a live session undercounts
   (see Session lifetime caveat). Run one uninterrupted fresh-login→expiry pass for a
   clean number.

---

## Go / No-Go for Phase 1

**Decision: ✅ GO** — proceed to Phase 1 (login automation + Arkose assessment).

Rationale, against the gate criteria:

- ✅ **Transport proven without SigV4** — real JSON intercepted and saved.
- ✅ **Cold navigate fires the full flow** with `2xx` — no per-search in-SPA clicking
  required.
- ✅ **Schema confirmed** (miles + taxes match the live UI to the dollar/point).
- ✅ **Session lifetime is operationally usable** — ~30 min per login supports
  "log in once, search a batch." This rules out the No-Go "5–10 min idle-logout"
  regime.

**Carry these constraints/risks into Phase 1:**

- **~30-minute re-auth cadence.** Design for a full re-login (Aeroplan# + password +
  **2FA**, possibly **Arkose**) roughly every half hour of sustained scraping. This
  makes **Phase 1's Arkose-on-automated-login question the critical path** — if
  scripted login can't clear Arkose unattended, sustained scraping is blocked
  regardless of the (good) transport.
- **Email 2FA reuse.** Re-auth should lean on email 2FA via the existing
  `scripts/mfa_responder.py` (Gmail IMAP) rather than SMS.
- **Instrument hardening** (limitations #2, #3) before Phase 2 per-search capture.
- **Account safety** at the implied volume (a search batch every ~30 min) — keep low,
  consider the dedicated account once Phase 1 clears Arkose.

**Next:** draft a Phase 1 plan focused on (a) deterministic Gigya login + email-2FA
automation, and (b) an empirical Arkose go/no-go for a scripted Playwright browser —
the one unknown that gates everything downstream.

---

## Addendum — Calendar-strip recon (cheapest-per-day path), 2026-06-02

Follow-up investigation into using the **`air-calendars` price strip** as the data
source for a "cheapest economy award per day" scraper (instead of the full flight
list). Tool: `scripts/experiments/aeroplan_calendar_recon.py` (air-calendars only;
supports `--flexibility` URL injection and `--windows/--step-days` tiling).

### How `air-calendars` actually paginates

- **5 days per call, fixed.** The strip is driven by `itineraries[0].flexibility` in
  the POST body, which the SPA **hardcodes to `2`** (= ±2 days = a 5-day window
  centered on `departureDateTime`). Confirmed live: request `2026-08-15` → returns
  `2026-08-13 … 2026-08-17`; request `2026-06-08` → returns `2026-06-06 … 2026-06-10`.
- **The strip arrows fire real API calls.** Clicking `‹`/`›` in the booted SPA fires a
  fresh `air-calendars` POST (new `departureDateTime`, still `flexibility: 2`) — it is
  **not** client-side paging of a larger payload. So a click = one 5-day API call,
  just **without** the full SPA cold-boot.
- **The strip is UI-bounded to ~1 week of clicking** from the searched date (observed:
  search Jun 1 → could only reach ~May 30 … Jun 8, then the arrow dead-ends; past days
  show blank). To go further you must issue a new search. This is a UI limit, not an
  API limit.
- Clicking **keeps the URL fixed** (`departureDate0` never changes); the date lives in
  the POST body. Observed `marketCode=TNB` (transborder) for YYZ-LAX — the SPA sets the
  correct value; our `DOM` default was auto-corrected.

### Test (A) — can a URL param widen `flexibility`? → **NO**

Injected `&flexibility=10&flex=10&calendarFlexibility=10` into the availability URL.
The SPA **ignored all three** and still sent `flexibility: 2`, returning 5 days. URL
injection cannot widen the window. ❌

### `air-calendars` response schema (richer detail)

`{ data[], meta, dictionaries }`. Each `data[]` entry (one per day):
- `departureDate`
- cheapest economy: `prices.unitPrices[0].milesConversion.convertedMiles.base` (miles)
  + `...convertedMiles.totalTaxes` (cents CAD)
- `fareInfos[]`: `fareClass`, `ticketDesignator`, `corporateCode`, `fareType`
- `fareFamilyCode` (e.g. `STANDARD` → `dictionaries.fareFamilyWithServices` maps to
  `commercialFareFamily: RWDECO`, `cabin: eco`)
- `bounds[]`: the cheapest routing for that day, incl. `originLocationCode` /
  `destinationLocationCode` (note some days route via a different airport, e.g.
  YYZ→**ONT** instead of LAX, when cheaper)
- `dictionaries` resolves airport/city/country/currency codes; `meta.office.officeId`.

Request body carries `frequentFlyer.cardNumber` = the **member number** (redact);
`commercialFareFamilies: [RWDECO, RWDPRECC, RWDBUS, RWDFIRST]`.

### Design consequence

The cheapest-per-day scraper is fundamentally **5 days per `air-calendars` call**. The
only ways to fire those calls, ranked:

| Approach | Days/call | Cost | Status |
|---|---|---|---|
| URL `flexibility` param | — | — | ❌ dead (test A) |
| Navigate per window (`goto` each) | 5 | full cold-boot per call (~17s + Akamai/Arkose re-run) | ✅ reliable; heavy for wide ranges |
| Stay-booted + click strip | 5 | quiet, but bounded ~1 wk/search → must re-search anyway | ⚠️ marginal; brittle selectors |
| **In-page replay, big `flexibility`** | **~61 (flex 30)** | reuse booted page's signed client | ❓ **untested — gated by SigV4 on `air-calendars`** |

**The remaining high-value unknown** is in-page replay: can we fire `air-calendars`
ourselves from the booted page with a fat `flexibility` (e.g. 30 → ~2 months in one
call)? `air-calendars` is the SigV4-signed endpoint, so a modified body likely breaks
the signature (→ 403) unless we route through the app's own signing client. If it
works, the scraper becomes "one boot → a few huge calls"; if not, fall back to
navigate-per-5-days, scoped to the date ranges that actually matter.

### Test (B) — in-page replay of `air-calendars` (fat `flexibility`)? → **NO (dead)**

Tool: `scripts/experiments/aeroplan_sigv4_replay_probe.py` — boots the page, captures a
real `air-calendars` request (live SigV4 headers + body), then re-fires it from inside
the booted page with various header sets / `credentials` modes and a modified body
(`flexibility: 30`, date +60d). Live run 2026-06-02 (baseline = 200 / 5 days):

| Experiment | creds | Result |
|---|---|---|
| `control_exact` (full auth hdrs, **unmodified** body) | omit | **403** (server reached) |
| `full_modbody` (full auth hdrs, flex=30 body) | omit | **403** (same error body) |
| `drop_sigv4` (no SigV4 hdrs, flex=30) | omit | **403** (smaller/auth error) |
| all of the above **with cookies** | include | **`TypeError: Failed to fetch`** (CORS-blocked) |

**Verdict: in-page replay is not viable — both credential modes dead-end.**
- `credentials:'omit'` reaches the gateway but returns **403 even for a byte-identical
  replay** of the request the SPA just ran successfully → a re-issued `fetch` cannot
  reproduce a valid SigV4 signature (browser rewrites the canonical request; the STS
  signing key is internal to the page and unreachable). The modified-body variant gives
  the *same* 403, so widening `flexibility` is moot — there is no valid signature.
- `credentials:'include'` (needed to send the session cookie) is refused at the **CORS**
  layer (`Failed to fetch`).
- So there is no path to a 200 from a self-issued call. The endpoint is hardened against
  exactly this. **Option B is closed; the only viable path is navigate-per-5-days.**
- (Instrument gap: the OPTIONS-preflight capture recorded 0 records, so the exact CORS
  rejection headers weren't logged — doesn't change the conclusion.)

**Net design conclusion for the cheapest-per-day scraper:** 5 days per `air-calendars`
call, fired via full navigation (cold-boot each). "As wide/fast as possible" is bounded
by navigations-per-30-min-session + freeze risk; genuine breadth needs multiple
accounts (bigger build, higher account/legal risk). Build the **navigate-per-5-day-window
MVP**; do not invest further in self-issued replay.

### Instrument notes (calendar recon)

- **Fresh-login+consent bleed:** the *first* navigate right after a brand-new login
  goes through the OIDC consent chain (`clogin/pages/consent → afterConsent →
  idpresponse`); that redirect tail interrupts the first `page.goto` (`Navigation … is
  interrupted by another navigation to …afterConsent`), so the first window captures
  nothing. A **reused/established session captures cleanly.** Fix: after a fresh login,
  do a throwaway warm-up navigate (or wait for the app domain) before the first real
  capture.
- Eager `expect_response` (read the body the instant it arrives, before the next
  navigation) fixed the earlier `Network.getResponseBody: No resource…` body-eviction
  error.
- Verified capture (reused session): `air-calendars.json` = real 5-day payload,
  `air-calendars_request.json` = the exact signed request body (member number
  redacted) — kept for any future SigV4-replay assessment.

---

## Phase 1 (login automation) — moved

The Phase 1 login / Arkose findings now live in their own doc:
**[`phase-1-login-automation.md`](./phase-1-login-automation.md)**.

TL;DR: ✅ a scripted Playwright login **clears Arkose** and reaches 2FA (SMS default;
email available for unattended re-auth via `mfa_responder`). Arkose — the single biggest
Phase-1 risk — is **resolved green**; production login automation is the remaining build.
```
