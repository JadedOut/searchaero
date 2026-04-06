"""curl_cffi experiment script for United Airlines award calendar API.

Runs three experiments to validate that curl_cffi can reliably bypass
Cloudflare and fetch award calendar data using a manual bearer token.

Usage:
    python test_curl_cffi.py              # runs all experiments sequentially
    python test_curl_cffi.py --experiment 1   # runs only Experiment 1
    python test_curl_cffi.py --experiment 2   # runs only Experiment 2
    python test_curl_cffi.py --experiment 3   # runs only Experiment 3
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

from curl_cffi.requests import Session
from dotenv import load_dotenv

import united_api


# ---------------------------------------------------------------------------
# Token loading
# ---------------------------------------------------------------------------


def load_credentials() -> tuple:
    """Load bearer token and cookies from .env file, or exit with instructions.

    Returns:
        (bearer_token, cookies) tuple
    """
    # Load .env from the same directory as this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(script_dir, ".env")
    load_dotenv(dotenv_path)

    token = os.getenv("UNITED_BEARER_TOKEN", "").strip()
    cookies = os.getenv("UNITED_COOKIES", "").strip()

    if not token or "paste_your_token_here" in token:
        print("ERROR: UNITED_BEARER_TOKEN not configured.\n")
        print("To obtain credentials from Chrome DevTools:")
        print("  1. Log into united.com in Chrome (complete Gmail MFA)")
        print("  2. Open DevTools (F12) -> Network tab")
        print("  3. Search for an award flight (e.g., YYZ to LAX, 'Book with miles')")
        print("  4. Find a request to /api/flight/FetchAwardCalendar")
        print("  5. Click the request -> Headers tab")
        print("  6. Copy 'x-authorization-api' value -> UNITED_BEARER_TOKEN in .env")
        print("  7. Copy 'cookie' value -> UNITED_COOKIES in .env")
        print("\nSee .env.sample for the template.")
        sys.exit(1)

    if not cookies or "paste_your_cookie" in cookies:
        print("WARNING: UNITED_COOKIES not configured.")
        print("  FetchAwardCalendar requires Akamai cookies from your browser.")
        print("  In DevTools, copy the 'cookie' header from any /api/flight/ request")
        print("  and add it as UNITED_COOKIES in .env")
        print("  Continuing without cookies (will likely fail)...\n")

    # Print redacted values to confirm they loaded
    redacted_token = token[:10] + "..."
    print(f"Bearer token loaded: {redacted_token}")
    if cookies:
        redacted_cookies = cookies[:30] + "..." if len(cookies) > 30 else cookies
        print(f"Cookies loaded: {redacted_cookies}")
    else:
        print("Cookies: not set")
    return token, cookies


# ---------------------------------------------------------------------------
# Experiment 1 — Single API Call
# ---------------------------------------------------------------------------


def experiment_1(bearer_token: str, cookies: str = "") -> bool:
    """Single API call to test Cloudflare bypass and API connectivity."""
    print("\n")
    print("=" * 50)
    print("Experiment 1 — Single API Call")
    print("=" * 50)

    # Build request for YYZ -> LAX, 30 days from today
    depart_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    origin, destination = "YYZ", "LAX"

    print(f"Route: {origin} -> {destination}")
    print(f"Departure: {depart_date}")
    print(f"Impersonation: chrome136")
    print(f"Cookies: {'yes' if cookies else 'no'}")
    print()

    body = united_api.build_calendar_request(origin, destination, depart_date)
    headers = united_api.build_headers(bearer_token, cookies)

    session = Session(impersonate="chrome136")

    try:
        start = time.time()
        response = session.post(
            united_api.CALENDAR_URL,
            json=body,
            headers=headers,
        )
        elapsed_ms = (time.time() - start) * 1000
    except Exception as e:
        print(f"FAIL: Request exception: {e}")
        return False
    finally:
        session.close()

    # Log HTTP status and response time
    print(f"HTTP Status: {response.status_code}")
    print(f"Response time: {elapsed_ms:.0f}ms")

    # Log key response headers
    print("\nKey response headers:")
    for hdr_name in sorted(response.headers.keys()):
        lower = hdr_name.lower()
        if lower in ("content-type",) or lower.startswith("cf-") or "rate-limit" in lower:
            print(f"  {hdr_name}: {response.headers[hdr_name]}")

    # Log response body snippet
    print(f"\nResponse body (first 500 chars):")
    print(response.text[:500])
    print()

    # Validate
    is_valid, error_type, details = united_api.validate_response(response)

    if is_valid:
        # Parse solutions
        try:
            data = response.json()
        except Exception:
            data = {}
        solutions = united_api.parse_calendar_solutions(data)

        # Count unique dates with data
        dates_with_data = len(set(s["date"] for s in solutions if s["date"]))

        # Sample cabin/miles values
        print(f"Solutions parsed: {len(solutions)} records")
        print(f"Days with pricing data: {dates_with_data}")
        if solutions:
            print("\nSample records:")
            for s in solutions[:5]:
                print(f"  {s['date']} | {s['cabin']:20s} | {s['miles']:,.0f} miles | ${s['taxes_usd']:.2f} tax")

        print(f"\nPASS: curl_cffi successfully fetched award calendar data through "
              f"Cloudflare. {dates_with_data} days with pricing data returned.")
        return True
    else:
        print(f"\nFAIL: curl_cffi failed. Status: {response.status_code}. "
              f"Error type: {error_type}. Details: {details}")
        if response.status_code == 403:
            print("\nHint: Try a different impersonation target (e.g., chrome116, chrome120, "
                  "safari17_2_ios) — Cloudflare may have updated their fingerprint rules.")
        return False


# ---------------------------------------------------------------------------
# Experiment 2 — Minimum Viable Request
# ---------------------------------------------------------------------------


def experiment_2(bearer_token: str, cookies: str = "") -> tuple:
    """Test which request components are actually required.

    Returns:
        (passed: bool, min_config: str) — overall pass and the minimum config label
    """
    print("\n")
    print("=" * 50)
    print("Experiment 2 — Minimum Viable Request")
    print("=" * 50)

    depart_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    origin, destination = "YYZ", "LAX"
    body = united_api.build_calendar_request(origin, destination, depart_date)

    results = {}

    # --- Test A: Full headers with cookies ---
    print("\nTest A — Full headers with cookies...")
    headers_a = united_api.build_headers(bearer_token, cookies)
    session_a = Session(impersonate="chrome136")
    try:
        resp_a = session_a.post(united_api.CALENDAR_URL, json=body, headers=headers_a)
        is_valid_a, err_a, det_a = united_api.validate_response(resp_a)
        results["A"] = {"pass": is_valid_a, "status": resp_a.status_code}
        print(f"  Status: {resp_a.status_code}, Valid: {is_valid_a}")
    except Exception as e:
        results["A"] = {"pass": False, "status": f"ERR: {e}"}
        print(f"  Exception: {e}")
    finally:
        session_a.close()

    time.sleep(3)

    # --- Test B: Minimal headers (auth + cookies only) ---
    print("\nTest B — Minimal headers (only auth, Content-Type, User-Agent, Accept + cookies)...")
    headers_b = {
        "x-authorization-api": bearer_token,
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    if cookies:
        headers_b["Cookie"] = cookies
    session_b = Session(impersonate="chrome136")
    try:
        resp_b = session_b.post(united_api.CALENDAR_URL, json=body, headers=headers_b)
        is_valid_b, err_b, det_b = united_api.validate_response(resp_b)
        results["B"] = {"pass": is_valid_b, "status": resp_b.status_code}
        print(f"  Status: {resp_b.status_code}, Valid: {is_valid_b}")
    except Exception as e:
        results["B"] = {"pass": False, "status": f"ERR: {e}"}
        print(f"  Exception: {e}")
    finally:
        session_b.close()

    time.sleep(3)

    # --- Test C: Bad token (should fail) ---
    print("\nTest C — Bad token (should fail)...")
    headers_c = united_api.build_headers("bearer INVALID_TOKEN_12345", cookies)
    session_c = Session(impersonate="chrome136")
    try:
        resp_c = session_c.post(united_api.CALENDAR_URL, json=body, headers=headers_c)
        is_valid_c, err_c, det_c = united_api.validate_response(resp_c)
        # Pass = we correctly got an error (not valid)
        got_expected_error = not is_valid_c
        results["C"] = {
            "pass": got_expected_error,
            "status": resp_c.status_code,
            "got_expected_error": got_expected_error,
        }
        print(f"  Status: {resp_c.status_code}, Valid: {is_valid_c}, "
              f"Got expected error: {got_expected_error}")
    except Exception as e:
        results["C"] = {"pass": True, "status": f"ERR: {e}", "got_expected_error": True}
        print(f"  Exception (counts as expected error): {e}")
    finally:
        session_c.close()

    # Determine minimum config
    if results["B"]["pass"]:
        min_config = "bearer token only"
    elif results["A"]["pass"]:
        min_config = "bearer + sec-* headers"
    else:
        min_config = "full headers + cookies"

    # Print summary
    pass_a = "PASS" if results["A"]["pass"] else "FAIL"
    pass_b = "PASS" if results["B"]["pass"] else "FAIL"
    pass_c = "PASS" if results["C"]["pass"] else "FAIL"
    got_err = "yes" if results["C"].get("got_expected_error", False) else "no"

    print()
    print("Experiment 2 — Minimum Viable Request")
    print("-" * 38)
    print(f"Test A (no cookies):      {pass_a} (status: {results['A']['status']})")
    print(f"Test B (minimal headers): {pass_b} (status: {results['B']['status']})")
    print(f"Test C (bad token):       {pass_c} (got expected error: {got_err})")
    print()
    print(f"Minimum required: [{min_config}]")

    overall = results["A"]["pass"] and results["C"]["pass"]
    return overall, min_config


# ---------------------------------------------------------------------------
# Experiment 3 — Rate & Reliability
# ---------------------------------------------------------------------------

ROUTES = [
    ("YYZ", "LAX"), ("YYZ", "SFO"), ("YYZ", "ORD"),
    ("YVR", "LAX"), ("YUL", "JFK"), ("YYZ", "DEN"),
    ("YYC", "SEA"), ("YOW", "EWR"), ("YYZ", "IAH"),
    ("YVR", "SFO"),
]


def experiment_3(bearer_token: str, cookies: str = "", min_config: str = "full headers") -> bool:
    """Make 10 successive calls with 7-second delays to test rate limiting."""
    print("\n")
    print("=" * 50)
    print("Experiment 3 — Rate & Reliability (10 calls, 7s delay)")
    print("=" * 50)

    # Determine header builder based on minimum config
    def make_headers():
        if min_config == "bearer token only":
            h = {
                "x-authorization-api": bearer_token,
                "Content-Type": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/146.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            }
            if cookies:
                h["Cookie"] = cookies
            return h
        else:
            return united_api.build_headers(bearer_token, cookies)

    call_results = []
    impersonate_target = "chrome136"

    # Reuse a single session across all calls (like a real browser).
    # Creating a new TLS session per call triggers Akamai detection.
    session = Session(impersonate=impersonate_target)

    for i, (orig, dest) in enumerate(ROUTES):
        call_num = i + 1
        # Stagger departure dates: 30, 60, 90, ... 300 days out
        days_out = 30 * call_num
        depart_date = (datetime.now() + timedelta(days=days_out)).strftime("%Y-%m-%d")

        body = united_api.build_calendar_request(orig, dest, depart_date)
        headers = make_headers()

        try:
            start = time.time()
            response = session.post(united_api.CALENDAR_URL, json=body, headers=headers)
            elapsed_ms = (time.time() - start) * 1000
        except Exception as e:
            call_results.append({
                "num": call_num,
                "route": f"{orig}-{dest}",
                "status": "ERR",
                "time_ms": 0,
                "valid": False,
                "solutions": 0,
                "error_type": "exception",
                "cf_ray": "N/A",
                "notes": str(e),
            })
            print(f"  #{call_num} {orig}-{dest}: Exception: {e}")
            if i < len(ROUTES) - 1:
                time.sleep(7)
            continue

        is_valid, error_type, details = united_api.validate_response(response)
        cf_ray = response.headers.get("cf-ray", "N/A")

        solutions_count = 0
        if is_valid:
            try:
                data = response.json()
                solutions = united_api.parse_calendar_solutions(data)
                solutions_count = len(solutions)
            except Exception:
                pass

        notes = ""
        if not is_valid:
            notes = f"{error_type}: {details[:60]}"

        call_results.append({
            "num": call_num,
            "route": f"{orig}-{dest}",
            "status": response.status_code,
            "time_ms": elapsed_ms,
            "valid": is_valid,
            "solutions": solutions_count,
            "error_type": error_type,
            "cf_ray": cf_ray,
            "notes": notes,
        })

        valid_str = "YES" if is_valid else "NO"
        print(f"  #{call_num} {orig}-{dest}: {response.status_code} | "
              f"{elapsed_ms:.0f}ms | Valid: {valid_str} | Solutions: {solutions_count}")

        # If 403, try reopening session with a different impersonation target
        if response.status_code == 403 and impersonate_target == "chrome136":
            print(f"    -> 403 detected (cf-ray: {cf_ray}). Retrying with chrome120...")
            session.close()
            impersonate_target = "chrome120"
            session = Session(impersonate=impersonate_target)
            try:
                start = time.time()
                resp_retry = session.post(
                    united_api.CALENDAR_URL, json=body, headers=headers
                )
                elapsed_retry = (time.time() - start) * 1000
                is_valid_r, err_r, det_r = united_api.validate_response(resp_retry)
                print(f"    -> Retry result: {resp_retry.status_code} | "
                      f"{elapsed_retry:.0f}ms | Valid: {is_valid_r}")
                if is_valid_r:
                    print("    -> Impersonation change HELPED. Continuing with chrome120.")
                else:
                    print("    -> Impersonation change did NOT help.")
                    session.close()
                    impersonate_target = "chrome136"
                    session = Session(impersonate=impersonate_target)
            except Exception as e:
                print(f"    -> Retry exception: {e}")
                session.close()
                impersonate_target = "chrome136"
                session = Session(impersonate=impersonate_target)

        # Delay between calls (skip after last call)
        if i < len(ROUTES) - 1:
            time.sleep(7)

    session.close()

    # Print summary table
    print()
    print("Experiment 3 — Rate & Reliability (10 calls, 7s delay)")
    print("-" * 55)
    print(f"{'#':<3}| {'Route':<10}| {'Status':<7}| {'Time(ms)':<9}| {'Valid':<6}| {'Solutions':<10}| Notes")

    for r in call_results:
        valid_str = "YES" if r["valid"] else "NO"
        print(f"{r['num']:<3}| {r['route']:<10}| {str(r['status']):<7}| "
              f"{r['time_ms']:<9.0f}| {valid_str:<6}| {r['solutions']:<10}| {r['notes']}")

    # Compute stats
    successes = sum(1 for r in call_results if r["valid"])
    total = len(call_results)
    success_rate = successes / total * 100 if total > 0 else 0
    valid_times = [r["time_ms"] for r in call_results if r["time_ms"] > 0]
    avg_time = sum(valid_times) / len(valid_times) if valid_times else 0
    count_403 = sum(1 for r in call_results if r["status"] == 403)
    count_429 = sum(1 for r in call_results if r["status"] == 429)
    count_401 = sum(1 for r in call_results if r["status"] == 401)
    failures = [r for r in call_results if not r["valid"]]

    print()
    print("Summary:")
    print(f"- Success rate: {successes}/{total} ({success_rate:.0f}%)")
    print(f"- Avg response time: {avg_time:.0f}ms")
    if failures:
        fail_types = set(r["error_type"] for r in failures if r["error_type"])
        print(f"- Failures: {len(failures)} ({', '.join(fail_types)})")
    else:
        print("- Failures: none")
    print(f"- 403 (Cloudflare): {count_403}")
    print(f"- 429 (Rate limit): {count_429}")
    print(f"- 401 (Token expired): {count_401}")

    # Pass criteria: >= 90% success, no 403s, no 429s
    passed = success_rate >= 90 and count_403 == 0 and count_429 == 0

    print()
    if passed:
        print("VERDICT: PASS — curl_cffi is reliable at 7s intervals")
    else:
        reasons = []
        if success_rate < 90:
            reasons.append(f"success rate {success_rate:.0f}% < 90%")
        if count_403 > 0:
            reasons.append(f"{count_403} Cloudflare 403(s)")
        if count_429 > 0:
            reasons.append(f"{count_429} rate limit 429(s)")
        print(f"VERDICT: FAIL — {'; '.join(reasons)}")

    return passed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="curl_cffi experiment runner for United Airlines award calendar API"
    )
    parser.add_argument(
        "--experiment",
        type=int,
        choices=[1, 2, 3],
        default=None,
        help="Run a specific experiment (1, 2, or 3). Omit to run all.",
    )
    args = parser.parse_args()

    print("=" * 50)
    print("curl_cffi Experiment Runner")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    bearer_token, cookies = load_credentials()

    result_1 = None
    result_2 = None
    result_3 = None
    min_config = "full headers"

    if args.experiment is None or args.experiment == 1:
        result_1 = experiment_1(bearer_token, cookies)

    if args.experiment is None or args.experiment == 2:
        # Experiment 2 only runs if Experiment 1 passed
        if args.experiment == 2:
            # Running standalone — need to verify Experiment 1 first
            if result_1 is None:
                print("\nRunning Experiment 1 first (required for Experiment 2)...")
                result_1 = experiment_1(bearer_token, cookies)
            if not result_1:
                print("\nExperiment 2 SKIPPED — Experiment 1 did not pass.")
                result_2 = "SKIPPED"
            else:
                passed_2, min_config = experiment_2(bearer_token, cookies)
                result_2 = passed_2
        else:
            # Running as part of "all"
            if result_1:
                passed_2, min_config = experiment_2(bearer_token, cookies)
                result_2 = passed_2
            else:
                print("\nExperiment 2 SKIPPED — Experiment 1 did not pass.")
                result_2 = "SKIPPED"

    if args.experiment is None or args.experiment == 3:
        # Experiment 3 only runs if Experiment 1 passed
        if args.experiment == 3:
            # Running standalone — need to verify Experiment 1 first
            if result_1 is None:
                print("\nRunning Experiment 1 first (required for Experiment 3)...")
                result_1 = experiment_1(bearer_token, cookies)
            if not result_1:
                print("\nExperiment 3 SKIPPED — Experiment 1 did not pass.")
                result_3 = "SKIPPED"
            else:
                result_3 = experiment_3(bearer_token, cookies, min_config)
        else:
            # Running as part of "all"
            if result_1:
                result_3 = experiment_3(bearer_token, cookies, min_config)
            else:
                print("\nExperiment 3 SKIPPED — Experiment 1 did not pass.")
                result_3 = "SKIPPED"

    # Final summary
    def fmt(result):
        if result is None:
            return "NOT RUN"
        if result == "SKIPPED":
            return "SKIPPED"
        return "PASS" if result else "FAIL"

    exp1_str = fmt(result_1)
    exp2_str = fmt(result_2)
    exp3_str = fmt(result_3)

    # Determine architecture verdict
    if result_1 is True and result_3 is not False:
        arch_verdict = "CONFIRMED"
    elif result_1 is False:
        arch_verdict = "NOT VIABLE"
    else:
        arch_verdict = "INCONCLUSIVE"

    if arch_verdict == "CONFIRMED":
        next_step = "Build the production scraper"
    elif arch_verdict == "NOT VIABLE":
        next_step = "Fall back to Playwright for API calls"
    else:
        next_step = "Re-run experiments with a fresh token to confirm"

    print("\n")
    print("=" * 40)
    print("OVERALL RESULTS")
    print("=" * 40)
    print(f"Experiment 1 (Single call):        {exp1_str}")
    print(f"Experiment 2 (Minimum request):    {exp2_str}")
    print(f"Experiment 3 (Rate & reliability): {exp3_str}")
    print()
    print(f"Architecture: curl_cffi with manual bearer token [{arch_verdict}]")
    print()
    print(f"Next step: [{next_step}]")
    print("=" * 40)


if __name__ == "__main__":
    main()
