"""Tunable limits for the scraper and outreach.

Defaults are deliberately conservative to keep both Facebook accounts safe.
The most useful ones can be overridden in .env without touching code.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


# Facebook accounts - configure how many accounts are actively logged in (default 1).
# When you log in Account_2 later, set NUM_ACTIVE_ACCOUNTS=2 in .env without changing code.
NUM_ACTIVE_ACCOUNTS = _int("NUM_ACTIVE_ACCOUNTS", 1)
ALL_FB_ACCOUNTS = [
    {"id": "Account_1", "session_dir": "./fb_session_acc1", "viewport": {"width": 1366, "height": 768}},
    {"id": "Account_2", "session_dir": "./fb_session_acc2", "viewport": {"width": 1440, "height": 900}},
]
FB_ACCOUNTS = ALL_FB_ACCOUNTS[:max(1, min(NUM_ACTIVE_ACCOUNTS, len(ALL_FB_ACCOUNTS)))]

TIMEZONE = os.getenv("BOT_TIMEZONE", "America/Toronto")
LOCALE = "en-CA"

# Active hours in local time - outside them both accounts sit idle like a sleeping human (default 7am-1am)
ACTIVE_START_HOUR = _int("ACTIVE_START_HOUR", 7)
ACTIVE_END_HOUR = _int("ACTIVE_END_HOUR", 1)

# Per-account browsing limits
MAX_SEARCHES_PER_HOUR = _int("MAX_SEARCHES_PER_HOUR", 16)
MAX_DETAIL_VIEWS_PER_HOUR = _int("MAX_DETAIL_VIEWS_PER_HOUR", 12)
SEARCH_GAP_SECONDS = (90, 200)        # pause between two searches of the same account
LONG_BREAK_EVERY = (7, 11)            # searches before a longer break
LONG_BREAK_MINUTES = (6, 18)
STAGGER_START_SECONDS = (45, 120)     # second account starts later so the two never move in lockstep
MAX_CARDS_PER_SEARCH = 40
MAX_SCROLLS_PER_SEARCH = 6
CAUGHT_UP_AFTER_SEEN = 6              # stop scrolling after this many already-seen listings in a row

# First-message limits per account
MAX_MESSAGES_PER_DAY = _int("MAX_MESSAGES_PER_DAY", 8)
MIN_MINUTES_BETWEEN_MESSAGES = _int("MIN_MINUTES_BETWEEN_MESSAGES", 20)
OUTREACH_DRY_RUN = _bool("OUTREACH_DRY_RUN", False)   # log instead of sending (for testing)

# Gemini usage - shared by both accounts and rotated across the API keys
GEMINI_DAILY_CALLS_PER_KEY = _int("GEMINI_DAILY_CALLS_PER_KEY", 400)
GEMINI_MIN_SECONDS_BETWEEN_CALLS = 4.0   # per key
TRIAGE_BATCH_SIZE = 8
# Lite models first (~1s, no thinking tokens); heavier models are only a fallback
LITE_MODELS = ["gemini-2.5-flash-lite", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]
FALLBACK_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest", "gemini-3-flash-preview"]
NO_THINKING_MODELS = {"gemini-3.5-flash", "gemini-3.6-flash"}   # accept thinking_budget=0, which halves their tokens
MAX_PHOTOS_PER_DEAL = 3

# Deal logic
AT_MARKET_RATIO = 0.92            # skip the AI when asking >= 92% of a known market value
PRICE_DROP_RATIO = 0.90           # look at a seen listing again only if its price drops to <= 90%
LOCAL_COMP_DAYS = 10
LOCAL_COMP_MIN_SAMPLES = 3
SEEN_LOOKBACK_DAYS = 30

# Hands-off running
DAILY_SUMMARY_HOUR = _int("DAILY_SUMMARY_HOUR", 21)   # local hour for the daily Telegram summary
ACCOUNT_STATE_FILE = "account_state.json"             # remembers flagged accounts so a restart never retries them alone
BLOCKED_RETRY_HOURS = 24                              # a "temporarily blocked" account gets one retry after this long
MAX_WORKER_RESTARTS_PER_HOUR = 4                      # a crashing account browser is restarted at most this often
