# Scraping Air Canada Aeroplan Award Flight Points/Miles Pricing

*Research Report - Generated 2026-05-27*

---

## Table of Contents

1. [Aeroplan Authentication Flow](#aeroplan-authentication-flow) - Relevance: critical — must automate login + 2FA to  | Difficulty: hard | Risk: high — accounts have been deactivated fo
2. [Aeroplan Award Search API Endpoints](#aeroplan-award-search-api-endpoints) - Relevance: critical — identifying and replaying the | Difficulty: hard | Risk: high — API-level scraping was specifical
3. [Air Canada Anti-Bot Protection Stack](#air-canada-anti-bot-protection-stack) - Relevance: critical — same vendor as United (Akamai | Difficulty: moderate | Risk: medium — Akamai bypass is a known techni
4. [Cookie and Session Management](#cookie-and-session-management) - Relevance: critical — session duration and cookie m | Difficulty: hard | Risk: medium — standard cookie management, but
5. [March 2025 Login Wall](#march-2025-login-wall) - Relevance: critical — eliminates any possibility of | Difficulty: moderate | Risk: high — per-account monitoring means acco
6. [June 2025 Mandatory 2FA](#june-2025-mandatory-2fa) - Relevance: critical — 2FA on every login is the sin | Difficulty: hard | Risk: high — frequent 2FA from automated sessi
7. [AwardWiz (lg/awardwiz)](#awardwiz-lgawardwiz) - Relevance: high — demonstrates that JSON intercepti | Difficulty: moderate | Risk: low — reference architecture only, no di
8. [Aeroplanner (pburka/aeroplanner)](#aeroplanner-pburkaaeroplanner) - Relevance: low — extremely outdated (2016), pre-dat | Difficulty: N/A — not usable in current environment | Risk: N/A
9. [Flightplan (flightplan-tool/flightplan)](#flightplan-flightplan-toolflightplan) - Relevance: low — no longer maintained, but validate | Difficulty: N/A — not usable currently | Risk: N/A
10. [Cowtool (acrewardsearcher)](#cowtool-acrewardsearcher) - Relevance: moderate — demonstrates a successful Aer | Difficulty: N/A — shut down, reference only | Risk: N/A — shut down voluntarily
11. [seats.aero](#seatsaero) - Relevance: high — the most detailed public case stu | Difficulty: N/A — commercial product, not reproducib | Risk: critical — seats.aero was sued for milli
12. [AwardFares](#awardfares) - Relevance: moderate — demonstrates that commercial  | Difficulty: N/A — commercial product | Risk: N/A — they bear their own legal risk
13. [roame.travel](#roametravel) - Relevance: moderate — demonstrates the cached/daily | Difficulty: N/A — commercial product | Risk: N/A
14. [Point.me](#pointme) - Relevance: low — commercial competitor, technical a | Difficulty: N/A | Risk: N/A
15. [Apify Flight Award Scraper](#apify-flight-award-scraper) - Relevance: moderate — demonstrates that cloud-based | Difficulty: easy (to use as a service) — but learnin | Risk: low — account risk is on Apify's infrast
16. [Air Canada v. seats.aero Lawsuit](#air-canada-v-seatsaero-lawsuit) - Relevance: critical — establishes the legal risk ba | Difficulty: N/A | Risk: high — Air Canada has demonstrated willi
17. [Air Canada NDC API](#air-canada-ndc-api) - Relevance: low — NDC API is for authorized commerci | Difficulty: N/A — not accessible for personal projec | Risk: N/A
18. [Duffel API (Air Canada NDC)](#duffel-api-air-canada-ndc) - Relevance: low — Duffel's Air Canada NDC integratio | Difficulty: N/A — does not support award flight sear | Risk: N/A
19. [Playwright + curl_cffi Hybrid Architecture](#playwright--curl-cffi-hybrid-architecture) - Relevance: critical — this is the core architecture | Difficulty: hard | Risk: high — account deactivation risk + legal
20. [Account Deactivation Risk](#account-deactivation-risk) - Relevance: critical — account deactivation with poi | Difficulty: N/A — risk management, not implementatio | Risk: high — proven enforcement via account de

---

## Aeroplan Authentication Flow

### Basic Info

- **Name**: Aeroplan Authentication Flow
- **Type**: auth_flow
- **Description**: The login mechanism for aircanada.ca requires an Aeroplan member number (numeric) plus password.<br>As of March 2025, login is mandatory to access award flight searches.<br>As of June 2025, two-factor authentication (2FA) is mandatory for every login — users receive a one-time 6-digit verification code via SMS or email.<br>There is no authenticator app option.<br>Sessions timeout quickly, requiring re-authentication with 2FA even during single browsing sessions.<br>No device memory/trust option exists.
- **Relevance To Project**: critical — must automate login + 2FA to access award search, directly comparable to United's MP# + password + SMS MFA flow

### Technical Details

- **Technology Stack**: Standard web authentication via aircanada.ca — HTML form POST for credentials, server-side session management, 2FA code delivered via SMS or email
- **Api Endpoints**: Login form posts to aircanada.ca authentication endpoint (exact URL requires DevTools inspection). 2FA verification is a separate POST with the 6-digit code.
- **Request Format**: POST with form-data or JSON body containing Aeroplan number + password. 2FA step sends verification code in separate request.
- **Response Format**: Set-Cookie headers with session tokens on successful auth. Redirect to account dashboard or award search page.
- **Authentication Method**: Aeroplan member number (numeric) + password + mandatory 2FA (SMS or email one-time code). No OAuth, no API keys. Session maintained via cookies.

### Anti-Bot Specifics

- **Protection Vendor**: Akamai Bot Manager (inferred from _abck cookie presence on aircanada.ca)
- **Cookie Signatures**: _abck (Akamai sensor validation), ak_bmsc (Akamai bot management), plus standard session cookies
- **Sensor Mechanism**: Akamai's 512KB obfuscated JavaScript collects 100+ browser/device/behavioral signals, encrypts and POSTs as sensor_data payload. Login page likely has additional Akamai protection.
- **Tls Fingerprinting**: JA3/JA4 TLS fingerprinting — Akamai's most effective detection vector as of 2026. curl_cffi with Chrome impersonation would be required.
- **Aggressiveness Level**: Likely comparable to or more aggressive than United's Akamai, given Air Canada's active anti-scraping posture (lawsuit, login wall, mandatory 2FA)

### Implementation Feasibility

- **Difficulty Level**: hard
- **Estimated Effort**: 1-2 weeks for login automation including 2FA handling
- **Required Modifications**: Need to adapt the existing MFA responder (scripts/mfa_responder.py) to handle Aeroplan 2FA codes via SMS or email.<br>Login flow must handle Aeroplan number instead of MileagePlus number.<br>Session management needs new cookie handling for aircanada.ca domain.
- **Blocking Challenges**: 2FA is mandatory every login with no device trust — requires automated MFA code retrieval every session.<br>Session timeouts are reportedly aggressive, potentially requiring re-auth mid-scrape.<br>If SMS is the only reliable 2FA method, need a phone number that can receive Canadian SMS.
- **Risk Level**: high — accounts have been deactivated for scraping activity

### Legal and Compliance

- **Legal Status**: Terms of Use explicitly prohibit automated access. Active litigation against seats.aero for scraping.
- **Terms Of Use Violations**: Air Canada ToU prohibits 'automated scripts, robots, crawlers, screen scrapers, data mining' — scraping behind login is a clear ToU violation
- **Precedent Cases**: Air Canada v. seats.aero (2023, ongoing); hiQ v. LinkedIn (2022, 9th Circuit — public data scraping not CFAA violation, but does not apply to authenticated scraping)
- **Enforcement History**: Account deactivation for suspected scraping (HN report: $10K points frozen). Lawsuit against seats.aero. Mandatory login wall and 2FA implemented as countermeasures.

### Community Intelligence

- **Source Urls**: - https://onemileatatime.com/news/air-canada-aeroplan-log-in-award-searches/<br>- https://liveandletsfly.com/2fa-air-canada-aeroplan/<br>- https://news.ycombinator.com/item?id=30013567<br>- https://www.aircanada.com/ca/en/aco/home/aeroplan/your-aeroplan/identity-verification.html
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 4 independent sources (One Mile at a Time, Live and Let's Fly, Hacker News, Air Canada official)

### Uncertain Fields

- refresh_frequency

---

## Aeroplan Award Search API Endpoints

### Basic Info

- **Name**: Aeroplan Award Search API Endpoints
- **Type**: api_endpoint
- **Description**: The internal API endpoints used by aircanada.ca for award flight searches.<br>The website frontend makes XHR/fetch calls to backend APIs when a logged-in user searches for award flights.<br>AwardWiz noted that for Aeroplan and United, the approach is to intercept the results JSON as it comes rather than scraping HTML.<br>seats.aero distinguished between 'API scraping' (direct calls to Amadeus reservation system API) and 'screen scraping' (website interface), and their defense noted the API is not the same as the website covered by ToU.
- **Relevance To Project**: critical — identifying and replaying these API endpoints is the core technical challenge, directly analogous to United's /api/flight/FetchAwardCalendar

### Technical Details

- **Authentication Method**: Requires authenticated session cookies from login + 2FA. Likely needs: session cookie, _abck cookie (Akamai), possibly CSRF token in request headers.

### Anti-Bot Specifics

- **Protection Vendor**: Akamai Bot Manager
- **Cookie Signatures**: _abck (must be valid/non-burned), ak_bmsc, session authentication cookies
- **Sensor Mechanism**: API requests validated against Akamai sensor data — invalid _abck cookie will result in blocked requests (403 or challenge page)
- **Tls Fingerprinting**: JA3/JA4 must match a legitimate browser. curl_cffi with chrome impersonation should handle this.

### Implementation Feasibility

- **Difficulty Level**: hard
- **Estimated Effort**: 3-5 days for API discovery via DevTools, 1-2 weeks for full implementation
- **Required Modifications**: Need to perform manual DevTools inspection to discover: (1) exact API endpoint URLs, (2) required headers, (3) request body schema, (4) response parsing.<br>Then adapt curl_cffi calls with new endpoint, headers, and body format.<br>Cookie farm needs new domain and cookie names.
- **Blocking Challenges**: The biggest unknown — exact API endpoints, request format, and required headers are not publicly documented.<br>Must be discovered via manual browser inspection.<br>If Air Canada uses GraphQL, the query structure adds complexity.<br>If they use server-side rendering without clear API calls, the hybrid approach may not work and full browser automation may be needed.
- **Risk Level**: high — API-level scraping was specifically cited in the seats.aero lawsuit

### Legal and Compliance

- **Legal Status**: Active litigation against seats.aero for API scraping. ToU explicitly prohibits automated access.
- **Terms Of Use Violations**: Direct API calls with automated tools violate Air Canada ToU prohibiting robots, crawlers, and automated scripts
- **Precedent Cases**: Air Canada v. seats.aero — plaintiff distinguishes API scraping from screen scraping; seats.aero argues API is not the same as the website covered by ToU
- **Enforcement History**: seats.aero sued for API scraping. 265,552 shopping requests over two days cited as evidence.

### Community Intelligence

- **Source Urls**: - https://github.com/lg/awardwiz<br>- https://viewfromthewing.com/air-canada-says-award-scraping-is-computer-fraud-seats-aero-says-thats-anticompetitive/<br>- https://ndc.aircanada.com/api/documentation/ndcapis<br>- https://news.ycombinator.com/item?id=30013567
- **Last Verified**: 2026-05-27
- **Reliability Score**: likely
- **Corroborating Sources**: 3 sources (AwardWiz README, lawsuit reporting, NDC docs) — but exact endpoint details remain unconfirmed

### Uncertain Fields

- technology_stack
- api_endpoints
- request_format
- response_format
- refresh_frequency
- aggressiveness_level

---

## Air Canada Anti-Bot Protection Stack

### Basic Info

- **Name**: Air Canada Anti-Bot Protection Stack
- **Type**: anti_bot
- **Description**: Air Canada uses Akamai Bot Manager for bot detection on aircanada.ca.<br>This is the same vendor used by United Airlines, meaning the existing knowledge of Akamai's detection mechanisms directly applies.<br>Akamai relies on _abck cookies generated from sensor_data JS payloads, JA3/JA4 TLS fingerprinting, and HTTP header analysis.<br>As of 2026, TLS fingerprinting has become Akamai's most effective detection vector.
- **Relevance To Project**: critical — same vendor as United (Akamai), so existing cookie farm architecture should be adaptable.<br>However, Air Canada may have a more aggressive Akamai configuration given their anti-scraping focus.

### Technical Details

- **Technology Stack**: Akamai Bot Manager — 512KB obfuscated JavaScript loaded on every protected page, collects 100+ browser/device/behavioral signals
- **Api Endpoints**: Akamai sensor_data POST endpoint (automatically handled by the JS payload in browser)
- **Request Format**: sensor_data payload: encrypted device telemetry, browser properties, hardware fingerprints POSTed to Akamai validation endpoint
- **Response Format**: Valid _abck cookie set on successful sensor validation. Invalid/expired _abck results in challenge page or 403.
- **Authentication Method**: Cookie-based: valid _abck cookie required for all protected requests. Generated by Akamai JS in browser, validated server-side.

### Anti-Bot Specifics

- **Protection Vendor**: Akamai Bot Manager
- **Cookie Signatures**: _abck (primary bot detection cookie — generated after sensor_data validation), ak_bmsc (Akamai bot management session cookie), bm_sv (Akamai server validation)
- **Sensor Mechanism**: 512KB heavily obfuscated JavaScript collects: browser fingerprint, canvas fingerprint, WebGL data, screen resolution, timezone, installed plugins, mouse movements, keyboard events, touch events, device orientation, battery status, and other behavioral signals.<br>All encrypted and POSTed as sensor_data to Akamai's validation endpoint.
- **Tls Fingerprinting**: JA3/JA4 TLS fingerprinting is Akamai's most effective detection vector as of 2026.<br>A single mismatch between the _abck cookie's associated fingerprint and the request's TLS fingerprint invalidates the cookie.<br>curl_cffi with Chrome impersonation handles this by matching Chrome's TLS configuration.
- **Aggressiveness Level**: Estimated: equal to or more aggressive than United's Akamai.<br>Air Canada has invested heavily in anti-scraping (login wall, mandatory 2FA, active litigation) suggesting they may use a stricter Akamai configuration.<br>The 97% detection rate for Akamai Bot Manager (per Scrapfly benchmarks) applies.

### Implementation Feasibility

- **Difficulty Level**: moderate
- **Estimated Effort**: 2-3 days to adapt existing cookie farm to aircanada.ca domain
- **Required Modifications**: Adapt Playwright cookie farm to navigate aircanada.ca instead of united.com.<br>Update cookie domain and names.<br>Same fundamental approach: Playwright maintains a background browser that generates valid _abck cookies, which are then used by curl_cffi for API calls.<br>May need to adjust proactive refresh frequency based on Air Canada's specific burn rate.
- **Blocking Challenges**: Air Canada's Akamai configuration may be stricter — need to empirically test burn rate and detection sensitivity.<br>If Air Canada uses Akamai's Enterprise tier with enhanced behavioral analysis, the cookie farm may burn faster.<br>Also, login wall means the Playwright browser must be fully authenticated (including 2FA) to generate valid cookies for award search pages.
- **Risk Level**: medium — Akamai bypass is a known technique, but Air Canada's specific configuration is unknown

### Legal and Compliance

- **Legal Status**: Bypassing bot protection may strengthen CFAA claims in Air Canada's legal theory
- **Terms Of Use Violations**: Circumventing security measures explicitly violates Air Canada ToU
- **Precedent Cases**: Air Canada v. seats.aero — Air Canada alleged 'falsifying requests to bypass blocking procedures' though the court has not yet ruled on this claim
- **Enforcement History**: Air Canada has sued seats.aero partially for bypassing anti-bot measures

### Community Intelligence

- **Source Urls**: - https://scrapfly.io/bypass/akamai<br>- https://captaincompliance.com/education/_abck/<br>- https://www.zenrows.com/blog/bypass-akamai<br>- https://github.com/i7solar/Akamai<br>- https://scrapfly.io/blog/posts/how-to-bypass-akamai-anti-scraping
- **Last Verified**: 2026-05-27
- **Reliability Score**: likely
- **Corroborating Sources**: 5 sources on Akamai detection mechanisms. Air Canada specifically using Akamai is inferred from cookie patterns and industry context, not directly confirmed.

### Uncertain Fields

- refresh_frequency

---

## Cookie and Session Management

### Basic Info

- **Name**: Cookie and Session Management
- **Type**: auth_flow
- **Description**: How authenticated sessions persist on aircanada.ca after login + 2FA.<br>Sessions reportedly timeout quickly — users report needing to re-authenticate even during single browsing sessions.<br>The HN scraper author had to 'steal the session token from the browser cookie' to make API requests.<br>Multiple cookie types are involved: Akamai bot detection cookies (_abck, ak_bmsc), session authentication cookies, and potentially CSRF tokens.
- **Relevance To Project**: critical — session duration and cookie management directly determine scraping throughput and architecture. Short sessions mean more frequent re-authentication (including 2FA), increasing complexity.

### Technical Details

- **Technology Stack**: HTTP cookies set by aircanada.ca — combination of Akamai-managed cookies and application session cookies
- **Api Endpoints**: N/A — cookies are set via Set-Cookie headers during login and page loads
- **Request Format**: Cookies must be included in all subsequent requests via Cookie header
- **Response Format**: Set-Cookie headers on login success and during Akamai sensor validation
- **Authentication Method**: Session maintained via cookies after Aeroplan number + password + 2FA code verification. Multiple cookies required simultaneously: auth session cookie(s) + valid Akamai _abck cookie.

### Anti-Bot Specifics

- **Protection Vendor**: Akamai Bot Manager
- **Cookie Signatures**: _abck (Akamai sensor — burns after N API calls), ak_bmsc (Akamai session), bm_sv (Akamai server validation), plus application-specific session cookies (names unknown — require DevTools inspection)
- **Sensor Mechanism**: _abck cookie is tied to the browser fingerprint that generated it.<br>Using it from a different TLS fingerprint (e.g., raw curl vs Chrome) will invalidate it.<br>curl_cffi with Chrome impersonation solves TLS matching.
- **Tls Fingerprinting**: Cookie is bound to the TLS fingerprint that generated it — must use same fingerprint profile for cookie generation and API calls

### Implementation Feasibility

- **Difficulty Level**: hard
- **Estimated Effort**: Integrated with login flow — 1-2 weeks total including session management
- **Required Modifications**: Extend cookie farm to maintain both Akamai cookies and auth session cookies simultaneously.<br>Need to handle session expiry detection and automatic re-login (including 2FA).<br>May need a persistent Playwright browser that stays logged in while periodically refreshing _abck cookies — similar to United approach but with added 2FA re-auth complexity.
- **Blocking Challenges**: Session timeout frequency is the key unknown.<br>If sessions expire every 15-30 minutes, the 2FA re-auth overhead becomes a major bottleneck.<br>If sessions last hours (like United's ~24h), the approach is much more viable.<br>Also need to determine if the Aeroplan mobile app maintains longer sessions (one source says 'the app allows you to stay continuously logged in').
- **Risk Level**: medium — standard cookie management, but aggressive timeouts could reduce throughput

### Legal and Compliance

- **Legal Status**: Using stolen/extracted session cookies for automated access violates ToU
- **Terms Of Use Violations**: ToU prohibits automated access regardless of authentication method
- **Precedent Cases**: HN user's account was frozen for scraping with extracted session cookies
- **Enforcement History**: Account deactivation for scraping detected via session analysis

### Community Intelligence

- **Source Urls**: https://news.ycombinator.com/item?id=30013567, https://liveandletsfly.com/2fa-air-canada-aeroplan/, https://onemileatatime.com/news/air-canada-aeroplan-log-in-award-searches/
- **Last Verified**: 2026-05-27
- **Reliability Score**: likely
- **Corroborating Sources**: 3 sources — HN firsthand report on cookie extraction, blog reports on session behavior

### Uncertain Fields

- refresh_frequency
- aggressiveness_level

---

## March 2025 Login Wall

### Basic Info

- **Name**: March 2025 Login Wall
- **Type**: anti_bot
- **Description**: As of March 13, 2025, Air Canada requires Aeroplan members to log into their accounts to search award availability on aircanada.ca.<br>Previously, award searches were accessible without authentication.<br>This was explicitly implemented to block automated award-scraping tools, making it easier to monitor and limit the total number of award searches from any one user.
- **Relevance To Project**: critical — eliminates any possibility of unauthenticated scraping.<br>All approaches must handle full login flow including 2FA.<br>This is a deliberate anti-scraping measure that increases complexity significantly.

### Technical Details

- **Technology Stack**: Server-side access control on aircanada.ca — award search pages redirect to login if no valid session
- **Api Endpoints**: Award search endpoints now require authenticated session cookies — unauthenticated requests redirect to login page
- **Request Format**: N/A — this is an access control change, not an API format change
- **Response Format**: Unauthenticated requests get HTTP 302 redirect to login page
- **Authentication Method**: Aeroplan number + password + mandatory 2FA required before accessing award search

### Anti-Bot Specifics

- **Protection Vendor**: Application-level access control (in addition to Akamai)
- **Cookie Signatures**: Valid authenticated session cookie required in addition to Akamai _abck
- **Sensor Mechanism**: N/A — login wall is application-level, not bot detection
- **Refresh Frequency**: Session-dependent — when session expires, must re-login with 2FA
- **Aggressiveness Level**: Highly effective anti-scraping measure — eliminates casual/anonymous scraping entirely, forces account-based access that can be individually monitored and throttled

### Implementation Feasibility

- **Difficulty Level**: moderate
- **Estimated Effort**: Included in authentication flow implementation (1-2 weeks)
- **Required Modifications**: Must implement full Aeroplan login automation.<br>Cannot use the pre-March-2025 approach of scraping public award search pages without auth.<br>Every scraping session starts with: (1) navigate to aircanada.ca, (2) enter Aeroplan# + password, (3) complete 2FA, (4) then search.
- **Blocking Challenges**: Each account's search volume can be individually monitored and rate-limited.<br>Air Canada can set per-account search thresholds.<br>Multiple accounts may be needed for high-volume scraping, but each account requires its own 2FA phone number/email.
- **Risk Level**: high — per-account monitoring means account-level detection and deactivation is straightforward for Air Canada

### Legal and Compliance

- **Legal Status**: Strengthens CFAA arguments — scraping behind login is 'unauthorized access' under stricter interpretations
- **Terms Of Use Violations**: Logging in constitutes agreement to ToU, which explicitly prohibits automated access
- **Precedent Cases**: hiQ v. LinkedIn (9th Circuit 2022) found public data scraping is not CFAA violation — but this does NOT apply to data behind a login wall. Login wall specifically addresses this legal distinction.
- **Enforcement History**: Implemented March 2025 as direct countermeasure to scraping tools including seats.aero

### Community Intelligence

- **Source Urls**: https://onemileatatime.com/news/air-canada-aeroplan-log-in-award-searches/, https://onemileatatime.com/news/airlines-shut-down-websites-scraping-awards/
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 2 major travel blogs confirmed the change with screenshots and community discussion

---

## June 2025 Mandatory 2FA

### Basic Info

- **Name**: June 2025 Mandatory 2FA
- **Type**: anti_bot
- **Description**: As of early June 2025, Aeroplan made two-factor authentication mandatory for all logins.<br>Users receive a one-time 6-digit verification code via SMS or email every time they log in.<br>The option to disable 2FA in account settings has been permanently removed.<br>Sessions timeout quickly, requiring re-entry of 2FA codes even during single browsing sessions.<br>There is no device trust/remember option.
- **Relevance To Project**: critical — 2FA on every login is the single biggest obstacle for automated scraping. The existing mfa_responder.py handles United's SMS MFA, but Aeroplan's implementation may differ.

### Technical Details

- **Technology Stack**: Server-side 2FA enforcement — one-time code delivered via SMS to mobile or email
- **Api Endpoints**: 2FA verification endpoint on aircanada.ca — receives the 6-digit code POST
- **Request Format**: POST with 6-digit verification code after initial credential authentication
- **Response Format**: Session establishment on valid code — Set-Cookie with authenticated session
- **Authentication Method**: SMS or email one-time 6-digit code, mandatory every login, no device trust persistence

### Anti-Bot Specifics

- **Protection Vendor**: Application-level 2FA (not Akamai-specific)
- **Cookie Signatures**: Session cookies set only after successful 2FA verification
- **Sensor Mechanism**: N/A — 2FA is knowledge-based, not behavioral
- **Refresh Frequency**: Every login — and sessions reportedly timeout aggressively, meaning frequent re-2FA
- **Aggressiveness Level**: Very aggressive — mandatory every login, no skip option, aggressive session timeouts force frequent re-auth

### Implementation Feasibility

- **Difficulty Level**: hard
- **Estimated Effort**: 3-5 days to adapt MFA responder
- **Required Modifications**: Adapt scripts/mfa_responder.py to handle Aeroplan 2FA codes.<br>Two approaches: (1) Email-based 2FA — use existing Gmail IMAP monitoring to watch for Air Canada verification emails, parse 6-digit code, auto-submit.<br>(2) SMS-based 2FA — need programmatic SMS access (Twilio number, Google Voice, or IMAP-forwarded SMS).<br>Email-based is likely easier since mfa_responder.py already does Gmail IMAP.
- **Blocking Challenges**: If Air Canada only offers SMS (not email) for 2FA, need a phone number with SMS forwarding.<br>Aggressive session timeouts mean MFA codes may be needed every 15-30 minutes during continuous scraping.<br>Code delivery latency (seconds to minutes) adds to each re-auth cycle.<br>If Air Canada implements CAPTCHA alongside 2FA in the future, this becomes much harder.
- **Risk Level**: high — frequent 2FA from automated sessions is detectable as suspicious behavior

### Legal and Compliance

- **Legal Status**: 2FA bypass for automated access strengthens 'unauthorized access' arguments under CFAA
- **Terms Of Use Violations**: Automating 2FA submission violates the spirit and likely the letter of ToU
- **Precedent Cases**: No specific precedent for 2FA automation in scraping context
- **Enforcement History**: 2FA was implemented as a deliberate anti-scraping countermeasure

### Community Intelligence

- **Source Urls**: https://liveandletsfly.com/2fa-air-canada-aeroplan/, https://www.aircanada.com/ca/en/aco/home/aeroplan/your-aeroplan/identity-verification.html
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 2 sources — travel blog report and Air Canada official page

---

## AwardWiz (lg/awardwiz)

### Basic Info

- **Name**: AwardWiz (lg/awardwiz)
- **Type**: tool
- **Description**: Open-source Node.js/TypeScript project for searching award flight availability across multiple airlines including Aeroplan.<br>Uses a custom-built 'Arkalis' detection-sensitive scraping engine.<br>For Aeroplan and United, the approach intercepts the results JSON from the browser rather than parsing HTML.<br>Development environment includes XVFB and Chromium for visual debugging of scrapers.<br>Repository was archived on September 11, 2024 and is now read-only.
- **Relevance To Project**: high — demonstrates that JSON interception (rather than HTML scraping) works for Aeroplan, validating the API replay approach.<br>Arkalis engine shows anti-detection techniques.<br>However, project is archived and pre-dates login wall + mandatory 2FA.

### Technical Details

- **Technology Stack**: Node.js, TypeScript, Chromium browser automation (via Arkalis custom engine), XVFB for headless display, VSCode Dev Container
- **Api Endpoints**: Intercepts Aeroplan's internal JSON API responses during browser-based award searches — specific URLs not documented in README
- **Request Format**: Browser-based — Arkalis drives Chromium to make legitimate search requests, then intercepts the JSON responses
- **Response Format**: JSON responses containing award flight availability data — parsed from browser network traffic
- **Authentication Method**: Unclear from README — project predates mandatory login requirement (March 2025). May not have required authentication when last active.

### Anti-Bot Specifics

- **Protection Vendor**: Handles multiple airline anti-bot systems via Arkalis engine
- **Cookie Signatures**: Arkalis manages cookie lifecycle within the browser — specific cookie handling not documented
- **Sensor Mechanism**: Arkalis is described as 'detection-sensitive' — actively avoids triggering anti-bot mitigations. Uses real Chromium browser to generate authentic browser fingerprints.
- **Tls Fingerprinting**: Uses real Chromium browser — TLS fingerprint naturally matches Chrome
- **Refresh Frequency**: N/A — uses persistent browser sessions
- **Aggressiveness Level**: Designed to handle multiple airlines' anti-bot systems — demonstrates that Aeroplan's protections were bypassable with browser automation as of 2024

### Implementation Feasibility

- **Difficulty Level**: moderate
- **Estimated Effort**: N/A — project is archived, value is in architecture reference not direct reuse
- **Required Modifications**: Cannot directly reuse (archived Node.js project, different language).<br>Key insight to adapt: the JSON interception pattern for Aeroplan award results.<br>Could implement similar interception in Playwright (Python) by monitoring network responses during award searches.
- **Blocking Challenges**: Project is archived since Sept 2024 — predates login wall (March 2025) and mandatory 2FA (June 2025).<br>The anti-detection techniques may be outdated.<br>No documentation on how Aeroplan authentication was handled.
- **Risk Level**: low — reference architecture only, no direct account risk

### Legal and Compliance

- **Legal Status**: Open-source project — no known legal action against the developer
- **Terms Of Use Violations**: Automated scraping violates airline ToU generally
- **Enforcement History**: No known enforcement action against AwardWiz project

### Community Intelligence

- **Source Urls**: https://github.com/lg/awardwiz, https://github.com/lg/awardwiz/blob/master/README.md
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: GitHub repository directly inspected

---

## Aeroplanner (pburka/aeroplanner)

### Basic Info

- **Name**: Aeroplanner (pburka/aeroplanner)
- **Type**: tool
- **Description**: Python/Scrapy-based Aeroplan scraper from 2016.<br>Very preliminary proof-of-concept with only 3 commits.<br>Hardcoded for a single route (YYZ to LHR on 2016-07-09).<br>Requires Aeroplan member ID and PIN as command-line arguments.<br>Extracts structured flight data including aircraft types, cabin classes, mileage requirements, and itinerary details.<br>Apache-2.0 license.
- **Relevance To Project**: low — extremely outdated (2016), pre-dates current anti-bot protections, login wall, and 2FA.<br>Scrapy spider approach (HTTP-only, no browser) is almost certainly blocked by current Akamai protection.<br>Value is limited to seeing Aeroplan's historical data structure.

### Technical Details

- **Technology Stack**: Python, Scrapy web scraping framework
- **Api Endpoints**: Scrapes Aeroplan website HTML pages — no API interception
- **Request Format**: Scrapy HTTP requests with form-data authentication
- **Response Format**: HTML parsed by Scrapy selectors — outputs to results.json with flight segments, mileage costs, cabin classes
- **Authentication Method**: Aeroplan Member ID (numeric) + PIN passed as command-line arguments

### Anti-Bot Specifics

- **Protection Vendor**: N/A — 2016 predates current Akamai deployment
- **Cookie Signatures**: Standard session cookies only — no Akamai handling
- **Sensor Mechanism**: None — no anti-bot evasion
- **Tls Fingerprinting**: None — uses Python requests/Scrapy default TLS
- **Aggressiveness Level**: N/A — Aeroplan had minimal bot protection in 2016

### Implementation Feasibility

- **Difficulty Level**: N/A — not usable in current environment
- **Estimated Effort**: N/A — would need complete rewrite
- **Required Modifications**: Complete rewrite needed — Scrapy HTTP-only approach cannot handle Akamai JS challenges, 2FA, or modern SPA frontends.<br>Only reference value is the Aeroplan data model (flight fields, mileage structure).
- **Blocking Challenges**: 100% blocked by current anti-bot stack — no JS execution, no browser fingerprint, no Akamai cookie handling

### Legal and Compliance

- **Legal Status**: Open-source project — no known legal issues
- **Terms Of Use Violations**: Automated scraping with credentials violates Aeroplan ToU
- **Enforcement History**: No known enforcement — too small/old to attract attention

### Community Intelligence

- **Source Urls**: https://github.com/pburka/aeroplanner
- **Last Verified**: 2026-05-27
- **Reliability Score**: outdated
- **Corroborating Sources**: 1 source — GitHub repository, 3 commits, last activity 2016

---

## Flightplan (flightplan-tool/flightplan)

### Basic Info

- **Name**: Flightplan (flightplan-tool/flightplan)
- **Type**: tool
- **Description**: JavaScript library for scraping and parsing airline websites for award inventory using Puppeteer (Headless Chrome).<br>FlyerTalk community project, no longer maintained.<br>Supported multiple airlines for award availability searches.<br>Had a dedicated FlyerTalk thread explaining how to search a year of award inventory.
- **Relevance To Project**: low — no longer maintained, but validates that Puppeteer/browser automation was the standard approach for award flight scraping before current anti-bot measures

### Technical Details

- **Technology Stack**: Node.js, Puppeteer (Headless Chrome)
- **Api Endpoints**: Browser automation — navigates airline websites and scrapes rendered pages
- **Request Format**: Full browser requests via Puppeteer — handles JavaScript rendering
- **Response Format**: Parsed award availability data from rendered web pages
- **Authentication Method**: Varied by airline — likely used airline-specific credentials

### Anti-Bot Specifics

- **Protection Vendor**: Pre-dates current aggressive anti-bot measures
- **Cookie Signatures**: Standard browser cookies managed by Puppeteer
- **Sensor Mechanism**: Uses real Chromium browser — generates authentic browser fingerprints
- **Tls Fingerprinting**: Real Chrome TLS fingerprint via Puppeteer
- **Aggressiveness Level**: N/A — built before current anti-bot era

### Implementation Feasibility

- **Difficulty Level**: N/A — not usable currently
- **Required Modifications**: N/A — no longer maintained, would need complete rewrite. Architectural pattern (Puppeteer browser automation) is the predecessor to the Playwright approach used in the current United scraper.
- **Blocking Challenges**: Abandoned project — no updates for modern anti-bot measures

### Legal and Compliance

- **Legal Status**: Open-source project — no known legal issues
- **Terms Of Use Violations**: Automated scraping violates airline ToU generally
- **Enforcement History**: Project shut down voluntarily

### Community Intelligence

- **Source Urls**: https://github.com/flightplan-tool/flightplan, https://www.flyertalk.com/forum/travel-tools/1918538-flightplan-how-search-year-award-inventory-no-longer-maintained.html
- **Last Verified**: 2026-05-27
- **Reliability Score**: outdated
- **Corroborating Sources**: 2 sources — GitHub repo and FlyerTalk thread

---

## Cowtool (acrewardsearcher)

### Basic Info

- **Name**: Cowtool (acrewardsearcher)
- **Type**: tool
- **Description**: Unofficial advanced Air Canada reward search tool built by FlyerTalk community member 'canadiancow'.<br>Exposed a REST API at acrewardsearcher.cowtool.com with URL-based search parameters (origin, destination, dates, cabin class, passenger count).<br>Offered faster searching than Air Canada's official website, plus price alerts and database-backed filtering.<br>Shut down permanently in October 2023 due to anticipated high traffic costs.<br>Differentiated from seats.aero by doing live user-initiated searches with caching rather than continuous pre-scraping.
- **Relevance To Project**: moderate — demonstrates a successful Aeroplan search tool architecture.<br>Key insight: live search + caching is less likely to attract legal attention than continuous pre-scraping.<br>REST API design pattern is a useful reference.

### Technical Details

- **Api Endpoints**: REST API at acrewardsearcher.cowtool.com — URL parameters: ?pax=1&origins=YVR&destinations=YYZ&dates=2022-01-01&cabinJ=true
- **Request Format**: GET requests with query parameters for search criteria
- **Response Format**: Structured flight availability data with sorting and filtering capabilities
- **Authentication Method**: User registration required (to prevent abuse). Backend authentication to Air Canada not detailed.

### Anti-Bot Specifics

- **Protection Vendor**: Handled Air Canada's bot protection at the time (pre-2023)
- **Cookie Signatures**: Not detailed in available sources
- **Sensor Mechanism**: Not detailed — the developer noted the architecture was 'hacked together without thinking too much'
- **Aggressiveness Level**: Successfully operated for several years before voluntary shutdown — suggests Air Canada's pre-2023 bot protection was manageable

### Implementation Feasibility

- **Difficulty Level**: N/A — shut down, reference only
- **Required Modifications**: Key architectural insight to adopt: live search with caching (search on-demand then cache results) rather than continuous pre-scraping of all routes. This reduces request volume and legal exposure.
- **Blocking Challenges**: Shut down pre-login-wall and pre-mandatory-2FA — would not work in current environment without significant adaptation
- **Risk Level**: N/A — shut down voluntarily

### Legal and Compliance

- **Legal Status**: No known legal action — voluntarily shut down before Air Canada's lawsuit campaign
- **Terms Of Use Violations**: Automated searching violated ToU but was not pursued legally
- **Precedent Cases**: Developer 'canadiancow' was mentioned in FlyerTalk as operating differently from seats.aero
- **Enforcement History**: No known enforcement — shutdown was voluntary due to cost concerns

### Community Intelligence

- **Source Urls**: https://www.flyertalk.com/forum/air-canada-aeroplan/2039840-restricted-access-see-wiki-unofficial-advanced-ac-reward-search-tool-cowtool-7.html
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 1 primary source — FlyerTalk thread with developer participation

### Uncertain Fields

- technology_stack

---

## seats.aero

### Basic Info

- **Name**: seats.aero
- **Type**: tool
- **Description**: Commercial award flight aggregator operated by Localhost LLC.<br>Scraped Air Canada Aeroplan award availability at scale — displayed up to 265,552 Aeroplan-available routes over two-day periods.<br>Used a combination of API scraping (direct calls to Amadeus reservation system) and screen scraping.<br>Implements rate-limiting and claims to protect airlines from excessive load.<br>Sued by Air Canada in October 2023 in Delaware federal court.<br>Settlement talks reached impasse February 17, 2026.<br>Case ongoing.
- **Relevance To Project**: high — the most detailed public case study of Aeroplan scraping at scale.<br>Their technical approach (API scraping + screen scraping), volume (265K routes), and legal consequences provide the best reference for what works and what risks exist.

### Technical Details

- **Api Endpoints**: Directly accessed Air Canada's Amadeus reservation system API — seats.aero's defense noted 'the API is not the same as the Air Canada website covered by the site terms'
- **Request Format**: HTTP requests to Air Canada's API with modified HTTP headers — Air Canada alleged they 'falsified requests to bypass blocking procedures' but the article noted this was just 'changing the HTTP header of the requests'
- **Response Format**: Award availability data including routes, cabin classes, mileage costs — sufficient to populate a search engine with 265K+ routes

### Anti-Bot Specifics

- **Protection Vendor**: Bypassed Air Canada's bot protection (presumably Akamai)
- **Sensor Mechanism**: Modified HTTP headers to avoid detection — characterized by defense as standard, legal HTTP header changes
- **Refresh Frequency**: Rate-limited at approximately 1 request per second (265K requests over 2 days)
- **Aggressiveness Level**: Successfully bypassed Air Canada's protections at scale for extended periods — suggests protections were manageable until legal action was taken

### Implementation Feasibility

- **Difficulty Level**: N/A — commercial product, not reproducible
- **Required Modifications**: Key lessons: (1) API scraping was the primary approach — direct Amadeus API calls, not just browser scraping.<br>(2) Rate limiting at ~1 req/sec was still detected.<br>(3) HTTP header modification was characterized as not illegal by defense.<br>(4) Pre-login-wall access was significantly easier.
- **Blocking Challenges**: Since March 2025 login wall and June 2025 mandatory 2FA, the approach seats.aero used (pre-authentication public scraping) is no longer viable. Current approach must be fully authenticated.
- **Risk Level**: critical — seats.aero was sued for millions in damages

### Legal and Compliance

- **Legal Status**: Active litigation — Air Canada v. Localhost LLC (seats.aero), US District Court of Delaware, filed Oct 19, 2023. Settlement talks impasse Feb 17, 2026.
- **Terms Of Use Violations**: Violated Air Canada ToU prohibiting 'automated scripts, robots, crawlers, screen scrapers, data mining'
- **Precedent Cases**: hiQ v. LinkedIn (9th Circuit 2022) — public data scraping not CFAA violation. But seats.aero case involves behind-ToU access and potential API access distinct from website.
- **Enforcement History**: Sued for CFAA violations, breach of contract (ToU), Lanham Act trademark violations (displaying Air Canada logos), trespass to chattels (server burden). Multiple claims totaling millions in damages.

### Community Intelligence

- **Source Urls**: - https://seats.aero/lawsuit<br>- https://viewfromthewing.com/air-canada-says-award-scraping-is-computer-fraud-seats-aero-says-thats-anticompetitive/<br>- https://viewfromthewing.com/navigating-the-gray-zone-air-canadas-lawsuit-and-the-future-of-award-search-tools/<br>- https://www.flyertalk.com/forum/air-canada-aeroplan/2138872-ac-files-suit-against-seats-aero-11.html<br>- https://www.ded.uscourts.gov/sites/ded/files/opinions/23-1177.pdf
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 5+ sources — court documents, seats.aero official page, multiple travel blogs, FlyerTalk forum

### Uncertain Fields

- technology_stack
- cookie_signatures
- tls_fingerprinting
- authentication_method

---

## AwardFares

### Basic Info

- **Name**: AwardFares
- **Type**: tool
- **Description**: Commercial award flight search engine supporting Air Canada Aeroplan among many programs.<br>Offers blog guides on finding Aeroplan award flights.<br>Provides both cached availability data and real-time searches.<br>One of the larger commercial award search tools still operating with Aeroplan support as of 2026.
- **Relevance To Project**: moderate — demonstrates that commercial Aeroplan scraping continues despite legal risks. Their continued operation suggests viable technical approaches exist, but specific methods are proprietary.

### Technical Details

- **Response Format**: Web interface showing award availability with points pricing, cabin classes, dates, and routes

### Implementation Feasibility

- **Difficulty Level**: N/A — commercial product
- **Required Modifications**: N/A — value is as a competitive reference showing Aeroplan data is still obtainable
- **Risk Level**: N/A — they bear their own legal risk

### Legal and Compliance

- **Legal Status**: No known lawsuit from Air Canada (unlike seats.aero). May have a licensing arrangement or may be operating under the radar.
- **Terms Of Use Violations**: Automated access to Aeroplan data likely violates ToU
- **Precedent Cases**: Air Canada v. seats.aero is the relevant precedent
- **Enforcement History**: No known enforcement action against AwardFares for Aeroplan scraping

### Community Intelligence

- **Source Urls**: https://awardfares.com/programs/air-canada-aeroplan, https://blog.awardfares.com/aeroplan-guide/
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 2 sources — AwardFares website and blog

### Uncertain Fields

- technology_stack
- api_endpoints
- request_format
- cookie_signatures
- sensor_mechanism
- tls_fingerprinting
- refresh_frequency
- aggressiveness_level
- authentication_method

---

## roame.travel

### Basic Info

- **Name**: roame.travel
- **Type**: tool
- **Description**: Y Combinator S23 award flight search engine.<br>Displays cached/pre-scraped award availability data rather than real-time searches.<br>Refreshes a curated list of routes daily.<br>SkyView results only available for routes Roame has cached.<br>Supports Aeroplan among other programs.<br>Backed by YC, suggesting commercial viability of the approach.
- **Relevance To Project**: moderate — demonstrates the cached/daily-refresh model for award data.<br>Their approach of curating specific routes and refreshing daily (rather than scraping all routes continuously) may be a more sustainable pattern.

### Technical Details

- **Response Format**: Web interface with award availability, points pricing, and route visualization

### Anti-Bot Specifics

- **Refresh Frequency**: Daily route refresh cadence (not per-request scraping)

### Implementation Feasibility

- **Difficulty Level**: N/A — commercial product
- **Required Modifications**: Key architectural lesson: cached/daily-refresh model reduces scraping volume and legal exposure compared to real-time scraping.<br>For personal use, could scrape specific routes of interest daily rather than trying to cover all routes.

### Legal and Compliance

- **Legal Status**: No known lawsuit — YC backing suggests legal review was done
- **Terms Of Use Violations**: Likely violates airline ToU for automated access
- **Precedent Cases**: Air Canada v. seats.aero is the relevant precedent
- **Enforcement History**: No known enforcement action

### Community Intelligence

- **Source Urls**: - https://roame.travel/<br>- https://roame.travel/programs/aeroplan<br>- https://news.ycombinator.com/item?id=41100094<br>- https://www.pointsandplaces.com/post/how-to-find-aeroplan-redemptions-using-roame
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 4 sources — official site, HN launch discussion, blog review

### Uncertain Fields

- technology_stack
- api_endpoints
- request_format
- cookie_signatures
- sensor_mechanism
- tls_fingerprinting
- aggressiveness_level
- authentication_method

---

## Point.me

### Basic Info

- **Name**: Point.me
- **Type**: tool
- **Description**: Commercial points search tool that aggregates award availability across multiple loyalty programs including Aeroplan.<br>Allows searching by credit card points transferable to various airlines.<br>FlyerTalk discussions suggest these tools use web scraping to gather data, potentially running queries from user-owned devices to avoid detection.
- **Relevance To Project**: low — commercial competitor, technical approach is proprietary. The 'user device' scraping approach mentioned in FlyerTalk is notable as an anti-detection strategy.

### Technical Details

- **Response Format**: Points-based search results across multiple programs

### Implementation Feasibility

- **Required Modifications**: Notable pattern from FlyerTalk: some tools run searches from user devices using user credentials — distributes traffic across many IPs and avoids centralized scraping detection.

### Legal and Compliance

- **Legal Status**: No known lawsuit from Air Canada
- **Terms Of Use Violations**: Automated access violates ToU
- **Precedent Cases**: Air Canada v. seats.aero
- **Enforcement History**: No known enforcement

### Community Intelligence

- **Source Urls**: https://www.flyertalk.com/forum/travel-tools/2150969-data-sources-used-award-search-engines.html
- **Last Verified**: 2026-05-27
- **Reliability Score**: speculative
- **Corroborating Sources**: 1 source — FlyerTalk forum discussion with community speculation

### Uncertain Fields

- technology_stack
- api_endpoints
- request_format
- cookie_signatures
- sensor_mechanism
- tls_fingerprinting
- refresh_frequency
- aggressiveness_level
- authentication_method

---

## Apify Flight Award Scraper

### Basic Info

- **Name**: Apify Flight Award Scraper
- **Type**: tool
- **Description**: Cloud-based Apify actor (igolaizola/flight-award-scraper) for scraping award flight availability from multiple loyalty programs including Aeroplan.<br>Extracts mileage costs, taxes/fees, cabin availability, and full itineraries.<br>Last updated March 2026, suggesting active maintenance.<br>Available as a pay-per-use cloud service on Apify's platform.
- **Relevance To Project**: moderate — demonstrates that cloud-based browser automation can still scrape Aeroplan as of 2026. However, technical details are proprietary to the Apify actor.

### Technical Details

- **Technology Stack**: Apify platform — likely uses Puppeteer or Playwright browser automation in Apify's cloud infrastructure
- **Api Endpoints**: Scrapes airline websites via browser automation
- **Request Format**: Apify actor input: origin, destination, dates, cabin class, loyalty program
- **Response Format**: Structured JSON output: mileage costs, taxes, cabin classes, flight segments, itinerary details

### Anti-Bot Specifics

- **Protection Vendor**: Handles airline anti-bot via Apify's infrastructure (proxy rotation, browser fingerprint management)
- **Cookie Signatures**: Managed by Apify's browser automation
- **Sensor Mechanism**: Apify provides residential proxies and browser fingerprinting capabilities
- **Tls Fingerprinting**: Real browser TLS fingerprint via Apify's Chromium instances
- **Refresh Frequency**: Per-search — no persistent sessions
- **Aggressiveness Level**: Successfully operates as of March 2026 — Apify's cloud infrastructure and proxy rotation help avoid detection

### Implementation Feasibility

- **Difficulty Level**: easy (to use as a service) — but learning their approach is hard (proprietary)
- **Estimated Effort**: N/A for direct use — could subscribe to Apify. For building our own: inspiration only.
- **Required Modifications**: Could use Apify as a data source instead of building our own scraper.<br>Or study their actor's approach (if source is available) for implementation ideas.<br>However, relying on a third-party service for a personal project adds cost and dependency.
- **Blocking Challenges**: Cost (Apify usage fees), rate limits, dependency on third party, no control over implementation
- **Risk Level**: low — account risk is on Apify's infrastructure, not personal Aeroplan account

### Legal and Compliance

- **Legal Status**: Commercial service — Apify bears platform-level legal risk
- **Terms Of Use Violations**: Scraping violates airline ToU
- **Enforcement History**: No known enforcement against this specific Apify actor

### Community Intelligence

- **Source Urls**: https://apify.com/igolaizola/flight-award-scraper
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 1 source — Apify marketplace listing

### Uncertain Fields

- authentication_method

---

## Air Canada v. seats.aero Lawsuit

### Basic Info

- **Name**: Air Canada v. seats.aero Lawsuit
- **Type**: legal
- **Description**: Air Canada and Aeroplan Inc.<br>filed suit against Localhost LLC (seats.aero) in the US District Court of Delaware on October 19, 2023 (Case No.<br>23-1177).<br>Claims include: (1) breach of contract (ToU violation), (2) CFAA unauthorized access, (3) Lanham Act trademark violations (displaying AC logos), (4) trespass to chattels (server burden).<br>Settlement talks reached impasse on February 17, 2026.<br>Case is ongoing with no final ruling as of May 2026.
- **Relevance To Project**: critical — establishes the legal risk baseline for any Aeroplan scraping project. Key legal arguments and Air Canada's enforcement posture directly affect risk assessment.

### Technical Details

- **Technology Stack**: Legal proceeding — not a technical system

### Implementation Feasibility

- **Required Modifications**: Legal risk mitigation: (1) personal use only reduces but does not eliminate risk, (2) low request volume reduces trespass to chattels claims, (3) not displaying Air Canada branding eliminates Lanham Act claims, (4) no commercial use eliminates strongest enforcement motivation
- **Blocking Challenges**: Legal risk is the primary non-technical blocker for any Aeroplan scraping project
- **Risk Level**: high — Air Canada has demonstrated willingness to pursue legal action against scrapers

### Legal and Compliance

- **Legal Status**: Active litigation — settlement impasse Feb 2026, case ongoing
- **Terms Of Use Violations**: ToU prohibits automated scripts, robots, crawlers, screen scrapers, data mining. Accepted by logging into the website.
- **Precedent Cases**: hiQ v.<br>LinkedIn (9th Circuit 2022) — scraping publicly accessible data is not CFAA violation.<br>BUT this does not apply to data behind login walls.<br>The login wall (March 2025) specifically addresses this legal distinction.<br>Also: Van Buren v.<br>United States (Supreme Court 2021) — narrowed CFAA 'exceeds authorized access' provision.
- **Enforcement History**: Filed lawsuit Oct 2023, seeking millions in damages.<br>Prior to lawsuit: account deactivations for scraping, implementation of login wall, mandatory 2FA.<br>Settlement talks failed.<br>Air Canada described as 'aggressively trying to block' scraping tools.

### Community Intelligence

- **Source Urls**: - https://seats.aero/lawsuit<br>- https://www.ded.uscourts.gov/sites/ded/files/opinions/23-1177.pdf<br>- https://viewfromthewing.com/air-canada-says-award-scraping-is-computer-fraud-seats-aero-says-thats-anticompetitive/<br>- https://viewfromthewing.com/navigating-the-gray-zone-air-canadas-lawsuit-and-the-future-of-award-search-tools/<br>- https://newmedialaw.proskauer.com/2023/11/17/another-web-scraping-dispute-focused-on-travel-data/<br>- https://www.flyertalk.com/forum/air-canada-aeroplan/2138872-ac-files-suit-against-seats-aero-11.html<br>- https://awardwallet.com/news/air-canada-aeroplan/air-canada-sues-seats-aero/
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 7+ sources including court documents, legal analysis, travel blogs, FlyerTalk forum

---

## Air Canada NDC API

### Basic Info

- **Name**: Air Canada NDC API
- **Type**: api_endpoint
- **Description**: Official Air Canada NDC (New Distribution Capability) API for authorized technology partners.<br>Uses SOAP/XML protocol per airline industry standard.<br>Supports flight search, booking, cancellation, void, hold orders, corporate fares, and Aeroplan membership inclusion.<br>Available through direct integration or via aggregators like Duffel.<br>Key agency partners include Priceline, Flight Centre, Fareportal, Flighthub, Hopper.<br>Does NOT appear to support award/points flight redemption searches.
- **Relevance To Project**: low — NDC API is for authorized commercial partners only, requires business agreement with Air Canada, uses SOAP/XML (not REST/JSON), and critically does NOT appear to support award flight searches.<br>Not viable for personal scraping project.

### Technical Details

- **Technology Stack**: SOAP/XML API per NDC standard, 24/7 monitoring and support for partners
- **Api Endpoints**: ndc.aircanada.com — includes search, book, cancel, void, hold order endpoints
- **Request Format**: SOAP/XML payloads per NDC specification — includes environmental access keys and headers
- **Response Format**: XML responses per NDC standard
- **Authentication Method**: API keys/credentials issued to authorized partners after business agreement. Requires application and approval by Air Canada.

### Anti-Bot Specifics

- **Protection Vendor**: N/A — authorized API access
- **Cookie Signatures**: N/A — API key authentication
- **Aggressiveness Level**: N/A — authorized access

### Implementation Feasibility

- **Difficulty Level**: N/A — not accessible for personal projects
- **Required Modifications**: N/A — cannot use NDC API for award flight scraping. (1) Requires business partnership, (2) SOAP/XML protocol, (3) does not appear to support award redemption searches.
- **Blocking Challenges**: No access for personal projects. No award flight search capability.

### Legal and Compliance

- **Legal Status**: Authorized commercial API — legal for approved partners
- **Terms Of Use Violations**: N/A — authorized access under partner agreement

### Community Intelligence

- **Source Urls**: - https://ndc.aircanada.com/api/documentation/ndcapis<br>- https://ndc.aircanada.com/en/api/gettingstarted/apisetup<br>- https://ndc.aircanada.com/en/api/gettingstarted/apisorchestration<br>- https://duffel.com/blog/air-canada-ndc-on-duffel
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 4 sources — Air Canada official NDC docs and Duffel integration announcement

---

## Duffel API (Air Canada NDC)

### Basic Info

- **Name**: Duffel API (Air Canada NDC)
- **Type**: api_endpoint
- **Description**: Duffel is a third-party API aggregator that launched an NDC connection to Air Canada with full API support for search, book, void, cancellation, hold orders, and 'inclusion of Aeroplan membership'.<br>The Aeroplan membership inclusion is notable — it means the API can associate bookings with Aeroplan accounts — but this likely refers to earning points on paid flights, not searching/redeeming award flights.
- **Relevance To Project**: low — Duffel's Air Canada NDC integration handles commercial flight bookings, not award redemptions.<br>The 'Aeroplan membership inclusion' refers to points earning, not points spending.<br>Not viable for award search scraping.

### Technical Details

- **Technology Stack**: REST API (Duffel's wrapper around NDC SOAP/XML)
- **Api Endpoints**: api.duffel.com — Air Canada flight search and booking endpoints
- **Request Format**: JSON REST API (Duffel abstracts NDC XML into modern REST/JSON)
- **Response Format**: JSON responses with flight offers, pricing, booking details
- **Authentication Method**: Duffel API key — requires Duffel developer account

### Anti-Bot Specifics

- **Protection Vendor**: N/A — authorized API

### Implementation Feasibility

- **Difficulty Level**: N/A — does not support award flight searches
- **Required Modifications**: N/A — cannot use for award scraping
- **Blocking Challenges**: Does not support award/points flight redemption searches

### Legal and Compliance

- **Legal Status**: Authorized commercial API
- **Terms Of Use Violations**: N/A — authorized access

### Community Intelligence

- **Source Urls**: https://duffel.com/blog/air-canada-ndc-on-duffel, https://duffel.com/flights/airlines/air-canada
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 2 sources — Duffel official blog and product page

---

## Playwright + curl_cffi Hybrid Architecture

### Basic Info

- **Name**: Playwright + curl_cffi Hybrid Architecture
- **Type**: architecture
- **Description**: The proposed architecture for Aeroplan scraping, adapted from the existing United MileagePlus scraper.<br>Two-layer design: (1) Playwright as a 'cookie farm' — maintains a background browser that navigates aircanada.ca, handles login + 2FA, generates valid Akamai _abck cookies by executing Akamai's sensor JS naturally.<br>(2) curl_cffi makes fast API calls (~300ms) using the farmed cookies, with Chrome TLS fingerprint impersonation to match JA3/JA4 signatures.<br>This approach works for United because both Akamai layers (JS sensor + TLS fingerprint) are satisfied.
- **Relevance To Project**: critical — this is the core architecture decision. The key question is whether the same dual-layer approach works for Air Canada's specific Akamai configuration and authentication requirements.

### Technical Details

- **Technology Stack**: Python, Playwright (browser automation), curl_cffi (HTTP client with TLS fingerprint impersonation), asyncio
- **Api Endpoints**: Layer 1: Playwright navigates aircanada.ca pages to farm cookies. Layer 2: curl_cffi replays discovered API endpoints with farmed cookies.
- **Request Format**: curl_cffi: POST/GET with Chrome impersonation headers, farmed cookies, discovered endpoint-specific body format
- **Response Format**: JSON award availability data (same format as browser receives)
- **Authentication Method**: Playwright handles full login flow: Aeroplan# + password + 2FA code entry. Session cookies + _abck cookie extracted and passed to curl_cffi.

### Anti-Bot Specifics

- **Protection Vendor**: Designed to bypass Akamai Bot Manager
- **Cookie Signatures**: _abck (farmed by Playwright), ak_bmsc, session auth cookies — all extracted from Playwright browser and used by curl_cffi
- **Sensor Mechanism**: Playwright browser naturally executes Akamai's 512KB sensor JS, generating valid _abck cookies without needing to reverse-engineer the sensor payload
- **Tls Fingerprinting**: curl_cffi impersonates Chrome's TLS fingerprint (JA3/JA4) — must match the same Chrome version that Playwright uses to generate cookies
- **Refresh Frequency**: On United: proactive refresh every 2 API calls, burns after 3-4. Air Canada's specific rate needs empirical testing.
- **Aggressiveness Level**: Proven effective against United's Akamai. Air Canada likely uses similar Akamai tier — same approach should work in principle.

### Implementation Feasibility

- **Difficulty Level**: hard
- **Estimated Effort**: 2-4 weeks total: 1 week for auth + 2FA automation, 1 week for API discovery + curl_cffi integration, 1-2 weeks for burn rate tuning and reliability
- **Required Modifications**: From existing United scraper: (1) New domain: aircanada.ca instead of united.com.<br>(2) New login flow: Aeroplan# instead of MP#, different form selectors.<br>(3) 2FA automation: adapt mfa_responder for Aeroplan email/SMS codes.<br>(4) API discovery: use DevTools to find award search endpoints and their request/response format.<br>(5) Cookie management: update cookie domain and names.<br>(6) Burn rate calibration: empirically test how fast _abck cookies burn on Air Canada.
- **Blocking Challenges**: Three major unknowns: (1) API endpoints — must discover via DevTools, if Air Canada uses server-side rendering without clear API calls, the hybrid approach fails.<br>(2) 2FA frequency — if sessions timeout every 15 min, 2FA overhead dominates.<br>(3) Account monitoring — Air Canada may detect unusual search patterns per-account even if Akamai is bypassed.
- **Risk Level**: high — account deactivation risk + legal risk from active AC anti-scraping posture

### Legal and Compliance

- **Legal Status**: Would violate Air Canada ToU. Scraping behind authentication strengthens CFAA claims.
- **Terms Of Use Violations**: Multiple violations: automated access, bot usage, circumventing security measures (Akamai bypass), exceeding authorized use
- **Precedent Cases**: Air Canada v. seats.aero (ongoing) — directly relevant. Personal/non-commercial use reduces but does not eliminate legal risk.
- **Enforcement History**: Air Canada has sued commercial scrapers and deactivated individual accounts

### Community Intelligence

- **Source Urls**: Internal: core/cookie_farm.py, scrape.py, core/hybrid_scraper.py, https://scrapfly.io/blog/posts/how-to-bypass-akamai-anti-scraping, https://github.com/lg/awardwiz
- **Last Verified**: 2026-05-27
- **Reliability Score**: likely
- **Corroborating Sources**: Existing United implementation proves the architecture. AwardWiz validates JSON interception for Aeroplan. Akamai bypass techniques are well-documented.

---

## Account Deactivation Risk

### Basic Info

- **Name**: Account Deactivation Risk
- **Type**: legal
- **Description**: Air Canada has demonstrated willingness to deactivate Aeroplan accounts suspected of scraping.<br>A Hacker News user reported their account (containing approximately $10,000 worth of Aeroplan points) was flagged for 'suspicions of fraud' after scraping activity was detected.<br>Restoration required several lengthy phone calls.<br>Air Canada ultimately restored the account after reviewing the user's code.<br>With mandatory login, every scraping operation is tied to a specific Aeroplan account, making detection and enforcement straightforward.
- **Relevance To Project**: critical — account deactivation with points frozen is the most immediate personal risk. Unlike a lawsuit, this can happen rapidly and affect real money (accumulated points).

### Technical Details

- **Technology Stack**: N/A — organizational enforcement action

### Anti-Bot Specifics

- **Protection Vendor**: Air Canada's internal fraud detection systems + Akamai anomaly detection
- **Sensor Mechanism**: Per-account search volume monitoring (enabled by login wall), behavioral pattern analysis, IP-based analysis, Akamai bot scoring
- **Aggressiveness Level**: Aggressive — login wall was implemented specifically to enable per-account monitoring and throttling

### Implementation Feasibility

- **Difficulty Level**: N/A — risk management, not implementation
- **Required Modifications**: Risk mitigation strategies: (1) Use a dedicated Aeroplan account with minimal points balance — never scrape from an account with valuable points.<br>(2) Rate-limit aggressively — mimic human search patterns (slow, irregular intervals).<br>(3) Search only specific routes of personal interest, not all 265K routes.<br>(4) Implement realistic user behavior: mouse movements, scrolling, reading delays in Playwright.<br>(5) Vary search times across the day.<br>(6) Use residential IP, not datacenter/VPN.
- **Blocking Challenges**: Cannot fully eliminate detection risk.<br>Air Canada's login wall enables per-account volume tracking that cannot be bypassed technically.<br>The only mitigation is to keep volume extremely low and search patterns natural.
- **Risk Level**: high — proven enforcement via account deactivation, points freezing

### Legal and Compliance

- **Legal Status**: Account deactivation is a contractual right under Air Canada's terms — no legal recourse for the scraper
- **Terms Of Use Violations**: Scraping violates ToU; Air Canada reserves right to terminate accounts for violations
- **Precedent Cases**: HN user account deactivation — resolved after phone calls and code review, but outcome is not guaranteed
- **Enforcement History**: Confirmed: account deactivation with points frozen (HN report).<br>Confirmed: lawsuit against commercial scraper (seats.aero).<br>Confirmed: technical countermeasures (login wall, mandatory 2FA) implemented to enable enforcement.

### Community Intelligence

- **Source Urls**: https://news.ycombinator.com/item?id=30013567, https://onemileatatime.com/news/air-canada-aeroplan-log-in-award-searches/, https://liveandletsfly.com/air-canada-fraud/
- **Last Verified**: 2026-05-27
- **Reliability Score**: confirmed
- **Corroborating Sources**: 3 sources — firsthand HN account, multiple travel blog reports on Air Canada's anti-fraud measures

---
