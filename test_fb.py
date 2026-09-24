import asyncio
from playwright.async_api import async_playwright
import settings
from fb_browser import launch_account, account_problem

async def test_session():
    print("Testing Facebook Account_1 session...")
    async with async_playwright() as p:
        acc = settings.FB_ACCOUNTS[0]
        context = await launch_account(p, acc)
        page = context.pages[0] if context.pages else await context.new_page()
        
        url = "https://www.facebook.com/marketplace/toronto/search/?query=samsung"
        print(f"Navigating to: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        
        title = await page.title()
        current_url = page.url
        print(f"\n📄 Page Title: {title}")
        print(f"🔗 Current URL: {current_url}")
        
        # Check cookies
        cookies = await context.cookies("https://www.facebook.com")
        c_user = next((c for c in cookies if c.get("name") == "c_user"), None)
        if c_user:
            print(f"✅ Active Login Cookie found (c_user = {c_user['value']})")
        else:
            print("⚠️ No c_user cookie found - account is NOT logged in or session expired.")
            
        problem = await account_problem(page)
        print(f"🔍 account_problem check result: {problem or 'HEALTHY'}")
        
        # Save screenshot
        await page.screenshot(path="debug_fb_screen.png")
        print("📸 Screenshot saved to 'debug_fb_screen.png'")
        
        await context.close()

if __name__ == "__main__":
    asyncio.run(test_session())
