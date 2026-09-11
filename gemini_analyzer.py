import io
import os
import time
import logging
import threading
from datetime import date
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from PIL import Image
from google import genai
from google.genai import types
import settings

logging.getLogger("google.genai").setLevel(logging.ERROR)
load_dotenv()

# Load all 3 API keys (plus GEMINI_API_KEY from older .env files)
api_keys = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
    os.getenv("GEMINI_API_KEY")
]
valid_keys = list(dict.fromkeys(k for k in api_keys if k))
if not valid_keys:
    print("⚠️ No Gemini API keys found in .env (GEMINI_API_KEY_1). Listings can't be analyzed until one is set.")

models_list = settings.LITE_MODELS + settings.FALLBACK_MODELS


# Cheap first pass over search cards (one call for a whole batch)
class CardTriage(BaseModel):
    ref: int = Field(description="The card number from the input.")
    is_target_device: bool = Field(description="True only for an actual phone being sold - not accessories, wanted posts, services, tablets, watches or other products.")
    normalized_model: str = Field(description="Slug like 'iphone_13_pro_128gb', 'galaxy_s23_ultra_256gb', 'pixel_8_128gb' (omit storage if unknown).")
    condition_hint: str = Field(description="MINT, GOOD, FAIR, DAMAGED, LOCKED, PARTS or UNKNOWN - only from the title words.")
    market_value_cad: int = Field(description="Realistic used selling price in CAD on Toronto Marketplace today for this model working in good condition (not retail). Use the given comp when provided.")
    worth_opening: bool = Field(description="True if the price could leave at least $40 CAD profit after likely repairs.")


class TriageBatch(BaseModel):
    items: list[CardTriage]


# Strict Schema Enforcement
class DealAnalysis(BaseModel):
    reasoning: str = Field(description="Short step-by-step math and damage calculation (max 40 words).")
    deal_tier: str = Field(description="Must be ELITE_FLIP, PRIME_FLIP, GOOD_DEAL, or TRASH.")
    normalized_model: str = Field(description="Normalized device slug (e.g., 'iphone_13_pro_128gb').")
    extracted_title: str = Field(description="Clean, human-readable device title.")
    condition_grade: str = Field(description="MINT, GOOD, FAIR, DAMAGED, LOCKED or PARTS.")
    defect_summary: str = Field(description="Flaws stated in the text or visible in the photos, or 'None'.")
    estimated_repair_cost: int = Field(description="Integer CAD repair cost for this exact model. 0 if pristine.")
    market_value: int = Field(description="Integer CAD resale value used for the math.")
    projected_profit: int = Field(description="Integer CAD profit: (Market Value - Repair Cost) - Asking Price.")
    scam_risk: str = Field(description="LOW, MEDIUM or HIGH.")
    auto_message_text: str = Field(description="Ultra-short human starter text. Zero prices or numbers allowed.")


class GeminiPool:
    """Rotates calls across API keys and models, cooling down whatever is rate-limited or broken."""

    def __init__(self, keys: list[str]):
        self.clients = [genai.Client(api_key=k) for k in keys]
        self.lock = threading.Lock()
        self.cooldown = {}       # (key index or None, model or None) -> time it becomes usable again
        self.last_call = {}      # key index -> time of its last request
        self.calls_today = {}    # key index -> requests today
        self.tokens_today = 0
        self.day = date.today()
        self.next_key = 0

    def _usable(self, i: int, model: str, now: float) -> bool:
        if self.calls_today.get(i, 0) >= settings.GEMINI_DAILY_CALLS_PER_KEY:
            return False
        return all(self.cooldown.get(k, 0) <= now for k in ((i, model), (i, None), (None, model)))

    def _penalize(self, i: int, model: str, err: str):
        msg = err.lower().replace(" ", "")
        now = time.time()
        if "apikeynotvalid" in msg or "api_key_invalid" in msg or "permission_denied" in msg:
            self.cooldown[(i, None)] = now + 86400
            print(f"   ⚠️ Gemini key #{i + 1} is invalid - skipping it. Check the key in .env")
        elif "429" in msg or "resource_exhausted" in msg:
            self.cooldown[(i, model)] = now + (6 * 3600 if "perday" in msg else 65)
        elif "404" in msg or "not_found" in msg:
            self.cooldown[(None, model)] = now + 86400
        elif "503" in msg or "unavailable" in msg or "overloaded" in msg:
            self.cooldown[(i, model)] = now + 30
        elif "400" in msg or "invalid_argument" in msg:
            self.cooldown[(None, model)] = now + 3600
        else:
            self.cooldown[(i, model)] = now + 20

    def _config(self, model: str, schema):
        extra = {}
        if model in settings.NO_THINKING_MODELS:
            extra["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        return types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema,
                                           temperature=0.3, max_output_tokens=2048,
                                           automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True), **extra)

    def generate(self, contents, schema, models: list[str]) -> str | None:
        for model in models:
            for _ in range(len(self.clients)):
                with self.lock:
                    if self.day != date.today():
                        self.day, self.calls_today, self.tokens_today = date.today(), {}, 0
                    i = self.next_key
                    self.next_key = (i + 1) % len(self.clients)
                    now = time.time()
                    if not self._usable(i, model, now):
                        continue
                    wait = self.last_call.get(i, 0) + settings.GEMINI_MIN_SECONDS_BETWEEN_CALLS - now
                    self.last_call[i] = now + max(0.0, wait)
                    self.calls_today[i] = self.calls_today.get(i, 0) + 1
                if wait > 0:
                    time.sleep(wait)
                try:
                    resp = self.clients[i].models.generate_content(model=model, contents=contents, config=self._config(model, schema))
                    if resp and resp.text:
                        with self.lock:
                            self.tokens_today += (resp.usage_metadata.total_token_count or 0) if resp.usage_metadata else 0
                        return resp.text
                except Exception as e:
                    with self.lock:
                        self._penalize(i, model, str(e))
        return None

    def stats(self) -> str:
        now = time.time()
        invalid = sum(1 for i in range(len(self.clients)) if self.cooldown.get((i, None), 0) > now)
        note = f" ({invalid} invalid - check .env)" if invalid else ""
        return f"Gemini today: {sum(self.calls_today.values())} calls, {self.tokens_today:,} tokens across {len(self.clients)} key(s){note}"


pool = GeminiPool(valid_keys) if valid_keys else None


def triage_cards(cards: list[dict]) -> dict[int, CardTriage] | None:
    """cards: [{'ref', 'price', 'title', 'location', 'comp'}]. Returns results by ref, or None if the AI is unavailable."""
    if not pool:
        return None
    lines = []
    for c in cards:
        comp = f" | comp ${c['comp']:.0f}" if c.get("comp") else ""
        lines.append(f"{c['ref']}. price ${c['price']:.0f} | title \"{c['title']}\" | {c['location']}{comp}")
    prompt = f"""
    You pre-screen Facebook Marketplace search cards for a Toronto phone flipper. Each card only has a price, a short title and a location.
    Today is {date.today():%B %d, %Y}. Phones released after your training data (e.g. iPhone 17 / iPhone Air, Pixel 10, newer Galaxy S models)
    are real - never reject a listing because you don't recognise the model, and remember older models have depreciated since then.
    Bundles of several phones count as phones.
    Return exactly one item per card, using the same ref number.
    - is_target_device: false for accessories (cases, chargers, boxes), wanted/ISO posts, repair or unlock services, tablets, watches, earbuds and non-phone products.
    - condition_hint: judge only from the title words; UNKNOWN if nothing is said.
    - market_value_cad: the realistic price that model SELLS for used on Toronto Facebook Marketplace today, working and in good
      condition - not retail and not optimistic asking prices. If a comp is given, use it.
    - worth_opening: true if the asking price could plausibly leave at least $40 CAD profit after typical repairs for the hinted condition
      (iCloud/MDM locked or parts-only phones are worth about 25% of market value).

    Cards:
    {chr(10).join(lines)}
    """
    text = pool.generate(prompt, TriageBatch, models_list)
    if not text:
        return None
    try:
        return {item.ref: item for item in TriageBatch.model_validate_json(text).items}
    except Exception:
        return None


def _prepare_photo(data: bytes):
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((768, 768))   # keeps image tokens low
    return img


def analyze_deal(listing: dict, photos: list[bytes]) -> DealAnalysis | None:
    """Full analysis of one promising listing: description + photos + market value."""
    if not pool:
        return None
    images = []
    for data in photos[: settings.MAX_PHOTOS_PER_DEAL]:
        try:
            images.append(_prepare_photo(data))
        except Exception:
            pass
    description = (listing.get("description") or "not available")[:1500]
    prompt = f"""
    You are an expert Canadian electronics arbitrage buyer and repair technician.
    Today is {date.today():%B %d, %Y}. Newer phone models than you know about exist, and used prices of older models have dropped since your training data.

    Listing Title: "{listing['title']}"
    Asking Price: ${listing['price']:.0f} CAD
    Facebook Condition Field: {listing.get('fb_condition') or 'not given'}
    Description: "{description}"
    Market Value (working, good condition): ${listing['market_value']:.0f} CAD (source: {listing['comp_source']})
    Photos attached: {len(images)}

    CRITICAL RULES:
    1. Do NOT guess or invent defects. Only report flaws stated in the description or clearly visible in the photos. If battery health or damage is not mentioned or visible, assume it is normal.
    2. Pricing & Profit Math:
       - Estimate repair costs at typical Toronto repair-shop prices for THIS exact model (a newer Pro Max screen costs far more than an older base model). Baselines: screen damage ~$150, battery < 80% ~$60, back glass crack ~$90.
       - iCloud / MDM Locked or parts only: value is 25% of Market Value.
       - If the listing is clearly a different model or storage than the market value refers to, adjust market_value accordingly.
       - A market value with source "ai_estimate" is only a rough guess - be conservative with it.
       - True Value = (Market Value - estimated_repair_cost).
       - Projected Profit = (True Value - Asking Price).
    3. Deal Tiers:
       - "ELITE_FLIP": Projected profit > $150 CAD OR ROI > 50%.
       - "PRIME_FLIP": Projected profit between $50 and $150 CAD.
       - "GOOD_DEAL": Projected profit between $20 and $50 CAD.
       - "TRASH": Negative/low profit, overpriced, scam, or parts item priced like working.
    4. scam_risk: HIGH for signs like stock/catalog photos only, a price far too good for a working phone, shipping or e-transfer-first requests, or "brand new sealed" far below retail. A HIGH scam_risk deal must be TRASH.
    5. AUTO-MESSAGE RULES (STRICT):
       - Keep it to a single, casual human sentence (under 10 words).
       - DO NOT mention any price, number, or cash amount.
       - DO NOT start negotiating. The client negotiates manually after the seller replies.
       - The sole purpose is to register immediate interest before anyone else.
       - Vary greetings randomly: "hey, still available?", "hi is this still available?", "hey there, still got this?", "interested, is this still up?"
    """
    text = pool.generate([prompt, *images], DealAnalysis, models_list)
    if not text:
        return None
    try:
        return DealAnalysis.model_validate_json(text)
    except Exception:
        return None


def gemini_stats() -> str:
    return pool.stats() if pool else "Gemini: no API keys configured"
