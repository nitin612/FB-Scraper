"""Sends the first 'still available?' message from a Facebook account's own browser.

Outreach now runs inside continuous_scraper.py: between searches, each account messages
queued deals within the daily caps in settings.py. Start the scraper to use it.
"""
import random

UNAVAILABLE_PHRASES = ("this listing is no longer available", "listing is no longer available", "this item has been sold")
MESSAGING_BLOCK_PHRASES = (
    "you can't message", "you can’t message", "message couldn't be sent", "message couldn’t be sent",
    "you're temporarily blocked", "you’re temporarily blocked", "limit how often you can",
)


async def _page_text(page) -> str:
    try:
        return (await page.locator("body").inner_text(timeout=4000))[:4000].lower()
    except Exception:
        return ""


async def send_human_message(page, listing_url: str, message_text: str) -> tuple[bool, str]:
    """Returns (sent, reason). reason 'MESSAGING_BLOCKED' means Facebook is limiting this account's messages."""
    try:
        await page.goto(listing_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(random.randint(4000, 6000))
        if any(p in await _page_text(page) for p in UNAVAILABLE_PHRASES):
            return False, "Listing no longer available"

        textbox = page.locator('textarea, [aria-label="Message to seller"], [aria-label*="Message"], div[contenteditable="true"]').locator('visible=true').first
        primary_msg_btn = page.locator('div[aria-label="Message"], button:has-text("Message")').locator('visible=true').first

        if await textbox.count() == 0 and await primary_msg_btn.count() > 0:
            await primary_msg_btn.click(delay=random.randint(50, 150))
            await page.wait_for_timeout(random.randint(3000, 4500))
            textbox = page.locator('textarea, [aria-label="Message to seller"], [aria-label*="Message"], div[contenteditable="true"]').locator('visible=true').first

        if await textbox.count() == 0:
            return False, "Input box not found"

        await textbox.click(delay=random.randint(50, 150))
        await page.wait_for_timeout(random.randint(500, 1200))
        await textbox.press_sequentially(message_text, delay=random.randint(35, 80))
        await page.wait_for_timeout(random.randint(1200, 2500))

        send_btn = page.locator('div[aria-label="Send message"], button[aria-label="Send"], div[role="button"]:has-text("Send")').locator('visible=true').first
        if await send_btn.count() > 0:
            await send_btn.click(delay=random.randint(50, 150))
        else:
            await textbox.press("Enter", delay=random.randint(50, 150))

        await page.wait_for_timeout(1500)
        confirm_btn = page.locator('button:has-text("Send"), button:has-text("Continue")').locator('visible=true').first
        if await confirm_btn.count() > 0:
            await confirm_btn.click(delay=random.randint(50, 150))

        await page.wait_for_timeout(random.randint(4000, 6000))
        if any(p in await _page_text(page) for p in MESSAGING_BLOCK_PHRASES):
            return False, "MESSAGING_BLOCKED"
        return True, "Delivered"
    except Exception as e:
        return False, str(e)[:60]


if __name__ == "__main__":
    print("Outreach now runs inside the scraper: each Facebook account messages the deals it queues,")
    print("within the daily limits in settings.py. Start it with: run.bat scraper  (or ./run.sh scraper)")
