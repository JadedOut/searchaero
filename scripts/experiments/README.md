# United Award Search API Experiments

Empirical testing of curl_cffi against United's award search API.

## Prerequisites

- Python 3.10+
- A MileagePlus account linked to a Gmail address (needed for login MFA)

## Obtaining a Bearer Token

United requires Gmail-based MFA for login, so we obtain the bearer token manually via Chrome DevTools:

1. Open Chrome and navigate to [united.com](https://www.united.com).
2. Log in with your MileagePlus credentials. Complete the Gmail MFA prompt.
3. Open DevTools (`F12`) and switch to the **Network** tab.
4. On united.com, perform any award search:
   - Enter an origin and destination (e.g., YYZ to LAX).
   - Check **"Book with miles"**.
   - Click **Search**.
5. In the Network tab, find a request to `/api/flight/FetchAwardCalendar` or `/api/flight/FetchFlights`.
6. Click the request, open the **Headers** tab, and locate the `x-authorization-api` header.
7. Copy the full value (it starts with `bearer `).

## Setup

1. Copy the example env file and paste your token:

   ```bash
   cp .env.sample .env
   ```

   Edit `.env` and replace the placeholder with your actual bearer token.

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Running the Experiments

```bash
python test_curl_cffi.py
```

## What to Expect

The script will run a series of experiments against the United API and print PASS/FAIL verdicts for each one. A successful run confirms that curl_cffi can bypass TLS fingerprinting and that your bearer token is valid.

## Token Expiry

Tokens last several hours. Get a fresh one if you see 401 errors. It is recommended to grab a new token at the start of each working session.
