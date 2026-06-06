# Aeroplan Phase 3 — Unattended Re-Auth + Scheduled Scrape: Architecture + Live Gates

**Purpose.** Phase 2 shipped the keyboard-driven, human-in-the-loop scraper (one warm
login, one route, a handful of capped windows). Phase 3 closes the gap to **unattended
operation**: a re-auth-and-resume loop that survives the ~30–40 min session TTL across a
wide span, an **email-2FA** delivery path so no human relays a code, and a
**program-aware scheduled wrapper** that wakes the PC and runs a HEADED single-route
Aeroplan scrape per route. The code is **shipped and offline-tested**; the **live runs
below are the user's gate** — they touch the real authenticated account, and Claude
cannot drive them.

This doc is two things: (1) the findings/architecture record (house style: TL;DR banner,
tables, honest caveats), and (2) **the account-safe live runbook the USER executes** to
gate the three remaining GO/NO-GO behaviors. Agents built and offline-tested the code;
**every live gate is a human eyeball — no code assertion decides correctness.**

---

## TL;DR — Status: 🟡 BUILT + offline-tested; **Gate 1 PASSED live (2026-06-05); Gate 3 OPEN; Gate 2 DEPRIORITIZED (2026-06-05).**

**Phase 3 delivers** the bounded re-auth-and-resume loop, the email-2FA contract
(login asks for the code by email; the Gmail-IMAP responder fetches the aeroplan.com
code), and a program-aware scheduled path that emits a HEADED, single-route Aeroplan
`search` command per route on a wake-to-run task.

| Question | Result |
|---|---|
| Survive the ~30–40 min TTL across a wide span? | ✅ **SHIPPED — bounded re-auth-and-resume loop.** `core/aeroplan_runner.py::run_aeroplan_route_with_reauth` drives `scrape_route_aeroplan`; on `expired=True` with windows remaining it re-authenticates and resumes from the next unscraped window. Capped by `max_reauths` (default 4) + `deadline_seconds`. Wired into `cli.py::_scrape_route_aeroplan_live`. Tested by `tests/test_aeroplan_reauth_loop.py` (**7/7**). |
| Unattended 2FA (no human relay)? | ✅ **LIVE-VERIFIED — Gate 1 PASSED (2026-06-05).** `search --program aeroplan YYZ LAX --mfa-method email` logged in with NO human relaying a code: login clicked the Email "Send Code", `scripts/mfa_responder.py` detected the request (`sender filter 'aeroplan.com'`), fetched the code from Gmail via contextual match, wrote it back; login confirmed and stored 10 rows. Contract pinned by `tests/test_aeroplan_email_2fa_contract.py`; email-selection + sender corrected against the live Gigya DOM + real code email (2026-06-04). |
| Cold-profile / fresh-IP Arkose? | ⚪ **NOT NEEDED (2026-06-05) for the single-laptop deployment** — the profile stays warm by construction; cold only arises on a machine/profile/IP reset, recoverable with a one-time manual login. Downgraded from a gate to an edge case (see *Gate 2 → Reassessment*). |
| Program-aware scheduling? | ✅ **SHIPPED.** `scripts/scheduled_scrape.py` emits a HEADED, single-route `cli.py search --program aeroplan <O> <D> --mfa-file --mfa-method email` per route for groups tagged `program="aeroplan"` (no `--headless/--ephemeral/--file`). `searchaero schedule add --program aeroplan` registers a wake-to-run task, persists `program` on the route group, and uses an Aeroplan-aware (larger) minimum interval. **Gate 3** is the end-to-end wake-triggered cycle. |
| Live-validated end-to-end? | 🟡 **NO — that is Gate 3.** Everything below the loop is offline-tested; the unattended cycle is the user's final gate. |

**Locked decisions (this phase):**

- **Re-auth is the runner's job, not the scraper's.** `scrape_route_aeroplan` still
  STOPS at the first expired window and surfaces `expired=True`; `aeroplan_runner`
  owns the loop, the bounds, and the resume math. Pure orchestration — no browser, no
  DB, no Playwright import.
- **Email-2FA, not SMS, is the unattended channel.** SMS needs a human to read the
  text. Email lets the Gmail-IMAP responder close the loop. SMS remains available for
  attended runs.
- **Aeroplan scheduling stays HEADED + single-route + per-route command.** The wrapper
  never collapses Aeroplan routes into one headless `--file` batch (that is the United
  shape). One headed Chrome, one route, one login, capped windows — per route.

---

## Architecture — the loop, the contract, the wrapper

Three shipped pieces sit on top of the Phase-2 scraper. The scraper is unchanged; Phase 3
wraps it.

```
   searchaero schedule add --program aeroplan ──► route group tagged program="aeroplan"
                                                  (persisted on the group, Aeroplan-aware
                                                   minimum interval, wake-to-run task)
                              │  (Windows Task Scheduler wakes PC, runs the .bat)
                              ▼
   scripts/scheduled_scrape.py
     ├─ starts scripts/mfa_responder.py (Gmail IMAP daemon)
     └─ for each aeroplan route ► HEADED single-route command:
           cli.py search --program aeroplan <O> <D> --mfa-file --mfa-method email
                              │
                              ▼
   cli.py::_scrape_route_aeroplan_live
     ├─ AeroplanSession().start()            (HEADED — never headless)
     ├─ ensure_logged_in(mfa_method="email") ──► mfa_request{email, aeroplan.com}
     │                                            └─ mfa_responder writes the code
     ├─ _aeroplan_warmup(session)            (absorb post-login OIDC consent redirect)
     └─ core/aeroplan_runner.run_aeroplan_route_with_reauth(...)
           └─ loops scrape_route_aeroplan; on expired=True + windows left:
                 ensure_logged_in() + warmup ► resume from next unscraped window
                 (capped by max_reauths=4 and deadline_seconds)
                              │
                              ▼
        program-tagged rows in the shared `availability` table  (program='aeroplan')
```

### The re-auth-and-resume loop (SHIPPED)

`core/aeroplan_runner.py::run_aeroplan_route_with_reauth` is **pure orchestration** — it
owns no browser logic (only `session.ensure_logged_in()` + a caller-supplied `warmup`
hook), no DB logic (forwards `conn` untouched), imports with zero side effects, and never
imports Playwright. Behavior:

- Runs `scrape_route_aeroplan` over the span. A wide span (5-day windows) can outlive one
  ~30–40 min session.
- On `expired=True` **with windows remaining**, it recovers the exact window the scraper
  bailed on (parsed from the scraper's `"session expired at window {date}: …"` message),
  re-authenticates (`ensure_logged_in()` + `warmup`), and **resumes from that first
  unscraped window** — not from the start.
- **Bounds (whichever trips first stops cleanly):** `max_reauths` (default **4**) and
  `deadline_seconds` (overall wall-clock budget; `None` = unbounded).
- Returns the per-batch scraper dict **aggregated across batches**, plus `reauths`,
  `batches`, and `span_complete` (True iff the whole requested span was covered). A cap
  that stops the span early yields `span_complete=False` — `cli.py` logs a WARNING.
- The wall-clock deadline uses an **injectable monotonic clock** so the unit tests
  control elapsed time without real sleeping.

Wired into `cli.py::_scrape_route_aeroplan_live` (the live `search --program aeroplan`
path), which constructs the headed `AeroplanSession`, logs in, runs `_aeroplan_warmup`,
then hands the route to the loop. Tested by `tests/test_aeroplan_reauth_loop.py` — **7/7
passing**.

### The email-2FA contract (LOCKED, not live)

The login → responder protocol is pinned by `tests/test_aeroplan_email_2fa_contract.py`:

- The login driver emits an `mfa_request` JSON with `mfa_method == "email"` and
  `sender_filter == "aeroplan.com"` (the module constant `AEROPLAN_MFA_SENDER ==
  "aeroplan.com"`, **not** `"@united.com"` and **not** `"aircanada.com"`). The real
  Aeroplan code email is from `Aeroplan <info@communications.aeroplan.com>` (confirmed
  against a live email 2026-06-04), so the filter must be the `aeroplan.com` domain — the
  earlier `aircanada.com` guess never matched that From header.
- `scripts/mfa_responder.py::get_email_code` ACCEPTS a `From: …@communications.aeroplan.com`
  message and REJECTS a `From: …@united.com` message when `sender_filter="aeroplan.com"` —
  with both present it extracts only the aeroplan.com code (newest-first scan). The contract
  test includes a fixture built from the real email body ("Verification code: 418595") that
  asserts the contextual match returns the code and never a footer number or `000000`.

The responder reads `SEARCHAERO_GMAIL_SENDER` (the Gmail address it logs into) and
`SEARCHAERO_GMAIL_APP_PASSWORD` (a Google **app password**, not the account password)
from the environment (or a `.env` in the project root / `~/.searchaero/.env`). The
2FA *completion* mechanism (poll file → fill code field → submit → logged-in) is identical
for SMS and email and was already proven live in Phase 1; only the email *delivery
selection* + the *Gmail fetch* are unverified — that is **Gate 1**.

### The program-aware scheduled wrapper (SHIPPED)

`scripts/scheduled_scrape.py` dispatches per route group by its `program` tag:

- `program == "united"` (or missing) → **exactly one** headless `--file` batch command
  (the unchanged United shape).
- `program == "aeroplan"` → **one HEADED single-route command per route** in the group's
  routes file, built by `_build_aeroplan_search_cmd`:
  `cli.py search --program aeroplan <O> <D> --mfa-file --mfa-method email` —
  **no `--headless`, no `--ephemeral`, no `--file`.**

`searchaero schedule add --program aeroplan --routes routes/<file>.txt` persists
`program` on the route group dict and computes the **minimum interval with Aeroplan-aware
constants** (`core/scheduler.py`: `AEROPLAN_LOGIN_OVERHEAD_MINUTES = 7`,
`AEROPLAN_PER_ROUTE_MINUTES = 18` — deliberately heavier than United because Aeroplan
runs HEADED, single-route, with login + TTL overhead per route). The minimum interval is
therefore *larger* for Aeroplan; a too-short `--interval` is rejected with the computed
floor. The task is registered with **wake-to-run** enabled (AC wake timers via `powercfg`).

### New / changed files (Phase 3)

| File | Role | New/changed |
|---|---|---|
| `core/aeroplan_runner.py` | `run_aeroplan_route_with_reauth` — bounded re-auth-and-resume loop over one span. Pure orchestration; injectable clock; `max_reauths`/`deadline_seconds` bounds; returns aggregated totals + `reauths`/`batches`/`span_complete`. | **NEW** |
| `cli.py` | `_scrape_route_aeroplan_live` now drives the loop (was a single scrape pass); `_aeroplan_warmup` reused as the post-reauth warm-up hook. `schedule add --program {united,aeroplan}`. | changed |
| `scripts/scheduled_scrape.py` | `_build_aeroplan_search_cmd` (HEADED single-route Aeroplan command) + `_build_group_search_cmds` program-aware dispatch (aeroplan → one command per route). | changed |
| `scripts/mfa_responder.py` | `get_email_code(sender_filter=…)` filters Gmail on the request's sender; honors `sender_filter="aeroplan.com"`. | changed (Phase 1/2 base) |
| `core/scheduler.py` | Aeroplan-aware `estimate_scrape_minutes` / `compute_min_interval(program=…)`; heavier per-route + login constants. | changed |
| `tests/test_aeroplan_reauth_loop.py` | Loop coverage — resume math, caps, deadline, span_complete (**7/7**). | **NEW** |
| `tests/test_aeroplan_email_2fa_contract.py` | Email-2FA contract — `mfa_method:"email"` + `sender_filter:"aeroplan.com"`; responder accept/reject + real-email (`418595`) extraction fixture. | **NEW** |

---

## Account-safe discipline (applies to EVERY gate below)

**This block is non-negotiable. Read it before running anything.**

- **HEADED ONLY — by construction.** `AeroplanSession` refuses headless; the scheduled
  wrapper never passes `--headless/--ephemeral/--file` for Aeroplan. A visible Chrome
  window launches on every run. There is no headless Aeroplan path.
- **Human-in-the-loop for the live gates.** A human starts each gate and records GO/NO-GO.
  **Agents NEVER drive the live account.** Claude built and offline-tested the code; it
  does not log in.
- **Single account, single route per run, capped windows.** One MP# account, one route
  per invocation, a handful of 5-day windows (`--from/--to` kept narrow). Never fan out.
- **Never park or transfer miles.** Searches only. A freeze is recoverable; a transfer is
  not. No booking, no transfer, no parking — ever.
- **~30–40 min session TTL is the unit of work.** The shipped loop re-auths across that
  boundary, but keep spans sane and the re-auth cap small. Watch for `span_complete=False`
  (a cap stopped the span early — re-run a narrower span, do not crank caps blindly).
- **PC must SLEEP, not shut down.** Wake-to-run uses Task Scheduler wake timers
  (`powercfg`). A shut-down PC never wakes. Keep it plugged in (AC) — wake-from-sleep is
  unreliable on battery.
- **The machine must be LOGGED ON with an interactive desktop on wake.** Headed Chrome can
  only launch onto an interactive session. **If the machine cannot provide an interactive
  desktop when it wakes (locked-with-no-session, headless server, "run whether user is
  logged on or not" with no console), Phase-3 scheduling is NOT viable as designed** — the
  headed browser has nowhere to draw and the cycle fails. State this plainly to anyone
  enabling the schedule: a logged-on, unlocked (or auto-unlocking) interactive session at
  wake time is a hard prerequisite.

The user **records GO/NO-GO + the observed signals** for each gate. These are **human
eyeball gates — no code assertion decides correctness.**

---

## The three live gates

> All raw commands below use the venv Python:
> `C:/Users/jiami/local_workspace/seataero-src/.venv/Scripts/python.exe`.
> Run them from the project root `C:/Users/jiami/local_workspace/seataero-src`.
> `<O> <D>` = a single near-future route (e.g. `YYZ LAX`); keep `--from/--to` to a few
> 5-day windows.

### Gate 1 (TODO-1) — Email-2FA, live  ✅ PASSED (2026-06-05)

> **RESULT — GO (2026-06-05).** `search --program aeroplan YYZ LAX --mfa-method email`
> (from 2026-07-01 to 2026-07-05) logged in via email 2FA with **no human relaying a
> code** and stored 10 rows. Responder log: `MFA request detected (sender filter
> 'aeroplan.com')` → `MFA code found via contextual match` → `Wrote MFA code`. Search log:
> `Aeroplan login confirmed` → `10 found, 10 stored, 0 rejected`. All three pre-run fixes
> validated live: sender = `aeroplan.com` (was the wrong `aircanada.com`), extraction via
> contextual match (not the `000000` fallback), and the rewritten `select_email_2fa` reached
> the code field. Note: the first attempt failed at ~19s with `creds_rejected` — a transient,
> not bad creds (the classifier can't tell the two apart); a retry passed. The procedure
> below is retained for re-runs.

**Purpose.** Prove the unattended 2FA channel end-to-end: login clicks the **Email**
method's "Send Code" on the Gigya screen, the Gmail-IMAP responder fetches the
**aeroplan.com** code (from `info@communications.aeroplan.com`), and the session reaches
`logged_in_success` — with **no human relaying a code**. This is the last unverified piece
for fully unattended re-auth. (The email-selection step and the sender filter were corrected
against the live DOM + a real code email on 2026-06-04; this gate confirms them end-to-end.)

**Setup — set the responder's env (confirm the exact names; they are
`SEARCHAERO_GMAIL_SENDER` / `SEARCHAERO_GMAIL_APP_PASSWORD`, read by
`scripts/mfa_responder.py`):**

PowerShell, current session:

```powershell
$env:SEARCHAERO_GMAIL_SENDER = "<your-gmail-address>"
$env:SEARCHAERO_GMAIL_APP_PASSWORD = "<google-app-password>"   # app password, NOT the account password
```

(Equivalently, add both lines as `KEY=VALUE` to `~/.searchaero/.env` — the responder loads
the project-root `.env` or `~/.searchaero/.env` automatically.)

**Run — two terminals:**

Terminal 1 (responder daemon):

```powershell
C:/Users/jiami/local_workspace/seataero-src/.venv/Scripts/python.exe scripts/mfa_responder.py
```

Terminal 2 (headed login + scrape, email 2FA):

```powershell
C:/Users/jiami/local_workspace/seataero-src/.venv/Scripts/python.exe cli.py search --program aeroplan <O> <D> --mfa-file --mfa-method email --from <YYYY-MM-DD> --to <YYYY-MM-DD>
```

**GO** = login reaches `logged_in_success` **via email 2FA**: the responder logs that it
detected the `mfa_request` (`sender filter 'aeroplan.com'`), fetched the code from Gmail,
and wrote `mfa_response`; the headed session fills the code and confirms login; the scrape
then stores `program='aeroplan'` rows.

**NO-GO** = the **email-selection step fails** (`select_email_2fa` can't find/click the
Email method's "Send Code" in `.tfa-email-method`, or no `emailCode` field appears after —
the selectors are now DOM-confirmed against the live Gigya screen 2026-06-04, so a failure
here likely means the screen markup changed), **OR the Gmail fetch fails** (IMAP login
error, no aeroplan.com message found within the responder's retry window, or the
wrong/expired code). Record exactly which half failed, the responder log tail, and the
login screen state.

### Gate 2 (TODO-2) — Cold-profile / fresh-IP Arkose

**Purpose.** Every passing Arkose result so far is on a **warmed** profile (many prior
manual logins, same day, same IP). A fresh profile or new IP may score worse and trigger
an **interactive** FunCaptcha — which blocks unattended login from a clean machine
(`AeroplanSession` surfaces this as `status="arkose"` and **bails; it never auto-solves**).
This gate re-verifies Arkose stays passive on a clean profile.

**Run — login against a fresh, empty profile directory** (the flag is `--profile-dir`, on
the login-drive path; `AeroplanSession.__init__` accepts `profile_dir`):

```powershell
C:/Users/jiami/local_workspace/seataero-src/.venv/Scripts/python.exe scripts/experiments/aeroplan_login_drive.py --mfa-method sms --profile-dir <new-empty-temp-dir>
```

(SMS is fine here — this gate is about Arkose scoring on a cold profile, not the 2FA
channel. To stress a fresh **IP** as well, run from a different network. Use a brand-new
empty directory for `--profile-dir` so nothing is warmed.)

**GO** = Arkose stays **passive** through every step and the run reaches
`logged_in_success`. Record the result in `phase-1-login-automation.md`.

**NO-GO** = an **interactive FunCaptcha appears** (the session reports `status="arkose"`
and bails). Unattended login from a clean machine is then blocked — record the step it
appeared at and treat cold-machine scheduling as not-yet-safe.

#### Reassessment (2026-06-05) — Gate 2 is NOT needed for the single-laptop deployment

Gate 2 tests a condition that **does not occur in normal operation.** The scheduled scraper
always runs from the **same laptop against the same persistent `--profile-dir`**, on a
**stable residential IP**, every day — so the profile stays *warm by construction*
(accumulating cookies/history that keep the Arkose score high → transparent, no-puzzle
challenge). **Gate 1 already passed live under exactly these conditions.**

A **cold** profile only arises on a **trust reset** — a new/reinstalled machine, a wiped or
corrupted profile dir, or a materially changed IP — none of which are steady-state events.
And even then the failure is **recoverable without code**: do **one manual interactive
login** (solve the FunCaptcha yourself once) and the profile is warm permanently thereafter.

So the realistic worst case is "a one-time manual warm-up after a machine change," not
"unattended Aeroplan is broken." For a single-user, single-laptop deployment Gate 2 is
**downgraded from a required gate to a known edge case** — run it only if you plan to deploy
on a fresh machine / clean profile and want to confirm cold-start behavior ahead of time.
**Gate 3 no longer waits on it.**

### Gate 3 — End-to-end unattended cycle

**Purpose.** Prove the whole Phase-3 path runs **on a schedule, unattended**: the PC
wakes, headed Chrome launches on the interactive desktop, logs in via email 2FA, scrapes,
**re-auths if the span crosses a TTL boundary** (the shipped loop), and stores
program-tagged rows. Run this **after Gate 1 is GO.** (Gate 2 is no longer a prerequisite —
see *Reassessment (2026-06-05)* above; it's a fresh-machine edge case, not a steady-state gate.)

**Setup — register a single-route Aeroplan schedule** (one route file, one route):

```powershell
C:/Users/jiami/local_workspace/seataero-src/.venv/Scripts/python.exe cli.py schedule add --program aeroplan --routes routes/<one-aeroplan-route>.txt --interval <minutes>
```

(`schedule add` rejects a `--interval` below the **Aeroplan-aware** minimum — if rejected,
re-run with the printed floor. The route file must contain exactly **one** route. Wake
timers are enabled automatically; keep the PC plugged in.)

Confirm the env vars from Gate 1 are present where the scheduled task will read them (the
schedule's `--env-file`, or `~/.searchaero/.env`), so the responder can authenticate to
Gmail when the task fires.

Optionally inspect the exact command the wrapper will run, without executing:

```powershell
C:/Users/jiami/local_workspace/seataero-src/.venv/Scripts/python.exe scripts/scheduled_scrape.py --schedule-name <master-name> --dry-run
```

(Expect a HEADED single-route line:
`... cli.py search --program aeroplan <O> <D> --mfa-file --mfa-method email ...` — and
**no** `--headless/--ephemeral/--file`.)

**Then let the machine sleep and observe one wake-triggered cycle.** Check status and logs:

```powershell
C:/Users/jiami/local_workspace/seataero-src/.venv/Scripts/python.exe cli.py schedule status
C:/Users/jiami/local_workspace/seataero-src/.venv/Scripts/python.exe cli.py query --program aeroplan <O> <D>
```

**GO** = on the scheduled wake: the PC wakes from sleep; **headed Chrome launches on the
interactive desktop**; login completes via **email 2FA** (responder fetched the
aeroplan.com code); the scrape runs; if the span crossed a ~30–40 min TTL boundary the
**loop re-authenticated and resumed** (the run log shows `reauths >= 1` / multiple
`batches`, and `span_complete=True`); and `query --program aeroplan <O> <D>` returns rows
tagged `program='aeroplan'` for the wake-cycle dates. Temp schedule removed afterward
(`schedule remove <group>`).

**NO-GO** = any of: the PC didn't wake (shut down instead of slept, or no AC wake timer);
**no interactive desktop on wake** so headed Chrome couldn't launch (the hard prerequisite
in the discipline block — Phase-3 scheduling is then not viable as designed on this
machine); email 2FA failed (Gate-1 failure under the scheduler); the loop hit a cap
(`span_complete=False`) and left the span uncovered; or no `program='aeroplan'` rows
landed. Record which, plus the `schedule status` output and the
`~/.searchaero/logs/scheduled_scrape.jsonl` tail.

---

## Deferred / parked follow-ups (NOT Phase-3 blockers)

- **Seats / quota.** Still calendar-only (cheapest economy miles + taxes/day). The
  per-flight `quota` lives in the `air-bounds`/`polldapi` flight list — explicitly
  deferred (carried from Phase 2).
- **Currency column.** Aeroplan taxes are stored as CAD cents in the same column United
  uses for USD cents — a documented cross-program mismatch (no conversion yet). See
  Phase-2 *Assumption 2*.
- **Multi-route Aeroplan scheduling at scale.** The wrapper emits one headed command per
  Aeroplan route; large route lists multiply login overhead and wall-clock. Keep Aeroplan
  schedules to a small route count until Gate 3 is proven and timing is measured live.
- **Human-like cursor for the login (anti-bot hardening).** The Aeroplan login fills the
  member number + password with plain Playwright `.fill()` (instant value-set, no mouse
  movement, no per-character typing) — `core/aeroplan_session.py:355,380`. United's stack
  drives a human-like cursor via `core/ghost_click.py` (used by `core/cookie_farm.py`); the
  Aeroplan login was never wired to it. So far the robotic `.fill()` has logged in fine
  (2026-06-03 SMS, 2026-06-05 email) on a warmed profile, but Air Canada's Gigya/Arkose
  may flag the non-human input pattern — especially on a cold profile / fresh IP (Gate 2).
  **TODO:** port `ghost_click` (or `page.type` with randomized delays + small mouse moves)
  into the Aeroplan login so it interacts like United does, before relying on unattended
  cold-machine logins.

---

## Related

- [`phase-2-scraper.md`](./phase-2-scraper.md) — the scraper → store → query → alert path,
  the navigate-per-5-day transport, the two normalization assumptions, and the Phase-2
  live runbook (Slice A / Slice B, PASS 2026-06-03) this builds on.
- [`phase-1-login-automation.md`](./phase-1-login-automation.md) — scripted Gigya login +
  2FA, Arkose passive (warmed profile), `AeroplanSession` (`--profile-dir`).
- [`phase-0-transport-spike.md`](./phase-0-transport-spike.md) — transport, ~30–40 min
  session lifetime (the TTL the re-auth loop exists to survive), 5-day calendar strip.
- [`aeroplan-todo.md`](./aeroplan-todo.md) — TODO-1 (email-2FA live) and TODO-2
  (cold-profile Arkose) now point here; the re-auth loop is marked SHIPPED.
</content>
</invoke>
