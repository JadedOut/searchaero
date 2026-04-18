# CLI Command Reference

Quick reference for every `searchaero` command. All commands support `--json` for machine-readable output and `--db-path` to override the default database.

## Setup & Diagnostics

### `searchaero setup`

Check environment and create the database. Prompts for credentials interactively if `.env` is missing.

```bash
searchaero setup
searchaero setup --json
```

### `searchaero doctor`

Run comprehensive diagnostics: database integrity, Playwright, credentials, ntfy, data freshness.

```bash
searchaero doctor
```

### `searchaero status`

Show database statistics and route coverage.

```bash
searchaero status
searchaero status --json
```

### `searchaero help <topic>`

Focused mini-guides on specific topics.

```bash
searchaero help              # list all topics
searchaero help mfa          # SMS verification
searchaero help proxy        # IP rotation / Akamai blocks
searchaero help watches      # Watchlist and notifications
searchaero help alerts       # Price alerts
searchaero help scraping     # How scraping works
```

---

## Scraping

### `searchaero search`

Scrape award availability from United.

```bash
# Single route (~2 min)
searchaero search YYZ LAX

# Batch from file
searchaero search --file routes/canada_test.txt

# Parallel workers
searchaero search --file routes/canada_us_all.txt --workers 3
```

| Flag | Default | Description |
|------|---------|-------------|
| `ORIGIN DEST` | — | IATA airport codes (e.g., YYZ LAX) |
| `--file, -f` | — | Route list file (one `ORIGIN DEST` per line) |
| `--workers, -w` | 1 | Parallel browser workers (requires `--file`) |
| `--headless` | off (single), on (batch) | Run browser without GUI |
| `--proxy` | — | SOCKS5/HTTP proxy URL |
| `--delay` | 3.0 | Seconds between API calls |
| `--mfa-file` | off | Use file-based MFA handoff instead of stdin |
| `--skip-scanned` | on | Skip already-scraped routes in parallel mode |
| `--json` | off | Machine-readable output |

---

## Querying

### `searchaero query`

Query cached availability data.

```bash
# Basic query
searchaero query YYZ LAX

# Filter by cabin and sort by price
searchaero query YYZ LAX --cabin business --sort miles

# Date range
searchaero query YYZ LAX --from 2026-06-01 --to 2026-08-31

# Specific date detail
searchaero query YYZ LAX --date 2026-07-15

# Price trend (compact sparkline — great for chat/email)
searchaero query YYZ LAX --sparkline
searchaero query YYZ LAX --cabin business --sparkline

# Price trend (full ASCII chart — wide, best in expanded view)
searchaero query YYZ LAX --graph

# Multi-program table (shows per-program availability)
searchaero query YYZ LAX --table-view programs

# Price history
searchaero query YYZ LAX --history
searchaero query YYZ LAX --date 2026-07-15 --history

# Auto-refresh stale data
searchaero query YYZ LAX --refresh

# Export formats
searchaero query YYZ LAX --json
searchaero query YYZ LAX --csv
```

| Flag | Default | Description |
|------|---------|-------------|
| `ORIGIN DEST` | — | Required. IATA airport codes |
| `--date, -d` | — | Single date detail (YYYY-MM-DD) |
| `--from` | — | Start of date range (inclusive) |
| `--to` | — | End of date range (inclusive) |
| `--cabin, -c` | all | `economy`, `business`, or `first` |
| `--sort, -s` | date | Sort by `date`, `miles`, or `cabin` |
| `--sparkline` | off | Compact sparkline trend with low/high/avg stats |
| `--graph` | off | Full ASCII price chart (wide) |
| `--summary` | off | Deal summary card |
| `--table-view` | — | Alternative table layout: `programs` (multi-program flat table) |
| `--history` | off | Show price history instead of current snapshot |
| `--refresh` | off | Auto-scrape if data is stale/missing |
| `--ttl` | 12.0 | Hours before data is considered stale |
| `--fields` | all | Comma-separated fields for JSON output |
| `--csv` | off | CSV output (mutually exclusive with other format flags) |
| `--json` | off | JSON output |

> **Note:** `--sparkline`, `--graph`, `--summary`, `--table-view`, `--csv`, and `--json` are mutually exclusive.

---

## Price Alerts

One-shot checks against cached data. No daemon needed.

### `searchaero alert add`

```bash
searchaero alert add YYZ LAX --max-miles 70000
searchaero alert add YYZ LAX --max-miles 70000 --cabin business --from 2026-06-01 --to 2026-08-31
```

| Flag | Description |
|------|-------------|
| `ORIGIN DEST` | Required |
| `--max-miles` | Required. Trigger when price is at or below this |
| `--cabin, -c` | Optional cabin filter |
| `--from` / `--to` | Optional travel date window |

### `searchaero alert list`

```bash
searchaero alert list          # active alerts only
searchaero alert list --all    # include expired
searchaero alert list --json
```

### `searchaero alert check`

```bash
searchaero alert check
searchaero alert check --json
```

### `searchaero alert remove`

```bash
searchaero alert remove 1
```

---

## Watchlist & Notifications

Automated monitoring with push notifications via [ntfy.sh](https://ntfy.sh).

### `searchaero watch setup`

```bash
searchaero watch setup --ntfy-topic searchaero-a7f3b9c2e1d4f856
searchaero watch setup --ntfy-topic my-topic --ntfy-server https://my-ntfy.example.com
searchaero watch setup --gmail-sender me@gmail.com --gmail-recipient you@example.com
```

### `searchaero watch add`

```bash
searchaero watch add YYZ LAX --max-miles 20000
searchaero watch add YYZ LAX --max-miles 70000 --cabin business --every 6h
```

| Flag | Default | Description |
|------|---------|-------------|
| `ORIGIN DEST` | — | Required |
| `--max-miles` | — | Required. Notification threshold |
| `--cabin, -c` | all | Cabin filter |
| `--from` / `--to` | — | Travel date window |
| `--every` | 12h | Check frequency: `hourly`, `6h`, `12h`, `daily`, `twice-daily` |

### `searchaero watch list`

```bash
searchaero watch list
searchaero watch list --all    # include expired
```

### `searchaero watch check`

```bash
searchaero watch check
searchaero watch check --no-scrape   # skip scraping stale routes
searchaero watch check --no-notify   # skip sending notifications
```

### `searchaero watch remove`

```bash
searchaero watch remove 1
```

### `searchaero watch run`

Start the watch daemon (foreground, Ctrl+C to stop). Checks watches on their schedule.

```bash
searchaero watch run
```

---

## Other

### `searchaero schema`

Print command schemas for agent introspection.

```bash
searchaero schema              # all commands
searchaero schema query        # single command
```

---

## Agent Skill

The `/flights` agent skill teaches Claude Code how to use these CLI commands automatically. You don't need to memorize flags — just ask about flights in natural language.

The skill file lives at `.claude/skills/flights/SKILL.md`.
