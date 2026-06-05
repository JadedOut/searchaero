# Aeroplan — To-Do / Open Gates

Running list of deferred Aeroplan work — now also the index of the **three open Phase-3
live gates** (the unattended machinery is built; the live runs are the user's).

Last updated: 2026-06-04.

> **2026-06-03 update (Phase 2 built).** The Phase-2 scraper → store → query → alert path
> is **BUILT and offline-tested**; the **live gate is the user's** (Slice A / Slice B in
> [`phase-2-scraper.md`](./phase-2-scraper.md)). The program-tagging decision (item 3 below)
> is **RESOLVED — Option A (shared table + `program` column)**. The seats/quota deferral and
> the email-responder / cold-Arkose gates (TODO-1, TODO-2) remain the **parked follow-ups**,
> not blockers for the keyboard-driven MVP. Per-item status annotated inline below.

> **2026-06-04 update (Phase 3 built).** The **unattended re-auth-and-resume loop is now
> SHIPPED** (`core/aeroplan_runner.py::run_aeroplan_route_with_reauth`, tested 7/7), the
> **email-2FA contract is locked** (`tests/test_aeroplan_email_2fa_contract.py`), and a
> **program-aware scheduled path** is shipped (HEADED single-route Aeroplan command per
> route; `schedule add --program aeroplan` with Aeroplan-aware minimum interval). The
> remaining work is **three user-run live gates** — Gate 1 (email-2FA live = TODO-1),
> Gate 2 (cold-profile Arkose = TODO-2), Gate 3 (end-to-end unattended cycle) — all
> documented in the new runbook
> [`phase-3-unattended.md`](./phase-3-unattended.md). See the new **Phase 3** section below.

---

## Phase 1 — open gates (deferred, NOT yet done)

Phase 1 (automated login) is **functionally validated** as of 2026-06-03: a live scripted
login against the real account reached `logged_in_success` in ~65 s, Arkose passive at
every step, 2FA completed via the file-handoff protocol (SMS path). See
[`phase-1-login-automation.md`](./phase-1-login-automation.md) → "Live validation
2026-06-03". Two pieces from the Phase-1 build remain **unverified live**:

### TODO-1 — Verify email-2FA selection + Gmail IMAP responder (live)  ✅ DONE (2026-06-05)
- **Status:** ✅ **DONE (live) — Gate 1 PASSED 2026-06-05.** `search --program aeroplan YYZ LAX
  --mfa-method email` logged in via email 2FA with no human relaying a code (responder
  matched `sender filter 'aeroplan.com'`, found the code via contextual match, wrote it back;
  login confirmed, 10 rows stored). See [`phase-3-unattended.md`](./phase-3-unattended.md) →
  *Gate 1 — PASSED*. Previously **contract-locked offline**
  (`tests/test_aeroplan_email_2fa_contract.py`: login emits
  `mfa_request{mfa_method:"email", sender_filter:"aeroplan.com"}`; the responder
  accepts the aeroplan.com code and rejects united.com). Sender + email-selection step
  corrected against the live Gigya DOM + a real code email (2026-06-04, from
  `info@communications.aeroplan.com`). Still never exercised end-to-end against the real
  account. **This is Gate 1** in the Phase-3 runbook —
  [`phase-3-unattended.md`](./phase-3-unattended.md) → *Gate 1 (TODO-1) — Email-2FA, live*
  has the exact two-terminal commands and GO/NO-GO criteria.
- **Why deferred:** the 2026-06-03 live run used **SMS** (code relayed by the user). The
  email path was blocked because `SEARCHAERO_GMAIL_SENDER` / `SEARCHAERO_GMAIL_APP_PASSWORD`
  are not set in `~/.searchaero/.env`.
- **What's unverified specifically:**
  1. `select_email_2fa()` — clicks the **Email** method's "Send Code" in `.tfa-email-method`
     on the Gigya screen, then waits for the `emailCode` field. Selectors are now
     DOM-confirmed (2026-06-04) but the click→send→fill→submit path has never run live.
  2. The **Gmail-IMAP responder** fetching the Aeroplan code end-to-end
     (`mfa_request` `sender_filter="aeroplan.com"` → responder reads Gmail → writes
     `mfa_response`).
- **What IS already proven:** the 2FA *completion* mechanism (poll file → fill code field →
  submit → logged-in) is identical for SMS and email and is proven live. Only the email
  *delivery selection* + the *Gmail fetch* remain.
- **To do it:** add `SEARCHAERO_GMAIL_SENDER` (the Gmail address) + `SEARCHAERO_GMAIL_APP_PASSWORD`
  (a Google app password) to `~/.searchaero/.env`, then run the unattended email path:
  - Terminal 1: `.venv\Scripts\python.exe scripts\mfa_responder.py`
  - Terminal 2: `.venv\Scripts\python.exe scripts\experiments\aeroplan_login_drive.py --mfa-method email`
  - Success = PHASE-1 VERDICT reports `logged_in_success` via email 2FA.
- **Priority:** needed for **fully unattended** re-auth (Phase 3 scheduled scraping); NOT a
  blocker for building/testing the Phase-2 scraper at the keyboard with a warm session.
  The re-auth loop that consumes this (Phase-3 item 3) is now **SHIPPED** — this live gate
  is the last thing standing between the loop and fully unattended operation.

### TODO-2 — Re-verify Arkose on a cold profile / fresh IP
- **Status:** NOT DONE (open gate). All passing Arkose results so far are on a **warmed**
  profile (many prior manual logins same day/IP). **This is Gate 2** in the Phase-3
  runbook — [`phase-3-unattended.md`](./phase-3-unattended.md) → *Gate 2 (TODO-2) —
  Cold-profile / fresh-IP Arkose* has the exact `--profile-dir` command and GO/NO-GO.
- **Risk if bad:** a fresh profile or new IP may score worse and get an **interactive**
  FunCaptcha → unattended login from a clean machine is blocked (the session manager
  surfaces this as `status="arkose"` and bails; it never auto-solves).
- **To do it:** run the login against a fresh/empty `--profile-dir <new temp dir>` (and/or a
  different network/IP):
  - `.venv\Scripts\python.exe scripts\experiments\aeroplan_login_drive.py --mfa-method sms --profile-dir <new temp dir>`
  - **GO** = Arkose stays passive, reaches `logged_in_success`. **NO-GO** = interactive
    Arkose appears. Record the result in `phase-1-login-automation.md`.
- **Priority:** a Phase-3 risk gate (unattended-from-clean-machine). Not a Phase-2 blocker.

---

## Phase 2 — Search + normalize + store + alert (✅ DONE — air-calendars MVP shipped + **live-verified 2026-06-03**, Slice A PASS)

> **Live-verified 2026-06-03.** `search --program aeroplan YYZ LAX` (3 windows, SMS login)
> stored 10 rows tagged `program='aeroplan'`; baseline 12,500 mi / CA$170.59 matches Phase 0.
> Two minor follow-ups (now ✅ FIXED 2026-06-03): (a) **warm-up navigate after fresh login**
> (`cli.py::_aeroplan_warmup`) — window 1 was eaten by the post-login OIDC consent redirect;
> (b) **UTF-8 stdout** at `cli.py::main()` — fixes the cp1252 `UnicodeEncodeError` on the
> `→` tip line / Rich tables under redirected Windows stdout. **Slice B end-to-end ✅ PASS**:
> an `alert check` matched 8 Aeroplan fares each tagged `program=Aeroplan` (matched-row output
> now shows a Program column). See [`phase-2-scraper.md`](./phase-2-scraper.md) → *Live verification result*.


The scraper itself. See [`phase-0-transport-spike.md`](./phase-0-transport-spike.md)
(addendum: calendar-strip recon) for the transport facts this builds on, and
[`phase-2-scraper.md`](./phase-2-scraper.md) for the build + the live runbook. Outline,
with build status annotated:

1. ✅ **BUILT — Navigate-per-5-days `air-calendars` scraper.** 5 days per call,
   `flexibility:2` fixed (URL-flex widening and in-page SigV4 replay are both proven
   **dead** — Phase 0 tests A and B). `core/aeroplan_scraper.py::scrape_route_aeroplan`
   fires each 5-day window via a full navigate on a logged-in `AeroplanSession`, intercepts
   the `air-calendars` JSON (proven eager-capture pattern — no SigV4 work), with a
   session-expiry guard (`expired=True` on login-redirect/401/403). Transport/parse split
   into `core/aeroplan_api.py`.
2. ✅ **BUILT (MVP, calendar-only) — Parse DAPI JSON → fare rows.** Cheapest economy miles
   `prices.unitPrices[].milesConversion.convertedMiles.base` + taxes `...totalTaxes`
   (cents CAD), per-day entries, via `parse_calendar_response`. **Seats `quota` is DEFERRED**
   — it lives in the `air-bounds`/`polldapi` flight list (separate capture, still outstanding
   from Phase 0); the MVP is calendar-only. (See *Deferred* in the Phase-2 doc.)
3. ✅ **RESOLVED — Option A (shared table + `program` column).** The `availability` table
   now keys on `UNIQUE(program, origin, destination, date, cabin, award_type)`; an
   idempotent `ensure_program_column()` migration backfills existing rows to `'united'`,
   and all read queries accept optional `program=`. (Option (b) separate Aeroplan DB was
   rejected — it would fork the read/alert pipeline.)
4. ✅ **BUILT — Wired into query + alerts/watches.** Rows land program-tagged
   (`db.upsert_availability(program='aeroplan')`); `searchaero query --program {united,aeroplan}`
   (omit = all) and the program-aware matcher read the same shared table;
   `core/presentation.format_programs_table` shows the real per-row program. The CLI exposes
   `searchaero search --program aeroplan` (single-route, headed, MFA email by default).

**Status:** the path is built and **offline-tested**; correctness + end-to-end are gated by
the **user's manual live run** (Slice A correctness eyeball, Slice B store→query→alert) in
[`phase-2-scraper.md`](./phase-2-scraper.md).

**Two documented assumptions flagged for a human eye** (detail in the Phase-2 doc):
(a) the calendar strip is economy-only → MVP stores `cabin='economy', award_type='Standard'`
(flag if raw `fareFamilyCode` should be preserved); (b) Aeroplan taxes stored **as-is in CAD
cents** in the same column United uses for **USD cents** — a documented cross-program currency
mismatch (no conversion, no `currency` column yet).

**Gating rule:** Phase 2's scraper was **built and is testable with the SMS/warm session**
today. **Fully unattended** Phase-2/3 operation still depends on the parked follow-ups —
TODO-1 (email responder) and TODO-2 (cold-profile Arkose) — being green.

---

## Phase 3 — Unattended re-auth + scheduled scrape (🟡 BUILT — Gate 1 PASSED 2026-06-05; Gates 2 + 3 open)

> **2026-06-04.** The unattended machinery is **built and offline-tested**; the runbook
> with the exact commands + GO/NO-GO for all three gates is
> [`phase-3-unattended.md`](./phase-3-unattended.md). The discipline block there is a hard
> read before any live run (HEADED-only, single account/route, **PC must SLEEP not shut
> down**, and the machine must be **logged on with an interactive desktop on wake** or
> Phase-3 scheduling is not viable as designed).

Phase-3 prerequisites and items, with status annotated:

1. ✅ **TODO-1 — email-2FA live: PASSED 2026-06-05 (Gate 1).** Logged in via email 2FA with
   no human relaying a code, 10 rows stored. Contract pinned by
   `tests/test_aeroplan_email_2fa_contract.py`; live result in
   [`phase-3-unattended.md`](./phase-3-unattended.md) → *Gate 1 — PASSED*. See TODO-1 above.
2. 🟡 **TODO-2 — cold-profile / fresh-IP Arkose.** **Live = Gate 2** in
   [`phase-3-unattended.md`](./phase-3-unattended.md). See TODO-2 above.
3. ✅ **SHIPPED — bounded re-auth-and-resume loop.**
   `core/aeroplan_runner.py::run_aeroplan_route_with_reauth` drives `scrape_route_aeroplan`
   in a bounded loop; on session expiry with windows remaining it re-authenticates
   (`session.ensure_logged_in()` + warm-up) and resumes from the next unscraped window,
   capped by `max_reauths` (default 4) and `deadline_seconds`. Wired into
   `cli.py::_scrape_route_aeroplan_live`. Pure orchestration (no browser/DB/Playwright;
   injectable clock). Tested by `tests/test_aeroplan_reauth_loop.py` (**7/7**).
   (Previously parked under Phase-2 *Deferred follow-ups* → "Unattended re-auth loop";
   now done.)
4. ✅ **SHIPPED — program-aware scheduled path.** `scripts/scheduled_scrape.py` emits a
   HEADED, single-route `cli.py search --program aeroplan <O> <D> --mfa-file --mfa-method
   email` command per route for groups tagged `program="aeroplan"` (no
   `--headless/--ephemeral/--file`); `searchaero schedule add --program aeroplan` registers
   a wake-to-run task, persists `program` on the route group, and applies an Aeroplan-aware
   (larger) minimum interval (`core/scheduler.py` heavier per-route + login constants).
5. 🟡 **Gate 3 — end-to-end unattended cycle.** Register a single-route Aeroplan schedule
   and observe one wake-triggered cycle (headed Chrome on the interactive desktop → email
   2FA → scrape → re-auth across TTL → program-tagged rows). Run only after Gates 1 + 2 are
   GO. Commands + GO/NO-GO in [`phase-3-unattended.md`](./phase-3-unattended.md) → *Gate 3*.

**Status:** the loop and the scheduled path are **built and offline-tested**; unattended
operation is gated by the **user's manual live runs** (Gate 1 email-2FA, Gate 2
cold-profile Arkose, Gate 3 end-to-end cycle) in
[`phase-3-unattended.md`](./phase-3-unattended.md).
