import os
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_telegram_alert(text: str, listing_id: str, listing_url: str, image_url: str = None):
    keyboard = {"inline_keyboard": [[{"text": "🔗 View Listing", "url": listing_url}]]}
    if image_url:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", json={"chat_id": CHAT_ID, "photo": image_url, "caption": text, "parse_mode": "HTML", "reply_markup": keyboard})
    else:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "reply_markup": keyboard})