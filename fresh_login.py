"""
fresh_login.py — Run on the Oracle server via VNC to create a fresh Facebook session
bound to this server's IP address.

Usage:
    DISPLAY=:99 ./venv/bin/python fresh_login.py
"""
import asyncio
import json
from playwright.async_api import async_playwright

ACCOUNTS = [
    {"id": "Account_1", "state_file": "state_acc1.json"},
]


async def fresh_login(account: dict):
    state_file = account["state_file"]
    print(f"\n{'='*55}")
    print(f"  Fresh Login: {account['id']}  ->  {state_file}")
    print(f"{'='*55}")
    print("  WARNING: DO NOT press Enter until you can see your Facebook feed!")
    print("  A browser window will open. You MUST type your email + password.")
    print(f"{'='*55}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        # Completely blank context -- no old cookies
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/Toronto",
        )
        page = await context.new_page()

        print("Opening Facebook login page...")
        await page.goto("https://www.facebook.com/login/", wait_until="domcontentloaded")

        print("Please log in with your Facebook email and password.")
        print("After you can see your News Feed or Marketplace, come back here.\n")
        input("Press Enter AFTER you are fully logged in: ")

        # Verify the login worked
        cookies = await context.cookies(["https://www.facebook.com"])
        c_user = next((c for c in cookies if c["name"] == "c_user"), None)

        if c_user:
            state = await context.storage_state(path=state_file)
            fb_cookies = [c for c in state["cookies"] if "facebook" in c.get("domain", "")]
            print(f"\nSUCCESS! Saved {len(fb_cookies)} cookies to {state_file}")
            print(f"   c_user = {c_user['value'][:8]}...")
        else:
            print("\nFAILED -- c_user cookie not found. Please run again and complete login.")

        await browser.close()


if __name__ == "__main__":
    for acc in ACCOUNTS:
        asyncio.run(fresh_login(acc))
