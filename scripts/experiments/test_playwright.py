"""Playwright experiment script for United Airlines award calendar API.

Uses a persistent browser context so login session survives between runs.
First run: browser opens, you log in manually (Gmail MFA). Session is saved.
Subsequent runs: session is reused, no login needed (until it expires).

The script intercepts FetchAwardCalendar API responses made by the real
browser, extracting JSON data while Akamai's JavaScript keeps cookies valid.

Usage:
    python test_playwright.py                    # run all experiments
    python test_playwright.py --experiment 1     # single API call via page navigation
    python test_playwright.py --experiment 2     # 10 calls at scraping pace
    python test_playwright.py --login            # just open browser for login, then exit
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, BrowserContext

import united_api

# Persistent browser profile directory (survives between runs)
SCRIPT_DIR = Path(__file__).parent.resolve()
USER_DATA_DIR = SCRIPT_DIR / ".browser-profile"

# Routes for reliability test
ROUTES = [
    ("YYZ", "LAX"), ("YYZ", "SFO"), ("YYZ", "ORD"),
    ("YVR", "LAX"), ("YUL", "JFK"), ("YYZ", "DEN"),
    ("YYC", "SEA"), ("YOW", "EWR"), ("YYZ", "IAH"),
    ("YVR", "SFO"),
]


# ---------------------------------------------------------------------------
# Browser setup
# ---------------------------------------------------------------------------


def launch_browser(playwright, headless=False):
    """Launch real Chrome with persistent context and anti-detection args.

    Uses channel='chrome' to launch the user's installed Chrome.
    Anti-detection args hide Playwright's automation signals from Akamai.
    """
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        headless=headless,
        channel="chrome",
        viewport={"width": 1280, "height": 800},
        args=[
            "--disable-blink-features=AutomationControlled",
        ],
        ignore_default_args=["--enable-automation"],
    )
    return context


def check_logged_in(page: Page, navigate: bool = True) -> bool:
    """Check if we have a valid logged-in session.

    Args:
        navigate: If True, navigate to united.com first. Set False to check
                  current page without disrupting user activity.
    """
    if navigate:
        page.goto("https://www.united.com/en/ca/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
    try:
        content = page.content()
        if "Sign in" in content and "myAccount" not in content.lower():
            return False
        return True
    except Exception:
        return False


def wait_for_login(page: Page):
    """Open united.com and wait for user to log in manually."""
    print("\n" + "=" * 50)
    print("MANUAL LOGIN REQUIRED")
    print("=" * 50)
    print("1. The browser is open at united.com")
    print("2. Click 'Sign in' and log in with your MileagePlus account")
    print("3. Complete Gmail MFA when prompted")
    print("4. Once logged in, come back here and press Enter")
    print("=" * 50)

    page.goto("https://www.united.com/en/ca/", wait_until="domcontentloaded", timeout=30000)

    # Poll for login — check current page state without navigating/reloading
    print("\nPolling for login (checking every 30s, timeout 30min)...")
    deadline = time.time() + 1800  # 30 minutes
    while time.time() < deadline:
        time.sleep(30)
        if check_logged_in(page, navigate=False):
            break
        print("  Still waiting for login...")
    else:
        print("ERROR: Login timed out after 5 minutes.")
        return

    if check_logged_in(page):
        print("Login confirmed! Session saved to .browser-profile/")
    else:
        print("WARNING: Could not confirm login. Continuing anyway...")


# ---------------------------------------------------------------------------
# API call via page navigation + response interception
# ---------------------------------------------------------------------------


def fetch_calendar_via_navigation(
    page: Page, origin: str, destination: str, depart_date: str, timeout_ms: int = 60000
) -> dict:
    """Navigate to award search and intercept the FetchAwardCalendar response.

    This is how a real user triggers the API call — by loading the search page.
    Akamai cookies stay fresh because the browser JS is running.

    Returns:
        dict with keys: success, status_code, data, elapsed_ms, error
    """
    captured_response = {"json": None, "status": None}
    api_urls_seen = []

    def handle_response(response):
        url = response.url
        # Log API calls for debugging
        if "/api/" in url or "FetchAward" in url or "Calendar" in url:
            api_urls_seen.append(f"{response.status} {url[:120]}")
        if "FetchAwardCalendar" in url:
            try:
                captured_response["status"] = response.status
                captured_response["json"] = response.json()
            except Exception as e:
                api_urls_seen.append(f"  PARSE ERROR: {e}")

    page.on("response", handle_response)

    # Build the award search URL
    search_url = (
        f"https://www.united.com/en/us/fsr/choose-flights"
        f"?f={origin}&t={destination}&d={depart_date}"
        f"&tt=1&at=1&sc=7&px=1&taxng=1&newHP=True&clm=7&st=bestmatches"
    )

    start = time.time()
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_ms)
        # Wait for the calendar API response
        deadline = time.time() + (timeout_ms / 1000)
        while captured_response["json"] is None and time.time() < deadline:
            page.wait_for_timeout(500)
    except Exception as e:
        page.remove_listener("response", handle_response)
        return {
            "success": False,
            "status_code": None,
            "data": None,
            "elapsed_ms": (time.time() - start) * 1000,
            "error": str(e),
        }

    elapsed_ms = (time.time() - start) * 1000
    page.remove_listener("response", handle_response)

    # Debug: show all API URLs seen during navigation
    if api_urls_seen:
        print(f"  API URLs seen during navigation:")
        for u in api_urls_seen:
            print(f"    {u}")
    else:
        print(f"  WARNING: No /api/ URLs seen during navigation")

    if captured_response["json"] is not None:
        data = captured_response["json"]
        status = data.get("data", {}).get("Status")
        return {
            "success": status == 1,
            "status_code": captured_response["status"],
            "data": data,
            "elapsed_ms": elapsed_ms,
            "error": None if status == 1 else f"Status={status}",
        }
    else:
        return {
            "success": False,
            "status_code": captured_response.get("status"),
            "data": None,
            "elapsed_ms": elapsed_ms,
            "error": "FetchAwardCalendar response not captured",
        }


def fetch_calendar_via_api(
    page: Page, context: BrowserContext, origin: str, destination: str, depart_date: str
) -> dict:
    """Make a direct API call using the browser's authenticated context.

    Uses page.evaluate() to make a fetch() call from within the browser,
    inheriting all cookies and Akamai tokens automatically.

    Returns:
        dict with keys: success, status_code, data, elapsed_ms, error
    """
    body = united_api.build_calendar_request(origin, destination, depart_date)
    body_json = json.dumps(body)

    start = time.time()
    try:
        result = page.evaluate(
            """async (bodyJson) => {
                // Get bearer token from the anonymous-token endpoint
                let token = '';
                try {
                    const tokenResp = await fetch('/api/auth/anonymous-token', {
                        method: 'GET',
                        credentials: 'same-origin',
                    });
                    if (tokenResp.ok) {
                        const tokenData = await tokenResp.json();
                        // Token is usually in tokenData.data.token or tokenData.token
                        token = tokenData?.data?.token?.hash
                            || tokenData?.data?.token
                            || tokenData?.token?.hash
                            || tokenData?.token
                            || '';
                        // If we didn't find it, dump the structure for debugging
                        if (!token) {
                            return { status: -1, data: null, hadToken: false,
                                     debug: JSON.stringify(tokenData).substring(0, 500) };
                        }
                    }
                } catch(e) {
                    return { status: -1, data: null, hadToken: false, debug: 'token fetch error: ' + e.message };
                }

                const headers = {
                    'Content-Type': 'application/json',
                    'x-authorization-api': token,
                };

                const resp = await fetch('/api/flight/FetchAwardCalendar', {
                    method: 'POST',
                    headers: headers,
                    body: bodyJson,
                    credentials: 'same-origin',
                });
                const status = resp.status;
                let data = null;
                try { data = await resp.json(); } catch(e) {}
                return { status, data, hadToken: !!token };
            }""",
            body_json,
        )
        elapsed_ms = (time.time() - start) * 1000

        status_code = result["status"]
        data = result["data"]
        had_token = result.get("hadToken", False)
        if result.get("debug"):
            print(f"  DEBUG token response: {result['debug']}")

        if status_code == 200 and data and data.get("data", {}).get("Status") == 1:
            return {
                "success": True,
                "status_code": status_code,
                "data": data,
                "elapsed_ms": elapsed_ms,
                "error": None,
            }
        else:
            return {
                "success": False,
                "status_code": status_code,
                "data": data,
                "elapsed_ms": elapsed_ms,
                "error": f"HTTP {status_code}" + (" (no token found)" if not had_token else ""),
            }
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        return {
            "success": False,
            "status_code": None,
            "data": None,
            "elapsed_ms": elapsed_ms,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Experiment 1 — Single call (both methods)
# ---------------------------------------------------------------------------


def debug_token_search(page: Page):
    """Search everywhere in the browser for the bearer token."""
    print("\n--- DEBUG: Searching for bearer token ---")

    result = page.evaluate("""() => {
        const found = {};

        // Check localStorage
        const lsKeys = [];
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            lsKeys.push(key);
            const val = localStorage.getItem(key);
            if (val && val.length < 5000) {
                if (val.toLowerCase().includes('bearer') || val.toLowerCase().includes('authorization')) {
                    found['localStorage:' + key] = val.substring(0, 200);
                }
            }
        }
        found['_ls_keys'] = lsKeys;

        // Check sessionStorage
        const ssKeys = [];
        for (let i = 0; i < sessionStorage.length; i++) {
            const key = sessionStorage.key(i);
            ssKeys.push(key);
            const val = sessionStorage.getItem(key);
            if (val && val.length < 5000) {
                if (val.toLowerCase().includes('bearer') || val.toLowerCase().includes('authorization')) {
                    found['sessionStorage:' + key] = val.substring(0, 200);
                }
            }
        }
        found['_ss_keys'] = ssKeys;

        // Check cookies
        found['_cookies'] = document.cookie.substring(0, 500);

        // Check for common global variables
        try { if (window.__NEXT_DATA__) found['__NEXT_DATA__'] = 'exists'; } catch(e) {}
        try { if (window.__APP_INITIAL_STATE__) found['__APP_INITIAL_STATE__'] = 'exists'; } catch(e) {}

        return found;
    }""")

    for key, val in result.items():
        print(f"  {key}: {val}")
    print("--- END DEBUG ---\n")


def experiment_1(page: Page, context: BrowserContext) -> bool:
    """Test both API call methods: page navigation and direct fetch."""
    print("\n")
    print("=" * 50)
    print("Experiment 1 — Single API Call (Playwright)")
    print("=" * 50)

    # Debug: find where the token lives
    debug_token_search(page)

    depart_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    origin, destination = "YYZ", "LAX"

    # Method A: Direct API call via page.evaluate(fetch())
    print(f"\nMethod A — Direct API call via browser fetch()")
    print(f"Route: {origin} -> {destination}, Date: {depart_date}")

    result_a = fetch_calendar_via_api(page, context, origin, destination, depart_date)
    print(f"  Status: {result_a['status_code']}, Time: {result_a['elapsed_ms']:.0f}ms")

    if result_a["success"]:
        solutions = united_api.parse_calendar_solutions(result_a["data"])
        dates_with_data = len(set(s["date"] for s in solutions if s["date"]))
        print(f"  Solutions: {len(solutions)} records, {dates_with_data} days")
        if solutions:
            print("  Sample:")
            for s in solutions[:3]:
                print(f"    {s['date']} | {s['cabin']:20s} | {s['miles']:,.0f} miles")
        print(f"\n  PASS: Direct API call works. {dates_with_data} days returned.")
        return True
    else:
        print(f"  FAIL: {result_a['error']}")

    # Method B: Navigation-based (intercept the real browser API call)
    print(f"\nMethod B — Navigation-based (intercept real API call)")
    print(f"Route: {origin} -> {destination}, Date: {depart_date}")

    result_b = fetch_calendar_via_navigation(page, origin, destination, depart_date)
    print(f"  Status: {result_b['status_code']}, Time: {result_b['elapsed_ms']:.0f}ms")

    if result_b["success"]:
        solutions = united_api.parse_calendar_solutions(result_b["data"])
        dates_with_data = len(set(s["date"] for s in solutions if s["date"]))
        print(f"  Solutions: {len(solutions)} records, {dates_with_data} days")
        if solutions:
            print("  Sample:")
            for s in solutions[:3]:
                print(f"    {s['date']} | {s['cabin']:20s} | {s['miles']:,.0f} miles")
        print(f"\n  PASS: Navigation method works. {dates_with_data} days returned.")
        return True
    else:
        print(f"  FAIL: {result_b['error']}")
        return False


# ---------------------------------------------------------------------------
# Experiment 2 — Rate & Reliability (10 calls)
# ---------------------------------------------------------------------------


def experiment_2(page: Page, context: BrowserContext) -> bool:
    """Make 10 successive API calls with 7-second delays."""
    print("\n")
    print("=" * 50)
    print("Experiment 2 — Rate & Reliability (10 calls, 7s delay)")
    print("=" * 50)

    call_results = []

    for i, (orig, dest) in enumerate(ROUTES):
        call_num = i + 1
        days_out = 30 * call_num
        depart_date = (datetime.now() + timedelta(days=days_out)).strftime("%Y-%m-%d")

        result = fetch_calendar_via_api(page, context, orig, dest, depart_date)

        solutions_count = 0
        if result["success"] and result["data"]:
            solutions = united_api.parse_calendar_solutions(result["data"])
            solutions_count = len(solutions)

        call_results.append({
            "num": call_num,
            "route": f"{orig}-{dest}",
            "status": result["status_code"] or "ERR",
            "time_ms": result["elapsed_ms"],
            "valid": result["success"],
            "solutions": solutions_count,
            "notes": result["error"] or "",
        })

        valid_str = "YES" if result["success"] else "NO"
        print(f"  #{call_num} {orig}-{dest}: {result['status_code'] or 'ERR'} | "
              f"{result['elapsed_ms']:.0f}ms | Valid: {valid_str} | "
              f"Solutions: {solutions_count}")

        if i < len(ROUTES) - 1:
            time.sleep(7)

    # Summary
    successes = sum(1 for r in call_results if r["valid"])
    total = len(call_results)
    success_rate = successes / total * 100 if total > 0 else 0
    valid_times = [r["time_ms"] for r in call_results if r["time_ms"] > 0]
    avg_time = sum(valid_times) / len(valid_times) if valid_times else 0

    print()
    print(f"{'#':<3}| {'Route':<10}| {'Status':<7}| {'Time(ms)':<9}| {'Valid':<6}| {'Solutions':<10}| Notes")
    for r in call_results:
        valid_str = "YES" if r["valid"] else "NO"
        print(f"{r['num']:<3}| {r['route']:<10}| {str(r['status']):<7}| "
              f"{r['time_ms']:<9.0f}| {valid_str:<6}| {r['solutions']:<10}| {r['notes']}")

    print()
    print(f"Summary:")
    print(f"- Success rate: {successes}/{total} ({success_rate:.0f}%)")
    print(f"- Avg response time: {avg_time:.0f}ms")

    passed = success_rate >= 90
    if passed:
        print("\nVERDICT: PASS — Playwright is reliable at 7s intervals")
    else:
        print(f"\nVERDICT: FAIL — success rate {success_rate:.0f}% < 90%")

    return passed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Playwright experiment runner for United Airlines award calendar API"
    )
    parser.add_argument(
        "--experiment", type=int, choices=[1, 2], default=None,
        help="Run a specific experiment (1 or 2). Omit to run all.",
    )
    parser.add_argument(
        "--login", action="store_true",
        help="Just open browser for manual login, then exit.",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run headless (skip for first run — you need to see the browser to log in).",
    )
    args = parser.parse_args()

    print("=" * 50)
    print("Playwright Experiment Runner")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Browser profile: {USER_DATA_DIR}")
    print("=" * 50)

    with sync_playwright() as p:
        context = launch_browser(p, headless=args.headless)
        page = context.pages[0] if context.pages else context.new_page()

        # Check if logged in
        print("\nChecking login status...")
        logged_in = check_logged_in(page)

        if not logged_in:
            if args.headless:
                print("ERROR: Not logged in and running headless. "
                      "Run without --headless first to log in.")
                context.close()
                sys.exit(1)
            wait_for_login(page)

        if args.login:
            print("\nLogin complete. Exiting.")
            context.close()
            return

        print("Session active. Starting experiments...\n")

        result_1 = None
        result_2 = None

        if args.experiment is None or args.experiment == 1:
            result_1 = experiment_1(page, context)

        if args.experiment is None or args.experiment == 2:
            if args.experiment == 2 and result_1 is None:
                print("\nRunning Experiment 1 first...")
                result_1 = experiment_1(page, context)
            if result_1 is False:
                print("\nExperiment 2 SKIPPED — Experiment 1 failed.")
                result_2 = "SKIPPED"
            else:
                result_2 = experiment_2(page, context)

        # Final summary
        def fmt(result):
            if result is None:
                return "NOT RUN"
            if result == "SKIPPED":
                return "SKIPPED"
            return "PASS" if result else "FAIL"

        print("\n")
        print("=" * 40)
        print("OVERALL RESULTS")
        print("=" * 40)
        print(f"Experiment 1 (Single call):        {fmt(result_1)}")
        print(f"Experiment 2 (Rate & reliability): {fmt(result_2)}")
        print()

        if result_1 is True:
            print("Architecture: Playwright with persistent context [CONFIRMED]")
            print("Next step: Build the production scraper")
        elif result_1 is False:
            print("Architecture: Playwright [FAILED — investigate further]")
        else:
            print("Architecture: [INCONCLUSIVE]")

        print("=" * 40)

        context.close()


if __name__ == "__main__":
    main()
