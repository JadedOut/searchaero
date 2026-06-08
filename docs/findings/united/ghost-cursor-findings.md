# Ghost cursor (synthetic mouse) — findings & why it stays disabled

**Date:** 2026-06-08. **Verdict: ghost cursor is intermittently unreliable AND not needed. It stays DISABLED on both United and Aeroplan.**

The codebase has an optional "ghost cursor" path — human-like Bézier mouse movement via `python_ghost_cursor`, wrapped in `core/ghost_click.py`, gated behind `use_ghost_cursor` in `core/cookie_farm.py::_auto_login` (the `_click_drawer_button` and `_open_sign_in_drawer` branches). It is **off by default** (both `_auto_login` calls pass the default `use_ghost_cursor=False`). It had **never actually run in production** — see the scipy finding below.

## What an A/B test showed (United login, ephemeral profile)

| Run | Ghost cursor | Outcome |
|---|---|---|
| Baseline | OFF | Reached MFA cleanly, **100% reliable** (matches 180/180 historical burn-in) |
| Attempt 1 | ON | ghost-click on "Continue" **timed out 15s** → regular-click retry recovered → scraped |
| Attempt 2 | ON | ghost-click reached MFA clean (~8s) |
| Attempt 3 (earlier) | ON | ghost-click reached MFA clean (~17s) |

So with ghost cursor ON, **~1 in 3 attempts the ghost-click times out for 15s**, then the login's regular-click retry rescues it. Every run still scraped (the retry masks it), but ghost-on adds up to ~15s of dead time ~⅓ of the time for **zero benefit** (United passes Akamai 100% without it).

## Root cause of the 15s timeout (it's our wiring, not the library)

- **The 15000ms timeout is NOT from `python_ghost_cursor`.** The library has no 15000ms timeout anywhere and never calls `Locator.wait_for`. The error (`Locator.wait_for: Timeout 15000ms exceeded`) comes from **our own** `core/cookie_farm.py:539`: `page.locator(_PASSWORD_FIELD).wait_for(state="hidden", timeout=15000)` (the post-"Sign in" guard). (The experiments twin used in the test: `scripts/experiments/cookie_farm.py:499`.)
- **ghost_cursor fails SILENTLY.** `python_ghost_cursor/playwright_sync/_spoof.py` wraps its `mouse.down()/up()` in try/except → `logger.debug` → returns `None`. So when the Bézier coordinate **misses the button**, the click is a **no-op that reports success**. The page never advances, and the *next downstream* `wait_for` (15s) is what surfaces it. The "1 in 3" is the miss; the 15s is just the next checkpoint noticing nothing happened.
- **Why it misses intermittently:**
  1. We pass a **stale `ElementHandle`** (`ghost_click_button_by_text` in `core/ghost_click.py:29-34` grabs the *last visible* `<button>` early, then clicks after Bézier computation). United's sign-in drawer **animates/re-renders** → the handle goes stale or the button moves → the coordinate click lands off-target.
  2. ghost_cursor clicks by **raw coordinate** (`page.mouse.down/up` at a point), which **bypasses Playwright's actionability/hit-testing**. A normal `locator.click()` re-resolves the element and auto-waits — which is exactly why the OFF path is 100% reliable.
  3. Overshoot (`overshootThreshold=500px`) aimed at a `bounding_box()` captured *before* the move; if the button shifts during the drawer slide-in, mouse-up misses.

## Salvageable? Yes, but never worth it

Fixes would be: wait for drawer-settle before grabbing the target; pass a **fresh specific selector** (not a stale handle); **verify + retry on a short 2s timeout** instead of eating 15s. But even fully fixed, **coordinate-based ghost-click is strictly less reliable than `locator.click()`** (no actionability guarantee), and:
- **United (Akamai) doesn't need it** — 180/180 burn-in passed with regular clicks.
- **Aeroplan (Arkose) doesn't need it either, and it could hurt** — see `docs/findings/aeroplan/bot-detection.md` → *Addendum (2026-06-08)*: Arkose passivity is reputation-driven (fingerprint + IP + warm session), behavioral mouse is secondary, and synthetic Bézier/CDP movement has its own detectable tells.

## Dependency note

`python_ghost_cursor` needs **`scipy`** (Bézier math) at *movement* time — but `scipy` is **not in the project deps**. The first enable attempt crashed instantly with `ModuleNotFoundError: No module named 'scipy'`, which is proof ghost cursor never ran in production. `scipy` was pip-installed ad hoc for the test; it is NOT in `pyproject.toml`. **If ghost cursor is ever revived, scipy must be added to deps** (and the wiring fixed per above, behind a fast verify-and-fallback rather than a 15s stall).

## Bottom line

Keep ghost cursor **off**. The old `cookie_farm.py:297` comment ("Ghost cursor is NOT used — it can hang indefinitely and lock the MCP session permanently") was correct in spirit; the MCP server is now gone, so a stall no longer bricks anything, but the underlying flake (silent coordinate-miss → downstream timeout) is real and the feature has no upside on either backend.
