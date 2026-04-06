"""Hybrid architecture validation experiments.

Tests the curl_cffi + Playwright cookie farm approach to validate that
proactive cookie refresh prevents Akamai burns and achieves 90%+ success.

Usage:
    python test_hybrid.py                    # run all experiments
    python test_hybrid.py --experiment 1     # single call validation
    python test_hybrid.py --experiment 2     # cookie refresh cycle
    python test_hybrid.py --experiment 3     # full reliability test
    python test_hybrid.py --experiment 4     # burn recovery test
"""

import argparse
import time
from datetime import datetime, timedelta

from cookie_farm import CookieFarm
from hybrid_scraper import HybridScraper
import united_api


# ---------------------------------------------------------------------------
# Test routes
# ---------------------------------------------------------------------------

ROUTES = [
    ("YYZ", "LAX"), ("YYZ", "SFO"), ("YYZ", "ORD"),
    ("YVR", "LAX"), ("YUL", "JFK"), ("YYZ", "DEN"),
    ("YYC", "SEA"), ("YOW", "EWR"), ("YYZ", "IAH"),
    ("YVR", "SFO"),
]


# ---------------------------------------------------------------------------
# Experiment 1 — Single Call
# ---------------------------------------------------------------------------


def experiment_1(farm: CookieFarm) -> bool:
    """Single call validation: start scraper, fetch one route, check result."""
    print("\n")
    print("=" * 60)
    print("Experiment 1 — Single Call")
    print("=" * 60)

    origin, destination = "YYZ", "LAX"
    depart_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    print(f"Route: {origin} -> {destination}")
    print(f"Departure: {depart_date}")
    print()

    scraper = HybridScraper(farm, refresh_interval=2)
    scraper.start()

    try:
        result = scraper.fetch_calendar(origin, destination, depart_date)
    finally:
        scraper.stop()

    # Print result details
    status = result["status_code"] or "ERR"
    print(f"\nHTTP Status: {status}")
    print(f"Response time: {result['elapsed_ms']:.0f}ms")
    print(f"Cookie refreshed: {'yes' if result['cookie_refreshed'] else 'no'}")

    if result["success"]:
        solutions = united_api.parse_calendar_solutions(result["data"])
        dates_with_data = len(set(s["date"] for s in solutions if s["date"]))

        print(f"Solutions parsed: {len(solutions)} records")
        print(f"Days with pricing data: {dates_with_data}")

        if solutions:
            print("\nSample records:")
            for s in solutions[:5]:
                print(f"  {s['date']} | {s['cabin']:20s} | "
                      f"{s['miles']:,.0f} miles | ${s['taxes_usd']:.2f} tax")

        print(f"\nPASS: Hybrid scraper fetched award calendar data. "
              f"{dates_with_data} days with pricing returned.")
        return True
    else:
        print(f"\nFAIL: {result['error']}")
        return False


# ---------------------------------------------------------------------------
# Experiment 2 — Cookie Refresh Cycle (6 calls)
# ---------------------------------------------------------------------------


def experiment_2(farm: CookieFarm) -> bool:
    """Cookie refresh cycle: 6 calls with refresh_interval=2, verify refreshes."""
    print("\n")
    print("=" * 60)
    print("Experiment 2 — Cookie Refresh Cycle (6 calls, refresh_interval=2)")
    print("=" * 60)

    scraper = HybridScraper(farm, refresh_interval=2)
    scraper.start()

    call_results = []
    routes_subset = ROUTES[:6]

    try:
        for i, (orig, dest) in enumerate(routes_subset):
            call_num = i + 1
            days_out = 30 * call_num
            depart_date = (datetime.now() + timedelta(days=days_out)).strftime("%Y-%m-%d")

            print(f"\n  #{call_num} {orig}-{dest} ({depart_date}, +{days_out}d)...")
            result = scraper.fetch_calendar(orig, dest, depart_date)

            # Strip internal fields
            result.pop("_exception", None)
            result.pop("_response", None)
            result["call_num"] = call_num
            result["route"] = f"{orig}-{dest}"

            call_results.append(result)

            # Live feedback
            status = result["status_code"] or "ERR"
            valid_str = "YES" if result["success"] else "NO"
            refresh_str = "yes" if result["cookie_refreshed"] else ""
            print(f"  #{call_num} {orig}-{dest}: {status} | "
                  f"{result['elapsed_ms']:.0f}ms | Valid: {valid_str} | "
                  f"Solutions: {result['solutions_count']}"
                  + (f" | Refreshed: {refresh_str}" if refresh_str else ""))

            # Delay between calls (skip after last)
            if i < len(routes_subset) - 1:
                time.sleep(7)
    finally:
        scraper.stop()

    # Print per-call table
    print()
    print("-" * 95)
    print(f"{'#':<4}| {'Route':<10}| {'Status':<7}| {'Time(ms)':<9}| "
          f"{'Valid':<6}| {'Solutions':<10}| {'Cookie Refresh':<15}| Notes")
    print("-" * 95)

    for r in call_results:
        valid_str = "YES" if r["success"] else "NO"
        status_str = str(r["status_code"]) if r["status_code"] else "ERR"
        refresh_str = "yes" if r["cookie_refreshed"] else ""
        notes = r.get("error") or ""
        if notes and len(notes) > 40:
            notes = notes[:40] + "..."

        print(f"{r['call_num']:<4}| {r['route']:<10}| {status_str:<7}| "
              f"{r['elapsed_ms']:<9.0f}| {valid_str:<6}| {r['solutions_count']:<10}| "
              f"{refresh_str:<15}| {notes}")

    # Stats
    total = len(call_results)
    successes = sum(1 for r in call_results if r["success"])
    refreshes = sum(1 for r in call_results if r["cookie_refreshed"])
    success_rate = successes / total * 100 if total else 0

    print()
    print("Summary:")
    print(f"  Successes: {successes}/{total} ({success_rate:.0f}%)")
    print(f"  Cookie refreshes: {refreshes}")

    # PASS: >= 5/6 success AND at least 2 cookie refreshes
    passed = successes >= 5 and refreshes >= 2

    print()
    if passed:
        print(f"PASS: {successes}/{total} succeeded with {refreshes} cookie refreshes.")
    else:
        reasons = []
        if successes < 5:
            reasons.append(f"only {successes}/6 succeeded (need >= 5)")
        if refreshes < 2:
            reasons.append(f"only {refreshes} refreshes (need >= 2)")
        print(f"FAIL: {'; '.join(reasons)}")

    return passed


# ---------------------------------------------------------------------------
# Experiment 3 — Full Reliability Test (10 routes)
# ---------------------------------------------------------------------------


def experiment_3(farm: CookieFarm) -> bool:
    """Full reliability test: all 10 routes via scrape_routes(), 7s delay."""
    print("\n")
    print("=" * 60)
    print("Experiment 3 — Full Reliability Test (10 routes, 7s delay)")
    print("=" * 60)

    scraper = HybridScraper(farm, refresh_interval=2)
    scraper.start()

    try:
        results = scraper.scrape_routes(ROUTES, delay=7.0)
    finally:
        scraper.stop()

    # Print full summary table
    print()
    print("-" * 95)
    print(f"{'#':<4}| {'Route':<10}| {'Status':<7}| {'Time(ms)':<9}| "
          f"{'Valid':<6}| {'Solutions':<10}| {'Cookie Refresh':<15}| Notes")
    print("-" * 95)

    for r in results:
        valid_str = "YES" if r["success"] else "NO"
        status_str = str(r["status_code"]) if r["status_code"] else "ERR"
        refresh_str = "yes" if r["cookie_refreshed"] else ""
        notes = r.get("error") or ""
        if notes and len(notes) > 40:
            notes = notes[:40] + "..."

        print(f"{r['call_num']:<4}| {r['route']:<10}| {status_str:<7}| "
              f"{r['elapsed_ms']:<9.0f}| {valid_str:<6}| {r['solutions_count']:<10}| "
              f"{refresh_str:<15}| {notes}")

    # Aggregate stats
    total = len(results)
    successes = sum(1 for r in results if r["success"])
    success_rate = successes / total * 100 if total else 0
    valid_times = [r["elapsed_ms"] for r in results if r["elapsed_ms"] > 0]
    avg_time = sum(valid_times) / len(valid_times) if valid_times else 0
    refreshes = sum(1 for r in results if r["cookie_refreshed"])

    print("-" * 95)
    print()
    print("Summary:")
    print(f"  Success rate:      {successes}/{total} ({success_rate:.0f}%)")
    print(f"  Avg response time: {avg_time:.0f}ms")
    print(f"  Cookie refreshes:  {refreshes}")

    failures = [r for r in results if not r["success"]]
    if failures:
        error_types = set()
        for f in failures:
            if f["error"]:
                etype = f["error"].split(":")[0] if ":" in f["error"] else f["error"]
                error_types.add(etype)
        print(f"  Failures:          {len(failures)} ({', '.join(sorted(error_types))})")
    else:
        print("  Failures:          none")

    # PASS: >= 9/10 success (90%+)
    passed = successes >= 9

    print()
    if passed:
        print(f"PASS: {successes}/{total} succeeded ({success_rate:.0f}%). "
              f"Hybrid architecture achieves 90%+ reliability.")
    else:
        print(f"FAIL: {successes}/{total} succeeded ({success_rate:.0f}%). "
              f"Need >= 9/10 (90%+).")

    return passed


# ---------------------------------------------------------------------------
# Experiment 4 — Burn Recovery
# ---------------------------------------------------------------------------


def experiment_4(farm: CookieFarm) -> bool:
    """Burn recovery test: high refresh_interval to trigger burn, verify recovery."""
    print("\n")
    print("=" * 60)
    print("Experiment 4 — Burn Recovery")
    print("=" * 60)
    print("Strategy: refresh_interval=100 (effectively never proactive refresh)")
    print("Goal: trigger a cookie burn, then verify reactive recovery")
    print()

    scraper = HybridScraper(farm, refresh_interval=100)
    scraper.start()

    call_results = []
    burn_detected = False
    recovery_after_burn = False
    max_calls = 10  # Upper bound — stop early if we get burn + recovery

    try:
        for i in range(max_calls):
            call_num = i + 1
            # Cycle through routes, staggering dates
            orig, dest = ROUTES[i % len(ROUTES)]
            days_out = 30 * call_num
            depart_date = (datetime.now() + timedelta(days=days_out)).strftime("%Y-%m-%d")

            print(f"\n  #{call_num} {orig}-{dest} ({depart_date}, +{days_out}d)...")
            result = scraper.fetch_calendar(orig, dest, depart_date)

            # Strip internal fields
            result.pop("_exception", None)
            result.pop("_response", None)
            result["call_num"] = call_num
            result["route"] = f"{orig}-{dest}"

            call_results.append(result)

            # Live feedback
            status = result["status_code"] or "ERR"
            valid_str = "YES" if result["success"] else "NO"
            refresh_str = "yes" if result["cookie_refreshed"] else ""
            print(f"  #{call_num} {orig}-{dest}: {status} | "
                  f"{result['elapsed_ms']:.0f}ms | Valid: {valid_str} | "
                  f"Solutions: {result['solutions_count']}"
                  + (f" | Refreshed: {refresh_str}" if refresh_str else ""))

            # Track burn/recovery events
            # A reactive refresh (cookie_refreshed=True with refresh_interval=100)
            # means a cookie burn was detected and the scraper auto-recovered.
            if result["cookie_refreshed"]:
                burn_detected = True
                if result["success"]:
                    recovery_after_burn = True
                    print(f"  ** Burn detected + successful recovery on call #{call_num} **")

            # If we've seen both burn and recovery, no need to keep going
            if burn_detected and recovery_after_burn:
                print(f"\n  Burn + recovery confirmed. Stopping early after {call_num} calls.")
                break

            # Delay between calls (skip after last)
            if i < max_calls - 1:
                time.sleep(7)
    finally:
        scraper.stop()

    # Print burn/recovery event log
    print()
    print("-" * 95)
    print(f"{'#':<4}| {'Route':<10}| {'Status':<7}| {'Time(ms)':<9}| "
          f"{'Valid':<6}| {'Solutions':<10}| {'Cookie Refresh':<15}| Notes")
    print("-" * 95)

    for r in call_results:
        valid_str = "YES" if r["success"] else "NO"
        status_str = str(r["status_code"]) if r["status_code"] else "ERR"
        refresh_str = "yes" if r["cookie_refreshed"] else ""
        notes = r.get("error") or ""
        if notes and len(notes) > 40:
            notes = notes[:40] + "..."

        print(f"{r['call_num']:<4}| {r['route']:<10}| {status_str:<7}| "
              f"{r['elapsed_ms']:<9.0f}| {valid_str:<6}| {r['solutions_count']:<10}| "
              f"{refresh_str:<15}| {notes}")

    total = len(call_results)
    successes = sum(1 for r in call_results if r["success"])
    refreshes = sum(1 for r in call_results if r["cookie_refreshed"])

    print()
    print("Summary:")
    print(f"  Total calls:       {total}")
    print(f"  Successes:         {successes}")
    print(f"  Cookie refreshes:  {refreshes} (all reactive, proactive disabled)")
    print(f"  Burn detected:     {'yes' if burn_detected else 'no'}")
    print(f"  Recovery success:  {'yes' if recovery_after_burn else 'no'}")

    # PASS: at least one burn detected AND at least one successful recovery
    passed = burn_detected and recovery_after_burn

    print()
    if passed:
        print("PASS: Cookie burn detected and reactive refresh recovered successfully.")
    else:
        reasons = []
        if not burn_detected:
            reasons.append("no cookie burn detected (Akamai may not have burned cookies "
                           "within the test window)")
        if burn_detected and not recovery_after_burn:
            reasons.append("burn detected but recovery failed")
        print(f"FAIL: {'; '.join(reasons)}")
        if not burn_detected:
            print("  Note: This can happen if Akamai's burn threshold is higher than expected.")
            print("  The reactive recovery mechanism still exists — it just wasn't triggered.")

    return passed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Hybrid architecture validation experiments "
                    "(curl_cffi + Playwright cookie farm)"
    )
    parser.add_argument(
        "--experiment",
        type=int,
        choices=[1, 2, 3, 4],
        default=None,
        help="Run a specific experiment (1-4). Omit to run all.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the cookie farm browser in headless mode.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Hybrid Architecture Validation Experiments")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Headless: {args.headless}")
    print("=" * 60)

    # Start the shared cookie farm
    print("\nStarting cookie farm...")
    farm = CookieFarm(headless=args.headless)
    farm.start()

    result_1 = None
    result_2 = None
    result_3 = None
    result_4 = None

    # Track timing for summary stats
    total_refreshes = 0
    total_refresh_time = 0.0
    total_requests = 0
    total_request_time = 0.0

    try:
        farm.ensure_logged_in()

        # --- Experiment 1 ---
        if args.experiment is None or args.experiment == 1:
            start_t = time.time()
            result_1 = experiment_1(farm)
            elapsed = time.time() - start_t
            total_requests += 1
            total_request_time += elapsed

        if args.experiment is not None and args.experiment > 1 and result_1 is None:
            # Need Experiment 1 as a gate for later experiments
            print("\nRunning Experiment 1 first (validates basic connectivity)...")
            result_1 = experiment_1(farm)

        # Gate: skip remaining experiments if Experiment 1 fails
        if result_1 is False and args.experiment is None:
            print("\nExperiment 1 FAILED. Skipping remaining experiments.")
            result_2 = "SKIPPED"
            result_3 = "SKIPPED"
            result_4 = "SKIPPED"
        elif result_1 is False and args.experiment is not None and args.experiment > 1:
            print(f"\nExperiment {args.experiment} SKIPPED — Experiment 1 did not pass.")
        else:
            # --- Experiment 2 ---
            if args.experiment is None or args.experiment == 2:
                if result_1 is not False:
                    time.sleep(3)  # Let cookies settle
                    result_2 = experiment_2(farm)

            # --- Experiment 3 ---
            if args.experiment is None or args.experiment == 3:
                if result_1 is not False:
                    time.sleep(3)
                    result_3 = experiment_3(farm)

            # --- Experiment 4 ---
            if args.experiment is None or args.experiment == 4:
                if result_1 is not False:
                    time.sleep(3)
                    result_4 = experiment_4(farm)

    finally:
        farm.stop()

    # --- Overall Summary ---
    def fmt(result):
        if result is None:
            return "NOT RUN"
        if result == "SKIPPED":
            return "SKIPPED"
        return "PASS" if result else "FAIL"

    exp1_str = fmt(result_1)
    exp2_str = fmt(result_2)
    exp3_str = fmt(result_3)
    exp4_str = fmt(result_4)

    # Determine architecture verdict
    # CONFIRMED if Experiment 3 passes (the critical reliability test)
    if result_3 is True:
        arch_verdict = "CONFIRMED"
    elif result_1 is True and result_3 is None:
        arch_verdict = "INCONCLUSIVE (run Experiment 3 to confirm)"
    elif result_1 is False:
        arch_verdict = "NOT VIABLE"
    elif result_3 is False:
        arch_verdict = "NOT VIABLE"
    else:
        arch_verdict = "INCONCLUSIVE"

    # Determine next step
    if arch_verdict.startswith("CONFIRMED"):
        next_step = "Build production scraper with cookie farm"
    elif "NOT VIABLE" in arch_verdict:
        next_step = "Investigate alternatives"
    else:
        next_step = "Re-run full experiment suite to confirm"

    print("\n")
    print("=" * 60)
    print("OVERALL RESULTS")
    print("=" * 60)
    print(f"Experiment 1 (Single call):          {exp1_str}")
    print(f"Experiment 2 (Cookie refresh cycle): {exp2_str}")
    print(f"Experiment 3 (Full reliability):     {exp3_str}")
    print(f"Experiment 4 (Burn recovery):        {exp4_str}")
    print()
    print(f"Architecture: curl_cffi + Playwright cookie farm [{arch_verdict}]")
    print()
    print(f"Next step: [{next_step}]")
    print("=" * 60)


if __name__ == "__main__":
    main()
