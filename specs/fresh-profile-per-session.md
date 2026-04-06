# Plan: Fresh Browser Profile Per Session

## Task Description
Switch the cookie farm from reusing a persistent browser profile to starting with a fresh profile every session. This prevents stale/flagged Akamai cookies from poisoning new runs. The user logs in manually once at the start of each session, then the scraper runs with clean cookies. Also change the default delay from 0.5s to 3s for sustained reliability.

## Objective
1. Each scraper session starts with a brand new Chrome profile — no leftover Akamai cookies from previous runs
2. User manually logs in once at session start (the `_wait_for_login()` flow)
3. Login session is maintained in-memory for the duration of the run, but NOT persisted to disk for reuse
4. Default delay changed to 3s for production stability
5. Old `.browser-profile` directory is no longer used by default

## Problem Statement
The persistent browser profile at `.browser-profile/` stores Akamai `_abck` cookies across sessions. When the scraper starts the next day, Chrome loads yesterday's flagged cookies. Akamai's server-side state remembers "this cookie lineage was associated with bot-like behavior" and immediately burns the session, even from a clean IP after 19+ hours of cool-down. This caused today's first run to burn at only 7 requests despite a fresh start.

## Solution Approach

**Use a temporary directory** for the browser profile instead of `.browser-profile/`. Python's `tempfile.mkdtemp()` creates a unique temp dir per run. When the scraper stops, the temp dir is cleaned up. No cookies persist between sessions.

**Keep the persistent profile option** via a `--persist-profile` flag for development/debugging, but make ephemeral the default.

**Change default delay** from implicit 0.5s to 3s. The 0.5s delay works in short bursts on a clean IP but is unsustainable for multi-hour runs from a single residential IP.

## Relevant Files

- `scripts/experiments/cookie_farm.py` — Change `start()` to use a temp dir by default. Add `ephemeral` parameter to `__init__`. Clean up temp dir in `stop()`.
- `scripts/burn_in.py` — Add `--persist-profile` flag. Pass ephemeral mode to CookieFarm. Change `--delay` default to 3.
- `scrape.py` — Same changes as burn_in.py for consistency.
- `scripts/experiments/hybrid_scraper.py` — No changes needed.

## Implementation Phases

### Phase 1: Foundation
Add ephemeral profile support to CookieFarm. Make it the default. Keep persistent as opt-in.

### Phase 2: Core Implementation
Update burn_in.py and scrape.py CLI flags. Change delay default. Ensure cleanup on stop/crash.

### Phase 3: Integration & Polish
Verify restart() works with ephemeral profiles (it should create a new temp dir). Ensure _kill_orphaned_chrome still works.

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
  - Name: profile-fixer
  - Role: Implement ephemeral profile in cookie_farm.py and update CLI flags in burn_in.py and scrape.py
  - Agent Type: general-purpose
  - Resume: true

- Builder
  - Name: validator
  - Role: Verify changes are correct by reading modified files
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Implement Ephemeral Profile in CookieFarm
- **Task ID**: ephemeral-profile
- **Depends On**: none
- **Assigned To**: profile-fixer
- **Agent Type**: general-purpose
- **Parallel**: false
- In `cookie_farm.py`, add `ephemeral: bool = True` parameter to `CookieFarm.__init__()`:
  ```python
  def __init__(self, user_data_dir=None, headless=False, ephemeral=True):
      if ephemeral and user_data_dir is None:
          self._ephemeral = True
          self._user_data_dir = Path(tempfile.mkdtemp(prefix="seataero-browser-"))
      else:
          self._ephemeral = False
          self._user_data_dir = Path(user_data_dir) if user_data_dir else DEFAULT_USER_DATA_DIR
  ```
- Add `import tempfile, shutil` at the top of cookie_farm.py
- In `stop()`, after closing the browser, clean up the temp dir if ephemeral:
  ```python
  if self._ephemeral and self._user_data_dir.exists():
      try:
          shutil.rmtree(self._user_data_dir, ignore_errors=True)
          print(f"Cleaned up ephemeral profile: {self._user_data_dir}")
      except Exception:
          pass
  ```
- In `restart()`, when ephemeral, create a NEW temp dir (don't reuse the old one — it may have flagged cookies):
  ```python
  if self._ephemeral:
      old_dir = self._user_data_dir
      self._user_data_dir = Path(tempfile.mkdtemp(prefix="seataero-browser-"))
      try:
          shutil.rmtree(old_dir, ignore_errors=True)
      except Exception:
          pass
  ```
  Place this BEFORE the `self.start()` call in restart().
- Print the profile mode on start: `print(f"Cookie farm started ({'ephemeral' if self._ephemeral else 'persistent'} profile)")` in `start()`

### 2. Update burn_in.py CLI Flags
- **Task ID**: update-burnin-cli
- **Depends On**: ephemeral-profile
- **Assigned To**: profile-fixer
- **Agent Type**: general-purpose
- **Parallel**: false
- Add `--persist-profile` flag to argparse:
  ```python
  parser.add_argument("--persist-profile", action="store_true",
      help="Reuse persistent browser profile instead of ephemeral (default: ephemeral)")
  ```
- Change `--delay` default from current value to 3:
  ```python
  parser.add_argument("--delay", type=float, default=3,
      help="Seconds between API calls (default: 3)")
  ```
- When creating CookieFarm, pass `ephemeral=not args.persist_profile`:
  ```python
  farm = CookieFarm(user_data_dir=profile_dir, headless=args.headless, ephemeral=not args.persist_profile)
  ```
- Update the startup banner to show profile mode: `print(f"Profile:           {'persistent' if args.persist_profile else 'ephemeral (fresh)'}")`

### 3. Update scrape.py CLI Flags
- **Task ID**: update-scrape-cli
- **Depends On**: ephemeral-profile
- **Assigned To**: profile-fixer
- **Agent Type**: general-purpose
- **Parallel**: true (can run alongside update-burnin-cli)
- Add same `--persist-profile` flag to scrape.py's argparse
- Change `--delay` default to 3
- Pass `ephemeral=not args.persist_profile` to CookieFarm constructor
- Update startup banner to show profile mode

### 4. Validate All Changes
- **Task ID**: validate-all
- **Depends On**: ephemeral-profile, update-burnin-cli, update-scrape-cli
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Read cookie_farm.py and verify: `ephemeral` parameter exists in `__init__`, `tempfile.mkdtemp` used for ephemeral, `shutil.rmtree` in `stop()`, new temp dir in `restart()`
- Read burn_in.py and verify: `--persist-profile` flag exists, `--delay` default is 3, `ephemeral=not args.persist_profile` passed to CookieFarm
- Read scrape.py and verify: same changes as burn_in.py
- Verify no references to the old hardcoded `.browser-profile` path remain as the default behavior

## Acceptance Criteria
- `CookieFarm(ephemeral=True)` creates a temp dir and cleans it up on `stop()`
- `CookieFarm(ephemeral=False)` uses the old persistent `.browser-profile` directory
- Default is ephemeral (no flag needed for fresh profile)
- `--persist-profile` flag exists in both burn_in.py and scrape.py
- `--delay` default is 3 in both burn_in.py and scrape.py
- `restart()` creates a new temp dir when ephemeral (not reusing potentially flagged one)
- Temp dir cleanup happens in `stop()` for ephemeral profiles

## Validation Commands
```bash
# Verify ephemeral parameter in CookieFarm
grep "ephemeral" C:/Users/jiami/local_workspace/seataero/scripts/experiments/cookie_farm.py

# Verify persist-profile flag
grep "persist.profile" C:/Users/jiami/local_workspace/seataero/scripts/burn_in.py
grep "persist.profile" C:/Users/jiami/local_workspace/seataero/scrape.py

# Verify delay default is 3
grep "delay.*default.*3" C:/Users/jiami/local_workspace/seataero/scripts/burn_in.py
grep "delay.*default.*3" C:/Users/jiami/local_workspace/seataero/scrape.py

# Verify tempfile import
grep "import tempfile" C:/Users/jiami/local_workspace/seataero/scripts/experiments/cookie_farm.py

# Verify shutil cleanup in stop
grep "shutil.rmtree" C:/Users/jiami/local_workspace/seataero/scripts/experiments/cookie_farm.py
```

## Notes
- The ephemeral profile means the user MUST log in manually at the start of every run. This is intentional — it guarantees clean Akamai cookies.
- Auto-login via Gmail MFA still works if credentials are configured in `.env`. The ephemeral profile just means the login isn't reused from a previous session.
- The old `.browser-profile/` directory can be deleted by the user if they want. It's no longer used by default.
- `restart()` during a run creates a new temp dir. This means after a crash recovery, the browser has zero cookies and must re-authenticate. Fix 1 (restart calls ensure_logged_in) handles this.
- The 3s default delay is conservative but sustainable. Users can override with `--delay 0.5` for short tests.

---

## Hotfix: Non-invasive login polling

### Problem
`_wait_for_login()` polls `_is_logged_in()` every 30s while the user is mid-login. `_is_logged_in()` calls `self._page.content()` (serializes the entire DOM) and `self._page.evaluate(fetch('/api/auth/anonymous-token'))` (fires an HTTP request inside the page context). Both disrupt the active login flow — the sign-in drawer resets, typed credentials are lost, and the page may reload.

### Solution
Replace the `_is_logged_in()` call inside `_wait_for_login()`'s polling loop with a lightweight cookie-only check via `self._context.cookies()`. This is a Playwright CDP call that reads the browser's cookie jar **without touching the page DOM or running any JS**. The user's login flow is completely undisturbed.

### Implementation

#### 5. Fix login polling to use cookie-only check
- **Task ID**: fix-login-polling
- **Depends On**: ephemeral-profile
- **Assigned To**: profile-fixer
- **Agent Type**: general-purpose
- **Parallel**: false
- Add a new method `_has_login_cookies()` to CookieFarm that checks cookies without touching the page:
  ```python
  def _has_login_cookies(self) -> bool:
      """Check for logged-in cookies WITHOUT touching the page DOM.

      Uses Playwright's cookie jar API (CDP-level) which does not interfere
      with any in-progress page interactions like the login flow.
      """
      try:
          cookies = self._context.cookies("https://www.united.com")
          cookie_names = {c["name"] for c in cookies}
          # United sets these cookies after successful authentication
          login_indicators = {"MileagePlusID", "uaLoginToken", "MP_AToken"}
          return bool(cookie_names & login_indicators)
      except Exception:
          return False
  ```
- In `_wait_for_login()`, replace `if self._is_logged_in():` inside the polling loop with `if self._has_login_cookies():`. Keep the **post-loop** confirmation check using `_is_logged_in()` (at that point login is done, so DOM access is safe).

#### 6. Validate login polling fix
- **Task ID**: validate-login-polling
- **Depends On**: fix-login-polling
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `_has_login_cookies()` method exists and uses `self._context.cookies()` (not `self._page`)
- Verify `_wait_for_login()` polling loop calls `_has_login_cookies()` not `_is_logged_in()`
- Verify the post-loop confirmation still uses `_is_logged_in()` (this is intentional — login is done by then)

---

## Hotfix 2: Stop auto-login from driving the page during MFA

### Problem
`_auto_login()` automates the full login flow including MFA (Steps 4-10: clicks "try a different way", selects email radio, enters code via Gmail IMAP). These page interactions disrupt the user when they need to manually enter an SMS MFA code. The page reloads/navigates mid-MFA input.

### Solution
After `_auto_login()` enters email + password and clicks "Sign in" (Steps 1-3), if MFA is presented, stop and return False. This hands off to `_wait_for_login()` which polls cookies non-invasively while the user completes MFA manually in the browser.

### Implementation

#### 7. Stop auto-login after credentials, hand off MFA to user
- **Task ID**: stop-autologin-at-mfa
- **Depends On**: fix-login-polling
- **Assigned To**: profile-fixer
- **Agent Type**: general-purpose
- **Parallel**: false
- In `_auto_login()`, after Step 3 (enter password + click Sign in), check if already logged in. If yes, return True. If MFA is presented (not logged in), print a message and return False to hand off to manual flow:
  ```python
  # Step 3 done — check if MFA is needed
  if self._is_logged_in():
      print("  Auto-login successful (no MFA required)!")
      return True

  print("  MFA required — complete it manually in the browser")
  return False
  ```
- Remove Steps 4-10 (MFA automation via Gmail IMAP). This code is dead weight now that ephemeral profiles require fresh login each time.

#### 8. Validate auto-login MFA handoff
- **Task ID**: validate-mfa-handoff
- **Depends On**: stop-autologin-at-mfa
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `_auto_login()` returns after Step 3 (password + sign in)
- Verify Steps 4-10 (MFA automation) are removed
- Verify `ensure_logged_in()` flow: auto-login returns False on MFA → falls through to `_wait_for_login()` with cookie polling
