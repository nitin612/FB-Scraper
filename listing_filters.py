"""Free, local checks on Marketplace search cards so Gemini only sees listings worth a look."""
import re
from dataclasses import dataclass

PRICE_RE = re.compile(r"(?:CA\$|C\$|\$)\s*([\d,]+)")
LOCATION_RE = re.compile(r"^[A-Za-zÀ-ÿ .'\-]+,\s*[A-Z]{2}$")
NOISE_LINES = {"just listed", "sponsored", "pending", "sold"}

STORAGE_RE = re.compile(r"\b(\d{1,4})\s*(gb|tb)\b", re.I)
VALID_STORAGE = {16, 32, 64, 128, 256, 512, 1024, 2048}
IPHONE_RE = re.compile(r"\biphone\s*(se|air|x[rs]?|\d{1,2})\s*(pro\s*max|pro|plus|max|mini|air|e)?\b", re.I)
GALAXY_RE = re.compile(r"\b(s\d{1,2}|note\s*\d{1,2}|z\s*fold\s*\d|z\s*flip\s*\d|a\d{2})\s*(ultra|plus|\+|fe)?(?![a-z0-9])", re.I)
PIXEL_RE = re.compile(r"\bpixel\s*(\d{1,2})\s*(a)?\s*(pro\s*xl|pro\s*fold|pro|xl|fold)?\b", re.I)

WANTED_RE = re.compile(r"\b(looking for|wanted|wtb|want to buy|iso|in search of|buying|we buy|cash for)\b", re.I)
SERVICE_RE = re.compile(r"\b(repair|unlock(?:ing)?|fix(?:ing)?)\s+(services?|shop|store|special)\b|\bwe\s+(fix|repair|unlock)\b", re.I)
ACCESSORY_RE = re.compile(r"\b(case|cases|cover|covers|charger|chargers|charging|cable|screen protector|tempered glass|protectors?|"
                          r"box only|empty box|mount|holder|skin|strap|adapter|dock|earbuds|earphones|headphones|stylus|power bank|battery pack)\b", re.I)
FOR_DEVICE_RE = re.compile(r"\bfor\s+(?:an?\s+|the\s+)?(iphone|samsung|galaxy|pixel)\b", re.I)
WITH_EXTRAS_RE = re.compile(r"\b(with|w/|comes with|includes?|including|\+|and)\s+(?:a\s+|the\s+|original\s+)?(case|cases|charger|box|cover|cable)\b", re.I)
# Phones only - the keywords (samsung, pixel...) also return TVs, appliances, tablets, watches and earbuds
NOT_PHONE_RE = re.compile(r"\b(tv|television|fridge|refrigerator|washer|dryer|monitor|microwave|oven|dishwasher|soundbar|freezer|stove|"
                          r"air conditioner|vacuum|printer|speaker|watch|smartwatch|tablet|tab|ipad|laptop|macbook|chromebook|buds)\b", re.I)

LOCKED_RE = re.compile(r"\b(icloud|activation lock|mdm|blacklist(?:ed)?|bad imei|financed|google lock|frp)\b", re.I)
PARTS_RE = re.compile(r"\b(for parts|parts only|as is|not working|(?:doesn'?t|won'?t) (?:turn|power) on|no power|water damaged?)\b", re.I)
DAMAGED_RE = re.compile(r"\b(crack(?:ed|s)?|shattered|broken|damaged?|dent(?:ed|s)?|lines? on|green line|burn[- ]?in|dead pixels?|"
                        r"bad battery|needs? (?:a )?(?:new )?(?:screen|battery))\b", re.I)
MINT_RE = re.compile(r"\b(mint|like new|brand new|sealed|new in box|bnib|open box|flawless|10/10|excellent)\b", re.I)


LISTED_RE = re.compile(r"\blisted\s+(?:over\s+)?(just now|an?|\d+)\s*(minute|hour|day|week|month|year)?s?\b", re.I)
AGE_UNITS = {"minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000, "year": 31536000}


def listed_ago_seconds(text: str) -> int | None:
    """'Listed 3 hours ago in Toronto, ON' -> 10800. None if the page doesn't say."""
    m = LISTED_RE.search(text)
    if not m:
        return None
    if m.group(1).lower() == "just now":
        return 0
    if not m.group(2):
        return None
    count = 1 if m.group(1).lower() in ("a", "an") else int(m.group(1))
    return count * AGE_UNITS[m.group(2).lower()]


@dataclass
class Card:
    price: float
    previous_price: float | None
    title: str
    location: str


def parse_card(text: str) -> Card:
    """Card text looks like 'CA$320\\nCA$350\\nPixel 7 mint\\nMississauga, ON' (current price, optional old price, title, location)."""
    prices, title, location = [], "", ""
    for line in (l.strip() for l in text.splitlines()):
        if not line or line.lower() in NOISE_LINES:
            continue
        if line.lower() == "free":
            prices.append(0.0)
        elif PRICE_RE.fullmatch(line):
            prices.append(float(PRICE_RE.fullmatch(line).group(1).replace(",", "")))
        elif LOCATION_RE.match(line) and title:
            location = line
        elif not title:
            title = line
    price = prices[0] if prices else 0.0
    previous = prices[1] if len(prices) > 1 and prices[1] > price else None
    return Card(price, previous, title, location)


def _storage(text: str) -> int:
    sizes = []
    for num, unit in STORAGE_RE.findall(text):
        size = int(num) * (1024 if unit.lower() == "tb" else 1)
        if size in VALID_STORAGE:
            sizes.append(size)
    return max(sizes) if sizes else 0


def detect_model(text: str) -> tuple[str | None, str | None]:
    """Deterministic model slug like 'iphone_13_pro_128gb' so the same phone always groups together."""
    t = text.lower()
    if m := IPHONE_RE.search(t):
        variant = re.sub(r"\s+", "_", (m.group(2) or "").strip())
        if "air" in (m.group(1), variant):
            brand, base = "apple", "iphone_air"   # "iPhone Air" and "iPhone 17 Air" are the same phone
        else:
            brand, base = "apple", f"iphone_{m.group(1)}" + (f"_{variant}" if variant else "")
    elif ("galaxy" in t or "samsung" in t) and not re.search(r"\btab\b", t) and (m := GALAXY_RE.search(t)):
        variant = (m.group(2) or "").replace("+", "plus")
        brand, base = "samsung", "galaxy_" + re.sub(r"\s+", "_", m.group(1)) + (f"_{variant}" if variant else "")
    elif m := PIXEL_RE.search(t):
        variant = re.sub(r"\s+", "_", m.group(3)) if m.group(3) else ""
        brand, base = "google", f"pixel_{m.group(1)}{m.group(2) or ''}" + (f"_{variant}" if variant else "")
    else:
        return None, None
    storage = _storage(t)
    return brand, base + (f"_{storage}gb" if storage else "")


def condition_from_text(text: str) -> str:
    if LOCKED_RE.search(text):
        return "LOCKED"
    if PARTS_RE.search(text):
        return "PARTS"
    if DAMAGED_RE.search(text):
        return "DAMAGED"
    if MINT_RE.search(text):
        return "MINT"
    return "UNKNOWN"


def local_filter(card: Card, has_model: bool, min_price: float, max_price: float) -> str | None:
    """Returns why a card can be skipped without AI, or None if it deserves a closer look."""
    title = card.title
    if card.price <= 0:
        return "no_price"
    if card.price < min_price or card.price > max_price:
        return "out_of_range"
    if WANTED_RE.search(title):
        return "wanted_post"
    if SERVICE_RE.search(title):
        return "service"
    if FOR_DEVICE_RE.search(title) and not _storage(title):
        return "accessory"
    if ACCESSORY_RE.search(title) and not WITH_EXTRAS_RE.search(title) and not _storage(title):
        return "accessory"
    if not has_model and NOT_PHONE_RE.search(title):
        return "not_a_phone"
    return None


def fingerprint(card: Card) -> str:
    """Same title + price + area = the same item reposted under a new listing id."""
    title = re.sub(r"[^a-z0-9]+", " ", card.title.lower()).strip()
    return f"{title}|{int(card.price)}|{card.location.lower()}"


def parse_target_item(raw_item: str, default_min: float = 50, default_max: float = 2500) -> dict | None:
    """Parses a single search target entry into its query name and custom or default price bounds.
    Works generically for ANY search term or product name.
    Supported format examples:
      - '<keyword>: <min>-<max>' (e.g. 'keyword: 50-200', 'keyword: $50 - $200', 'keyword: 50 to 200')
      - '<keyword> [<min>-<max>]' or '<keyword> (<min>-<max>)'
      - '<keyword> = <min>-<max>'
      - '<keyword>: -<max>' or '<keyword>: <=<max>' (max price only)
      - '<keyword>: <min>+' or '<keyword>: >=<min>' (min price only)
      - '<keyword>' (standard item inheriting the global default min/max)
    """
    raw_item = (raw_item or "").strip()
    if not raw_item:
        return None

    # Pattern 1: Bracketed at the end: ... (25-150) or ... [25-150] or ... ($25 - $150)
    bracket_match = re.search(r"[\(\[]\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:-|to|:)\s*\$?\s*(\d+(?:\.\d+)?)\s*[\)\]]$", raw_item, re.IGNORECASE)
    if bracket_match:
        query = raw_item[:bracket_match.start()].strip()
        min_p = float(bracket_match.group(1))
        max_p = float(bracket_match.group(2))
        return {
            "query": query,
            "min_price": min(min_p, max_p),
            "max_price": max(min_p, max_p),
            "has_custom_range": True,
            "raw": raw_item,
        }

    # Pattern 2: Separator (: or = or @): query: 25-150 or query: $25 - $150 or query: 25 to 150 or query: 25:150
    sep_match = re.search(r"[:=@]\s*(.+)$", raw_item)
    if sep_match:
        query_part = raw_item[:sep_match.start()].strip()
        range_part = sep_match.group(1).strip()

        # 1) "25-150" or "$25 - $150" or "25 to 150" or "25:150"
        m_range = re.search(r"^\$?\s*(\d+(?:\.\d+)?)\s*(?:-|to|:)\s*\$?\s*(\d+(?:\.\d+)?)$", range_part, re.IGNORECASE)
        if m_range:
            min_p = float(m_range.group(1))
            max_p = float(m_range.group(2))
            return {
                "query": query_part,
                "min_price": min(min_p, max_p),
                "max_price": max(min_p, max_p),
                "has_custom_range": True,
                "raw": raw_item,
            }

        # 2) "-150" or "<=150" or "<150" or "max 150" (only max)
        m_max = re.search(r"^(?:-|<=?|max\s*)\s*\$?\s*(\d+(?:\.\d+)?)$", range_part, re.IGNORECASE)
        if m_max:
            max_p = float(m_max.group(1))
            return {
                "query": query_part,
                "min_price": float(default_min),
                "max_price": max_p,
                "has_custom_range": True,
                "raw": raw_item,
            }

        # 3) "800+" or ">=800" or ">800" or "min 800" or "800-" (only min)
        m_min = re.search(r"^(?:>=?|>|min\s*)?\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:\+|>=?)?$", range_part, re.IGNORECASE)
        if m_min:
            min_p = float(m_min.group(1))
            return {
                "query": query_part,
                "min_price": min_p,
                "max_price": float(default_max),
                "has_custom_range": True,
                "raw": raw_item,
            }

    # Pattern 3: Fallback standard keyword without custom price range
    return {
        "query": raw_item,
        "min_price": float(default_min),
        "max_price": float(default_max),
        "has_custom_range": False,
        "raw": raw_item,
    }


def parse_search_targets(raw_keywords: str, default_min: float = 50, default_max: float = 2500) -> list[dict]:
    """Parses a comma-separated search target string into a list of structured target dicts."""
    if not raw_keywords:
        return []
    targets = []
    items = [item.strip() for item in raw_keywords.split(",") if item.strip()]
    for item in items:
        parsed = parse_target_item(item, default_min, default_max)
        if parsed and parsed["query"]:
            targets.append(parsed)
    return targets


def format_search_targets(targets: list[dict], default_min: float = 50, default_max: float = 2500) -> str:
    """Formats a list of structured target dicts back into a comma-separated string."""
    formatted = []
    for t in targets:
        q = t.get("query", "").strip()
        if not q:
            continue
        min_p = float(t.get("min_price", default_min))
        max_p = float(t.get("max_price", default_max))
        has_custom = t.get("has_custom_range", False) or (min_p != float(default_min) or max_p != float(default_max))
        if has_custom:
            formatted.append(f"{q}: {int(min_p)}-{int(max_p)}")
        else:
            formatted.append(q)
    return ", ".join(formatted)

