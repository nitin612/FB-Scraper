"""Supabase access, alerts and time helpers shared by the account workers in continuous_scraper.py."""
import os
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from supabase import create_client, Client
import sys
import subprocess
import json
import settings
from telegram_bot import send_telegram_alert, esc   # re-exported for continuous_scraper

load_dotenv()
supabase_url = os.getenv("SUPABASE_URL") or ""
supabase_key = os.getenv("SUPABASE_KEY") or ""
supabase: Client = create_client(supabase_url, supabase_key)

REQUIRED_COLUMNS = ["listed_at", "normalized_model","live_comp_price", "local_comp_price", "market_value", "comp_source", "price_status",
                    "estimated_repair_cost", "projected_profit", "condition_grade", "scam_risk", "description", "location",
                    "fingerprint", "filter_reason", "previous_price", "found_by", "seller_id", "seller_name",
                    "contacted_by", "contacted_at", "cheaper_deal_id", "cheaper_deal_url", "cheaper_deal_price"]
CONDITION_BUCKETS = (["MINT", "GOOD", "UNKNOWN"], ["FAIR", "DAMAGED"], ["LOCKED", "PARTS"])


def check_schema() -> list[str]:
    try:
        supabase.table("listings").select(",".join(REQUIRED_COLUMNS)).limit(1).execute()
        return []
    except Exception:
        pass
    missing = []   # something is missing - find out exactly what
    for column in REQUIRED_COLUMNS:
        try:
            supabase.table("listings").select(column).limit(1).execute()
        except Exception:
            missing.append(column)
    return missing


def local_now() -> datetime:
    return datetime.now(ZoneInfo(settings.TIMEZONE))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ago_to_iso(seconds: int | None) -> str | None:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat() if seconds is not None else None


def local_midnight_utc_iso() -> str:
    midnight = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).isoformat()


def in_active_hours() -> bool:
    hour, start, end = local_now().hour, settings.ACTIVE_START_HOUR, settings.ACTIVE_END_HOUR
    return start <= hour < end if start < end else (hour >= start or hour < end)


def send_desktop_alert(title: str, message: str):
    try:
        if sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e", "on run argv",
                    "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                    "-e", "end run",
                    str(title),
                    str(message),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "win32":
            try:
                from plyer import notification
                notification.notify(title=title, message=message, app_name="Deal Hunter", timeout=10)
            except Exception:
                pass
        else:
            try:
                subprocess.run(["notify-send", str(title), str(message)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
    except Exception:
        pass


CONFIG_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".runtime_config.json")


def save_local_config(cfg: dict):
    """Atomically writes config to local runtime cache so the scraper detects changes in real-time."""
    try:
        tmp_file = CONFIG_CACHE_FILE + ".tmp"
        with open(tmp_file, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp_file, CONFIG_CACHE_FILE)
    except Exception:
        pass


def load_local_config() -> dict | None:
    """Loads config from local runtime cache if available."""
    try:
        if os.path.exists(CONFIG_CACHE_FILE):
            with open(CONFIG_CACHE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def save_bot_config(updates: dict) -> dict | None:
    """Updates Supabase and writes immediately to local cache file for instant reflection."""
    updated = None
    try:
        res = supabase.table("bot_settings").update(updates).eq("id", 1).execute()
        if res.data:
            updated = res.data[0]
            save_local_config(updated)
            return updated
    except Exception as e:
        print(f"⚠️ Error saving bot config to Supabase: {e}")
    # Fallback to updating local cache
    current = load_local_config() or {}
    current.update(updates)
    save_local_config(current)
    return current


def load_bot_config() -> dict | None:
    """Fetches config from Supabase and syncs local cache, falling back to local cache if offline."""
    try:
        res = supabase.table("bot_settings").select("*").eq("id", 1).execute()
        if res.data:
            save_local_config(res.data[0])
            return res.data[0]
    except Exception:
        pass
    return load_local_config()


def load_seen() -> tuple[dict[str, tuple[float, str]], dict[str, str]]:
    """Recent listings (incl. hidden trash) as {id: (price, status)} and {fingerprint: id}.
    Paged 1000 rows at a time because Supabase caps a single response at 1000 rows."""
    since = (datetime.now(timezone.utc) - timedelta(days=settings.SEEN_LOOKBACK_DAYS)).isoformat()
    seen, fingerprints, start = {}, {}, 0
    while True:
        rows = (supabase.table("listings").select("id,price,status,fingerprint")
                .gte("created_at", since).order("created_at").range(start, start + 999).execute().data)
        for r in rows:
            seen[r["id"]] = (r.get("price") or 0.0, r.get("status") or "")
            if r.get("fingerprint"):
                fingerprints[r["fingerprint"]] = r["id"]
        if len(rows) < 1000:
            return seen, fingerprints
        start += 1000


def lookup_existing(ids: list[str]) -> dict[str, tuple[float, str]]:
    """Older listings that are outside the in-memory window but already in the database."""
    if not ids:
        return {}
    rows = supabase.table("listings").select("id,price,status").in_("id", ids).execute().data
    return {r["id"]: (r.get("price") or 0.0, r.get("status") or "") for r in rows}


def save_listing(row: dict):
    supabase.table("listings").upsert(row).execute()


def update_listing(listing_id: str, fields: dict):
    supabase.table("listings").update(fields).eq("id", listing_id).execute()


def evaluate_intraday_pricing(normalized_model: str, condition_grade: str, current_price: float, current_id: str) -> dict:
    """Checks if a cheaper listing of the same model AND condition was found in the last 24h (hidden trash included)."""
    if not normalized_model or normalized_model == "unknown":
        return {"status": "NORMAL", "cheaper_deal": None, "badge": ""}
    bucket = next((b for b in CONDITION_BUCKETS if condition_grade in b), CONDITION_BUCKETS[0])
    one_day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    res = supabase.table("listings") \
        .select("id, price, url, title") \
        .eq("normalized_model", normalized_model) \
        .in_("condition_grade", bucket) \
        .gte("created_at", one_day_ago) \
        .order("price", desc=False) \
        .execute()

    records = [r for r in res.data if r["id"] != current_id and (r["price"] or 0) > 0]
    if not records:
        return {"status": "FIRST_TODAY", "cheaper_deal": None, "badge": "🆕 First of the Day"}

    lowest_recorded = records[0]
    if current_price < lowest_recorded["price"]:
        savings = lowest_recorded["price"] - current_price
        return {"status": "BEST_OF_DAY", "cheaper_deal": None, "badge": f"🏆 NEW LOW (-${savings:,.0f})"}
    elif current_price > lowest_recorded["price"]:
        diff = current_price - lowest_recorded["price"]
        return {"status": "OVERPRICED", "cheaper_deal": lowest_recorded, "badge": f"⚠️ OVERPRICED (+${diff:,.0f})"}
    return {"status": "MATCHED_LOW", "cheaper_deal": None, "badge": "⚖️ Matches Today's Low"}


def messages_sent_today(account_id: str) -> tuple[int, float]:
    """(messages sent today by this account, unix time of the latest one) - survives restarts."""
    rows = (supabase.table("listings").select("contacted_at").eq("contacted_by", account_id)
            .gte("contacted_at", local_midnight_utc_iso()).execute().data)
    stamps = [datetime.fromisoformat(r["contacted_at"]).timestamp() for r in rows if r.get("contacted_at")]
    return len(stamps), max(stamps, default=0.0)


APPROVED_PREFIX = "📲"   # outreach_log marker for deals the user approved from Telegram


def next_pending_outreach(exclude: set, approved_only: bool = False) -> dict | None:
    """Oldest queued deal - but deals the user approved from Telegram always go first."""
    rows = (supabase.table("listings").select("*").eq("status", "PENDING_OUTREACH")
            .order("created_at", desc=False).limit(25).execute().data)
    rows = [r for r in rows if r["id"] not in exclude]
    approved = [r for r in rows if str(r.get("outreach_log") or "").startswith(APPROVED_PREFIX)]
    if approved or approved_only:
        return approved[0] if approved else None
    return rows[0] if rows else None


def get_listing(listing_id: str) -> dict | None:
    rows = supabase.table("listings").select("*").eq("id", listing_id).limit(1).execute().data
    return rows[0] if rows else None


def count_listings(since_iso: str, column: str = "created_at", **filters) -> int:
    q = supabase.table("listings").select("id", count="exact").gte(column, since_iso)
    for col, value in filters.items():
        q = q.eq(col, value)
    return q.limit(1).execute().count or 0


def top_deals_since(since_iso: str, limit: int = 3) -> list[dict]:
    return (supabase.table("listings").select("title,price,projected_profit,deal_tier,url")
            .gte("created_at", since_iso).neq("status", "TRASH")
            .order("projected_profit", desc=True, nullsfirst=False).limit(limit).execute().data)


def seller_already_contacted(seller_id: str | None) -> bool:
    if not seller_id:
        return False
    rows = (supabase.table("listings").select("id").eq("seller_id", seller_id)
            .not_.is_("contacted_at", "null").limit(1).execute().data)
    return bool(rows)


def download_photos(urls: list[str]) -> list[bytes]:
    photos = []
    for url in urls:
        try:
            res = requests.get(url, timeout=15)
            if res.status_code == 200 and res.headers.get("content-type", "").startswith("image"):
                photos.append(res.content)
        except Exception:
            pass
    return photos
