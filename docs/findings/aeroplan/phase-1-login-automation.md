# Aeroplan Phase 1 — Login Automation: Findings

**Purpose.** Phase 0 measured that an Aeroplan session lasts only **~30–40 min**, after
which the scraper must re-authenticate (member # + password + 2FA, behind Gigya). Phase 1
automates that login so the scraper can re-auth unattended on that cadence. The single
**make-or-break unknown** was whether a *scripted* Playwright browser clears the
**Arkose FunCaptcha** on the login form — if it can't, unattended scraping is blocked
regardless of how good the transport is.

**Status (2026-06-03): ✅ PHASE 1 DONE / validated live.** A full scripted login against
the real account reached `logged_in_success` in ~65 s (Arkose passive at every step; 2FA
completed via the file-handoff protocol, SMS path — see *Live validation* below). The
**email-2FA end-to-end automation is BUILT**; two non-blocking gates remain parked in
[`aeroplan-todo.md`](./aeroplan-todo.md) (live email/Gmail-responder verification, and
cold-profile Arkose) — both are Phase-3 (fully-unattended) prerequisites, not Phase-2
blockers. Three pieces landed this session:

- `scripts/experiments/aeroplan_login_drive.py` — now drives email 2FA end-to-end
  (`--mfa-method email`, default): `select_email_2fa()` walks the "try a different way" →
  Email → Continue ladder, then `complete_email_2fa()` writes the
  `~/.searchaero/mfa_request` JSON (`sender_filter="aircanada.com"`), polls
  `~/.searchaero/mfa_response`, fills + submits the code, and confirms a logged-in state.
- `core/aeroplan_session.py` — **NEW** reusable `AeroplanSession` manager mirroring
  `core/cookie_farm.py` (`start()` / `stop()` / `is_logged_in()` /
  `login(mfa_method="email")` / `ensure_logged_in(...)`, context-manager support). Headed
  only (raises `HeadlessNotSupported`); reuses the driver's proven flow + selectors as the
  single source of truth; Arkose is detect-and-bail (`status="arkose"`, never auto-solved).
- `scripts/mfa_responder.py` — per-request `sender_filter` so the same Gmail-IMAP
  responder serves both United (`@united.com`, default) and Aeroplan (`aircanada.com`).

The **one remaining gate is manual**: re-verifying Arkose stays passive on a cold profile /
fresh IP (see *Remaining work* item 3 and the *Manual verification runbook* below). Phase 0
findings (transport, session lifetime, calendar strip, SigV4 replay) live in
[`phase-0-transport-spike.md`](./phase-0-transport-spike.md).

### ✅ Live validation — 2026-06-03 (real account, SMS path)

A full live run against the real account (`--mfa-method sms`, warmed profile) completed
end-to-end in **~65 s** with classification **`logged_in_success`**. This is the first
live confirmation that the *scripted* login mechanism reaches a logged-in state, not just
the 2FA wall:

- **Arkose: none detected at any step** — confirmed on a live scripted run (not only the
  earlier diagnostic).
- **Login mechanics proven live:** member # via `input[type="text"]:visible`, password
  via `input[type="password"]:visible`, submit via **`Enter` on the password field** (the
  Gigya `input[type=submit]` "Sign in" is `disabled=true`, so the Enter fallback is what
  actually submits — the click ladder times out by design).
- **2FA completed via the file-handoff protocol** → filled `phoneCode_0`
  (`aria-label="Enter code for phone ending with 978"`), submitted via Enter, landed on
  `clogin/pages/proxy?mode=afterLogin` → `clogin/pages/consent?...` (logged-in redirect
  chain). Worked selectors saved to `logs/aeroplan_live_validate/login_drive.json`.
- **Caveat — what this run did NOT exercise:** it used **SMS** (code relayed by the user),
  so the **email delivery-method selection** (`select_email_2fa`) and the **Gmail-IMAP
  responder** are still unverified live (blocked on `SEARCHAERO_GMAIL_*` env not being set
  at run time). The 2FA *completion* mechanism (file handoff → fill → submit) is identical
  between SMS and email, so only the email *selection + Gmail fetch* remain to confirm.
  Cold-profile Arkose (item 3) is also still open — this was a warmed profile.

Tool: `scripts/experiments/aeroplan_login_drive.py` — drives the scripted Gigya login
from a **logged-out warmed profile**, dumps the real selectors + detects Arkose at each
step, stops at the 2FA wall. Creds read from `~/.searchaero/.env`
(`AEROPLAN_NUMBER` / `AEROPLAN_PASSWORD`).

---

## Result: ✅ GO — a scripted login clears Arkose

- **ARKOSE: none detected at ANY step** (form display, after member #, after password
  submit). A scripted Playwright login **passed Arkose passively** on the warmed
  profile. **This retires the single biggest Phase-1 risk.**
- **Credentials accepted** — Gigya only advances to the 2FA screen *after* valid creds,
  so the stored password is correct and the scripted fill + submit works end-to-end up
  to 2FA.

## Login flow + working selectors

The login is a **single-step** Gigya (SAP CDC) screen-set served at
`https://www.aircanada.com/clogin/pages/login?gig_client_id=…` (both fields on one
screen — *not* the two-step flow we initially assumed):

| Element | Selector | Notes |
|---|---|---|
| Sign-in entry (on aircanada.com) | `button:has-text("Sign in")` | opens the Gigya form |
| Member # | `input[type="text"]:visible` | `id=gigya-loginID-…`, `name=username` |
| Password | `input[type="password"]:visible` | `id=gigya-password-…`, `name=password` |
| Submit | — | the Gigya `input[type=submit]` "Sign in" is `disabled` until valid → **submit reliably via `Enter` on the password field** |

## 2FA

- **SMS by default**: code field `input[name="phoneCode_0"][autocomplete="one-time-code"]`,
  buttons **"Send Code"** / **"Resend"**, delivered to the **phone ending 978**. The
  Gigya 2FA screen transitions **in place** (the URL stays `clogin/pages/login`).
- **Email 2FA is also available** (recon §2). For a *fully unattended* scraper, drive the
  login to select **email** 2FA → route the code through the existing
  `scripts/mfa_responder.py` (Gmail IMAP). **SMS keeps a phone in the loop** every
  ~30 min, so email is preferred for automation.

## Caveats (honest)

- **Warmed profile.** Arkose passed on a profile with *many* successful manual logins the
  same day. A **cold/fresh profile or a new IP may score worse** and get an interactive
  puzzle — **re-verify** before relying on unattended login from a clean profile.
- **Not yet end-to-end.** The driver **stops at the 2FA code screen** — it has not been
  driven through code entry to a confirmed logged-in state. Code entry itself is
  mechanically trivial (known field; we already do this for United via `mfa_responder`).
- **Classifier bug (cosmetic).** The driver's auto-classifier mislabels the in-place
  Gigya 2FA transition as `login_form_still_present` (it keys success on a URL change,
  but Gigya swaps screen-sets without navigating). **The raw step dumps are the source of
  truth**, not the printed classification.

## Remaining work to actually *build* Phase 1

1. ✅ **DONE — Complete 2FA entry** in the driver. `scripts/experiments/aeroplan_login_drive.py`
   now has `complete_email_2fa()` + `_fill_submit_and_wait_2fa()`: it writes the
   `~/.searchaero/mfa_request` JSON, polls `~/.searchaero/mfa_response` for the code, fills
   the code field, submits, and waits for the logged-in transition.
2. ✅ **DONE — Prefer email 2FA.** The driver defaults to `--mfa-method email`;
   `select_email_2fa()` drives the "try a different way" → Email → Continue ladder
   (selector ladders `TFA_DIFFERENT_WAY_SELECTORS` / `TFA_EMAIL_OPTION_SELECTORS` /
   `TFA_EMAIL_CONTINUE_SELECTORS`) so re-auth runs unattended via the Gmail-IMAP responder.
3. **Re-verify Arkose on a cold profile / fresh IP** to confirm it isn't only passing
   because the profile is heavily warmed. **← OPEN** — requires the manual live gate (see
   *Manual verification runbook* part (b) below).
4. ✅ **DONE — Package as a reusable core session manager.** `core/aeroplan_session.py`
   provides `AeroplanSession` (`start()` / `stop()` / `is_logged_in()` /
   `login(mfa_method="email")` / `ensure_logged_in(...)`) mirroring `core/cookie_farm.py`,
   reusing the driver's flow + selectors as the single source of truth. Covered by
   `tests/test_aeroplan_session.py` (10 offline tests, Playwright mocked).
5. ✅ **DONE — Fix the driver's classifier.** The classifier now prioritizes `tfa_required`
   and recognizes the in-place Gigya 2FA screen-set swap (no URL change); an explicit
   offline self-check asserts the in-place transition classifies as `tfa_required`.

## Manual verification runbook (Phase-1 close-out)

These are the **human-in-the-loop** gates — **no agent runs these**. Run from the repo root
(`C:\Users\jiami\local_workspace\seataero-src`) on Windows, with a visible (headed) browser.

**Required env in `~/.searchaero/.env`:** `AEROPLAN_NUMBER`, `AEROPLAN_PASSWORD`,
`SEARCHAERO_GMAIL_SENDER`, `SEARCHAERO_GMAIL_APP_PASSWORD`. The Aeroplan account's
**registered 2FA email must be the watched Gmail inbox**, otherwise the code never lands.

### (a) Live headed email-2FA login on the warmed profile

Two terminals. Terminal 1 runs the Gmail-IMAP responder; Terminal 2 drives the login.

Terminal 1 (start the MFA responder, leave it running):

```
.venv\Scripts\python.exe scripts\mfa_responder.py
```

Terminal 2 (run the email-2FA login):

```
.venv\Scripts\python.exe scripts\experiments\aeroplan_login_drive.py --mfa-method email
```

The driver writes `~/.searchaero/mfa_request` with `sender_filter=aircanada.com` (already
set by the driver); the responder reads that per-request filter so it matches **Aeroplan's**
sender (not United's `@united.com` default). **Success =** the driver's **PHASE-1 VERDICT**
line reports `logged_in_success` reached via **email** 2FA.

### (b) Cold-profile / fresh-IP Arkose re-verification (item 3 — the OPEN gate)

Run the **same** email-2FA login, but against a **fresh / empty** profile directory (and/or
a different network / IP) to confirm Arkose does **not** escalate to an interactive
FunCaptcha on a clean profile:

Terminal 1 (responder, as above):

```
.venv\Scripts\python.exe scripts\mfa_responder.py
```

Terminal 2 (login against a brand-new temp profile dir):

```
.venv\Scripts\python.exe scripts\experiments\aeroplan_login_drive.py --mfa-method email --profile-dir <a new temp dir>
```

- **GO:** Arkose stays passive and login reaches `logged_in_success` on the clean profile.
- **NO-GO:** an interactive Arkose challenge appears. The reusable session manager surfaces
  this as `status="arkose"` and bails (it never auto-solves).

**Record the result of (b) back in this doc** once the gate has been run.

## Reusable surface for Phase 2 (scraper)

The future Phase-2 scraper consumes the session manager directly — no driver internals:

```python
from core.aeroplan_session import AeroplanSession
```

Use `start()` then `ensure_logged_in(mfa_method="email")` to obtain (and lazily refresh) a
logged-in Aeroplan session on the ~30–40 min expiry cadence.

## Related

- [`phase-0-transport-spike.md`](./phase-0-transport-spike.md) — transport, session
  lifetime (~30–40 min), calendar strip (5 days/call), SigV4 replay (dead).
- `docs/findings/aeroplan/auth-recon.md` §2 — the Gigya / 2FA / Arkose recon.
- `scripts/mfa_responder.py` — Gmail-IMAP MFA responder (reuse for email 2FA).
