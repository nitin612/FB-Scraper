import asyncio
import os
import random
import re
import sys
import time
import urllib.parse
from collections import Counter, deque
from playwright.async_api import async_playwright

import settings
import telegram_bot
from fb_browser import launch_account, pause, human_scroll, account_problem, mark_account_problem, account_pause_reason
from listing_filters import parse_card, detect_model, local_filter, condition_from_text, fingerprint, listed_ago_seconds
from gemini_analyzer import triage_cards, analyze_deal, gemini_stats
from market_comps import get_ebay_sold_comp, get_local_comp, market_value, base_slug, ebay_status
from outreach_worker import send_human_message, UNAVAILABLE_PHRASES
from deal_pipeline import (supabase, check_schema, load_bot_config, load_local_config, CONFIG_CACHE_FILE,
                           load_seen, lookup_existing, save_listing, update_listing,
                           evaluate_intraday_pricing, messages_sent_today, next_pending_outreach, seller_already_contacted,
                           download_photos, send_telegram_alert, send_desktop_alert, esc, in_active_hours, local_now, utc_now_iso,
                           ago_to_iso, get_listing, count_listings, top_deals_since, local_midnight_utc_iso, APPROVED_PREFIX)

GRADES = {"MINT", "GOOD", "FAIR", "DAMAGED", "LOCKED", "PARTS", "UNKNOWN"}
TIERS = {"ELITE_FLIP", "PRIME_FLIP", "GOOD_DEAL", "TRASH"}
DEVICE_REASONS = {"at_market", "repost", "out_of_range", "low_margin", "ai_trash", "sold_or_removed"}  # real phones: keep model for comps


def clean_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "unknown"


class Shared:
    """State both accounts share: settings, what has been seen, and the outreach queue."""

    def __init__(self):
        self.config: dict | None = None
        self.config_version: int = 0
        self.seen: dict[str, tuple[float, str]] = {}
        self.fingerprints: dict[str, str] = {}
        self.claimed: set[str] = set()          # queued deals an account is sending right now
        self.approved: set[str] = set()         # deals approved from Telegram, waiting for a free account
        self.account_status: dict[str, str] = {}
        self._keyword_index = 0

    def update_config(self, new_cfg: dict | None) -> bool:
        if not new_cfg:
            return False
        old_cfg = self.config or {}

        old_kw = [k.strip().lower() for k in old_cfg.get("keywords", "").split(",") if k.strip()]
        new_kw = [k.strip().lower() for k in new_cfg.get("keywords", "").split(",") if k.strip()]

        kw_changed = old_kw != new_kw
        city_changed = str(old_cfg.get("target_city", "")).strip().lower() != str(new_cfg.get("target_city", "")).strip().lower()
        price_changed = (old_cfg.get("min_price"), old_cfg.get("max_price")) != (new_cfg.get("min_price"), new_cfg.get("max_price"))
        active_changed = old_cfg.get("is_active") != new_cfg.get("is_active")
        auto_changed = old_cfg.get("auto_message_enabled") != new_cfg.get("auto_message_enabled")

        self.config = new_cfg

        if kw_changed or city_changed or price_changed or active_changed or auto_changed:
            self.config_version += 1
            changes = []
            raw_new_kw = [k.strip() for k in new_cfg.get("keywords", "").split(",") if k.strip()]
            if kw_changed:
                self._keyword_index = 0   # restart rotation immediately with the first new keyword
                changes.append(f"Keywords -> {raw_new_kw}")
            if city_changed:
                changes.append(f"City -> {new_cfg.get('target_city')}")
            if price_changed:
                changes.append(f"Price -> ${new_cfg.get('min_price', 0):,} - ${new_cfg.get('max_price', 0):,}")
            if active_changed:
                changes.append(f"Active -> {new_cfg.get('is_active')}")
            if auto_changed:
                changes.append(f"Auto-message -> {new_cfg.get('auto_message_enabled')}")

            print(f"\n🔄 Live configuration updated: {', '.join(changes)}")
            if kw_changed and raw_new_kw:
                print(f"   ⚡ Scraper will immediately switch to: '{raw_new_kw[0]}'")
            return True
        return False

    def next_keyword(self) -> str | None:
        keywords = [k.strip() for k in (self.config or {}).get("keywords", "").split(",") if k.strip()]
        if not keywords:
            return None
        keyword = keywords[self._keyword_index % len(keywords)]
        self._keyword_index += 1
        return keyword


class AccountWorker:
    def __init__(self, account: dict, shared: Shared, playwright, start_delay: float):
        self.account, self.id, self.shared, self.pw = account, account["id"], shared, playwright
        self.start_delay = start_delay
        self.last_config_version = shared.config_version
        self.context = self.page = None
        self.problem: str | None = None
        self.search_times: deque = deque()
        self.detail_times: deque = deque()
        self.sent_today, self.last_sent, self.sent_day = 0, 0.0, None
        self.messaging_paused_until = 0.0
        self.errors_in_row = 0

    def log(self, msg: str):
        print(f"[{self.id}] {msg}")

    # ---------- safety ----------
    async def check_health(self) -> bool:
        problem = await account_problem(self.page)
        if problem:
            self.problem = problem
            mark_account_problem(self.id, problem)   # a restart won't retry this account until it is logged in again
            self.shared.account_status[self.id] = f"⛔ stopped - {problem}"
            self.log(f"⛔ Facebook shows {problem} - this account stops now to stay safe.")
            send_telegram_alert(f"⚠️ <b>{self.id} stopped:</b> Facebook shows <b>{problem}</b>.\n"
                                f"Log it in again with <code>run.bat setup</code> / <code>./run.sh setup</code>, then restart the scraper.",
                                "https://www.facebook.com")
        return bool(problem)

    @staticmethod
    def _within_hourly_cap(times: deque, cap: int) -> float:
        """Seconds to wait before another action fits in the rolling one-hour cap."""
        now = time.time()
        while times and now - times[0] > 3600:
            times.popleft()
        return 0.0 if len(times) < cap else times[0] + 3600 - now + random.uniform(5, 30)

    # ---------- searching ----------
    async def collect_cards(self) -> list[dict]:
        found, order, seen_streak = {}, [], 0
        for _ in range(settings.MAX_SCROLLS_PER_SEARCH + 1):
            for link in await self.page.locator("a[href*='/marketplace/item/']").all():
                m = re.search(r"/marketplace/item/(\d+)", await link.get_attribute("href") or "")
                if not m or m.group(1) in found:
                    continue
                lid = m.group(1)
                img = link.locator("img").first
                found[lid] = {"id": lid, "url": f"https://www.facebook.com/marketplace/item/{lid}/",
                              "text": await link.inner_text(), "img": await img.get_attribute("src") if await img.count() else None}
                order.append(lid)
                seen_streak = seen_streak + 1 if lid in self.shared.seen else 0
            # Newest first: a run of already-seen listings means we have caught up - no need to scroll further
            if len(order) >= settings.MAX_CARDS_PER_SEARCH or seen_streak >= settings.CAUGHT_UP_AFTER_SEEN:
                break
            await human_scroll(self.page, 1)
        return [found[i] for i in order[: settings.MAX_CARDS_PER_SEARCH]]

    def remember(self, listing_id: str, price: float, status: str, fp: str | None):
        self.shared.seen[listing_id] = (price, status)
        if fp:
            self.shared.fingerprints[fp] = listing_id

    def save_trash(self, x: dict, reason: str):
        """Hidden from the dashboard (status TRASH) but kept for duplicate checks and price comparisons."""
        card, c = x["card"], x["parsed"]
        save_listing({
            "id": card["id"], "title": c.title[:200], "price": c.price, "url": card["url"], "image_url": card["img"],
            "status": "TRASH", "deal_tier": "TRASH", "filter_reason": reason,
            "normalized_model": x.get("slug") if reason in DEVICE_REASONS else None,
            "condition_grade": x.get("condition") or condition_from_text(c.title),
            "market_value": x.get("market") or None, "comp_source": x.get("source"),
            "live_comp_price": x.get("ebay") or 0, "local_comp_price": x.get("local") or None,
            "location": c.location, "fingerprint": x["fp"], "previous_price": x.get("price_drop_from") or c.previous_price,
            "found_by": self.id, "created_at": utc_now_iso(),
        })
        self.remember(card["id"], c.price, "TRASH", x["fp"])

    async def scan(self, keyword: str):
        cfg = self.shared.config
        min_p, max_p = cfg["min_price"], cfg["max_price"]
        url = (f"https://www.facebook.com/marketplace/{cfg['target_city']}/search?sortBy=creation_time_descend"
               f"&query={urllib.parse.quote(keyword)}&minPrice={min_p}&maxPrice={max_p}")
        self.log(f"🔍 Searching '{keyword}'...")
        await self.page.goto(url, wait_until="domcontentloaded")
        self.search_times.append(time.time())
        await pause(3.5, 7)
        if await self.check_health():
            return

        cards = await self.collect_cards()
        unknown = [c["id"] for c in cards if c["id"] not in self.shared.seen]
        for lid, info in lookup_existing(unknown).items():
            self.shared.seen[lid] = info
        stats, candidates, scan_fps = Counter(), [], {}

        # Every phone on this results page is a live price observation - compare against them like a human would
        page_prices: dict[str, list[tuple[str, float]]] = {}
        for card in cards:
            if card["id"] in self.shared.seen:
                continue   # already counted through the database
            c = parse_card(card["text"])
            _, slug = detect_model(c.title)
            if slug and not local_filter(c, True, min_p, max_p) and condition_from_text(c.title) in ("MINT", "UNKNOWN"):
                page_prices.setdefault(slug, []).append((card["id"], c.price))

        for card in cards:
            c = parse_card(card["text"])
            x = {"card": card, "parsed": c, "fp": fingerprint(c), "price_drop_from": None}
            if card["id"] in self.shared.seen:
                old_price, old_status = self.shared.seen[card["id"]]
                if not (c.price and old_price and c.price <= old_price * settings.PRICE_DROP_RATIO and old_status in ("TRASH", "NEW")):
                    stats["already_seen"] += 1
                    continue
                x["price_drop_from"] = old_price     # price dropped - worth a second look
            _, x["slug"] = detect_model(c.title)
            reason = local_filter(c, bool(x["slug"]), min_p, max_p)
            other = self.shared.fingerprints.get(x["fp"]) or scan_fps.get(x["fp"])
            if not reason and not x["price_drop_from"] and other and other != card["id"]:
                reason = "repost"
            scan_fps.setdefault(x["fp"], card["id"])
            x["ebay"], x["local"], x["local_n"], x["market"], x["source"] = 0.0, 0.0, 0, 0.0, "none"
            if not reason and x["slug"]:
                others = [p for key in {x["slug"], base_slug(x["slug"])} for lid, p in page_prices.get(key, []) if lid != card["id"]]
                x["local"], x["local_n"] = get_local_comp(supabase, x["slug"], others)
                if x["local_n"] < 5:   # enough local samples make an eBay page load unnecessary
                    x["ebay"] = await get_ebay_sold_comp(self.context, x["slug"])
                x["market"], x["source"] = market_value(x["ebay"], x["local"], x["local_n"])
                if x["market"] and c.price >= x["market"] * settings.AT_MARKET_RATIO:
                    reason = "at_market"
            if reason:
                self.save_trash(x, reason)
                stats[reason] += 1
            else:
                candidates.append(x)

        promising = await self.triage(candidates, stats)
        for x in promising:
            await self.inspect_deal(x, stats)
            if self.problem:
                break
        summary = ", ".join(f"{k} {v}" for k, v in stats.most_common())
        self.log(f"   {len(cards)} cards -> {summary or 'nothing new'}")

    async def triage(self, candidates: list[dict], stats: Counter) -> list[dict]:
        promising = []
        for start in range(0, len(candidates), settings.TRIAGE_BATCH_SIZE):
            batch = candidates[start:start + settings.TRIAGE_BATCH_SIZE]
            payload = [{"ref": n, "price": x["parsed"].price, "title": x["parsed"].title,
                        "location": x["parsed"].location, "comp": x["market"]} for n, x in enumerate(batch, 1)]
            results = await asyncio.to_thread(triage_cards, payload)
            if results is None:
                stats["ai_unavailable_retry_later"] += len(batch)   # not saved, so they get another chance
                continue
            for n, x in enumerate(batch, 1):
                r = results.get(n)
                if not r:
                    self.save_trash(x, "ai_no_answer")
                    stats["ai_no_answer"] += 1
                    continue
                locally_known = bool(x["slug"])   # the regex recognised a real iPhone / Galaxy / Pixel
                x["slug"] = x["slug"] or clean_slug(r.normalized_model)
                x["condition"] = r.condition_hint if r.condition_hint in GRADES else condition_from_text(x["parsed"].title)
                if not x["market"]:
                    x["market"], x["source"] = market_value(0, 0, 0, r.market_value_cad)
                # Lite models don't know phones released after their training (e.g. iPhone 17 / Air) - trust the regex for those
                if not r.is_target_device and not locally_known:
                    self.save_trash(x, "not_a_phone")
                    stats["not_a_phone"] += 1
                elif not r.worth_opening:
                    self.save_trash(x, "low_margin")
                    stats["low_margin"] += 1
                else:
                    promising.append(x)
        return promising

    # ---------- listing details + full analysis ----------
    async def read_listing(self, url: str) -> dict:
        await self.page.goto(url, wait_until="domcontentloaded")
        self.detail_times.append(time.time())
        await pause(4, 7.5)
        if await self.check_health():
            return {}
        await human_scroll(self.page, random.randint(1, 2))
        await self.page.evaluate("""() => { const el = [...document.querySelectorAll('div[role="main"] span, div[role="main"] div[role="button"]')]
                                          .find(e => e.textContent.trim() === 'See more'); if (el) el.click(); }""")
        await pause(0.8, 1.8)
        main = self.page.locator('div[role="main"]').first
        text = await main.inner_text(timeout=6000) if await self.page.locator('div[role="main"]').count() else ""
        text = re.split(r"\n(?:Related searches|Today's picks)\n", text)[0]
        if any(p in text.lower() for p in UNAVAILABLE_PHRASES):
            return {"unavailable": True}
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        condition = lines[lines.index("Condition") + 1] if "Condition" in lines[:-1] else None
        body = lines[lines.index("Details") + 1:] if "Details" in lines else []
        body = [l for l in body if l not in ("Condition", condition, "See more", "See less") and "Location is approximate" not in l]
        photos = await self.page.eval_on_selector_all("img[alt^='Product photo of']", "els => [...new Set(els.map(e => e.src))]")
        sellers = await self.page.eval_on_selector_all("a[href*='/marketplace/profile/']", "els => els.map(e => [e.getAttribute('href'), e.innerText])")
        seller = next(((re.search(r"/profile/(\d+)", h), t) for h, t in sellers if re.search(r"/profile/(\d+)", h or "")), (None, None))
        return {"condition": condition, "description": "\n".join(body)[:2000], "photos": photos[: settings.MAX_PHOTOS_PER_DEAL],
                "listed_at": ago_to_iso(listed_ago_seconds(text)),
                "seller_id": seller[0].group(1) if seller[0] else None, "seller_name": (seller[1] or "").strip()[:80] or None}

    async def inspect_deal(self, x: dict, stats: Counter):
        card, c = x["card"], x["parsed"]
        details = {}
        if self._within_hourly_cap(self.detail_times, settings.MAX_DETAIL_VIEWS_PER_HOUR) == 0:
            details = await self.read_listing(card["url"])
            if self.problem:
                return
            if details.get("unavailable"):
                self.save_trash(x, "sold_or_removed")
                stats["sold_or_removed"] += 1
                return
        photos = await asyncio.to_thread(download_photos, details.get("photos") or ([card["img"]] if card["img"] else []))
        listing = {"title": c.title, "price": c.price, "fb_condition": details.get("condition"),
                   "description": details.get("description"), "market_value": x["market"], "comp_source": x["source"]}
        a = await asyncio.to_thread(analyze_deal, listing, photos)
        if a is None:
            stats["ai_unavailable_retry_later"] += 1
            return

        slug = x["slug"] or clean_slug(a.normalized_model)
        grade = a.condition_grade if a.condition_grade in GRADES else x.get("condition", "UNKNOWN")
        tier = a.deal_tier if a.deal_tier in TIERS else "TRASH"
        if a.scam_risk == "HIGH":
            tier = "TRASH"
        if tier == "TRASH":
            x.update(slug=slug, condition=grade, market=a.market_value or x["market"])
            self.save_trash(x, "ai_trash")
            stats["ai_trash"] += 1
            return

        intraday = evaluate_intraday_pricing(slug, grade, c.price, card["id"])
        cheaper = intraday.get("cheaper_deal") or {}
        cfg = self.shared.config
        # Prevent auto-messaging if the item is overpriced locally, looks risky, or has no valuation
        queue = bool(cfg.get("auto_message_enabled")) and tier in ("ELITE_FLIP", "PRIME_FLIP") \
            and intraday["status"] != "OVERPRICED" and a.scam_risk != "HIGH" and x["source"] != "none"
        status = "PENDING_OUTREACH" if queue else "NEW"
        previous = x["price_drop_from"] or c.previous_price
        photo = (details.get("photos") or [card["img"]])[0]
        save_listing({
            "id": card["id"], "title": a.extracted_title or c.title, "normalized_model": slug, "price": c.price,
            "previous_price": previous, "live_comp_price": x["ebay"], "local_comp_price": x["local"] or None,
            "market_value": a.market_value or x["market"], "comp_source": x["source"], "url": card["url"],
            "image_url": photo, "status": status, "deal_tier": tier,
            "price_status": intraday["status"], "cheaper_deal_id": cheaper.get("id"), "cheaper_deal_url": cheaper.get("url"),
            "cheaper_deal_price": cheaper.get("price") or 0, "defect_summary": a.defect_summary,
            "estimated_repair_cost": a.estimated_repair_cost, "projected_profit": a.projected_profit, "condition_grade": grade,
            "scam_risk": a.scam_risk, "description": details.get("description"), "location": c.location, "fingerprint": x["fp"],
            "filter_reason": None, "found_by": self.id, "seller_id": details.get("seller_id"), "seller_name": details.get("seller_name"),
            "auto_message_text": a.auto_message_text, "created_at": utc_now_iso(), "listed_at": details.get("listed_at"),
            "outreach_log": "⏳ Queued for Dispatch" if queue else "Skipped (auto-messaging off, overpriced, or high scam risk)",
        })
        self.remember(card["id"], c.price, status, x["fp"])
        stats[tier] += 1

        warning_text = ""
        if intraday["status"] == "OVERPRICED":
            warning_text = f"\n🚨 <b>DO NOT OVERPAY:</b> An active deal exists for <b>${cheaper.get('price', 0):,.0f}</b>!\n🔗 <a href='{cheaper.get('url', '#')}'>View Cheaper Listing</a>"
        tier_badge = "🔥🔥 ELITE" if tier == "ELITE_FLIP" else ("🚨 PRIME" if tier == "PRIME_FLIP" else "🟢 GOOD")
        was = f" (was ${previous:,.0f})" if previous else ""
        alert = (
            f"<b>{tier_badge}</b> | {intraday['badge']}\n"
            f"📱 <b>{esc(a.extracted_title or c.title)}</b> · {esc(grade)}\n"
            f"💰 Asking: <b>${c.price:,.0f} CAD</b>{was}\n"
            f"📈 Market: <b>${(a.market_value or x['market']):,.0f} CAD</b> ({esc(x['source'])})\n"
            f"🔧 Est. Repair: <b>-${a.estimated_repair_cost:,.0f}</b> | 💵 Proj. Profit: <b>${a.projected_profit:,.0f}</b>\n"
            f"🛠️ Flaws: {esc(a.defect_summary)}{warning_text}\n"
            f"👤 Found by {self.id}" + (" · ⏳ queued for auto-message" if queue else "")
        )
        await asyncio.to_thread(send_telegram_alert, alert, card["url"], photo, card["id"])
        if tier in ("ELITE_FLIP", "PRIME_FLIP"):
            send_desktop_alert(f"{tier_badge} (${c.price:,.0f})", f"{a.extracted_title or c.title}")

    # ---------- outreach ----------
    def can_message_now(self) -> bool:
        today = local_now().date()
        if self.sent_day != today:
            self.sent_day = today
            self.sent_today, self.last_sent = messages_sent_today(self.id)
        return (time.time() >= self.messaging_paused_until and self.sent_today < settings.MAX_MESSAGES_PER_DAY
                and time.time() - self.last_sent >= settings.MIN_MINUTES_BETWEEN_MESSAGES * 60)

    async def maybe_send_outreach(self):
        """Sends one queued first message if this account's safety caps allow it.
        Deals approved from Telegram go first and are sent even when auto-messaging is off."""
        auto = bool((self.shared.config or {}).get("auto_message_enabled"))
        if not (auto or self.shared.approved) or not self.can_message_now():
            return
        item = next_pending_outreach(self.shared.claimed, approved_only=not auto)
        if not item:
            if not auto:
                self.shared.approved.clear()   # nothing approved is waiting any more
            return
        approved = str(item.get("outreach_log") or "").startswith(APPROVED_PREFIX)
        title = esc(item.get("title"))
        self.shared.claimed.add(item["id"])
        try:
            if seller_already_contacted(item.get("seller_id")):
                update_listing(item["id"], {"status": "NEW", "outreach_log": "⏭️ Skipped: this seller was already messaged"})
                if approved:
                    telegram_bot.send_text(f"⏭️ Not sent - the seller of <b>{title}</b> was already messaged.")
                return
            text = item.get("auto_message_text") or "hey, still available?"
            if settings.OUTREACH_DRY_RUN:
                update_listing(item["id"], {"status": "NEW", "outreach_log": f"🧪 Dry run: {self.id} would have sent \"{text}\""})
                self.log(f"🧪 Dry run - would message listing {item['id']}: \"{text}\"")
                if approved:
                    telegram_bot.send_text(f"🧪 Dry run: {self.id} would have messaged the seller of <b>{title}</b>.")
                return
            success, reason = await send_human_message(self.page, item["url"], text)
            if success:
                self.sent_today += 1
                self.last_sent = time.time()
                update_listing(item["id"], {"status": "CONTACTED", "outreach_log": f"🟢 Sent via {self.id}",
                                            "contacted_by": self.id, "contacted_at": utc_now_iso()})
                self.log(f"✅ Message sent ({self.sent_today}/{settings.MAX_MESSAGES_PER_DAY} today).")
                telegram_bot.send_text(f"💬 <b>{self.id}</b> messaged the seller of <b>{title}</b> (${item['price']:,.0f})\n{item['url']}")
            else:
                update_listing(item["id"], {"status": "NEW", "outreach_log": f"🔴 Failed via {self.id}: {reason}"})
                if approved:
                    telegram_bot.send_text(f"🔴 {self.id} couldn't message the seller of <b>{title}</b>: {esc(reason)}")
                if reason == "MESSAGING_BLOCKED":
                    self.messaging_paused_until = time.time() + 24 * 3600
                    send_telegram_alert(f"⚠️ <b>{self.id}</b>: Facebook is limiting its messages. Messaging from this account is paused for 24h.",
                                        "https://www.facebook.com")
            await self.check_health()
        finally:
            self.shared.claimed.discard(item["id"])
            self.shared.approved.discard(item["id"])

    async def rest(self, seconds: float):
        """Pause between searches, but wake up immediately if Telegram approved a deal or settings changed in dashboard."""
        end = time.time() + seconds
        while (left := end - time.time()) > 0:
            if self.last_config_version < self.shared.config_version:
                self.log("⚡ Settings updated in dashboard - waking up immediately for next search!")
                break
            await asyncio.sleep(min(1.0, left))
            auto = bool((self.shared.config or {}).get("auto_message_enabled"))
            if (auto or self.shared.approved) and not self.problem and in_active_hours() and self.can_message_now():
                await self.maybe_send_outreach()

    # ---------- main loop ----------
    async def run(self):
        await asyncio.sleep(self.start_delay)
        self.context = await launch_account(self.pw, self.account)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.log(f"✅ Browser ready ({self.account['session_dir']}).")
        searches, next_break, idle_logged = 0, random.randint(*settings.LONG_BREAK_EVERY), False
        try:
            while not self.problem:
                cfg = self.shared.config
                if not cfg or not cfg.get("is_active"):
                    await self.rest(4.0)
                    continue
                if not in_active_hours():
                    if not idle_logged:
                        self.log(f"😴 Outside active hours ({settings.ACTIVE_START_HOUR}:00-{settings.ACTIVE_END_HOUR}:00) - resting.")
                        idle_logged = True
                    await self.rest(120.0)
                    continue
                idle_logged = False
                wait = self._within_hourly_cap(self.search_times, settings.MAX_SEARCHES_PER_HOUR)
                if wait:
                    self.log(f"🛡️ Hourly search cap reached - resting {wait / 60:.0f} min.")
                    await self.rest(wait)

                if self.last_config_version < self.shared.config_version:
                    self.last_config_version = self.shared.config_version
                    searches = 0

                keyword = self.shared.next_keyword()
                if not keyword:
                    await self.rest(10.0)
                    continue
                try:
                    await self.scan(keyword)
                    if not self.problem:
                        await self.maybe_send_outreach()
                    self.errors_in_row = 0
                except Exception as e:
                    self.errors_in_row += 1
                    self.log(f"⚠️ Scan error: {str(e)[:120]}")
                    if self.errors_in_row >= 3:
                        raise RuntimeError("browser keeps failing") from e
                    await pause(20, 45)
                searches += 1
                if searches >= next_break:
                    minutes = random.uniform(*settings.LONG_BREAK_MINUTES)
                    self.log(f"☕ Taking a {minutes:.0f} min break.")
                    await self.rest(minutes * 60)
                    searches, next_break = 0, random.randint(*settings.LONG_BREAK_EVERY)
                else:
                    await self.rest(random.uniform(*settings.SEARCH_GAP_SECONDS))
        finally:
            try:
                if self.context:
                    await self.context.close()
            except Exception:
                pass


async def supervise(account: dict, shared: Shared, playwright, start_delay: float):
    """Keeps one account running: restarts its browser after a crash, but never after Facebook flags the account."""
    restarts: deque = deque()
    delay = start_delay
    while True:
        worker = AccountWorker(account, shared, playwright, delay)
        shared.account_status[account["id"]] = "✅ running"
        try:
            await worker.run()
        except Exception as e:
            print(f"[{account['id']}] 💥 Crashed: {str(e)[:120]}")
        if worker.problem:
            return
        now = time.time()
        restarts.append(now)
        while restarts and now - restarts[0] > 3600:
            restarts.popleft()
        if len(restarts) > settings.MAX_WORKER_RESTARTS_PER_HOUR:
            shared.account_status[account["id"]] = "💥 stopped after repeated crashes"
            send_telegram_alert(f"💥 <b>{account['id']}</b> crashed {len(restarts)} times in an hour and was stopped. Restart the scraper.",
                                "https://www.facebook.com")
            return
        print(f"[{account['id']}] ♻️ Restarting its browser in 60s...")
        delay = 60


def handle_button(shared: Shared, action: str, listing_id: str) -> tuple[str, str]:
    """Acts on a Telegram button tap. Returns (reply text, listing url)."""
    row = get_listing(listing_id)
    if not row:
        return "Listing not found", "https://www.facebook.com/marketplace/"
    if action == "ign":
        update_listing(listing_id, {"status": "IGNORED", "outreach_log": "🚫 Ignored from Telegram"})
        return "🚫 Ignored - removed from the watchlist", row["url"]
    if action != "msg":
        return "Unknown action", row["url"]
    if row["status"] in ("CONTACTED", "PURCHASED"):
        return f"Already {row['status'].lower()}", row["url"]
    update_listing(listing_id, {"status": "PENDING_OUTREACH", "outreach_log": f"{APPROVED_PREFIX} Approved from Telegram - waiting for a free account"})
    shared.approved.add(listing_id)
    when = "shortly" if in_active_hours() else f"when active hours start ({settings.ACTIVE_START_HOUR}:00)"
    return (f"📲 Approved - the next free account will message the seller {when} "
            f"(safety gap: {settings.MIN_MINUTES_BETWEEN_MESSAGES} min between messages per account)"), row["url"]


async def poll_telegram(shared: Shared):
    """Listens for taps on the Message seller / Ignore buttons under deal alerts."""
    if not telegram_bot.enabled():
        return
    offset = None
    while True:
        updates = await asyncio.to_thread(telegram_bot.get_updates, offset)
        if updates is None:
            await asyncio.sleep(30)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            cb = update.get("callback_query")
            if not cb or not telegram_bot.is_our_chat(cb):
                continue
            action, _, listing_id = (cb.get("data") or "").partition(":")
            try:
                reply, url = handle_button(shared, action, listing_id)
            except Exception as e:
                reply, url = f"⚠️ Couldn't process that: {str(e)[:80]}", "https://www.facebook.com/marketplace/"
            await asyncio.to_thread(telegram_bot.answer_button, cb["id"], reply)
            message_id = (cb.get("message") or {}).get("message_id")
            if message_id:
                await asyncio.to_thread(telegram_bot.set_buttons, message_id, telegram_bot.link_buttons(url))
                await asyncio.to_thread(telegram_bot.send_text, reply, message_id)


def build_daily_summary(shared: Shared) -> str:
    since = local_midnight_utc_iso()
    checked, hidden = count_listings(since), count_listings(since, status="TRASH")
    lines = [f"📊 <b>Daily summary - {local_now():%b %d}</b>",
             f"🔎 Listings checked: <b>{checked}</b> (filtered out: {hidden})",
             f"🔥 Deals found: <b>{checked - hidden}</b>"]
    for d in top_deals_since(since):
        lines.append(f"   • <a href='{d['url']}'>{esc(d['title'])[:45]}</a> - ${d['price']:,.0f}, profit ${d.get('projected_profit') or 0:,.0f}")
    for acc in settings.FB_ACCOUNTS:
        sent = count_listings(since, "contacted_at", contacted_by=acc["id"])
        status = shared.account_status.get(acc["id"], "not running")
        lines.append(f"👤 {acc['id']}: {status} · {sent}/{settings.MAX_MESSAGES_PER_DAY} messages today")
    lines.append(f"🤖 {gemini_stats()}")
    lines.append(f"📈 eBay comps: {ebay_status()}")
    return "\n".join(lines)


async def daily_summary(shared: Shared):
    sent_on = None
    while True:
        await asyncio.sleep(300)
        now = local_now()
        if now.hour == settings.DAILY_SUMMARY_HOUR and sent_on != now.date():
            sent_on = now.date()
            try:
                await asyncio.to_thread(telegram_bot.send_text, build_daily_summary(shared))
            except Exception as e:
                print(f"⚠️ Daily summary failed: {e}")


async def refresh_config(shared: Shared):
    """Watches local file cache (sub-second response) and polls Supabase (every 5s) for live config updates."""
    last_mtime = 0.0
    counter = 0
    while True:
        try:
            if os.path.exists(CONFIG_CACHE_FILE):
                mtime = os.path.getmtime(CONFIG_CACHE_FILE)
                if mtime > last_mtime:
                    last_mtime = mtime
                    local_cfg = load_local_config()
                    if local_cfg:
                        shared.update_config(local_cfg)
        except Exception:
            pass

        counter += 1
        if counter >= 5:
            counter = 0
            try:
                remote_cfg = load_bot_config()
                if remote_cfg:
                    shared.update_config(remote_cfg)
            except Exception:
                pass

        await asyncio.sleep(1.0)


async def report_usage():
    while True:
        await asyncio.sleep(3600)
        print(f"📊 {gemini_stats()}")


async def main() -> int:
    """Exit code 0 = stopped on purpose (needs a human), so run.bat / run.sh won't restart it."""
    print("☁️ Professional Arbitrage Scanner Starting (multi-account)...")
    missing = check_schema()
    if missing:
        print(f"❌ Supabase 'listings' table is missing columns: {', '.join(missing)}. Run the SQL from the setup notes first.")
        return 0
    shared = Shared()
    shared.config = load_bot_config()
    shared.config_version = 1
    shared.seen, shared.fingerprints = load_seen()
    print(f"   {len(shared.seen)} recent listings already known - they won't be analyzed again.")
    if settings.OUTREACH_DRY_RUN:
        print("   🧪 OUTREACH_DRY_RUN is on - no real messages will be sent.")

    accounts = []
    for acc in settings.FB_ACCOUNTS:
        reason = None if os.path.isdir(acc["session_dir"]) else "not logged in yet"
        reason = reason or account_pause_reason(acc["id"])
        if reason:
            shared.account_status[acc["id"]] = f"⏸️ {reason}"
            print(f"⏸️ {acc['id']} skipped: {reason}. Log it in with setup, then restart the scraper.")
        else:
            accounts.append(acc)
    if not accounts:
        print("❌ No Facebook account can run. Run: run.bat setup  (or ./run.sh setup)")
        return 0

    async with async_playwright() as p:
        background = [asyncio.create_task(t) for t in (refresh_config(shared), report_usage(), poll_telegram(shared), daily_summary(shared))]
        await asyncio.gather(*(supervise(acc, shared, p, 0 if n == 0 else random.uniform(*settings.STAGGER_START_SECONDS) * n)
                               for n, acc in enumerate(accounts)))
        for task in background:
            task.cancel()
    print("🛑 All accounts stopped. Fix the issue above, then restart the scraper.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n👋 Scraper stopped.")
        sys.exit(0)
