"""Debug United login flow step by step (v4).

Uses correct selectors + JS click for the drawer's Sign in button.
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / ".env")

UNITED_EMAIL = os.getenv("UNITED_EMAIL", "").strip()
UNITED_PASSWORD = os.getenv("UNITED_PASSWORD", "").strip()
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()

SCREENSHOTS_DIR = SCRIPT_DIR / "debug_screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)


def screenshot(page, name):
    path = SCREENSHOTS_DIR / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"  Screenshot: {name}.png")


def dump_inputs(page):
    all_inputs = page.locator("input:visible")
    input_count = all_inputs.count()
    print(f"  Visible inputs: {input_count}")
    for i in range(min(input_count, 15)):
        inp = all_inputs.nth(i)
        try:
            attrs = page.evaluate("""(el) => ({
                type: el.type, name: el.name, id: el.id,
                placeholder: el.placeholder,
            })""", inp.element_handle())
            print(f"    [{i}] {attrs}")
        except Exception:
            pass


def main():
    print("=" * 60)
    print("Debug: United Login Flow (v4)")
    print("=" * 60)

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(SCRIPT_DIR / ".browser-profile-debug"),
        headless=False,
        channel="chrome",
        viewport={"width": 1280, "height": 800},
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    try:
        # Step 1: Navigate
        print("\nStep 1: Navigate to united.com...")
        page.goto("https://www.united.com/en/ca/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # Check if already logged in
        content = page.content()
        if "myaccount" in content.lower() and "Sign in" not in content[:3000]:
            print("  Already logged in!")
            screenshot(page, "01_already_logged_in")
            return

        # Step 2: Click Sign in
        print("\nStep 2: Click Sign in...")
        page.locator("text=Sign in").first.click()
        page.wait_for_timeout(3000)

        # Step 3: Enter email
        print("\nStep 3: Enter email in #MPIDEmailField...")
        email_input = page.locator('#MPIDEmailField')
        email_input.wait_for(state="visible", timeout=5000)
        email_input.fill(UNITED_EMAIL)
        page.wait_for_timeout(1000)

        # Click Continue
        print("  Clicking Continue...")
        page.locator('button:has-text("Continue")').first.click()
        page.wait_for_timeout(4000)
        screenshot(page, "04_after_continue")

        # Step 4: Enter password
        print("\nStep 4: Enter password...")
        pw_field = page.locator('#password')
        pw_field.wait_for(state="visible", timeout=5000)
        pw_field.fill(UNITED_PASSWORD)
        page.wait_for_timeout(1000)
        screenshot(page, "05_password_filled")

        # Click Sign in via JavaScript (avoids overlay interception)
        print("  Clicking Sign in (JS click on drawer button)...")
        clicked = page.evaluate("""() => {
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                const text = btn.textContent.trim();
                const rect = btn.getBoundingClientRect();
                if (text === 'Sign in' && rect.x > 650 && rect.width > 100) {
                    btn.click();
                    return {clicked: true, x: rect.x, y: rect.y, w: rect.width, text: text};
                }
            }
            // Fallback: list all buttons with "Sign in"
            const info = [];
            for (const btn of buttons) {
                if (btn.textContent.includes('Sign in')) {
                    const r = btn.getBoundingClientRect();
                    info.push({text: btn.textContent.trim().substring(0, 30), x: r.x, y: r.y, w: r.width, id: btn.id});
                }
            }
            return {clicked: false, candidates: info};
        }""")
        print(f"  Click result: {clicked}")

        page.wait_for_timeout(6000)
        screenshot(page, "06_after_signin_click")
        print(f"  URL: {page.url}")

        # Step 5: Post-password analysis
        print("\nStep 5: Analyzing page after sign-in...")
        dump_inputs(page)

        content = page.content()
        body_text = " ".join(page.locator("body").text_content().split())

        # Check if logged in
        if "myaccount" in content.lower():
            print("\n  LOGIN SUCCESSFUL!")
            screenshot(page, "07_logged_in")
            return

        # Check for MFA keywords
        mfa_keywords = ["verification", "verify", "code", "security", "different way",
                        "one-time", "passcode", "confirm"]
        found = [k for k in mfa_keywords if k.lower() in body_text.lower()]
        if found:
            print(f"  MFA keywords: {found}")
        else:
            # Check for error
            if "error" in body_text.lower() or "incorrect" in body_text.lower():
                print("  ERROR on page!")
            print(f"  Page text (500 chars): {body_text[:500]}")

        screenshot(page, "07_mfa_page")

        # Step 6: Handle MFA
        print("\nStep 6: MFA handling...")

        # Look for "try a different way"
        try_diff = page.locator("text=try a different way")
        if try_diff.count() > 0:
            print("  Clicking 'try a different way'...")
            try_diff.first.click()
            page.wait_for_timeout(3000)
            screenshot(page, "08_different_way")
        else:
            print("  No 'try a different way' link")

        # Document what options are available
        body_text2 = " ".join(page.locator("body").text_content().split())
        print(f"\n  Body text (600 chars): {body_text2[:600]}")

        # Look for email/text message options
        for label in ["email", "Email", "text message", "Text message", "phone", "Phone"]:
            loc = page.locator(f"text={label}")
            if loc.count() > 0:
                print(f"  Found '{label}' ({loc.count()} matches)")

        dump_inputs(page)
        screenshot(page, "09_mfa_options")

        print(f"\nFinal URL: {page.url}")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        screenshot(page, "99_error")
    finally:
        print("\nBrowser open for 30s...")
        time.sleep(30)
        ctx.close()
        pw.stop()


if __name__ == "__main__":
    main()
