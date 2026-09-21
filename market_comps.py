"""Market value for a phone: local median asking price of similar phones seen on Marketplace
plus Gemini AI resale value estimate.
"""
import statistics
import time
import re
from datetime import datetime, timedelta, timezone
import settings

_local_cache: dict[str, tuple[list[float], float]] = {}  # slug -> (recent database prices, fetched at)


def _trimmed_median(prices: list[float]) -> float:
    prices = sorted(prices)
    if len(prices) > 4:
        cut = max(1, int(len(prices) * 0.2))
        prices = prices[cut:-cut]
    return round(statistics.median(prices), 2) if prices else 0.0


def base_slug(slug: str) -> str:
    """'iphone_16_pro_max_256gb' -> 'iphone_16_pro_max' (listings that don't mention storage are usually base storage)."""
    return re.sub(r"_\d+gb$", "", slug or "")


def slug_to_query(slug: str) -> str:
    return slug.replace("_", " ").replace("gb", " gb").strip()


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
            rows = (
                supabase.table("listings")
                .select("price")
                .in_("normalized_model", list({slug, base_slug(slug)}))
                .in_("condition_grade", ["MINT", "GOOD", "UNKNOWN"])
                .gte("created_at", since)
                .limit(300)
                .execute()
                .data
            )
        except Exception:
            rows = []
        prices = [r["price"] for r in rows if r.get("price")]
        _local_cache[slug] = (prices, time.time())

    prices = prices + list(extra_prices or [])
    value = _trimmed_median(prices) if len(prices) >= settings.LOCAL_COMP_MIN_SAMPLES else 0.0
    return value, len(prices)


def market_value(local: float, local_n: int, ai_estimate: float = 0.0) -> tuple[float, str]:
    """Market value: local asking median if enough samples exist, otherwise Gemini AI estimate."""
    if local and local_n >= settings.LOCAL_COMP_MIN_SAMPLES:
        return local, f"local({local_n})"
    if ai_estimate:
        return float(ai_estimate), "ai_estimate"
    return 0.0, "none"
