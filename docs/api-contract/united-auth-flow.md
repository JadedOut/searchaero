# United Award API Authentication Flow

> Captured 2026-04-01 from united.com via Chrome DevTools.
> Reference: Scraperly assessment at https://scraperly.com/scrape/united-airlines

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication Sequence](#authentication-sequence)
3. [Bearer Token](#bearer-token)
4. [Required Headers for API Calls](#required-headers-for-api-calls)
5. [Session Lifecycle](#session-lifecycle)
6. [Implementation Considerations](#implementation-considerations)

---

## Overview

United's award search API requires authentication via a bearer token passed in a custom HTTP header. The token is obtained by logging in with a MileagePlus account through the web UI. There is no public OAuth flow or API key system -- the bearer token is extracted from the browser session after login.

**Key facts:**
- Auth mechanism: `x-authorization-api: bearer {token}` header
- Login method: MileagePlus number + password via Playwright
- **MFA required**: United sends a verification code via SMS (default) or email. SMS prompts in the terminal; email can be handled automatically via Gmail tools.
- No cookies required for the API calls themselves (the bearer token is sufficient)
- Token is ~207 characters, base64-like format starting with "DAAAA..."
- Sessions persist for hours; the hourly scrape cadence naturally keeps them warm

### Authentication Strategy (current)

Automated via Playwright:

1. `searchaero search` launches a Chromium browser via Playwright.
2. Playwright enters the MileagePlus number and password automatically.
3. United sends an MFA code (SMS by default, or email with `--mfa-method email`).
4. SMS: the agent prompts the user in the terminal. Email: the agent reads the code from Gmail automatically.
5. Session persists for the duration of the scrape invocation. Subsequent routes in the same batch reuse the session without re-authentication.

No manual token copying or DevTools interaction is required.

---

## Authentication Sequence

### Step 1: Navigate to Login Page

```
GET https://www.united.com/en/us
```

The user navigates to united.com. The page sets initial tracking cookies and Cloudflare challenge tokens.

### Step 2: Enter Email Address

The login flow is two-step. First, the user's MileagePlus number is entered automatically by Playwright. The UI sends a request to validate the account identifier.

The email entry step may trigger a Cloudflare JavaScript challenge if the browser fingerprint is suspicious. Standard Chrome with a real user-agent passes this automatically.

### Step 3: Enter Password

After the email is accepted, the user enters their password. The form submits credentials to United's authentication backend.

### Step 4: Session Established

Upon successful authentication:
- Session cookies are set on the `united.com` domain
- A bearer token becomes available for API calls
- The browser receives a redirect back to the main page or the search page

### Step 5: Bearer Token Available

After login, the bearer token is included in subsequent API requests via the `x-authorization-api` header. The token format is:

```
bearer DAAAA{base64-like-string}
```

The token is approximately 207 characters long. It does not appear to be a standard JWT (no dot-separated segments). It is likely a proprietary session token issued by United's auth service.

---

## Bearer Token

### Format

```
x-authorization-api: bearer DAAAA...{~200 chars}
```

| Property | Value |
|---|---|
| Header name | `x-authorization-api` |
| Prefix | `bearer ` (lowercase, with space) |
| Token length | ~207 characters |
| Token format | Base64-like, starts with "DAAAA" |
| Encoding | ASCII (no special characters observed) |

### Where It Comes From

The bearer token is set by United's authentication system after a successful login. It can be extracted from:

1. **Browser DevTools**: After logging in, inspect any XHR request to `/api/flight/*` and copy the `x-authorization-api` header value.
2. **Playwright/Puppeteer**: Intercept network requests after login and extract the header from outgoing API calls.
3. **Cookie-to-token derivation**: The token may be derivable from session cookies, but the exact mechanism has not been reverse-engineered. Using the header value directly from an intercepted request is the reliable approach.

### Token vs Cookies

An important distinction: the API calls themselves require the bearer token in the `x-authorization-api` header. Standard session cookies may or may not be required depending on how the request is made:

- **From the browser**: Both cookies and the bearer token are sent automatically.
- **From curl/HTTP client**: The bearer token header alone may be sufficient for API access, though including cookies from the session improves reliability and reduces Cloudflare challenge risk.

For the scraper, the recommended approach is to maintain both the bearer token and session cookies from the authenticated browser context.

---

## Required Headers for API Calls

### Minimum Required Headers

| Header | Example Value | Purpose |
|---|---|---|
| `x-authorization-api` | `bearer DAAAA...` | Authentication |
| `Content-Type` | `application/json` | Request body format |
| `User-Agent` | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36` | Browser identification |
| `Accept` | `application/json` | Response format |

### Recommended Additional Headers

These headers match a real Chrome browser request and reduce the chance of Cloudflare fingerprint rejection:

| Header | Example Value | Purpose |
|---|---|---|
| `sec-ch-ua` | `"Chromium";v="131", "Google Chrome";v="131"` | Chrome version hint |
| `sec-ch-ua-mobile` | `?0` | Not mobile |
| `sec-ch-ua-platform` | `"Windows"` | OS platform |
| `sec-fetch-dest` | `empty` | Fetch API metadata |
| `sec-fetch-mode` | `cors` | CORS mode |
| `sec-fetch-site` | `same-origin` | Same-origin request |
| `Origin` | `https://www.united.com` | Request origin |
| `Referer` | `https://www.united.com/en/us/fsr/choose-flights?f=YYZ&t=LAX&d=2026-04-02&tt=1&at=1&sc=7&px=1&taxng=1&newHP=True&clm=7&st=bestmatches&tqp=A` | Full referer URL |
| `Accept-Language` | `en-US,en;q=0.9` | Language preference |
| `Accept-Encoding` | `gzip, deflate, br, zstd` | Compression |

### Headers to Avoid

| Header | Risk |
|---|---|
| Non-browser `User-Agent` (e.g., `python-requests/2.28`) | Immediate Cloudflare block |
| Missing `sec-*` headers | TLS fingerprint mismatch with claimed UA |
| `x-forwarded-for` or proxy headers | Detection signal |

### curl_cffi Consideration

When using `curl_cffi` for direct HTTP (the recommended primary approach per Scraperly), the TLS fingerprint is automatically matched to Chrome. This means the `sec-*` headers should correspond to the Chrome version being impersonated. Mismatched versions between TLS fingerprint and headers are a detection signal.

**Note:** Pure curl_cffi authentication (replaying login POST requests) is NOT feasible because United's login requires MFA (SMS or email) — a verification code sent to the user's Gmail that must be entered in the browser. Token acquisition requires either manual browser login or Playwright with IMAP-based MFA code retrieval. curl_cffi is used only for API calls after a token has been obtained through other means.

---

## Session Lifecycle

### Session Duration

Based on observations and Scraperly reference:

| Event | Expected Timing |
|---|---|
| Session creation | On successful login |
| Session active | Hours (exact TTL not empirically determined) |
| Session expiry | Estimated once per day, possibly longer |
| Re-authentication needed | When API returns auth error or redirect to login |

The hourly scrape cadence (if running continuously) should keep the session alive through regular API activity. The session is most likely to expire during overnight periods when the scraper is idle, or after a server-side session cleanup.

### Detecting Session Expiry

Session expiry manifests in several ways:

1. **HTTP 401 Unauthorized**: The API rejects the bearer token. This is the cleanest signal.
2. **HTTP 302 Redirect to login page**: The server redirects to the authentication page instead of returning JSON. Check the `Location` header.
3. **HTML response instead of JSON**: The response `Content-Type` is `text/html` instead of `application/json`. This typically means Cloudflare or United's server is returning a login page or challenge page.
4. **JSON error response with auth-related error code**: The response is valid JSON but contains an error indicating the session is invalid.

### Re-Authentication Flow

When session expiry is detected:

1. **Discard the expired bearer token.** Do not retry with the same token.
2. **Clear session cookies** associated with the expired session.
3. **Perform the full login flow** (Steps 1-5 above) to obtain a new bearer token.
4. **Extract the new bearer token** from the first successful API request after login.
5. **Resume scraping** with the new token.

For Playwright-based auth:
- Use `launch_persistent_context` with a dedicated user data directory per account.
- On session expiry, navigate to the login page and re-authenticate.
- The persistent context preserves cookies between scraper runs, so re-login is only needed when the session actually expires.

For curl_cffi-based auth:
- Maintain a cookie jar per account.
- On session expiry, either (a) replay the login POST requests to obtain a new token (requires reverse-engineering the login API), or (b) fall back to Playwright for re-authentication and then extract the new token for curl_cffi use.

### Token Refresh Strategy

```
On each API request:
  1. Send request with current bearer token
  2. If response is 200 OK with valid JSON:
     - Success. Continue.
  3. If response indicates auth failure (401, 302, HTML):
     - Mark current token as expired
     - Acquire lock on this account's re-auth
     - Perform re-authentication
     - Update bearer token
     - Retry the original request with new token
  4. If re-auth fails:
     - Quarantine account
     - Log error
     - Rotate to next account in the pool
```

### Multi-Account Session Management

When running multiple MileagePlus accounts:

- Each account has its own bearer token and cookie state.
- Accounts should not share tokens or cookies.
- Maintain a mapping: `account_id -> {bearer_token, cookies, last_used, health_status}`.
- When one account's session expires, only re-authenticate that specific account. Do not invalidate other accounts.
- Assign accounts to specific proxy IPs (account-to-proxy affinity) to avoid detection from IP switching.

---

## Implementation Considerations

### Playwright Approach (Recommended for Auth)

```
1. Create persistent browser context per account:
   context = browser.new_context(storage_state="account_1_state.json")

2. Navigate to united.com and check if already logged in:
   - If logged in: extract bearer token from any API request
   - If not: perform login flow

3. After login, save state:
   context.storage_state(path="account_1_state.json")

4. Extract bearer token by intercepting a network request:
   page.on("request", lambda req: capture_token(req))
   # Navigate to award search page to trigger an API call
   # Extract x-authorization-api header from the intercepted request

5. Use extracted token for curl_cffi requests (hybrid approach)
```

### curl_cffi Approach (Recommended for API Calls)

Once the bearer token is obtained (via Playwright or other means), use curl_cffi for the actual API calls:

```
curl_cffi advantages over Playwright for API calls:
- ~10x lower resource usage (no headless browser)
- ~5x faster request/response cycle
- Chrome TLS fingerprint impersonation handles Cloudflare
- Supports concurrent requests easily
```

The recommended hybrid model:
1. **Playwright** for authentication (login flow, token extraction)
2. **curl_cffi** for API calls (FetchAwardCalendar, FetchFlights)
3. Fall back to Playwright for API calls only if curl_cffi starts getting blocked

### Security

- Never store bearer tokens or credentials in git-tracked files.
- Bearer tokens should be held in memory only, or in encrypted storage.
- MileagePlus credentials belong in environment variables or an encrypted secrets file.
- Rotate tokens proactively before expiry if possible (re-auth during low-activity windows).
- Log token usage events (creation, refresh, expiry) for debugging but never log the token value itself.
