# Aeroplan Phase 4 — Program-Aware `/flights` Skill: Architecture + Notes

**Purpose.** Phases 1–3 made the **engine** bilingual: `search --program {united,aeroplan}`
and `query --program {united,aeroplan}` both exist, the headed `AeroplanSession` +
bounded re-auth runner are wired into `cli.py`, and the scheduled wrapper emits HEADED
single-route Aeroplan commands. But the **natural-language front door** — the `/flights`
skill at `.claude/skills/flights/SKILL.md` — was still United-only. Every line hardcoded
United assumptions: the `description` advertised United alone, the scrape command had no
`--program`, the MFA step asked for an SMS code. Saying *"scrape YYZ→LAX on Aeroplan"*
silently emitted a **United** command — the wrong-program failure.

Phase 4 teaches the parser what the engine already knows: detect the program from natural
language, then fork the four things that genuinely differ. It is a `SKILL.md` (prompt)
edit plus one hook-precision fix — **no CLI code changes** (the CLI was done in Phases 1–3).

---

## TL;DR — Status: ✅ BUILT + validated (2026-06-05). Sequential MVP, one `(program, route)` at a time.

| Question | Result |
|---|---|
| Can a user reach Aeroplan by talking? | ✅ **YES.** *"Scrape YYZ→LAX on Aeroplan"* (or "air canada", "AC") now emits a HEADED, single-route `search --program aeroplan YYZ LAX --mfa-file --mfa-method email` and displays via `query … --program aeroplan`. |
| United path unchanged? | ✅ **Zero regression.** No program named → the existing United command + `--mfa-method sms` default + "~2 min" + `--ephemeral` "fresh browser" option, all preserved under the default branch. |
| Aeroplan MFA in chat? | ✅ Email-2FA: poll `~/.searchaero/mfa_request`, resolve the 6-digit code from the most recent **Air Canada / Aeroplan** Gmail email (mirrors `mfa_responder.py:70` heuristic), ask-user fallback. **No SMS path** for Aeroplan. |
| Cross-program orchestration? | ❌ **Out of scope (Phase 5).** Programs run one `(program, route)` at a time, sequentially — a single MFA slot + one Aeroplan account make naive parallel scrapes corrupt each other's 2FA. |
| Stop-hook guardrail program-aware? | ✅ The post-scrape "you didn't show results" block reason now names the program actually scraped (reads the marker's stored command). |
| Every emitted flag CLI-valid? | ✅ `--program` present on `search` (`cli.py:2940`) and `query` (`cli.py:2966`); Aeroplan single-route guard at `cli.py:485`. CLI untouched. |

**Validation:** atomic-claim verifier proved 16/16 claims against deterministic oracles
(`--help` output, `SKILL.md` greps, synthetic Stop-hook invocations) + the full suite
(**163 passed**; the lone failure is the pre-existing environmental `test_eval_watches.py`,
commit `888ea41`, unrelated to this diff).

---

## The four divergences (everything else is shared)

A program-detection step sits at the top of the skill's workflow; only these four things
fork. Preamble, cache-first logic, verbatim-paste presentation, and alerts/watches are
shared.

| Dimension | United branch (unchanged, default) | Aeroplan branch (new) |
|---|---|---|
| **MFA channel** | `--mfa-method sms`, user pastes code in chat | `--mfa-method email`, Gmail MCP auto-resolve (Air Canada), ask-user fallback. No SMS. |
| **Route cardinality** | batch OK | **single route only** — CLI hard-rejects `--file`/`--workers` (`cli.py:485`) |
| **Browser/profile** | may use `--ephemeral` (fresh browser) | **persistent profile, headed, NO `--ephemeral`** — needs the warm session |
| **Time + display** | "~2 min"; `query ORIG DEST` | "tens of minutes, may re-auth"; `query ORIG DEST --program aeroplan` |

### Two Gmail-2FA paths — and why only ONE needs a Google App Password

Aeroplan's email-2FA code is fetched from Gmail two completely different ways depending on
whether a human (with Claude) is present. **They use different Gmail credentials. Confusing
the two is the easiest mistake here, so:**

| | Interactive — "scrape YYZ-LAX on Aeroplan" (you → Claude) | Unattended — a scheduled timer fires at 3am (Gate 3) |
|---|---|---|
| Who fetches the code | **Claude, via the Gmail MCP** (`mcp__claude_ai_Gmail__search_threads`) — the OAuth Gmail connection in the live session | **`scripts/mfa_responder.py`**, a bare Python process, via **IMAP** |
| Gmail credential needed | **NONE.** The MCP is already authenticated. No app password, no `.env` entry. | `SEARCHAERO_GMAIL_SENDER` + `SEARCHAERO_GMAIL_APP_PASSWORD` (a Google **App Password**), loaded from an env file via `scheduled_scrape.py --env-file` |
| When it runs | Every time you ask Claude to scrape Aeroplan | Only a fully-unattended scheduled run with no Claude/MCP present |

**This credential ALREADY EXISTS — it is not a new thing to create.** Both keys are set in
the repo-root **`./.env`** (gitignored). The unattended responder is **program-agnostic**:
`scheduled_scrape.py` builds BOTH the United (`_build_search_cmd`, line 136) and Aeroplan
(`_build_aeroplan_search_cmd`, line 174) scheduled commands with `--mfa-method email`, so
**United's unattended path uses the exact same App Password + IMAP responder.** There is **no
Aeroplan-specific credential step** — if United can scrape unattended, Aeroplan can too, on the
identical mechanism. (Note: `scheduled_scrape.py` does not auto-load `./.env`; the registered
schedule must pass `--env-file <path-to-.env>`, which `searchaero schedule add --env-file`
wires into the generated `.bat`.)

**Why the split:** the Gmail MCP only exists *inside a live Claude session*. A scheduled job
that wakes the PC at 3am has no Claude and no MCP — so a standalone script polls Gmail the
only way it can without Claude (IMAP), which needs its own credential. The App Password is
**not** a second human login; it's the headless machine's way in when Claude isn't there.

**Practical consequence:** if you only ever scrape (United *or* Aeroplan) by *asking Claude*,
the App Password is irrelevant — United interactive is SMS-paste, Aeroplan interactive is the
MCP. The App Password matters **only** for fully-unattended scheduled runs of *either* program,
and it is **already configured** in `./.env`. Earlier notes in this repo's history that framed
it as a missing Aeroplan prerequisite were wrong: it exists, and it is shared with United.

Program detection: `aeroplan` / `air canada` / `AC` → `program = aeroplan`; otherwise
default `program = united`. A `PROGRAM` placeholder threads through the shared commands
(`query ORIG DEST --program PROGRAM …`).

---

## Files changed

| File | Change | Git state |
|---|---|---|
| `.claude/skills/flights/SKILL.md` | Bilingual `description`; Program Detection step; Aeroplan scrape + MFA branches; `--program PROGRAM` on cache-check + display; quick-reference + rules updated for the sequential / single-route-Aeroplan contract. United path preserved as the default. | **tracked** |
| `.claude/skills/flights/hooks/validate_query_after_scrape.py` | `build_block_reason(command)` reads the marker's stored command, detects `--program aeroplan` (else united), and names `query ORIG DEST --program <program>` in the block reason. All other branches (no marker / stale / malformed / `stop_hook_active`) unchanged. | **gitignored** — lives, but not version-controlled (the whole `hooks/` dir is ignored) |

> ⚠️ **The hook lives in a gitignored path.** The program-aware block reason is correct on
> disk but does **not** travel with the repo (consistent with how `track_scrape_state.py`
> has always been handled). A fresh clone won't carry it. Force-add it (`git add -f`) if you
> want the precision fix to be reproducible — otherwise it's a local-only nicety on top of
> the already-program-agnostic core guardrail (the hook still fires for Aeroplan regardless;
> only its *message* is the local refinement).

---

## Why sequential (and what Phase 5 unlocks)

The MVP is deliberately one-at-a-time. The product goal — *scrape N airlines for the same
route at once, then compare* — is the **easy** kind of parallelism (different sites,
accounts, Akamai tenants → no shared rate bucket). The **one hard blocker** is the shared
MFA slot: `cli.py` hardcodes `~/.searchaero/mfa_request`/`mfa_response`, and
`scheduled_scrape.py::_clean_mfa_files()` wipes it between groups — which is exactly *why*
groups run sequentially today. Two concurrent scrapes would cross their 2FA codes.

Phase 5 (not done here): namespace the MFA slot per program/job (`--mfa-slot`), a responder
per slot, then concurrent dispatch + `query ORIG DEST --table-view programs`. **The
framework is cheap; the real cost is building more scraper backends** — at two programs the
wall-clock is dominated by the Aeroplan leg regardless, so Phase 5 only pays off once a
*third* backend exists. Comparison **already works sequentially** today via
`query ORIG DEST --table-view programs`.

---

## Plan of record

`specs/phase-4-program-aware-flights-skill.md` — the build plan (objective, acceptance
criteria, atomic claims, validation commands). Executed via `/build`; gated by the
single Outer Validation Loop.
