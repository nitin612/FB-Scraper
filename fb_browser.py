"""Browser launch and human-like actions shared by the scraper and setup_sessions.py."""
import asyncio
import json
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
    "you can't use this feature right now", "you’re restricted from",
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
    since = datetime.fromisoformat(entry["since"])
    if entry["problem"] == "BLOCKED" and datetime.now() - since > timedelta(hours=settings.BLOCKED_RETRY_HOURS):
        return None
    return f"{entry['problem']} since {since:%b %d %H:%M}"


def browser_args() -> list[str]:
    args = ["--disable-blink-features=AutomationControlled"]
    if sys.platform.startswith("linux"):
        # Needed on cloud servers / Docker (see server_setup.sh); on Windows/macOS they only add a warning bar
        args += ["--no-sandbox", "--disable-dev-shm-usage"]
    return args


async def launch_account(playwright, account: dict):
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


async def account_problem(page) -> str | None:
    """None if the account looks healthy, otherwise LOGGED_OUT / CHECKPOINT / BLOCKED."""
    url = page.url.lower()
    if "/checkpoint" in url:
        return "CHECKPOINT"
    if "/login" in url or "login.php" in url:
        return "LOGGED_OUT"
    try:
        text = (await page.locator("body").inner_text(timeout=4000))[:3000].lower()
    except Exception:
        return None
    if any(p in text for p in BLOCK_PHRASES):
        return "BLOCKED"
    if sum(p in text for p in LOGGED_OUT_PHRASES) >= 2:
        return "LOGGED_OUT"
    return None
