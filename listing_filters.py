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
