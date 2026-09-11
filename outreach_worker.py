"""Sends the first 'still available?' message from a Facebook account's own browser.

Outreach now runs inside continuous_scraper.py: between searches, each account messages
queued deals within the daily caps in settings.py. Start the scraper to use it.
"""
import random
import sys

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
        await page.wait_for_timeout(random.randint(3500, 5500))

        # Check if listing is unavailable
        initial_text = await _page_text(page)
        if any(p in initial_text for p in UNAVAILABLE_PHRASES):
            return False, "Listing no longer available"

        # Check if already messaged
        if "you sent a message" in initial_text or "message sent" in initial_text:
            return True, "Already messaged"

        # Possible message button selectors that trigger the message dialog
        msg_button_selectors = [
            'div[aria-label="Message"]:not([aria-disabled="true"])',
            'button:has-text("Message")',
            'div[role="button"]:has-text("Message")',
            'div[aria-label="Send message to seller"]',
            'div[aria-label="Send seller a message"]',
        ]

        # Textbox selectors on FB Marketplace
        textbox_selectors = [
            'textarea[aria-label*="Message"]',
            '[aria-label="Message to seller"]',
            '[aria-label="Send a message to this seller"]',
            'div[contenteditable="true"][role="textbox"]',
            'div[contenteditable="true"][aria-label*="Message"]',
            'div[role="textbox"]',
            'textarea',
        ]

        # Find visible textbox
        textbox = None
        for sel in textbox_selectors:
            loc = page.locator(sel).locator("visible=true").first
            if await loc.count() > 0:
                textbox = loc
                break

        # If no textbox found directly on page, click primary "Message" button to open modal
        if textbox is None:
            for btn_sel in msg_button_selectors:
                btn = page.locator(btn_sel).locator("visible=true").first
                if await btn.count() > 0:
                    await btn.click(delay=random.randint(60, 150))
                    await page.wait_for_timeout(random.randint(2500, 4000))
                    break

            # Try locating textbox again after clicking Message button
            for sel in textbox_selectors:
                loc = page.locator(sel).locator("visible=true").first
                if await loc.count() > 0:
                    textbox = loc
                    break

        if textbox is None:
            return False, "Input box not found"

        # Click textbox, clear any prefilled Facebook placeholder, and type human-like
        await textbox.click(delay=random.randint(60, 150))
        await page.wait_for_timeout(random.randint(400, 800))
        select_all_key = "Meta+A" if sys.platform == "darwin" else "Control+A"
        await page.keyboard.press(select_all_key)
        await page.wait_for_timeout(200)
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(300)
        await textbox.press_sequentially(message_text, delay=random.randint(35, 75))
        await page.wait_for_timeout(random.randint(1000, 2000))

        # Look for Send button
        send_selectors = [
            'div[aria-label="Send message"]',
            'button[aria-label="Send message"]',
            'div[aria-label="Send Message"]',
            'div[aria-label="Send"]',
            'button[aria-label="Send"]',
            'button:has-text("Send")',
            'div[role="button"]:has-text("Send")',
        ]

        sent = False
        for s_sel in send_selectors:
            send_btn = page.locator(s_sel).locator("visible=true").first
            if await send_btn.count() > 0:
                await send_btn.click(delay=random.randint(60, 150))
                sent = True
                break

        if not sent:
            await textbox.press("Enter", delay=random.randint(60, 150))

        # Check for secondary confirmation button (e.g. Continue/Send)
        await page.wait_for_timeout(1500)
        confirm_btn = page.locator('button:has-text("Send"), button:has-text("Continue")').locator("visible=true").first
        if await confirm_btn.count() > 0:
            await confirm_btn.click(delay=random.randint(60, 150))

        await page.wait_for_timeout(random.randint(3500, 5000))

        # Verify whether account is rate limited / blocked
        post_text = await _page_text(page)
        if any(p in post_text for p in MESSAGING_BLOCK_PHRASES):
            return False, "MESSAGING_BLOCKED"

        return True, "Delivered"
    except Exception as e:
        return False, str(e)[:100]


if __name__ == "__main__":
    print("Outreach now runs inside the scraper: each Facebook account messages the deals it queues,")
    print("within the daily limits in settings.py. Start it with: run.bat scraper  (or ./run.sh scraper)")
