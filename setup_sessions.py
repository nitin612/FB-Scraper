# setup_sessions.py
import asyncio
import sys
from playwright.async_api import async_playwright
import settings
from fb_browser import launch_account, clear_account_problem, is_account_logged_in

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass


async def setup_account(account: dict):
    async with async_playwright() as p:
        # Same browser fingerprint (window size, locale, timezone) the scraper uses later
        context = await launch_account(p, account)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.facebook.com/login/")
        print(f"\n👉 A browser window has opened for {account['id']} ({account['session_dir']}).")
        print("   Please log into your Facebook account inside that browser window.")
        input("\nPress Enter AFTER you have completely logged in and can see your feed or Marketplace: ")

        # Verify authentication cookies and export portable state
        logged_in = await is_account_logged_in(context)
        state_file = f"state_{account['id'].lower().replace('account_', 'acc')}.json"
        if logged_in:
            await context.storage_state(path=state_file)
        await context.close()

    if logged_in:
        clear_account_problem(account["id"])
        print(f"✅ {account['id']} successfully logged in and verified! Ready for scraping & auto-messaging (saved to {state_file}).")
    else:
        print(f"⚠️ WARNING: {account['id']} is NOT logged in. Facebook did not save a valid login session.")
        print("   Auto-messaging to sellers will fail until this account is logged in.")
        print("   Please run setup again and enter your Facebook username and password in the opened browser window.")


if __name__ == "__main__":
    accounts = settings.ALL_FB_ACCOUNTS
    print("==================================================")
    print("📱 Facebook Session Setup Tool")
    print("   Use a DIFFERENT Facebook account for each slot.")
    print("==================================================")
    for n, acc in enumerate(accounts, 1):
        print(f"{n}) Log in {acc['id']} ({acc['session_dir']})")
    print(f"{len(accounts) + 1}) Log in ALL accounts one after another")
    print("==================================================")
    choice = input(f"Select an option [1-{len(accounts) + 1}, default=1]: ").strip() or "1"

    if choice.isdigit() and 1 <= int(choice) <= len(accounts):
        asyncio.run(setup_account(accounts[int(choice) - 1]))
    elif choice == str(len(accounts) + 1):
        for acc in accounts:
            asyncio.run(setup_account(acc))
    else:
        print("Invalid choice.")
