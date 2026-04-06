---
name: Phase 1 Auth Strategy
description: United requires Gmail MFA on login; Phase 1 uses manual bearer token acquisition from Chrome DevTools daily
type: project
---

United requires Gmail-based MFA when logging in (contradicts earlier "No 2FA" claim in auth flow doc). For Phase 1 Canada scale, authentication is manual:

1. User logs into united.com in Chrome, completes Gmail MFA
2. Copies bearer token from DevTools Network tab (x-authorization-api header)
3. Pastes into `.env` as `UNITED_BEARER_TOKEN`
4. Scraper uses curl_cffi with this token for API calls (~2 hour daily sweep)
5. Repeat next day (~60 seconds manual effort)

Automated auth (Playwright + IMAP email reading) deferred to Phase 1b or when manual becomes burdensome. The auth flow doc at `docs/api-contract/united-auth-flow.md` has been corrected to reflect this (2026-04-01).
