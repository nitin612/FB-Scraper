"""Browser launch and human-like actions shared by the scraper and setup_sessions.py."""
import asyncio
import json
import os
import random
import sys
from datetime import datetime, timedelta
from playwright_stealth import Stealth
import settings

# Phrases Facebook shows when it restricts an account - on sight, that account stops
BLOCK_PHRASES = (
    "you're temporarily blocked", "you’re temporarily blocked", "temporarily restricted",
    "your account has been locked", "we suspended your account", "your account has been suspended",
    "confirm your identity", "we noticed unusual activity", "suspicious activity",
    "you can't use this feature right now", "you’re restricted from", "you can't buy or sell",
    "you can’t buy or sell", "restore your access to marketplace",
)
LOGGED_OUT_PHRASES = ("log in to facebook", "log into facebook", "create new account")


def load_account_state() -> dict:
    try:
        with open(settings.ACCOUNT_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_account_state(state: dict):
    with open(settings.ACCOUNT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def mark_account_problem(account_id: str, problem: str):
    state = load_account_state()
    state[account_id] = {"problem": problem, "since": datetime.now().isoformat(timespec="minutes")}
    _save_account_state(state)


def clear_account_problem(account_id: str):
    state = load_account_state()
    if state.pop(account_id, None) is not None:
        _save_account_state(state)


def account_pause_reason(account_id: str) -> str | None:
    """Why this account must not be started, or None. Logged-out / checkpoint accounts wait for setup;
    a temporary block gets one retry after BLOCKED_RETRY_HOURS."""
    entry = load_account_state().get(account_id)
    if not entry:
        return None
    import dateutil.parser
    try:
        since = dateutil.parser.isoparse(entry.get("since", ""))
    except Exception:
        try:
            since = datetime.fromisoformat(entry.get("since", ""))
        except Exception:
            since = datetime.now()
    if entry.get("problem") == "BLOCKED" and datetime.now() - since > timedelta(hours=settings.BLOCKED_RETRY_HOURS):
        return None
    return f"{entry.get('problem')} since {since:%b %d %H:%M}"


def browser_args() -> list[str]:
    args = ["--disable-blink-features=AutomationControlled"]
    if sys.platform.startswith("linux"):
        # Needed on cloud servers / Docker (see server_setup.sh); on Windows/macOS they only add a warning bar
        args += [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
        ]
    return args


async def _state_file(account: dict) -> str | None:
    """Returns path to portable storage_state JSON for this account, or None."""
    for candidate in (
        f"state_{account['id'].lower().replace('account_', 'acc')}.json",
        f"state_{account['id'].lower()}.json",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


async def launch_account(playwright, account: dict):
    if sys.platform.startswith("linux"):
        # On Linux/cloud: use a fresh native profile + inject cookies from state JSON.
        # Using fb_session_acc1 (Mac-imported) causes cookie conflicts — use a separate dir.
        linux_dir = account["session_dir"].rstrip("/") + "_linux"
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=linux_dir,
            headless=False,
            args=browser_args(),
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE,
            viewport=account["viewport"],
        )
        # Inject fresh decrypted cookies from the portable state JSON
        state_file = await _state_file(account)
        if state_file:
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                cookies = state_data.get("cookies", [])
                if cookies:
                    await context.add_cookies(cookies)
                    print(f"[{account['id']}] 🔑 Loaded {len(cookies)} cookies from {state_file}")
            except Exception as e:
                print(f"[{account['id']}] ⚠️ Could not load cookies from {state_file}: {e}")
    else:
        # On macOS: native Keychain-encrypted persistent profile works as-is
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=account["session_dir"],
            headless=False,
            args=browser_args(),
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE,
            viewport=account["viewport"],
        )
    await Stealth().apply_stealth_async(context)
    return context


async def pause(low: float, high: float):
    await asyncio.sleep(random.uniform(low, high))


async def human_scroll(page, steps: int = 1):
    for _ in range(steps):
        await page.mouse.move(random.randint(200, 1100), random.randint(150, 600), steps=random.randint(4, 12))
        await page.mouse.wheel(0, random.randint(380, 880))
        await pause(0.9, 2.3)


async def is_account_logged_in(context) -> bool:
    """True if Facebook's c_user session cookie exists, proving the user is logged in."""
    try:
        cookies = await context.cookies("https://www.facebook.com")
        return any(c.get("name") == "c_user" for c in cookies)
    except Exception:
        return False


async def account_problem(page) -> str | None:
    """None if the account looks healthy, otherwise LOGGED_OUT / CHECKPOINT / BLOCKED."""
    try:
        url = page.url.lower()
        title = await page.title()
    except Exception:
        url, title = "", ""

    if "/checkpoint" in url:
        print(f"⚠️ [Account Check] CHECKPOINT detected. URL: {page.url} | Title: {title}")
        try:
            await page.screenshot(path="debug_checkpoint.png")
            print("📸 Saved debug screenshot to 'debug_checkpoint.png'")
        except Exception:
            pass
        return "CHECKPOINT"

    if "/login" in url or "login.php" in url:
        print(f"⚠️ [Account Check] LOGGED_OUT detected (redirected to login URL). URL: {page.url} | Title: {title}")
        try:
            await page.screenshot(path="debug_login.png")
            print("📸 Saved debug screenshot to 'debug_login.png'")
        except Exception:
            pass
        return "LOGGED_OUT"

    try:
        text = (await page.locator("body").inner_text(timeout=4000))[:3000].lower()
    except Exception:
        return None

    for phrase in BLOCK_PHRASES:
        if phrase in text:
            print(f"⚠️ [Account Check] BLOCKED phrase detected: '{phrase}'. URL: {page.url}")
            try:
                await page.screenshot(path="debug_blocked.png")
            except Exception:
                pass
            return "BLOCKED"

    matched_logged_out = [p for p in LOGGED_OUT_PHRASES if p in text]
    if len(matched_logged_out) >= 2 and ("login" in url or "marketplace" not in url):
        print(f"⚠️ [Account Check] LOGGED_OUT phrases detected: {matched_logged_out}. URL: {page.url}")
        try:
            await page.screenshot(path="debug_login.png")
            print("📸 Saved debug screenshot to 'debug_login.png'")
        except Exception:
            pass
        return "LOGGED_OUT"

    return None
