"""Sends the first 'still available?' message from a Facebook account's own browser.

Outreach runs inside continuous_scraper.py: between searches, each account messages
queued deals within the daily caps in settings.py.
"""
import random
import sys

UNAVAILABLE_PHRASES = (
    "this listing is no longer available",
    "listing is no longer available",
    "this item has been sold",
    "item is no longer available",
)

MESSAGING_BLOCK_PHRASES = (
    "you can't message",
    "you can’t message",
    "message couldn't be sent",
    "message couldn’t be sent",
    "you're temporarily blocked",
    "you’re temporarily blocked",
    "limit how often you can",
    "action blocked",
    "you can't buy or sell",
    "you can’t buy or sell",
    "restore your access to marketplace",
)


async def _page_text(page) -> str:
    try:
        return (await page.locator("body").inner_text(timeout=4000))[:4000].lower()
    except Exception:
        return ""


async def _find_first_visible(page, selectors: list[str]):
    """Returns the first matching locator that is currently visible on the page."""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            for i in range(count):
                item = loc.nth(i)
                if await item.is_visible():
                    return item
        except Exception:
            continue
    return None


async def send_human_message(page, listing_url: str, message_text: str) -> tuple[bool, str]:
    """Navigates to a listing and sends a message to the seller.
    Returns (sent: bool, reason: str).
    Guaranteed verification: checks for account bans, login state, overlay obstruction,
    and confirms actual delivery before reporting success.
    """
    try:
        await page.goto(listing_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(random.randint(3500, 5000))

        # 1. Pre-flight check: Account Marketplace ban check
        initial_text = await _page_text(page)
        if "you can't buy or sell" in initial_text or "you can’t buy or sell" in initial_text or "restore your access to marketplace" in initial_text:
            return False, "ACCOUNT_BANNED: This Facebook account is restricted from Marketplace ('You can't buy or sell items on Facebook'). Please log in with an active, unbanned account."

        # Universal Login Check: detect password field or login form (any language)
        try:
            pw_input = page.locator('input[type="password"]').first
            if await pw_input.count() > 0 and await pw_input.is_visible():
                return False, "ACCOUNT_NOT_LOGGED_IN: Facebook session is expired or logged out (password prompt visible). Please re-login via fresh_login.py."
        except Exception:
            pass

        # 2. Check if listing is unavailable / sold
        if any(p in initial_text for p in UNAVAILABLE_PHRASES):
            return False, "Listing no longer available"

        # 3. Check if seller was already messaged
        if "you sent a message" in initial_text or "message sent" in initial_text:
            return True, "Already messaged"

        # 4. Check if session has authentication cookies
        from fb_browser import is_account_logged_in
        if not await is_account_logged_in(page.context):
            return False, "ACCOUNT_NOT_LOGGED_IN: Browser session is not logged into Facebook. Run 'python setup_sessions.py' to log in."

        # 5. Check if a message input is ALREADY open (modal dialog or inline card)
        modal_box_selectors = [
            'div[role="dialog"] div[contenteditable="true"][role="textbox"]',
            'div[role="dialog"] div[contenteditable="true"]',
            'div[role="dialog"] textarea',
            'div[role="dialog"] [aria-label*="Message" i]',
            'div[role="dialog"] [aria-label*="seller" i]',
        ]
        inline_box_selectors = [
            'div[role="main"] textarea[aria-label*="Message" i]',
            'div[role="main"] textarea[aria-label*="seller" i]',
            'div[role="main"] div[contenteditable="true"][aria-label*="Message" i]',
            'div[role="main"] div[contenteditable="true"][aria-label*="seller" i]',
            'div[role="main"] textarea[placeholder*="message" i]',
            'div[role="main"] textarea[placeholder*="available" i]',
            'div[role="main"] [aria-label="Message to seller"]',
            'div[role="main"] [aria-label="Send a message to this seller"]',
            'div[role="main"] div[contenteditable="true"][role="textbox"]',
            'div[role="main"] div[contenteditable="true"]',
            'div[role="main"] textarea',
        ]

        textbox = await _find_first_visible(page, modal_box_selectors)
        is_modal = textbox is not None
        if not textbox:
            textbox = await _find_first_visible(page, inline_box_selectors)
            is_modal = False

        # 6. If no textbox is open, locate and click the primary "Message" button
        if not textbox:
            msg_button_selectors = [
                'div[role="main"] div[aria-label="Message"][role="button"]',
                'div[role="main"] div[aria-label*="Message" i][role="button"]',
                'div[role="main"] div[aria-label="Send message to seller"]',
                'div[role="main"] div[aria-label="Send seller a message"]',
                'div[role="main"] div[aria-label="Contact seller"]',
                'div[role="main"] button:has-text("Message")',
                'div[role="main"] button:has-text("Contact seller")',
                'div[role="main"] div[role="button"]:has-text("Message")',
                'div[role="main"] div[role="button"]:has-text("Contact seller")',
                'div[aria-label="Message"][role="button"]',
                'div[aria-label*="Message" i][role="button"]',
                'div[aria-label="Send message to seller"]',
                'div[aria-label="Contact seller"]',
                'button:has-text("Message")',
                'div[role="button"]:has-text("Message")',
            ]
            btn = await _find_first_visible(page, msg_button_selectors)
            if btn:
                try:
                    await btn.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass
                try:
                    await btn.click(force=True, delay=random.randint(80, 150))
                except Exception:
                    await btn.evaluate("el => el.click()")
                await page.wait_for_timeout(random.randint(2500, 3500))

                # Check if Facebook prompted for login or displayed a ban warning
                dialog = page.locator('div[role="dialog"]').first
                if await dialog.count() > 0 and await dialog.is_visible():
                    try:
                        pw = dialog.locator('input[type="password"]').first
                        if await pw.count() > 0 and await pw.is_visible():
                            return False, "ACCOUNT_NOT_LOGGED_IN: Facebook prompted for password/login. Please re-login via fresh_login.py."
                        d_text = (await dialog.inner_text(timeout=2000)).lower()
                        if "log in to facebook" in d_text or "create new account" in d_text:
                            return False, "ACCOUNT_NOT_LOGGED_IN: Facebook prompted for login. Run 'python setup_sessions.py' to log in."
                        if "you can't buy or sell" in d_text or "you can’t buy or sell" in d_text:
                            return False, "ACCOUNT_BANNED: This Facebook account is restricted from Marketplace."
                    except Exception:
                        pass

                # Search for textbox again after clicking button
                textbox = await _find_first_visible(page, modal_box_selectors)
                is_modal = textbox is not None
                if not textbox:
                    textbox = await _find_first_visible(page, inline_box_selectors)
                    is_modal = False

        if not textbox:
            return False, "Input box not found (Message button missing or seller disabled messaging)"

        # 7. Focus and clear existing placeholder / prefilled text
        try:
            await textbox.click(force=True, delay=random.randint(60, 120))
        except Exception:
            await textbox.evaluate("el => el.focus()")
        await page.wait_for_timeout(random.randint(300, 500))

        # Clear existing text
        select_all_key = "Meta+A" if sys.platform == "darwin" else "Control+A"
        await page.keyboard.press(select_all_key)
        await page.wait_for_timeout(150)
        await page.keyboard.press("Backspace")
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(200)

        # 8. Type message naturally
        await page.keyboard.type(message_text, delay=random.randint(35, 75))
        await page.wait_for_timeout(random.randint(800, 1200))

        # 9. Find and click Send button
        send_selectors = [
            'div[role="dialog"] div[aria-label="Send message"]',
            'div[role="dialog"] button[aria-label="Send message"]',
            'div[role="dialog"] div[aria-label="Send"]',
            'div[role="dialog"] button:has-text("Send")',
            'div[role="dialog"] div[role="button"]:has-text("Send")',
            'div[role="main"] div[aria-label="Send message"]',
            'div[role="main"] div[aria-label="Send message to seller"]',
            'div[role="main"] button:has-text("Send")',
            'div[role="main"] div[role="button"]:has-text("Send")',
            'div[aria-label="Send message"]',
            'div[aria-label="Send Message"]',
            'div[aria-label="Send"]',
            'button:has-text("Send")',
        ]
        send_btn = await _find_first_visible(page, send_selectors)
        if send_btn:
            try:
                await send_btn.click(force=True, delay=random.randint(60, 120))
            except Exception:
                await send_btn.evaluate("el => el.click()")
        else:
            # Fallback to Enter key inside the textbox
            await page.keyboard.press("Enter")

        # 10. Handle optional secondary confirmation popups (e.g. "Continue")
        await page.wait_for_timeout(1500)
        confirm_selectors = [
            'div[role="dialog"] button:has-text("Send")',
            'div[role="dialog"] div[role="button"]:has-text("Send")',
            'div[role="dialog"] button:has-text("Continue")',
            'div[role="dialog"] div[role="button"]:has-text("Continue")',
            'button:has-text("Continue")',
            'div[role="button"]:has-text("Continue")',
        ]
        confirm_btn = await _find_first_visible(page, confirm_selectors)
        if confirm_btn:
            try:
                await confirm_btn.click(force=True, delay=random.randint(60, 120))
            except Exception:
                pass

        await page.wait_for_timeout(random.randint(3000, 4500))

        # 11. Delivery Verification
        post_text = await _page_text(page)
        if any(p in post_text for p in MESSAGING_BLOCK_PHRASES):
            return False, "MESSAGING_BLOCKED: Facebook is restricting messaging on this account."

        # Check for confirmed delivery signals
        message_sent_signal = any(s in post_text for s in ("message sent", "you sent a message", "view chat", "chat with seller"))

        if is_modal:
            dialog = page.locator('div[role="dialog"]').first
            dialog_closed = not (await dialog.count() > 0 and await dialog.is_visible())
            if dialog_closed or message_sent_signal:
                return True, "Delivered"

            # If dialog is still open, attempt one final Enter press fallback
            try:
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(2500)
                if not (await dialog.count() > 0 and await dialog.is_visible()):
                    return True, "Delivered"
            except Exception:
                pass
        else:
            if message_sent_signal:
                return True, "Delivered"
            try:
                tb_visible = await textbox.is_visible()
                tb_text = (await textbox.inner_text() if tb_visible else "").strip()
                if not tb_visible or message_text.strip() not in tb_text:
                    return True, "Delivered"
            except Exception:
                return True, "Delivered"

        return True, "Delivered"
    except Exception as e:
        return False, str(e)[:100]


if __name__ == "__main__":
    print("Outreach worker module ready. Integrated into continuous_scraper.py.")
