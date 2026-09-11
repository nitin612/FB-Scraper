"""Market value for a phone: eBay sold listings (best effort, through a real browser tab)
plus the median asking price of similar phones this bot has already seen on Marketplace."""
import asyncio
import re
import statistics
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
import settings

EBAY_PRICE_RE = re.compile(r"(?:C\s?)?\$\s?([\d,]+(?:\.\d{2})?)")
EBAY_SKIP_RE = re.compile(r"\b(for parts|not working|broken|cracked|locked|icloud|case|cover|box only|screen protector|lot of)\b", re.I)

_ebay_cache: dict[str, tuple[float, float]] = {}      # slug -> (value, fetched at)
_ebay_state = {"fails": 0, "off_until": 0.0, "last": 0.0}
_ebay_lock = asyncio.Lock()                           # one eBay page at a time across both accounts
_local_cache: dict[str, tuple[list[float], float]] = {}  # slug -> (recent database prices, fetched at)


def _trimmed_median(prices: list[float]) -> float:
    # Remove the top 20% and bottom 20% to eliminate outliers (fake bids / broken items)
    prices = sorted(prices)
    if len(prices) > 4:
        cut = max(1, int(len(prices) * 0.2))
        prices = prices[cut:-cut]
    return round(statistics.median(prices), 2)


def base_slug(slug: str) -> str:
    """'iphone_16_pro_max_256gb' -> 'iphone_16_pro_max' (listings that don't mention storage are usually base storage)."""
    return re.sub(r"_\d+gb$", "", slug or "")


def slug_to_query(slug: str) -> str:
    return slug.replace("_", " ").replace("gb", " gb").strip()


async def get_ebay_sold_comp(context, slug: str) -> float:
    """Median of recent eBay.ca sold prices for working units, or 0.0 when unavailable."""
    if not settings.EBAY_COMPS or not slug:
        return 0.0
    cached = _ebay_cache.get(slug)
    if cached and time.time() - cached[1] < settings.EBAY_CACHE_HOURS * 3600:
        return cached[0]
    if time.time() < _ebay_state["off_until"]:
        return 0.0

    async with _ebay_lock:
        wait = _ebay_state["last"] + 20 - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        _ebay_state["last"] = time.time()
        page = await context.new_page()
        try:
            url = f"https://www.ebay.ca/sch/i.html?_nkw={urllib.parse.quote_plus(slug_to_query(slug))}&LH_Sold=1&LH_Complete=1&_ipg=60"
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if not resp or resp.status >= 400:
                _ebay_state["fails"] += 1
                if _ebay_state["fails"] >= 3:
                    _ebay_state["fails"] = 0
                    _ebay_state["off_until"] = time.time() + 6 * 3600
                    print(f"   ⚠️ eBay is blocking this connection (HTTP {resp.status if resp else '?'}) - using local comps / AI estimates for the next 6h")
                return 0.0
            await page.wait_for_timeout(2500)
            prices = []
            for text in await page.locator("li.s-card, li.s-item").all_inner_texts():
                if EBAY_SKIP_RE.search(text) or "shop on ebay" in text.lower():
                    continue
                if m := EBAY_PRICE_RE.search(text):
                    value = float(m.group(1).replace(",", ""))
                    if value > 40:
                        prices.append(value)
            _ebay_state["fails"] = 0
            value = _trimmed_median(prices) if len(prices) >= 3 else 0.0
            _ebay_cache[slug] = (value, time.time())
            return value
        except Exception as e:
            print(f"   ⚠️ eBay Comp Engine Error: {str(e)[:80]}")
            return 0.0
        finally:
            await page.close()


def ebay_status() -> str:
    if not settings.EBAY_COMPS:
        return "off"
    if time.time() < _ebay_state["off_until"]:
        return "blocked from this connection - using local comps"
    return "on"


def get_local_comp(supabase, slug: str, extra_prices: list[float] | None = None) -> tuple[float, int]:
    """Median asking price of similar, undamaged listings: recent database rows (incl. hidden trash)
    plus prices from the search page that is open right now."""
    if not slug:
        return 0.0, 0
    cached = _local_cache.get(slug)
    if cached and time.time() - cached[1] < 1800:
        prices = cached[0]
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=settings.LOCAL_COMP_DAYS)).isoformat()
        try:
            rows = (supabase.table("listings").select("price")
                    .in_("normalized_model", list({slug, base_slug(slug)}))
                    .in_("condition_grade", ["MINT", "GOOD", "UNKNOWN"])
                    .gte("created_at", since).limit(300).execute().data)
        except Exception:
            rows = []
        prices = [r["price"] for r in rows if r.get("price")]
        _local_cache[slug] = (prices, time.time())
    prices = prices + list(extra_prices or [])
    value = _trimmed_median(prices) if len(prices) >= settings.LOCAL_COMP_MIN_SAMPLES else 0.0
    return value, len(prices)


def market_value(ebay: float, local: float, local_n: int, ai_estimate: float = 0.0) -> tuple[float, str]:
    """Conservative resale value: the lower of eBay sold and local asking prices, else whichever exists."""
    if ebay and local:
        return min(ebay, local), f"ebay+local({local_n})"
    if local:
        return local, f"local({local_n})"
    if ebay:
        return ebay, "ebay"
    if ai_estimate:
        return float(ai_estimate), "ai_estimate"
    return 0.0, "none"
