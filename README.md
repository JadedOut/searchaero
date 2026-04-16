# Searchaero

Track United MileagePlus award pricing for $0. Tell your agent where you want to go, and it handles the entire workflow: it scrapes United Airlines' Mileageplus, interprets results, graphs trends, watches for price drops, and notifies you via email or ntfy. Totally local, no API keys, and no subscriptions.

## Scope

- **Airline:** United MileagePlus only (Aeroplan coming soon!)
- **Routes:** Any origin/destination United serves
- **Coverage:** Full 337-day booking window, all cabin types
- **Not supported:** Partner awards, cash fares

## Quick start

Open Claude Code and paste this. Claude does the rest.

> Install searchaero: run `uv tool install searchaero` to install the CLI, then run `searchaero setup` to configure credentials and verify Playwright. After setup, ask me to find cheap flights.

Requirements: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), Python 3.13+, [uv](https://docs.astral.sh/uv/)

That's it. The `/flights` skill ships with this repo — when you clone it, Claude already knows how to use searchaero.

<details>
<summary>Manual install (without Claude)</summary>

### 1. Install

```bash
uv tool install searchaero
```

> **Why uv?** One dependency (`bezier`) doesn't ship Python 3.13 wheels yet. `uv` handles the source build automatically; regular `pip` fails without workarounds.

Or install from source:

```bash
git clone https://github.com/JadedOut/searchaero.git
cd searchaero
uv tool install .
```

### 2. Set up credentials

```bash
searchaero setup
```

Creates the database, checks Playwright, and prompts for your MileagePlus number and password. Just your MP number and password — no API keys needed. If all three checks show green, you're ready.

> **Heads up:** United requires verification when you log in. Each time you run a scrape, you'll be prompted for a code (SMS by default, or automatic via email — see [Getting Started](docs/getting-started.md)).

### 3. Ask Claude

In Claude Code, just ask:

```
What's the cheapest flight from Toronto to LA next month?
```

Or invoke the skill directly with `/flights`.

</details>

## See it work

You ask a question. The agent checks cached data, scrapes if needed, handles MFA, and presents the answer. One skill, end to end.

```
You:    What's the cheapest business class from Toronto to London next month?

Claude: Checking cached data... no results for YYZ-LHR.
        Starting a fresh scrape — this takes about 2 minutes.
        [MFA code requested — enter the 6-digit code from your phone]
        YYZ-LHR: 342 found, 342 stored across 337 days

        Cheapest: 
        ┌──────────┬──────────┬─────────┬──────────┐
        │ Date     │ Cabin    │ Miles   │ Stops    │
        ├──────────┼──────────┼─────────┼──────────┤
        │ Jul 12   │ Business │ 55,000  │ Nonstop  │
        │ Jul 15   │ Business │ 45,000  │ 1 stop   │
        │ Jul 19   │ Business │ 60,000  │ Nonstop  │
        └──────────┴──────────┴─────────┴──────────┘

You:    Show me the price trend as a graph.

Claude: YYZ -> LHR  |  Business  |  Price Trend

         80,000 ┤
         75,000 ┤          ╭╮
         70,000 ┤       ╭──╯│
         65,000 ┤      ╭╯   ╰╮
         60,000 ┼──╮  ╭╯     ╰╮
         55,000 ┤  ╰╮╭╯       │
         50,000 ┤   ╰╯        ╰╮
         45,000 ┤              ╰──
                Jul 01    Jul 15    Jul 29

        Min: 45,000 mi  Avg: 59,167 mi  8 dates

You:    Set up a watch — notify me if business drops under 50K.
Claude: [runs searchaero watch add YYZ LHR --max-miles 50000 --cabin business]
        Done. I'll check every 12 hours and notify you via ntfy.
```

**Example prompts:**
- *"Scrape fresh data for cheapest business class from New York to London in July"*
- *"Show me a price chart for YYZ to LAX for the next year"*
- *"Find deals under 30K miles from any airport I've scraped"*
- *"Set up a watchlist for paris to sanfran, business class, under 70K miles"*

## CLI reference

The agent uses these commands under the hood. You can also run them directly:

| Action | Command |
|--------|---------|
| Check cache | `searchaero query ORIG DEST --json` |
| Show table | `searchaero query ORIG DEST` |
| Show graph | `searchaero query ORIG DEST --graph` |
| Show summary | `searchaero query ORIG DEST --summary` |
| Find deals | `searchaero deals --json` |
| DB status | `searchaero status --json` |
| Scrape fresh | `searchaero search ORIG DEST --mfa-file --mfa-method email` |
| Add alert | `searchaero alert add ORIG DEST --max-miles N` |
| Check alerts | `searchaero alert check --json` |
| Add watch | `searchaero watch add ORIG DEST --max-miles N` |
| Check watches | `searchaero watch check --json` |
| Diagnostics | `searchaero doctor` |

## How scraping works

1. Seataero opens a Chromium browser via Playwright and logs into united.com with your MP number. Playwright is required to avoid getting blocked by United's bot detectors.
2. United sends a verification code (defaults to SMS) — the agent will ask you for the code or handle it automatically via email (see [Getting Started](docs/getting-started.md)) 
3. Once logged in, searchaero uses curl_cffi to scrape the award calendar API (one request returns ~30 days of pricing)
4. Results are stored in SQLite. Subsequent queries are read-only (no scraping needed)
5. MFA is required once per scrape invocation — if you scrape multiple routes in one batch, you'll only be prompted once

**Rate limiting:** Seataero adds delays between requests to avoid triggering United's bot detection. For recurring scrapes, use a minimum interval of **10 minutes** between runs.

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `BROWSER CRASH detected` | United's Akamai bot detection blocked your IP | Wait 10 minutes and retry, or use `--proxy` |
| MFA code times out | The 5-minute code window expired | Re-run the search — United will send a new code |
| `No availability found` | No scraped data for this route yet | Ask your agent to scrape it, or run `searchaero search ORIGIN DEST` |
| Database errors | Corrupted SQLite file | Delete `~/.searchaero/data.db` and run `searchaero setup` |
| Repeated Akamai blocks | Your home IP is flagged | Wait 10–15 minutes, or use `--proxy`. See `searchaero help proxy` |

Run `searchaero doctor` for a comprehensive diagnostic check.

## More documentation

- [Getting Started](docs/getting-started.md) — full walkthrough from install to first query
- [CLI Reference](docs/commands.md) — every command and flag
- [FAQ](docs/faq.md) — common questions and troubleshooting
- [Push Notifications](docs/getting-started.md#step-6-set-up-price-alerts-optional) — set up ntfy for phone alerts
