"""Telegram alerts with action buttons, and polling for the button taps."""
import html
import os
import requests
from typing import Any
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def enabled() -> bool:
    return bool(TOKEN and CHAT_ID)


def esc(text) -> str:
    """Listing text goes into Telegram HTML - escape it so a '<' or '&' in a title can't break the alert."""
    return html.escape(str(text or ""))


def _api(method: str, payload: dict, timeout: int = 15) -> dict:
    try:
        return requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=payload, timeout=timeout).json()
    except Exception as e:
        return {"ok": False, "description": str(e)[:100]}


def link_buttons(listing_url: str) -> dict:
    return {"inline_keyboard": [[{"text": "🔗 View on Facebook", "url": listing_url}]]}


def deal_buttons(listing_id: str, listing_url: str) -> dict:
    return {"inline_keyboard": [
        [{"text": "💬 Message seller", "callback_data": f"msg:{listing_id}"},
         {"text": "❌ Ignore", "callback_data": f"ign:{listing_id}"}],
        [{"text": "🔗 View on Facebook", "url": listing_url}],
    ]}


def send_telegram_alert(message: str, listing_url: str, image_url: str | None = None, listing_id: str | None = None):
    """Deal alerts (with a listing_id) get Message/Ignore buttons; other alerts only get the Facebook link."""
    if not enabled():
        return
    markup = deal_buttons(listing_id, listing_url) if listing_id else link_buttons(listing_url)
    payload: dict[str, Any] = {"chat_id": CHAT_ID, "parse_mode": "HTML", "reply_markup": markup}
    # Telegram downloads the photo itself, which can be slow - give it longer than a text message
    if image_url and _api("sendPhoto", {**payload, "photo": image_url, "caption": message}, timeout=40).get("ok"):
        return
    res = _api("sendMessage", {**payload, "text": message})
    if not res.get("ok"):
        print(f"⚠️ Telegram alert failed: {res.get('description')}")


def send_text(message: str, reply_to: int | None = None):
    if not enabled():
        return
    payload: dict[str, Any] = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_to:
        payload.update(reply_to_message_id=reply_to, allow_sending_without_reply=True)
    _api("sendMessage", payload)


def get_updates(offset: int | None) -> list[dict] | None:
    """Long-polls Telegram for button taps (waits up to 25s). None means Telegram returned an error."""
    payload: dict[str, Any] = {"timeout": 25, "allowed_updates": ["callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    res = _api("getUpdates", payload, timeout=35)
    return res.get("result", []) if res.get("ok") else None


def answer_button(callback_id: str, text: str):
    _api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})


def set_buttons(message_id: int, markup: dict):
    _api("editMessageReplyMarkup", {"chat_id": CHAT_ID, "message_id": message_id, "reply_markup": markup})


def is_our_chat(callback: dict) -> bool:
    """Only taps from the configured chat are acted on."""
    return str(((callback.get("message") or {}).get("chat") or {}).get("id")) == str(CHAT_ID)
