# Hybrid Architecture: curl_cffi + Playwright Cookie Farm

## Summary

The hybrid architecture combines curl_cffi's speed for API calls (~300ms per request) with Playwright's ability to maintain fresh Akamai cookies via real browser JavaScript execution. Playwright runs in the background as a "cookie farm" — it doesn't make API calls, but keeps `_abck` cookies valid by periodically navigating to united.com. curl_cffi pulls fresh cookies from the farm before each batch of requests.

This approach solves the core problem: Akamai burns static cookies after ~3-4 FetchAwardCalendar calls, making pure curl_cffi unviable, while pure Playwright is ~10x slower per request.

## Architecture

```
┌─────────────────────────────────────────┐
│           Cookie Farm (Playwright)       │
│                                          │
│  Real Chrome browser running in bg       │
│  ├─ Akamai JS sensor executing          │
│  ├─ _abck cookie refreshing             │
│  ├─ Periodic keep-alive navigations      │
│  └─ Exports cookies on demand            │
│                                          │
│  Trigger: navigates to united.com page   │
│  every N requests or every M minutes     │
│  to force Akamai JS to refresh cookies   │
└──────────────┬──────────────────────────┘
               │ fresh cookies
               ▼
┌─────────────────────────────────────────┐
│         API Caller (curl_cffi)           │
│                                          │
│  Fast HTTP with Chrome TLS fingerprint   │
│  ├─ Pulls cookies from Playwright        │
│  ├─ Makes FetchAwardCalendar POST        │
│  ├─ Detects cookie burn (stream reset)   │
│  └─ Requests cookie refresh on burn      │
│                                          │
│  Rate: 7s between calls                  │
│  Expected: 3-4 calls per cookie set      │
│  Cookie refresh: every 2 calls           │
└─────────────────────────────────────────┘
```

**Cookie refresh strategy:**
- **Proactive**: Refresh cookies every 2 calls (before the ~3-4 burn threshold)
- **Reactive**: On stream reset / empty response, immediately refresh and retry
- **Keep-alive**: Playwright navigates to a United page every 5 minutes to keep Akamai JS sensor active

## Cookie Lifecycle

1. **Generation**: Akamai's JavaScript sensor runs in the Playwright browser, generating an `_abck` cookie with sensor data
2. **Export**: `CookieFarm.get_cookies()` reads the cookie from Playwright's browser context and formats it as a `Cookie:` header string
3. **Usage**: curl_cffi includes the cookie in its `Cookie` header for FetchAwardCalendar POST requests
4. **Burn**: After ~3-4 API calls, Akamai invalidates the `_abck` cookie server-side (the cookie value doesn't change, but the server rejects it)
5. **Refresh**: `CookieFarm.refresh_cookies()` navigates to united.com, triggering the Akamai JS sensor to regenerate a fresh `_abck` cookie
6. **Cycle repeats**: Fresh cookies are exported for the next batch of curl_cffi calls

**Key cookies:**
- `_abck` — Akamai Bot Manager sensor cookie (the critical one that burns)
- `bm_sz` — Akamai Bot Manager session size
- `bm_sv` — Akamai Bot Manager session value

## Experiment Results

> **Status**: Template — populate after running `python test_hybrid.py`

| Experiment | Description | Result | Notes |
|---|---|---|---|
| 1 — Single call | Start farm, make one API call | PENDING | Validates basic pipeline |
| 2 — Cookie refresh cycle | 6 calls, refresh every 2 | PENDING | Validates proactive refresh |
| 3 — Full reliability | 10 routes, 7s delay | PENDING | Target: 90%+ success |
| 4 — Burn recovery | Skip refresh, trigger burn, verify recovery | PENDING | Validates reactive refresh |

**Run experiments:**
```bash
cd scripts/experiments
python test_hybrid.py
```

## Performance Comparison

| Approach | Success Rate | Avg Request Time | RAM Usage | Complexity |
|---|---|---|---|---|
| curl_cffi only (no cookies) | 0% | N/A (stream reset) | ~50 MB | Low |
| curl_cffi + static cookies | ~30% (burns after 3-4) | ~300ms | ~50 MB | Low |
| Pure Playwright | ~100% | ~3-5s | ~400 MB | Medium |
| **Hybrid (cookie farm)** | **target 90%+** | **~300ms + refresh overhead** | **~450 MB** | **Medium** |

## Optimal Cookie Refresh Interval

**Conservative (default):** Every 2 calls — well under the ~3-4 burn threshold. Adds ~1-2.5 seconds overhead per call on average (refresh takes 2-5s, amortized over 2 calls).

**Aggressive:** Every 3 calls — right at the burn threshold. Higher risk of burns but less overhead. Only recommended if experiments confirm consistent 3-4 call burn window.

**Time-based (to test):** Refresh on a timer (e.g., every 30 seconds) rather than per-call. If Akamai's burn is time-based rather than call-count-based, this could reduce unnecessary refreshes.

> **Critical finding needed**: Is the cookie burn based on call count, time, or both? Experiments must answer this.

## Implications for Phase 1 Production Scraper

### Dependencies
- `curl_cffi` — Chrome TLS fingerprint impersonation for API calls
- `playwright` — Real browser for cookie farm
- `python-dotenv` — Environment variable management (for any config overrides)

### Resource Requirements
- **RAM**: ~450 MB (Playwright Chrome context ~300-400 MB + Python process ~50 MB)
- **CPU**: Minimal (Playwright is mostly idle between refreshes; curl_cffi is lightweight)
- **Disk**: ~200 MB for Chrome profile data in `.browser-profile/`

### Daily Workflow
1. First run: Playwright opens headed Chrome → user logs in manually (Gmail MFA) → session saved
2. Subsequent runs: session reused from `.browser-profile/`, no login needed
3. Re-login needed: when session expires (estimated once per day)

### Throughput Estimate

At 2,000 Canada routes × 12 monthly windows = 24,000 calls:

| Metric | Value |
|---|---|
| API call time | ~300ms |
| Inter-call delay | 7s |
| Cookie refresh time | ~3s (every 2 calls → ~1.5s amortized) |
| Effective pace | ~8.5s per call |
| Total sweep time | 24,000 × 8.5s = ~56.7 hours |

**56.7 hours is too slow for a daily single-threaded sweep.** Optimizations needed:
1. Test if cookie refresh interval can be increased to 3 (saves ~0.5s/call → ~52.5 hours)
2. Test if inter-call delay can be reduced from 7s to 3-5s (saves 2-4s/call → ~25-40 hours)
3. If neither is enough: run 2 concurrent workers (each with its own cookie farm)

### Infrastructure
- **Laptop**: Sufficient for Phase 1 development and small-scale testing
- **VPS**: Hetzner CX32 (4 vCPU, 8 GB RAM) for production — CX22 (4 GB) is tight with PostgreSQL + Playwright

## Open Questions

- **Session duration**: How long does a single Playwright session last before requiring re-login? To be measured during burn-in testing.
- **Headless after login**: Can the cookie farm run headless after the initial headed login? This would allow unattended VPS operation.
- **Cookie burn trigger**: Is the burn based on call count, time elapsed, or both? This determines the optimal refresh strategy.
- **Shared cookies**: Can multiple curl_cffi sessions share cookies from one farm, or does each need its own? Relevant for future concurrency.
- **Token refresh**: Does the bearer token need periodic refresh from the farm, or does it stay valid independently of Akamai cookies?
- **Farm overhead optimization**: Can `context.cookies()` return fresh-enough cookies without a full page navigation? If Akamai's JS sensor runs continuously, cookies may stay fresh without explicit refresh.
