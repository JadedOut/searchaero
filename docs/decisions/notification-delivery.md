# Decision: Replace Gmail email alerts with Discord webhook

**Date:** 2026-05-21
**Status:** Accepted

## Decision

Replace Gmail SMTP (`smtplib`) and ntfy as alert delivery channels with a single Discord webhook (`urllib` POST). Delete the custom email MCP server dependency. Keep Gmail app password solely for MFA IMAP reading.

## Context

The current alert system sends watch-match notifications via Gmail SMTP and ntfy push notifications. Both have portability problems:

- **Gmail SMTP** requires a custom email MCP server built for the project owner's machine. The official Gmail MCP only creates drafts — it cannot send. New users would need to install the custom MCP and generate a Gmail app password just to receive alerts.
- **ntfy** requires installing a niche app most people have never heard of. The app itself is currently broken.
- **The official Gmail MCP** (Anthropic-hosted) had `gmail_send_draft` removed and the request to restore it was closed as NOT_PLANNED.

We evaluated:
1. **Keep Gmail SMTP** — works for us, but the custom email MCP is non-portable
2. **Add Resend API** — lateral move; increases credential count (Gmail app password still needed for MFA IMAP), emails from `@resend.dev` risk spam folders, Resend is a startup dependency
3. **Remove alerts entirely (scrape-to-DB only)** — simplest, but kills the watch system, the most differentiated feature
4. **Telegram bot** — no MCP needed, but requires installing Telegram and a non-obvious chat ID retrieval step. Niche outside crypto/Eastern European dev circles
5. **Discord webhook** — no MCP needed, most devs already have Discord, setup is ~3 minutes, zero credentials beyond a URL
6. **Pushover** — good push UX, but costs $4.99 and requires installing another niche app
7. **Slack webhook** — viable, but more setup clicks than Discord (create Slack App, enable webhooks)

## Justification

**Discord requires no MCP, no bot, no API key.** Right-click a channel → Integrations → New Webhook → copy URL. That URL is the entire config. The code is a single `urllib.request.POST` with a JSON body — identical pattern to the existing ntfy code in `notify.py`.

**Most users already have Discord.** Unlike Telegram, Pushover, or ntfy, Discord is mainstream in both the dev and travel/points communities. No new app to install.

**Credential count goes down.** Gmail SMTP needed `gmail_sender` + `gmail_app_password` + `gmail_recipient`. ntfy needed `ntfy_topic`. Discord needs one value: `discord_webhook_url`. Net reduction from 4 config fields to 1.

**Any simpler wouldn't work.** Removing alerts entirely (option 3) kills watches — the only feature that answers "tell me when X happens" instead of just "what's available now." Premium cabin deals last hours; passive monitoring matters.

**Any more complex is overengineered.** Supporting multiple delivery backends (email + Telegram + Discord + ntfy) is infrastructure for zero users. Discord alone covers push alerts. If a future user needs a different channel, every option is the same ~5 lines of `urllib` POST — swapping takes minutes, not days.

## What changes

| File | Change |
|---|---|
| `core/notify.py` | Add `send_discord()`, remove `send_email()` and `send_ntfy()`, update `notify_watch_matches()` and config functions |
| `core/eval_watches.py` | `run_eval_and_notify()` calls Discord instead of email |
| `~/.searchaero/config.json` | New: `discord_webhook_url`. Remove: `gmail_sender`, `gmail_recipient`, `ntfy_topic`, `ntfy_server` |
| `.claude/skills/flights/SKILL.md` | Remove email MCP references for "email me results" |
| `docs/getting-started.md` | Replace Gmail SMTP + ntfy setup with Discord webhook setup |

## Caveat: sleep, not shutdown

Windows Task Scheduler can wake a PC from sleep/hibernate to run the scrape, but it cannot power on a fully shut-down PC. Users must close the lid or let the PC sleep — not "Shut down" — for scheduled scrapes and Discord alerts to fire unattended.

## What doesn't change

- `scripts/mfa_responder.py` — still uses Gmail IMAP for MFA codes
- `scripts/scheduled_scrape.py` — still calls `run_eval_and_notify()`
- `core/eval_watches.py` — watch evaluation logic unchanged
- `core/db.py`, `scrape.py`, `cli.py` — untouched
