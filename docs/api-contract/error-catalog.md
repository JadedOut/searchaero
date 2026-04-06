# United Award API Error Catalog

> Based on HAR captures, Scraperly reference (https://scraperly.com/scrape/united-airlines),
> and the error handling taxonomy from the project brief.

---

## Table of Contents

1. [Error Classification](#error-classification)
2. [HTTP-Level Errors](#http-level-errors)
3. [Application-Level Errors](#application-level-errors)
4. [Data Anomalies](#data-anomalies)
5. [Detection and Response Matrix](#detection-and-response-matrix)

---

## Error Classification

Errors fall into three categories:

| Category | Detection | Examples |
|---|---|---|
| **HTTP-level** | HTTP status code != 200 | 403 Cloudflare block, 429 rate limit, 401 auth failure |
| **Application-level** | HTTP 200 but response body indicates an error | Empty results, warning messages, redirect to login via JSON |
| **Data anomaly** | HTTP 200 with valid structure but suspect values | Negative prices, impossible mile amounts, missing expected fields |

---

## HTTP-Level Errors

### 403 Forbidden (Cloudflare Block)

**Trigger**: Request blocked by Cloudflare's bot detection before reaching United's API server.

**Response characteristics**:
- HTTP status: `403`
- Content-Type: `text/html` (Cloudflare challenge page, not JSON)
- Body contains Cloudflare challenge JavaScript or a "Please verify you are a human" page
- May include a `cf-ray` header identifying the Cloudflare POP

**Common causes**:
- TLS fingerprint mismatch (e.g., claiming Chrome user-agent but using a Python TLS stack)
- Missing or mismatched `sec-*` headers
- Datacenter IP flagged by Cloudflare
- Too many requests from the same IP in a short window
- Stale or missing Cloudflare cookies (`__cf_bm`, `cf_clearance`)

**Recommended handling**:

```
1. Log the Cloudflare ray ID for debugging
2. Do NOT retry immediately -- this wastes requests and deepens the block
3. Rotate to a different proxy IP
4. Wait 10-30 minutes before retrying from the blocked IP
5. If using curl_cffi: verify the impersonation target matches your header set
6. If persistent: escalate to Playwright with stealth patches for this proxy/IP
7. Track 403 rate per proxy IP; quarantine IPs exceeding 10% 403 rate
```

**Scraperly reference**: "Cloudflare blocks can usually be bypassed with curl_cffi achieving ~85% success rate with Chrome TLS fingerprint impersonation."

---

### 429 Too Many Requests (Rate Limited)

**Trigger**: Too many requests from the same IP or account within United's rate limit window.

**Response characteristics**:
- HTTP status: `429`
- May include `Retry-After` header (seconds to wait)
- Body may be JSON with an error message or Cloudflare HTML

**Common causes**:
- Exceeding ~1-5 requests/minute per IP (Scraperly estimate)
- Burst of requests without inter-request delays
- Multiple accounts sharing the same IP hitting aggregate limits

**Recommended handling**:

```
1. Immediately stop sending requests from this IP/account
2. If Retry-After header is present: wait that duration
3. If no Retry-After: back off for 15-60 minutes
4. Reduce request rate for this IP/account going forward
5. Track 429 frequency per account and per proxy IP
6. If one account consistently triggers 429s: quarantine it for 2-4 hours
7. Ensure inter-request delays are 5-10 seconds minimum (per Scraperly recommendation)
```

**Scraperly reference**: "Rate limiting threshold is approximately 1-5 requests per minute per IP, with 10-30 minute cooldown periods."

---

### 401 Unauthorized (Invalid/Expired Token)

**Trigger**: Bearer token is invalid, expired, or missing.

**Response characteristics**:
- HTTP status: `401`
- Body may contain a JSON error message or be empty

**Common causes**:
- Bearer token has expired (session timeout)
- Token was revoked server-side (account locked, password changed)
- Malformed `x-authorization-api` header

**Recommended handling**:

```
1. Mark the current bearer token as expired
2. Attempt re-authentication for this account (see united-auth-flow.md)
3. If re-auth succeeds: retry the original request with the new token
4. If re-auth fails: quarantine the account, rotate to next account
5. Do NOT retry with the same expired token
```

---

### 302 Found (Redirect to Login)

**Trigger**: Session has expired server-side, and United redirects to the login page instead of serving the API response.

**Response characteristics**:
- HTTP status: `302`
- `Location` header points to a login URL (e.g., `https://www.united.com/en/us/account/signin`)
- No JSON body

**Common causes**:
- Session expired after extended idle period
- Server-side session cleanup
- Account locked or deactivated

**Recommended handling**:

```
1. Treat identically to 401: mark token as expired
2. Re-authenticate the account
3. IMPORTANT: Do not follow the redirect -- it leads to an HTML login page
   that an HTTP client cannot process. Re-authenticate programmatically.
4. Resume scraping with the new token
```

---

### 500 Internal Server Error

**Trigger**: United's backend encountered an unhandled exception.

**Response characteristics**:
- HTTP status: `500`
- Body may be JSON with error details or generic HTML error page

**Common causes**:
- Transient server issue
- Malformed request body that passes validation but causes a backend error
- United's backend under heavy load

**Recommended handling**:

```
1. Log the full response body for debugging
2. Retry once after a 30-second delay
3. If the second attempt also returns 500: skip this route/date
4. If 500s become frequent (>5% of requests): pause all scraping for 5 minutes
5. Do NOT retry aggressively -- 500s often indicate server strain
```

---

### 503 Service Unavailable

**Trigger**: United's API is down or undergoing maintenance.

**Response characteristics**:
- HTTP status: `503`
- May include a maintenance page

**Recommended handling**:

```
1. Pause all scraping activity
2. Wait 5-15 minutes
3. Test with a single request before resuming full scraping
4. If 503 persists for >1 hour: alert the operator
```

---

## Application-Level Errors

These return HTTP 200 but indicate problems in the response body.

### Empty Results (No Availability)

**Trigger**: The route/date has no award availability, or the route does not exist.

**Response characteristics**:
- HTTP status: `200`
- Valid JSON response
- `data.Calendar.Months[].Weeks[].Days[].Solutions` is an empty array `[]` for all days
- Or `data.Status` may be a non-1 value

**How to distinguish "no availability" from "route doesn't exist"**:
- **No availability**: The response structure is normal but Solutions are empty. Some days may have Solutions while others don't. This is expected -- not all dates have award seats.
- **Route doesn't exist**: All 30 days have empty Solutions AND the response may contain a Warning message. The `SearchFiltersOut` may have default/empty values.

**Recommended handling**:

```
1. If some days have Solutions and some don't: normal. Store the available days,
   record the empty days as "no availability" (not as errors).
2. If ALL 30 days have empty Solutions:
   a. Check if the route is valid (are Origin/Destination valid IATA codes for United?)
   b. Log a "no results" event for monitoring
   c. If this route consistently returns zero results across multiple scrapes:
      consider removing it from the route list
3. Do NOT treat empty results as errors requiring retry -- they are valid data.
```

---

### Warning Messages in Response

**Trigger**: The API returns results but with warning messages indicating partial or degraded data.

**Response characteristics**:
- HTTP status: `200`
- `data.Warnings[]` or `data.Trips[].Warnings[]` contains warning objects

**Observed warning structure** (from `sample-responses/detail-response.json`):

```json
{
  "MajorCode": "20003.26",
  "MajorDescription": "FlightShopping/ProviderBBX",
  "MinorCode": "10051",
  "MinorDescription": "ITAWaringMessage",
  "Message": "I'm sorry, we could not find answers to your query. "
}
```

**Known warning types**:

| Warning Key | Meaning |
|---|---|
| `NON_PREFERRED_CABIN` | Results include flights not in the preferred cabin |
| `CHANGE_OF_TERMINAL` | Terminal change at connection airport |
| `RISKY_CONNECTION` | Short connection time |
| `ARRIVAL` | Arrival-related advisory (e.g., next day arrival) |
| `LONG_LAYOVER` | Extended layover at connection airport |
| `STOP` | Stop (not connection) on a segment |
| `CHANGE_OF_AIRPORT_SLICE` | Connection requires changing airports |
| ITAWaringMessage (MinorCode 10051) | Backend could not process query |

**Recommended handling**:

```
1. Log warnings for monitoring but do not treat them as errors
2. Flight-level warnings (CHANGE_OF_TERMINAL, RISKY_CONNECTION) are informational
   and can be stored as metadata or ignored
3. The "could not find answers" warning (MinorCode 10051) alongside empty results
   may indicate a temporary backend issue -- schedule a retry in 30 minutes
4. Monitor warning frequency: a sudden increase may signal API changes
```

---

### Malformed JSON / Unexpected Response Format

**Trigger**: The response is not valid JSON, or the JSON structure has changed.

**Response characteristics**:
- HTTP status: `200`
- Content-Type may be `application/json` but body fails to parse
- Or body is valid JSON but missing expected fields (e.g., no `data.Calendar`)

**Common causes**:
- Partial response due to network interruption
- United deployed an API update that changed the response schema
- Cloudflare injected a challenge page with a 200 status (rare but possible)

**Recommended handling**:

```
1. Catch JSON parse exceptions explicitly
2. Log the raw response body (first 2 KB) for debugging
3. Do NOT store partially parsed data -- reject the entire response
4. Retry once after 10 seconds
5. If the same endpoint consistently returns malformed responses:
   a. Alert the operator immediately -- this may indicate an API schema change
   b. Pause scraping for this endpoint until investigated
6. Validate expected top-level structure: data.Calendar.Months exists,
   data.Trips exists, data.Status is present
```

---

### HTML Response on API Endpoint

**Trigger**: The server returns an HTML page instead of JSON for an API endpoint.

**Response characteristics**:
- HTTP status: `200` (misleadingly)
- Content-Type: `text/html` instead of `application/json`
- Body contains HTML (login page, error page, or Cloudflare challenge)

**Common causes**:
- Session expired (server redirected to login page without a 302)
- Cloudflare soft challenge that returns 200 with HTML
- CDN/load balancer routing error

**Recommended handling**:

```
1. Check Content-Type header before attempting JSON parse
2. If Content-Type is text/html:
   a. Check if body contains login form keywords ("sign in", "MileagePlus")
      -> Treat as session expiry, re-authenticate
   b. Check if body contains Cloudflare keywords ("cf-browser-verification")
      -> Treat as 403, rotate proxy
   c. Otherwise: log the response, skip this request, retry later
3. Always check Content-Type as the first validation step
```

---

## Data Anomalies

These are structurally valid responses with suspect data values. They indicate parsing bugs, API bugs, or unusual but legitimate data.

### Anomalous Price Values

| Anomaly | Detection | Action |
|---|---|---|
| Miles amount = 0 | `Prices[].Amount == 0 && Currency == "MILES"` | Reject row. Zero-mile awards do not exist. |
| Miles amount < 0 | `Prices[].Amount < 0` | Reject row. Log for investigation. |
| Miles amount > 500,000 | `Prices[].Amount > 500000` | Reject row. No United award exceeds this. Flag for investigation. |
| Tax amount < 0 | `Prices[].Amount < 0 && PricingType == "Tax"` | Reject row. Log for investigation. |
| Tax amount > 1,000 | `Prices[].Amount > 1000 && PricingType == "Tax"` | Flag for review. May be legitimate for long-haul international but unusual for US/Canada. |
| Missing MILES price | No Price with `Currency == "MILES"` | Reject solution. Incomplete data. |
| Missing USD tax | No Price with `Currency == "USD"` | Accept with null tax. Tax-free awards are theoretically possible but extremely rare. |

### Anomalous Date Values

| Anomaly | Detection | Action |
|---|---|---|
| Date in the past | `DateValue` before today | Skip. Calendar may include trailing past dates. |
| Date > 337 days out | `DateValue` more than 337 days from today | Skip. Beyond United's booking window. |
| DateValue format unexpected | Not `MM/DD/YYYY` | Log parsing error. Do not attempt to interpret. |

### Anomalous Structural Values

| Anomaly | Detection | Action |
|---|---|---|
| Unknown CabinType | CabinType not in the known mapping table | Log as new cabin type. Store the data but flag for review. May indicate API update. |
| Unknown AwardType | AwardType not "Saver" or "Standard" | Log as new award type. Store the data but flag for review. |
| CabinCount = 0 on a day with Solutions | Inconsistency | Use Solutions array as source of truth. CabinCount may be a display hint only. |
| Solutions on a DayNotInThisMonth = true day | Padding day with data | Process normally. The data is valid even if the day is in a different month. |

---

## Detection and Response Matrix

Summary of all error types with detection method and recommended action:

| Error | Detection | Severity | Action | Retry? |
|---|---|---|---|---|
| **403 Cloudflare block** | HTTP 403 + HTML body | High | Rotate proxy, wait 10-30 min | Yes, different IP |
| **429 Rate limited** | HTTP 429 | High | Back off 15-60 min for this IP/account | Yes, after cooldown |
| **401 Unauthorized** | HTTP 401 | Medium | Re-authenticate account | Yes, with new token |
| **302 Redirect** | HTTP 302 + Location header | Medium | Re-authenticate account | Yes, with new token |
| **500 Server error** | HTTP 500 | Medium | Wait 30s, retry once | Once |
| **503 Unavailable** | HTTP 503 | High | Pause all scraping 5-15 min | Yes, after pause |
| **Empty results** | 200 + empty Solutions | Low | Store as "no availability" | No |
| **Warning messages** | 200 + Warnings[] non-empty | Low | Log, continue | No |
| **Malformed JSON** | JSON parse failure | High | Log raw body, alert operator | Once |
| **HTML on API endpoint** | 200 + Content-Type text/html | Medium | Check for login/challenge | Depends on cause |
| **Zero/negative miles** | Amount <= 0 | Medium | Reject row, log | No |
| **Extreme miles** | Amount > 500,000 | Low | Reject row, flag | No |
| **Unknown CabinType** | Not in mapping table | Low | Store with flag, alert operator | No |

### Error Rate Thresholds

Monitor aggregate error rates and trigger alerts at these thresholds:

| Metric | Warning Threshold | Critical Threshold | Action at Critical |
|---|---|---|---|
| 403 rate (per proxy IP) | >5% of requests | >15% of requests | Quarantine IP |
| 429 rate (per account) | >2% of requests | >10% of requests | Quarantine account for 2h |
| 401 rate (per account) | >1% of requests | >5% of requests | Re-auth; if persistent, deactivate account |
| 500 rate (global) | >3% of requests | >10% of requests | Pause scraping, alert operator |
| Malformed JSON rate | >0.1% of requests | >1% of requests | Alert operator immediately (likely API change) |
| Empty results rate | N/A (expected) | >95% of all routes | Check if auth is broken (getting empty results for known-good routes) |

### Circuit Breaker Pattern

Implement a circuit breaker to prevent cascading failures:

```
State: CLOSED (normal operation)
  -> If error rate exceeds critical threshold for 5 consecutive minutes:
     Transition to OPEN

State: OPEN (all requests paused)
  -> After 5 minutes:
     Transition to HALF-OPEN

State: HALF-OPEN (testing)
  -> Send 1 test request
  -> If success: transition to CLOSED
  -> If failure: transition to OPEN (reset 5-minute timer)
```

Apply circuit breakers at two levels:
1. **Per-account**: Protects individual accounts from being burned
2. **Global**: Protects against United-wide outages or API changes
