# setup_sessions.py
import asyncio
from playwright.async_api import async_playwright
import settings
from fb_browser import launch_account, clear_account_problem


async def setup_account(account: dict):
    async with async_playwright() as p:
        # Same browser fingerprint (window size, locale, timezone) the scraper uses later
        context = await launch_account(p, account)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.facebook.com/marketplace/")
        print(f"Log into Facebook for {account['id']} ({account['session_dir']}), then press Enter in terminal...")
        input("Press Enter once fully logged in and Marketplace is accessible: ")
        await context.close()
    clear_account_problem(account["id"])   # the scraper may use this account again
    print(f"✅ {account['id']} saved. Start (or restart) the scraper to use it.")


if __name__ == "__main__":
    accounts = settings.FB_ACCOUNTS
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
