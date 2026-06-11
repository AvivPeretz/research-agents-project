"""
Run this script ONCE on your local machine (not on the server)
to create the initial Overleaf Chrome profile.
Also run it when the session expires to refresh it.

Usage: python3 setup_overleaf_session.py
After completion, copy the profile to the server:
    scp -r playwright_state/overleaf_profile/ user@server:/path/to/volume/
"""

from playwright.sync_api import sync_playwright
import time
from config import Config
import os


def setup_session():
    os.makedirs(Config.OVERLEAF_USER_DATA_DIR, exist_ok=True)

    print("🚀 Opening browser for Overleaf login...")
    print("📋 Credentials will be pre-filled automatically.")
    print("⚠️  If reCAPTCHA appears, solve it manually in the browser window.")
    print("⏳ You have 300 seconds (5 minutes).\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            Config.OVERLEAF_USER_DATA_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.overleaf.com/login")
        time.sleep(2)

        if Config.OVERLEAF_EMAIL:
            page.locator("#email").fill(Config.OVERLEAF_EMAIL)
            time.sleep(0.8)
        if Config.OVERLEAF_PASSWORD:
            page.locator("#password").fill(Config.OVERLEAF_PASSWORD)
            time.sleep(0.8)

        page.click('button[type="submit"]')

        try:
            page.wait_for_url("**/project", timeout=300000)
            print("✅ Login successful! Chrome profile saved to:")
            print(f"   {Config.OVERLEAF_USER_DATA_DIR}")
            print("\n📤 To deploy to server, run:")
            print(f"   scp -r {Config.OVERLEAF_USER_DATA_DIR}/ user@server:/path/to/volume/overleaf_profile/")
        except Exception:
            print("❌ Login timed out or failed.")
        finally:
            context.close()


if __name__ == "__main__":
    setup_session()
