"""Analyze burn-in JSONL logs and produce a structured report.

Reads one or more JSONL log files produced by the burn-in runner
and prints a formatted text report covering success rates, errors,
response times, per-route breakdowns, and hourly trends.
"""

import argparse
import glob
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_records(paths):
    """Load and parse JSONL records from one or more file paths.

    Each path may be a literal file or a glob pattern.
    Returns a list of parsed dicts.
    """
    records = []
    expanded = []
    for p in paths:
        matches = glob.glob(p)
        if not matches:
            print(f"Warning: no files matched pattern '{p}'", file=sys.stderr)
        expanded.extend(matches)

    if not expanded:
        return records

    for filepath in sorted(set(expanded)):
        try:
            with open(filepath) as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        print(
                            f"Warning: {filepath}:{lineno}: invalid JSON, skipping ({exc})",
                            file=sys.stderr,
                        )
        except OSError as exc:
            print(f"Warning: cannot open {filepath}: {exc}", file=sys.stderr)

    return records


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(record, key, default=0):
    """Safely get a value from a record, returning *default* if missing."""
    return record.get(key, default)


def fmt_duration(seconds):
    """Format a duration in seconds to a human-readable string."""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _parse_error_type(error_string):
    """Extract the error type token from an error string.

    Expected format examples:
        "Window 3: timeout"
        "Window 7: cookie_burn"
        "Window 1: cloudflare_block"
        "Window 5: rate_limit"

    Falls back to the full (lowered, stripped) string if no colon is found.
    """
    if ":" in error_string:
        after_colon = error_string.split(":", 1)[1].strip()
        # Take just the first token (word) as the type
        match = re.match(r"[\w_]+", after_colon)
        if match:
            return match.group(0).lower()
        return after_colon.lower()
    return error_string.strip().lower()


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def section_run_overview(records):
    """Print the Run Overview section."""
    print("=" * 60)
    print("  RUN OVERVIEW")
    print("=" * 60)

    timestamps = []
    for r in records:
        ts_str = r.get("timestamp")
        if ts_str:
            try:
                timestamps.append(datetime.fromisoformat(ts_str))
            except ValueError:
                pass

    if not timestamps:
        print("  No valid timestamps found.")
        print()
        return 0.0

    start = min(timestamps)
    end = max(timestamps)
    total_duration = (end - start).total_seconds()
    cycles = {r.get("cycle", 0) for r in records}
    total_cycles = max(cycles) if cycles else 0
    total_scrapes = len(records)
    unique_routes = {r.get("route", "unknown") for r in records}

    print(f"  Start time:          {start.isoformat()}")
    print(f"  End time:            {end.isoformat()}")
    print(f"  Total duration:      {fmt_duration(total_duration)}")
    print(f"  Total cycles:        {total_cycles}")
    print(f"  Total route scrapes: {total_scrapes}")
    print(f"  Unique routes:       {len(unique_routes)}")
    print()
    return total_duration


def section_success_metrics(records):
    """Print the Success Metrics section."""
    print("=" * 60)
    print("  SUCCESS METRICS")
    print("=" * 60)

    total_ok = sum(_get(r, "windows_ok") for r in records)
    total_failed = sum(_get(r, "windows_failed") for r in records)
    total_windows = total_ok + total_failed

    if total_windows > 0:
        window_rate = (total_ok / total_windows) * 100
    else:
        window_rate = 0.0

    successful_routes = sum(1 for r in records if _get(r, "windows_ok") >= 8)
    if records:
        route_rate = (successful_routes / len(records)) * 100
    else:
        route_rate = 0.0

    total_found = sum(_get(r, "solutions_found") for r in records)
    total_stored = sum(_get(r, "solutions_stored") for r in records)
    total_rejected = sum(_get(r, "solutions_rejected") for r in records)

    print(f"  Window success rate: {window_rate:.1f}%  ({total_ok}/{total_windows})")
    print(f"  Route success rate:  {route_rate:.1f}%  ({successful_routes}/{len(records)} routes with >= 8/12 windows OK)")
    print(f"  Solutions found:     {total_found}")
    print(f"  Solutions stored:    {total_stored}")
    print(f"  Solutions rejected:  {total_rejected}")
    print()


def section_session_events(records):
    """Print the Session Events section."""
    print("=" * 60)
    print("  SESSION EVENTS")
    print("=" * 60)

    expiries = [
        r for r in records if r.get("session_expired", False)
    ]

    if not expiries:
        print("  No session expiry events detected.")
        print()
        return

    expiry_times = []
    for r in expiries:
        ts_str = r.get("timestamp")
        if ts_str:
            try:
                expiry_times.append(datetime.fromisoformat(ts_str))
            except ValueError:
                pass
    expiry_times.sort()

    print(f"  Total expiry events: {len(expiry_times)}")
    print()
    for i, ts in enumerate(expiry_times):
        line = f"    {i + 1}. {ts.isoformat()}"
        if i > 0:
            gap = (ts - expiry_times[i - 1]).total_seconds()
            line += f"  (gap: {fmt_duration(gap)})"
        print(line)
    print()


def section_error_breakdown(records):
    """Print the Error Breakdown section.

    Parses error strings from the ``errors`` list in each record to
    extract and count error types.
    """
    print("=" * 60)
    print("  ERROR BREAKDOWN")
    print("=" * 60)

    error_counts = defaultdict(int)
    total_errors = 0
    for r in records:
        for err in r.get("errors", []):
            etype = _parse_error_type(err)
            error_counts[etype] += 1
            total_errors += 1

    if total_errors == 0:
        print("  No errors recorded.")
        print()
        return

    print(f"  Total errors: {total_errors}")
    print()
    print(f"    {'Error Type':<24} {'Count':>7} {'Pct':>8}")
    print(f"    {'----------':<24} {'-----':>7} {'---':>8}")

    for etype, count in sorted(error_counts.items(), key=lambda x: -x[1]):
        pct = (count / total_errors) * 100
        print(f"    {etype:<24} {count:>7} {pct:>7.1f}%")
    print()


def section_response_time(records):
    """Print the Response Time Stats section."""
    print("=" * 60)
    print("  RESPONSE TIME STATS")
    print("=" * 60)

    durations = [
        _get(r, "duration_seconds", None)
        for r in records
        if r.get("duration_seconds") is not None
    ]

    if not durations:
        print("  No duration data available.")
        print()
        return

    durations_sorted = sorted(durations)
    min_d = durations_sorted[0]
    max_d = durations_sorted[-1]
    avg_d = statistics.mean(durations)
    median_d = statistics.median(durations)
    p95_index = min(int(0.95 * len(durations_sorted)), len(durations_sorted) - 1)
    p95_d = durations_sorted[p95_index]

    print(f"  Min:     {min_d:>8.1f}s")
    print(f"  Average: {avg_d:>8.1f}s")
    print(f"  Median:  {median_d:>8.1f}s")
    print(f"  P95:     {p95_d:>8.1f}s")
    print(f"  Max:     {max_d:>8.1f}s")
    print()


def section_per_route(records):
    """Print the Per-Route Breakdown section.

    Shows each unique route with times scraped, window success rate, and
    average duration.  Sorted by window success rate ascending (worst first).
    """
    print("=" * 60)
    print("  PER-ROUTE BREAKDOWN")
    print("=" * 60)

    route_data = defaultdict(list)
    for r in records:
        route_data[r.get("route", "unknown")].append(r)

    # Build per-route stats
    route_stats = []
    for route, recs in route_data.items():
        n = len(recs)
        total_ok = sum(_get(r, "windows_ok") for r in recs)
        total_fail = sum(_get(r, "windows_failed") for r in recs)
        total_w = total_ok + total_fail
        win_rate = (total_ok / total_w * 100) if total_w > 0 else 0.0
        avg_dur = statistics.mean(
            _get(r, "duration_seconds", 0) for r in recs
        )
        route_stats.append((route, n, win_rate, avg_dur))

    # Sort by success rate ascending (worst routes first)
    route_stats.sort(key=lambda x: x[2])

    print(f"  {'Route':<12} {'Scrapes':>8} {'Win Rate':>10} {'Avg Dur':>10}")
    print(f"  {'-' * 12} {'-' * 8} {'-' * 10} {'-' * 10}")

    for route, n, win_rate, avg_dur in route_stats:
        print(
            f"  {route:<12} {n:>8} {win_rate:>9.1f}% {avg_dur:>9.1f}s"
        )
    print()


def section_hourly_trend(records, total_duration_seconds):
    """Print the Hourly Trend section.

    Skipped entirely if the total run duration is less than 1 hour.
    """
    if total_duration_seconds < 3600:
        return

    print("=" * 60)
    print("  HOURLY TREND")
    print("=" * 60)

    hourly = defaultdict(list)
    for r in records:
        ts_str = r.get("timestamp")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            continue
        hour_key = ts.strftime("%Y-%m-%d %H:00")
        hourly[hour_key].append(r)

    if not hourly:
        print("  No hourly data available.")
        print()
        return

    print(f"  {'Hour':<18} {'Scrapes':>8} {'Win Rate':>10} {'Avg Dur':>10}")
    print(f"  {'-' * 18} {'-' * 8} {'-' * 10} {'-' * 10}")

    for hour in sorted(hourly.keys()):
        recs = hourly[hour]
        n = len(recs)
        total_ok = sum(_get(r, "windows_ok") for r in recs)
        total_fail = sum(_get(r, "windows_failed") for r in recs)
        total_w = total_ok + total_fail
        rate = (total_ok / total_w * 100) if total_w > 0 else 0.0
        avg_dur = statistics.mean(
            _get(r, "duration_seconds", 0) for r in recs
        )
        print(
            f"  {hour:<18} {n:>8} {rate:>9.1f}% {avg_dur:>9.1f}s"
        )
    print()


# ---------------------------------------------------------------------------
# Report orchestration
# ---------------------------------------------------------------------------

def print_report(records):
    """Print the full analysis report."""
    print()
    total_duration = section_run_overview(records)
    section_success_metrics(records)
    section_session_events(records)
    section_error_breakdown(records)
    section_response_time(records)
    section_per_route(records)
    section_hourly_trend(records, total_duration)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Analyze burn-in JSONL logs and produce a structured report. "
            "Accepts one or more JSONL file paths (glob patterns supported)."
        ),
    )
    parser.add_argument(
        "logfiles",
        nargs="+",
        metavar="LOGFILE",
        help="Path(s) to JSONL log file(s). Glob patterns are expanded.",
    )
    args = parser.parse_args()

    records = load_records(args.logfiles)
    if not records:
        print("No records found in the provided log file(s).")
        sys.exit(1)

    # Sort all records by timestamp for consistent ordering
    records.sort(key=lambda r: r.get("timestamp", ""))

    print_report(records)


if __name__ == "__main__":
    main()
