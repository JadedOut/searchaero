# Aeroplan Auth + Award-Search API Reconnaissance

> **Method:** Live recon via authenticated browser session (Claude-in-Chrome) on 2026-05-29, account 5237••••08. A `fetch`/`XHR` interceptor captured request headers (secrets redacted), request bodies, and response bodies for the award-search flow. No credentials or tokens are stored in this doc.
>
> **TL;DR:** Air Canada's award stack is **much** harder to replay than United's. Login is **SAP Gigya (CDC)** gated by **Arkose Labs FunCaptcha**; the search API sits behind an **AWS API Gateway with SigV4-signed requests** and is powered by **Amadeus DAPI**. There are **four** independent bot/telemetry layers. Email-based 2FA is available (good — reuses our Gmail IMAP responder), but SigV4 + rotating STS creds + Arkose make a pure curl_cffi replay far less attractive than it was for United.

## 1. High-level flow

```
Sign in (www.aircanada.com)
  → /clogin/pages/proxy?context=<JWT>          (OIDC context, ~10 min exp)
  → /clogin/pages/login?gig_client_id=...        (Gigya screen-set "Kilo-RegistrationLogin")
  → [Gigya] accounts.login + Arkose FunCaptcha   (passive challenge, can escalate)
  → [Gigya] TFA: getProviders → initTFA → sendVerificationCode (SMS default, email available)
  → enter 6-digit code → finalize TFA
  → /clogin/pages/proxy?mode=afterConsent&consent={UID,clientID,scope,...}&sig=...
  → auth.api-gw.dbaas.aircanada.com/oauth2/idpresponse   (Gigya OIDC → AC gateway tokens)
  → logged in; SPA can now call the loyalty search API (SigV4 + bearer + id-token)
```

## 2. Identity layer — SAP Gigya / Customer Data Cloud

- **Login host:** `login.aircanada.com`
- **Gigya API key (public, client-side):** `3_zA5TRSBDlwybsx_1k8EyncAfJ2b62DJnoxPW60q4X9MqmBDJh1v_8QYaOTG8kZ8S`
- **Screen-set:** `Kilo-RegistrationLogin`
- **OIDC client_id:** `-pwiPl__b08rgQLobNxqF1Ig`
- **Scopes:** `openid profile ffp country device`
- **State token:** every step threads a `regToken` (Gigya registration token). 2FA calls also carry a `gigyaAssertion` JWT (aud `gigyaPhone`/`gigyaEmail`, ~5 min exp).

### Login + 2FA call sequence (all GET to `login.aircanada.com`, `format=json`)
| Step | Endpoint | Notes |
|------|----------|-------|
| 1 | `accounts.getScreenSets?screenSetIDs=Kilo-RegistrationLogin` | Renders login UI |
| 2 | `accounts.getAccountInfo?regToken=…` | After password accepted |
| 3 | `accounts.tfa.getProviders?regToken=…` | Lists 2FA providers |
| 4 | `accounts.tfa.initTFA?provider=gigyaPhone&mode=verify&regToken=…` | Phone path |
| 5 | `accounts.tfa.phone.getRegisteredPhoneNumbers?gigyaAssertion=…` | → `phoneID` |
| 6 | `accounts.tfa.initTFA?provider=gigyaEmail&mode=verify` | **Email path also offered** |
| 7 | `accounts.tfa.email.getEmails?gigyaAssertion=…` | Registered email |
| 8 | `accounts.tfa.phone.sendVerificationCode?…&phoneID=…&method=sms&regToken=…` | Sends SMS |
| 9 | (enter code) `accounts.tfa.phone.completeVerification` → `accounts.tfa.finalizeTFA` | Completes |

**SMS template (observed):** "Code: {code}\nYour Aeroplan® verification code / Votre code de vérification d'Aeroplan®". Code valid 5 minutes.

> **Automation note:** Both phone (SMS) and email 2FA are available. **Email 2FA lets us reuse `scripts/mfa_responder.py` (Gmail IMAP)** — no SMS gateway needed. This is the single biggest feasibility win. The registered email is the account's Gmail.

## 3. Award-search API — Amadeus DAPI behind AWS API Gateway

- **Host:** `akamai-gw.dbaas.aircanada.com` (real host header `api-gw.dbaas.aircanada.com`)
- **Path family:** `/loyalty/dapidynamic/1ASIUDALAC/...` and `/loyalty/dapidynamicplus/1ASIUDALAC/...`
  - `1A` = Amadeus GDS code, `AC` = Air Canada → backend is **Amadeus** (matches the seats.aero lawsuit allegation).
  - `dapidynamic` = base routes (SigV4-signed); `dapidynamicplus` = "plus" routes (bearer only).

### Endpoints (all POST, `application/json`)
| Endpoint | Sync? | Purpose |
|----------|-------|---------|
| `dapidynamic/.../v2/search/air-calendars` | **sync** | Price strip (±N days via `flexibility`). Returns prices directly (~9 KB). |
| `dapidynamicplus/.../v2/reward/market-token` | sync | Returns a market token used as `marketCode` in air-bounds. |
| `dapidynamicplus/.../v2/search/air-bounds` | **async** | Flight-list search. Returns `{pollId: <uuid>}` (~49 B). |
| `/loyalty/polldapi` | poll | `{pollId}` → poll until full flight list ready (~60 KB+). |

**Flight-list flow:** `market-token` → `air-bounds` (→ pollId) → `polldapi` (repeat until done).

### Auth header model (the hard part)
`air-calendars` (most protected) sends:
- `authorization` — **AWS SigV4 signature** (paired with `x-amz-date`, `x-amz-security-token`)
- `x-amz-date`, `x-amz-security-token` — **AWS SigV4 / temporary STS credentials** (rotate, ~1 h)
- `x-custom-id-token` — OIDC **id_token** (user identity, from the Gigya consent bridge)
- `x-api-key` — static API-gateway key
- `ama-session-token`, `ama-client-ref` (`…:1`, `…:2` incrementing) — Amadeus session state

`dapidynamicplus` routes (`air-bounds`, `market-token`) and `polldapi` send `authorization` (bearer) + `x-api-key` + `x-custom-id-token` (+ `ama-session-token` for bounds) — **no SigV4**.

> The temporary AWS creds + SigV4 signature are the crux of any curl_cffi replay: we'd need to (a) source rotating STS creds (likely a Cognito identity pool federated with the Gigya OIDC token — **to confirm**), (b) compute a fresh SigV4 signature per request, (c) supply `x-custom-id-token` + `x-api-key`, (d) maintain the Amadeus session token. Much heavier than United's `x-authorization-api: bearer <token>` + cookies.

### Request body schemas (no secrets; `<CARD>` = Aeroplan number)
**air-calendars** (price strip):
```json
{
  "searchPreferences": { "showUnavailableEntries": false, "showMilesPrice": true },
  "corporateCodes": ["REWARD"],
  "travelers": [{ "passengerTypeCode": "ADT" }],
  "currencyCode": "CAD",
  "itineraries": [{
    "originLocationCode": "YYZ", "destinationLocationCode": "LAX",
    "departureDateTime": "2026-06-03T00:00:00.000",
    "isRequestedBound": true, "flexibility": 2,
    "commercialFareFamilies": ["RWDECO", "RWDPRECC", "RWDBUS", "RWDFIRST"]
  }],
  "frequentFlyer": { "cardNumber": "<CARD>", "companyCode": "AC", "priorityCode": "9" }
}
```
**market-token:**
```json
{ "itineraries": [{ "originLocationCode": "YYZ", "destinationLocationCode": "LAX",
  "departureDateTime": "2026-06-02T00:00:00.000" }], "countryOfResidence": "CA" }
```
**air-bounds** (flight list):
```json
{
  "searchPreferences": { "showSoldOut": false, "showMilesPrice": true, "marketCode": "TBO" },
  "corporateCodes": ["REWARD"],
  "travelers": [{ "passengerTypeCode": "ADT" }],
  "currencyCode": "CAD",
  "itineraries": [{
    "originLocationCode": "YYZ", "destinationLocationCode": "LAX",
    "departureDateTime": "2026-06-02T00:00:00.000",
    "isRequestedBound": true, "commercialFareFamilies": ["REWARD"]
  }],
  "frequentFlyer": { "cardNumber": "<CARD>", "companyCode": "AC", "priorityCode": "9" }
}
```
**polldapi:** `{ "pollId": "<uuid>" }`

Fare families: `RWDECO` economy, `RWDPRECC` premium economy, `RWDBUS` business, `RWDFIRST` first.

### Response schemas
**air-calendars** → `{ data[], meta, dictionaries }`. One `data` entry per `departureDate`:
- Miles: `prices.unitPrices[].milesConversion.convertedMiles.base` (e.g. `12500`)
- Taxes: `...convertedMiles.totalTaxes` and `prices...totalTaxes` in **cents CAD** (e.g. `17060` = CA$170.60)
- `fareInfos[]` with `fareClass`, etc.

**air-bounds / polldapi** → `{ data: { airBoundGroups[] }, dictionaries }`:
- `airBoundGroups[].boundDetails`: `originLocationCode`, `destinationLocationCode`, `duration` (s), `ranking`, `segments[].flightId` (e.g. `SEG-AC785-YYZLAX-2026-06-02-0830`)
- `airBoundGroups[].airBounds[]` (each bookable fare):
  - `airBoundId`, `fareFamilyCode` (e.g. `STANDARD`)
  - `availabilityDetails[]`: `flightId`, `cabin` (`eco`), `bookingClass` (`H`), `statusCode` (`HK`), **`quota`** (seats left, e.g. `9`)
  - `fareInfos[]`: `fareClass`, `ticketDesignator`, `corporateCode`, `flightIds`
  - `prices.unitPrices[]` with `milesConversion`
- `dictionaries` resolves flight/location/aircraft IDs (standard Amadeus pattern).

## 4. Bot protection — four layers (vs United's one)
| Layer | Scope | Evidence |
|-------|-------|----------|
| **Arkose Labs (FunCaptcha)** | Login | `aircanada-api.arkoselabs.com/v2/8BAAFE0D-A867-4813-96D5-ABAF2C0D9B93/` — passed passively this run, can escalate to interactive (we will **not** auto-solve) |
| **Akamai Bot Manager** | `www.aircanada.com` | Random-path sensor beacons (`POST …/YKmhcKRp1`, 201/202) |
| **securitytrfx / "farenet"** | Redemption pages | `securitytrfx.com/js/ac/ac_redemption_v3.7.js`, `datacore-write.securitytrfx.com/blob/farenet/…` — fare-scraping-specific telemetry United lacks |
| **Glassbox** | Site-wide | `report.acacb.glassboxdigital.io` session replay (records mouse/scroll/timing) |

## 5. Implications vs the United hybrid

| Aspect | United | Aeroplan |
|--------|--------|----------|
| Login | MP# + password + SMS | Gigya + **Arkose FunCaptcha** + SMS/**email** 2FA |
| 2FA automation | `mfa_responder.py` (Gmail IMAP) | ✅ same, **if email 2FA** |
| Search API auth | `x-authorization-api: bearer` + Akamai cookies | **AWS SigV4 + STS creds + id_token + x-api-key + Amadeus session** |
| Search call | sync `FetchAwardCalendar` | calendars sync; **bounds async (poll)** |
| Backend | United internal | **Amadeus DAPI** |
| Bot layers | Akamai | Arkose + Akamai + securitytrfx + Glassbox |
| Account risk | none observed | **high** — login wall enables per-account throttling; deactivations documented |

**Architecture read:** A pure curl_cffi replay is far less attractive here — SigV4 with rotating STS creds and the Arkose-gated login mean **Playwright must do more of the work** (or all of it). Two viable paths:
1. **Full Playwright** (recommended first): drive the SPA, intercept the `polldapi`/`air-calendars` JSON responses (the AwardWiz approach). Slower (~5–10 s/search) but robust; no need to reverse SigV4.
2. **Playwright session + in-page `fetch`**: keep a logged-in Playwright context and call the API *from inside the page* (reusing the page's own SigV4 signer / tokens via `page.evaluate`), rather than exporting cookies to curl_cffi. Avoids re-implementing SigV4 while keeping calls fast.

## 6. Open questions / next steps
- **STS cred source:** confirm whether temp AWS creds come from a Cognito identity pool federated with the Gigya OIDC token, and the rotation interval.
- **Session/token lifetime:** measure how long the bearer/id_token/STS creds stay valid before re-auth (FlyerTalk anecdote: ~5–10 min idle logout — verify empirically).
- **Arkose escalation:** how often does login trigger an *interactive* FunCaptcha? That gates unattended runs.
- **Option-2 spike:** prototype calling `air-calendars` from inside a logged-in Playwright page via `page.evaluate(fetch(...))` to skip SigV4 re-implementation.
- **Round-trip / multi-pax:** capture `tripType=R` and ADT>1 payloads (only one-way / 1 ADT mapped so far).
- **Account safety:** use a low-value/dedicated Aeroplan account; keep volume minimal; cache aggressively.
