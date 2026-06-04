# Bot-detection / anti-automation systems on aircanada.com (Aeroplan award search) — detection signals and evasion for a Playwright + curl_cffi scraper

> Research report auto-generated from per-system JSON results. Field values marked uncertain in the source data are omitted below.

## Table of Contents

1. [Arkose Labs (FunCaptcha)](#arkose-labs-funcaptcha)  
   **Vendor**: Arkose Labs (FunCaptcha / Arkose Bot Manager). Founded by Kevin Gosschalk; the r…  
   **Scope**: Air Canada LOGIN / authentication page. Loaded from aircanada-api.arkoselabs.com…  
   **Headed-browser verdict**: Partial. A REAL headed Chrome driven by Playwright (with stealth patches for nav…

2. [Akamai Bot Manager](#akamai-bot-manager)  
   **Vendor**: Akamai Technologies — Bot Manager (Premier) running on Akamai's edge in front of…  
   **Scope**: Site-wide on www.aircanada.com (the edge sits in front of all origin traffic, in…  
   **Headed-browser verdict**: Yes, with caveats — this is the standard winning architecture and matches the ex…

3. [securitytrfx / FareNet](#securitytrfx-farenet)  
   **Scope**: Aeroplan REDEMPTION / award-search pages specifically. Loads securitytrfx.com/js…  
   **Headed-browser verdict**: Irrelevant to access. Because securitytrfx/FareNet does not gate or block, a hea…

4. [Glassbox](#glassbox)  
   **Vendor**: Glassbox Digital — session-replay / digital-experience analytics (DXA) platform.…  
   **Scope**: Site-wide on aircanada.com. POSTs to report.acacb.glassboxdigital.io/glassbox/re…  
   **Headed-browser verdict**: A real headed Playwright browser does NOT need to 'defeat' Glassbox for access (…


---


## Arkose Labs (FunCaptcha)


### Identification

**Vendor**

Arkose Labs (FunCaptcha / Arkose Bot Manager). Founded by Kevin Gosschalk; the rotate-the-image 3D puzzle was originally branded FunCaptcha.

**Page Scope**

Air Canada LOGIN / authentication page. Loaded from aircanada-api.arkoselabs.com/v2/8BAAFE0D-A867-4813-96D5-ABAF2C0D9B93/api.js and /settings. The GUID is Air Canada's Arkose public key (site key).

**Observed Endpoints**

aircanada-api.arkoselabs.com/v2/<public_key>/api.js (enforcement script), /v2/<public_key>/settings, plus runtime calls to /fc/gt2/public_key/<key>, /fc/gc/ (telemetry/bda blob), /fc/ca/ (challenge answer). Session token (verification token) returned on solve.


### Detection signals

**Browser Fingerprint**

Core signal. On load, the enforcement script collects a device/browser fingerprint: Canvas rendering hash, WebGL renderer/vendor string, AudioContext signature, installed-font enumeration, screen resolution/color depth, and navigator properties (plugins, languages, hardwareConcurrency, deviceMemory, userAgent). Headless/automation environments produce inconsistent or known-bad fingerprints (e.g., SwiftShader/llvmpipe WebGL renderer, missing audio stack, font list that doesn't match the claimed OS).

**Headless Automation Flags**

Detected via both fingerprint and behavior. Arkose's own blog confirms it added 'lower-level' detection focused on headless-browser behaviors. Concrete vectors documented by researchers: navigator.webdriver, CDP/automation artifacts, Chrome-headless WebGL renderer (SwiftShader), missing or stubbed AudioContext, font/permission/notification API inconsistencies, and the behavioral anomalies above. Vanilla Playwright/Puppeteer (headless) are flagged. A real headed Chrome controlled by Playwright closes the fingerprint gap but does NOT defeat the behavioral layer by itself.


### Challenge / escalation

**Passive Vs Active**

Two-stage. Stage 1 is invisible/passive: api.js loads, fingerprints, scores risk silently; low-risk sessions get a token with no visible challenge (transparent mode). Stage 2 is the Enforcement Challenge: the interactive 3D rotate-the-image puzzle, shown only when risk is elevated.

**Escalation Trigger**

Escalation to the interactive puzzle is driven by the risk score crossing Air Canada's configured threshold. Triggers: bad/headless fingerprint, anomalous or absent behavioral telemetry (straight-line mouse, no human entropy), bad IP reputation (datacenter/VPN/proxy), high request velocity, repeated logins from one device/IP, or fingerprint that doesn't match prior good sessions. Clean headed browser + human-like behavior + residential IP tends to stay in transparent (no-puzzle) mode.


### Evasion verdict

**Headed Playwright Verdict**

Partial. A REAL headed Chrome driven by Playwright (with stealth patches for navigator.webdriver, real GPU WebGL, real AudioContext) fixes the static fingerprint and can stay in transparent mode IF behavior is human-like and the IP is clean. It does NOT inherently beat the behavioral layer: you must drive realistic mouse paths, dwell times and typing cadence. When an actual rotate-puzzle is presented, Playwright cannot solve it programmatically with acceptable reliability — that requires a CAPTCHA-solving service (human/ML) given the public key + blob/surl, which returns a token. Best compliant strategy: keep risk low so the puzzle never appears (clean headed profile + residential IP + human pacing), and minimize login frequency by reusing an authenticated session.

**Curl Cffi Relevance**

Low for Arkose itself — Arkose is browser-JS-executed, so a pure HTTP client cannot run api.js or generate a valid bda telemetry blob. curl_cffi only helps for the surrounding aircanada.com requests (TLS impersonation to satisfy Akamai). The Arkose challenge fundamentally needs a real browser (Playwright) or a solving service; curl_cffi alone cannot produce a valid Arkose token.


### Uncertain / low-confidence fields

- behavioral telemetry
- cookie token validation
- tls fingerprint


---

## Akamai Bot Manager


### Identification

**Vendor**

Akamai Technologies — Bot Manager (Premier) running on Akamai's edge in front of www.aircanada.com. Sensor JS is 'bmak' (Bot Manager Akamai).

**Page Scope**

Site-wide on www.aircanada.com (the edge sits in front of all origin traffic, including the booking/award-search and login flows; Arkose and FareNet load behind Akamai).

**Observed Endpoints**

Randomized sensor-beacon paths, e.g. POST www.aircanada.com/FkCO/rhNM/.../YKmhcKRp1 returning 201/202 (the obfuscated random path is per-deployment). Cookies: _abck (primary validation token), bm_sz (session/device cookie), ak_bmsc (Akamai Bot Manager session cookie), sometimes bm_sv / sbsd.


### Detection signals

**Tls Fingerprint**

Major server-side layer. Akamai inspects the TLS ClientHello at the edge and computes JA3/JA4 (cipher-suite list + order, extensions, elliptic curves, GREASE values, ALPN, signature algorithms) plus HTTP/2 frame fingerprints (SETTINGS, WINDOW_UPDATE, header order/akamai-h2-fingerprint). A hash that matches no known real-browser profile flags the request immediately and forces the bmak/challenge path. This is the layer that defeats plain Python clients (requests/httpx) and is exactly why curl_cffi (TLS impersonation) is needed.

**Browser Fingerprint**

Collected client-side by bmak and encoded into sensor_data: Canvas hash, WebGL renderer/vendor, AudioContext signature, screen metrics, color depth, font enumeration, navigator plugins/userAgent/languages/hardwareConcurrency/deviceMemory, plus JS-environment probes (prototype chains, function arity, error-message strings, toString of native functions) and timing (navigation/paint/performance entries). Crucially Akamai also READS some of these server-side from the actual request, so JS-only spoofing that disagrees with the real environment is caught.

**Behavioral Telemetry**

sensor_data includes an event array of mouse movement (with realistic jitter), scroll velocity, keystroke timing, touch events, and event cadence relative to page lifecycle. Absent, too-uniform, or physically-impossible event streams (e.g., events with no human timing distribution) raise the bot score.

**Headless Automation Flags**

Detects vanilla automation on the first request. Vectors: navigator.webdriver true, CDP runtime artifacts, headless Chrome WebGL renderer (Google SwiftShader/llvmpipe), missing chrome.* objects, permission/plugin/language inconsistencies, automation-stubbed AudioContext, and the JS-environment probes above. Selenium with stock ChromeDriver and headless Puppeteer/Playwright are caught early. Stealth patches that only override JS properties get caught via native toString() inspection and cross-layer inconsistency.


### Challenge / escalation

**Escalation Trigger**

Bad JA3/JA4 or HTTP/2 fingerprint, missing/invalid _abck, sensor_data that fails validation, datacenter/proxy IP reputation, abnormal request cadence or path patterns, and cross-layer incoherence (e.g., Chrome UA but non-Chrome TLS). Any one can flip a request from 'pass' to 'challenge/block'.


### Evasion verdict

**Headed Playwright Verdict**

Yes, with caveats — this is the standard winning architecture and matches the existing United 'cookie farm' design. A REAL headed Chrome driven by Playwright generates authentic JA3/JA4 + HTTP/2 fingerprints, real Canvas/WebGL/Audio, and a coherent sensor_data payload, so it earns a valid _abck. The proven hybrid pattern: use a Playwright headed 'cookie farm' to solve the Akamai handshake and harvest valid _abck/bm_sz/ak_bmsc cookies, then hand those cookies to a fast HTTP client (curl_cffi) that impersonates the SAME Chrome's TLS/JA3 for the high-volume award-search API calls. The cookies stay valid only while TLS + headers + cadence remain coherent and the _abck hasn't burned; re-farm when it does. Pure stealth-patched browsers without a real GPU/audio stack can still leak.

**Curl Cffi Relevance**

High and essential. curl_cffi (libcurl + BoringSSL TLS impersonation, e.g. impersonate='chrome124') reproduces a real Chrome JA3/JA4 and HTTP/2 fingerprint so the harvested _abck cookie is accepted on subsequent requests. Without TLS impersonation the JA3 mismatch burns the cookie/triggers 403 regardless of correct cookies and headers. curl_cffi cannot itself generate the initial sensor_data/_abck — that needs the Playwright browser — so it is the 'fast replay' half of the hybrid.


### Uncertain / low-confidence fields

- cookie token validation
- passive vs active


---

## securitytrfx / FareNet


### Identification

**Page Scope**

Aeroplan REDEMPTION / award-search pages specifically. Loads securitytrfx.com/js/ac/ac_redemption_v3.7.js and beacons to POST datacore-write.securitytrfx.com/blob/farenet/1/41RC4N (HTTP 202).

**Observed Endpoints**

securitytrfx.com/js/ac/ac_redemption_v3.7.js (the FareNet pixel for Air Canada redemption), POST datacore-write.securitytrfx.com/blob/farenet/1/<id> -> 202 (the DataCore 'blob' write endpoint that streams captured fare/search events), and em-frame.securitytrfx.com (EveryMundo iframe/frame host).


### Detection signals

**Behavioral Telemetry**

FareNet's documented purpose is to capture the lowest fare per search and user SEARCH activity (origin/destination/dates/fare) from the booking/redemption engine and stream it to DataCore for the airTRFX marketing platform. It collects search/fare events and some user-behavior signals tied to fare interaction, NOT adversarial mouse/keystroke bot telemetry. Its job is fare-data harvesting for the airline's own marketing, not bot scoring.

**Cookie Token Validation**

No access-gating token/cookie validation observed. The blob endpoint returns 202 (accepted) — a fire-and-forget data write, not an auth handshake. It does not issue a cookie that the award-search API requires.


### Challenge / escalation

**Passive Vs Active**

Purely passive analytics/data-collection. There is no interactive challenge and no active block. It cannot stop a request to the redemption API.


### Evasion verdict

**Headed Playwright Verdict**

Irrelevant to access. Because securitytrfx/FareNet does not gate or block, a headed Playwright browser is not 'needed' to defeat it — nor is evasion required for access. For a low-profile scraper you may simply NOT load/execute ac_redemption_v3.7.js (e.g., block the securitytrfx.com domain), which avoids emitting fare-search analytics events entirely. The only caution: a real browser normally WOULD load this pixel, so a session that loads everything except securitytrfx could look slightly atypical to a correlation engine — but FareNet itself does no blocking. Treat it as data-collection to optionally suppress, not a detector to beat.

**Curl Cffi Relevance**

None for access. A curl_cffi HTTP client hitting the redemption API simply never executes the pixel, so no FareNet beacon fires. No TLS impersonation is needed to satisfy securitytrfx because it does not validate anything.


### Uncertain / low-confidence fields

- vendor
- tls fingerprint
- browser fingerprint
- headless automation flags
- escalation trigger


---

## Glassbox


### Identification

**Vendor**

Glassbox Digital — session-replay / digital-experience analytics (DXA) platform. Endpoint host acacb = Air Canada's Glassbox tenant.

**Page Scope**

Site-wide on aircanada.com. POSTs to report.acacb.glassboxdigital.io/glassbox/reporting/...

**Observed Endpoints**

report.acacb.glassboxdigital.io/glassbox/reporting/ (batched session-recording event uploads). Tagless auto-capture script injected site-wide.


### Detection signals

**Tls Fingerprint**

Not a TLS gate. Glassbox is a third-party reporting beacon; it does not inspect JA3 or proxy/block origin traffic.

**Browser Fingerprint**

Captures environment/device context as part of session metadata (user agent, screen/viewport, page DOM state) for replay, but it is not an adversarial fingerprinting/anti-bot engine. Its 'tagless' architecture records 100% of interactions and DOM mutations for replay/compliance.

**Behavioral Telemetry**

This is its core data. Glassbox records mouse movement, clicks/taps, scroll, keystroke events (struct/field-level, with masking), form-field interactions, navigation sequences, page dwell/timing, and DOM changes — full session replay. It explicitly flags behaviors like values PASTED instead of typed, rapid navigation loops, extended pauses, erratic navigation, and 'unhuman' field entry. So it captures exactly the behavioral signals that distinguish a human from a script.

**Cookie Token Validation**

No access-gating token. It sets its own session/visitor IDs for replay correlation, but the award-search API does not depend on a Glassbox cookie. The reporting POSTs are fire-and-forget telemetry.


### Challenge / escalation

**Passive Vs Active**

Passive by default. It is analytics/session-replay that RECORDS; it does not itself present a challenge or block a request in real time. Its output can FEED a fraud/bot decision (forensically or via integration), and Glassbox advertises real-time anomaly detection, but the enforcement/block would happen in another system (e.g., Akamai or a fraud engine), not Glassbox.


### Evasion verdict

**Headed Playwright Verdict**

A real headed Playwright browser does NOT need to 'defeat' Glassbox for access (it cannot block you). But because Glassbox records behavior, the same human-like behavior you need for Arkose/Akamai also keeps your Glassbox session looking human: realistic mouse paths, typed (not injected/pasted) input, varied timing, no tight navigation loops. Alternatively, since it is non-blocking, you may block the glassboxdigital.io domain so no session is recorded at all (lowest forensic footprint) — though a totally-absent Glassbox session is itself slightly atypical. Net: not an access barrier; mitigate by either suppressing the beacon or behaving like a human so the recording is unremarkable.

**Curl Cffi Relevance**

None for access. A curl_cffi client never executes the Glassbox JS, so it emits no session recording and Glassbox sees nothing from it (it would only ever 'see' browser-driven sessions). No TLS impersonation needed to satisfy Glassbox.


### Uncertain / low-confidence fields

- headless automation flags
- escalation trigger


---

# How to Scrape Air Canada Aeroplan — Architecture (from the ground up)

> Companion to [`auth-recon.md`](./auth-recon.md) (auth flow + API endpoints/schemas) and the detection-systems research above. This section explains *why* the scraper must be built the way it is, by starting from the existing United design and watching each assumption break.

## 1. The Problem

You want, on demand, the **miles price + seat availability** for an Aeroplan route/date. That data only exists behind a login wall (since March 2025) and is served by an aggressively defended API. The concrete failure modes:

- **You can't just HTTP-GET it.** A naive `requests.get()` dies before the page loads — Akamai reads your TLS handshake at the edge and drops you.
- **You can't just log in with a script.** Login is fronted by Arkose (FunCaptcha) + mandatory 2FA. A headless bot trips the risk score and gets a puzzle it can't solve.
- **You can't copy a token into a fast HTTP client** (what makes United work). The Aeroplan search call is **AWS SigV4-signed with a credential that rotates ~hourly.** A copied token is dead within the hour; forged signatures are rejected instantly.
- **If you brute-force it anyway**, the login wall ties every request to your account, and Air Canada has frozen accounts (points included) for scraping.

So the problem is: *hold a live, authenticated, continuously-signed browser identity, extract structured data through it at a human-like rate, and never trip the two systems that can actually block you.*

## 2. The Architecture

### 2a. The United baseline (what already exists)

```
Playwright (headed real Chrome): logs in once, runs Akamai JS -> earns _abck, "cookie farm" refreshes it
        |  exports cookies (static strings)
        v
curl_cffi (fast HTTP, fakes Chrome TLS): holds a STATIC bearer token, POST FetchAwardCalendar (sync),
        ~300ms/call, burns _abck after ~3-4 calls
```

This works on one quiet assumption: **the only thing that expires fast is the Akamai cookie.** Bearer token is static, the call is synchronous, and there's exactly one defense system.

### 2b. What's actually true for Aeroplan (every assumption breaks)

```
DEFENSE LAYERS (only 2 actually block):
  Akamai Bot Manager    -> site-wide gate (TLS + sensor + _abck)     <- BLOCKS
  Arkose / FunCaptcha   -> login only (risk score -> puzzle)          <- BLOCKS
  FareNet (securitytrfx)-> marketing pixel, gates nothing               ignore
  Glassbox              -> session recorder, gates nothing               ignore

IDENTITY (SAP Gigya): password -> regToken -> 2FA (email/SMS) -> id_token
        |  OIDC consent bridge
        v
THE 4 CREDENTIALS the search API demands:
  1. bearer token       (you're logged in)
  2. x-custom-id-token  (who you are - OIDC id_token)
  3. x-api-key          (this is the AC website)
  4. AWS STS creds      -> recomputed SigV4 signature PER REQUEST     <- the killer

SEARCH API (Amadeus DAPI, behind AWS API Gateway):
  air-calendars  -> SYNC  -> price strip (miles+taxes per date)
  air-bounds     -> ASYNC -> returns a pollId, NOT data
       market-token (get ticket) -> air-bounds (submit) -> polldapi (poll until 60KB flight list)
```

## 3. Why Each Decision Is Correct

**Decision 1 — Use a real browser (Playwright) as the engine, not curl_cffi.** This inverts the United design (where the browser is a cookie accessory). *Remove the browser and login breaks (no Arkose token — an HTTP client can't generate it) and search breaks (no valid SigV4 signature). There is no static credential you can carry.*

**Decision 2 — Make calls from inside the logged-in page (`page.evaluate(fetch)`), not by exporting creds to curl_cffi.** The most important, least obvious choice. To replay externally you'd have to extract rotating AWS STS keys, refresh them (~1h), re-implement SigV4, *and* track the incrementing `ama-session-token`/`ama-client-ref`. *Export to curl_cffi and you've signed up to maintain an AWS signer that drifts to rejection the moment STS rotates.* Calling from inside the page reuses the page's own signer and live credentials for free. (Pure DOM scraping is the slower fallback.)

**Decision 3 — Reuse one authenticated session; minimize logins.** Login is the high-risk, high-signal step (Arkose escalation, 2FA latency, per-account velocity tracking). *Log in per search and you maximize Arkose escalation AND hand AC the cleanest bot signal — login frequency — the exact thing that freezes accounts.*

**Decision 4 — Choose email 2FA, wired to the existing Gmail IMAP responder (`scripts/mfa_responder.py`).** SMS needs telephony plumbing you don't have; email reuses tooling you already own. *Remove the email path and you add a Twilio/phone dependency for zero gain.*

**Decision 5 — Implement the async poll loop (market-token -> air-bounds -> polldapi).** `air-bounds` returns a `pollId`, not flights. *Treat it as synchronous (like FetchAwardCalendar) and you get a 49-byte job ticket and zero flights.* The price strip (`air-calendars`) IS synchronous — the design must treat the two endpoints differently.

**Decision 6 — Low volume, varied timing, aggressive caching, one dedicated low-value account.** The only mitigation for the unbeatable threat: the login wall makes every request account-attributable, and behavioral scoring flags scraper-like *cadence* even with a perfect fingerprint. *Skip the rate discipline and the account freezes regardless of how clean the browser is.*

**Decision 7 — Never auto-solve the Arkose puzzle; engineer to avoid it.** Solving needs a paid human/ML service and crosses a line. A real headed browser + clean residential IP + human pacing keeps the risk score below the puzzle threshold so it stays invisible. *Rely on solving and the system periodically halts on an unsolvable wall.*

**Decision 8 — Treat FareNet and Glassbox as noise (optionally domain-block).** Neither gates anything. *Removing effort here costs nothing.* The same human-like behavior needed for Akamai/Arkose keeps the Glassbox recording unremarkable.

## 4. The Minimum Viable Version

The simplest design that works: **one real headed Chrome (Playwright), logged in once via Gigya with email-2FA auto-answered over IMAP, kept warm, driving the award API from inside the page, polling for the async flight list, at a human rate, from a throwaway account.**

| Component | Why it exists | What breaks without it |
|---|---|---|
| Headed real Chrome (Playwright) | Real TLS/Canvas/WebGL/audio + runs Arkose & AWS signer JS | Akamai drops you; Arkose can't be tokenized; no SigV4 — total failure |
| Gigya login automation | Only door since the March 2025 login wall | No session -> award API returns redirects, no data |
| Email 2FA via Gmail IMAP | Mandatory 2FA every login; email reuses existing tooling | Can't complete login unattended, or bolt on telephony |
| Session reuse / warm context | Logins are the high-risk, Arkose-gated, high-signal step | Every search re-triggers Arkose + 2FA + velocity flags -> freeze |
| In-page `fetch` (`page.evaluate`) | Reuses live STS creds + SigV4 signer + Amadeus session | Re-implement SigV4 + refresh STS hourly + track Amadeus tokens; signatures drift to rejection |
| Async poll loop | `air-bounds` returns a job ticket, not flights | You get an empty `{pollId}` and zero results |
| Rate limit + cache + dedicated account | Login wall makes every call account-attributable; volume is the tell | Account frozen (points included) regardless of fingerprint quality |

**Strip:** the entire curl_cffi "fast replay" half of the United design — counterproductive here, because re-implementing SigV4 externally is more fragile than calling from the page. Effort against FareNet/Glassbox reduces to an optional domain-block. **Cannot strip:** the real browser, session reuse, and in-page calling.

> **Open caveat (unverified):** how long the session/STS credentials stay valid (FlyerTalk anecdote: ~5–10 min idle). If minutes not hours, the "log in once, stay warm" assumption tightens — keep the page active with light background navigation and accept more frequent re-auth. **First thing to measure when building:** a throwaway-account spike that logs in, fires one `air-calendars` from inside the page, and times how long the credentials survive — that single number decides how aggressive the session-reuse loop can be.
