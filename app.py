import streamlit as st
import os
from streamlit_autorefresh import st_autorefresh
from supabase import create_client, Client
import pandas as pd
import requests
from io import BytesIO
import json
from PIL import Image, ImageDraw
from google import genai
from google.genai import types
import time
from dotenv import load_dotenv
import logging
import settings
from fb_browser import load_account_state

logging.getLogger("google.genai").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
load_dotenv()

# Global Rotation Settings for Hardware Inspector
api_keys = [os.getenv("GEMINI_API_KEY_1"), os.getenv("GEMINI_API_KEY_2"), os.getenv("GEMINI_API_KEY_3"), os.getenv("GEMINI_API_KEY")]
valid_keys = [k for k in api_keys if k]
models_list = ['gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-flash-lite-latest', 'gemini-3.1-flash-lite-preview', 'gemini-3-flash-preview', 'gemini-2.5-flash', 'gemini-3.5-flash']

def get_config(name: str) -> str:
    # .env (used by run.bat / run.sh) first, then .streamlit/secrets.toml
    return os.getenv(name) or st.secrets[name]

def val(x) -> float:
    return float(x) if x is not None and pd.notna(x) else 0.0

def when(ts) -> str:
    if ts is None or pd.isna(ts) or ts == "":
        return "—"
    return pd.to_datetime(ts, utc=True).tz_convert(settings.TIMEZONE).strftime("%b %d, %I:%M %p")

@st.cache_resource
def init_connection():
    return create_client(get_config("SUPABASE_URL"), get_config("SUPABASE_KEY"))

supabase = init_connection()

st.set_page_config(page_title="Deal Hunter CRM", page_icon="📱", layout="wide")
st.title("📱 Arbitrage CRM & Live Scanner")
st_autorefresh(interval=30000, limit=None, key="data_refresh")

settings_req = supabase.table("bot_settings").select("*").eq("id", 1).execute()
bot_config = settings_req.data[0] if settings_req.data else None

@st.cache_data(show_spinner=False)
def inspect_hardware_with_ai(img_url: str):
    res = requests.get(img_url)
    img = Image.open(BytesIO(res.content)).convert("RGB")
    
    prompt = """
    You are an expert mobile device hardware inspector. Analyze this phone/laptop listing photo.
    Identify physical damage (cracks, heavy scratches, edge dents) and authenticity red flags.
    Return a strictly formatted JSON object matching this schema:
    {"authenticity_status": "Genuine, Suspect, or Fake", "condition_summary": "Brief analysis.", "defects": [{"label": "Cracked bottom right corner", "severity": "High", "box_2d": [ymin, xmin, ymax, xmax]}]}
    The "box_2d" values MUST be integers scaled from 0 to 1000.
    """
    
    response_text = None
    for key in valid_keys:
        for model_name in models_list:
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model=model_name, contents=[prompt, img],
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                if response and response.text:
                    response_text = response.text
                    break
            except Exception:
                continue
        if response_text: break
            
    if not response_text:
        raise Exception("All API keys and models exhausted. Rate limit exceeded.")
        
    data = json.loads(response_text)
    draw = ImageDraw.Draw(img)
    width, height = img.size
    
    for defect in data.get("defects", []):
        try:
            ymin, xmin, ymax, xmax = defect["box_2d"]
            y0, x0, y1, x1 = (ymin/1000)*height, (xmin/1000)*width, (ymax/1000)*height, (xmax/1000)*width
            color = "red" if defect.get("severity") == "High" else "orange"
            draw.rectangle([x0, y0, x1, y1], outline=color, width=4)
            text = f"⚠️ {defect['label']}"
            draw.rectangle([x0, max(0, y0-15), x0+len(text)*6, y0], fill=color)
            draw.text((x0+2, max(0, y0-15)), text, fill="white")
        except: pass
            
    return img, data

tab1, tab2, tab3 = st.tabs(["🔥 Deals Watchlist", "💼 Active Inventory", "⚙️ Scanner Controls"])

with tab1:
    response = supabase.table("listings").select("*").in_("status", ["NEW", "PENDING_OUTREACH"]).order("created_at", desc=True).execute()
    deals = response.data
    
    if not deals:
        st.info("🎉 Watchlist is clear! No unprocessed deals right now.")
    else:
        df = pd.DataFrame(deals)
        if 'created_at' in df.columns:
            df['created_at'] = pd.to_datetime(df['created_at']).dt.tz_convert('America/Toronto')

        col_search, col_filter = st.columns([3, 1])
        search_query = col_search.text_input("🔍 Filter by keyword", placeholder="e.g. S24, iPhone...")
        max_p = col_filter.number_input("Max Price", value=100000, step=1000)
            
        filtered_df = df[df['price'] <= max_p]
        if search_query:
            filtered_df = filtered_df[filtered_df['title'].str.contains(search_query, case=False, na=False)]

        def format_deal(x):
            tier = x.get('deal_tier')
            icon = "🔥🔥" if tier == 'ELITE_FLIP' else ("🚨" if tier == 'PRIME_FLIP' else "🟢")
            q_icon = "⏳" if x.get('status') == 'PENDING_OUTREACH' else ""
            badge = " 🏆(NEW LOW)" if x.get('price_status') == 'BEST_OF_DAY' else (" ⚠️(OVERPRICED)" if x.get('price_status') == 'OVERPRICED' else "")
            profit_txt = f" | 💵 +${val(x.get('projected_profit')):,.0f}" if val(x.get('projected_profit')) else ""
            return f"{icon}{q_icon}{badge} | 💰 ${x['price']:,.0f}{profit_txt} | {x['title'][:60]}"

        if not filtered_df.empty:
            selected_deal = st.selectbox("Select deal to inspect:", filtered_df.to_dict('records'), format_func=format_deal)
            action_placeholder = st.empty()

            with action_placeholder.container():
                col_left, col_right = st.columns([1, 1], gap="large")

                with col_left:
                    st.markdown("#### 💰 Intraday Price Analysis")
                    p_status = selected_deal.get("price_status")
                    if p_status == "OVERPRICED":
                        st.error(f"⚠️ **DO NOT OVERPAY:** An active listing for this model was found earlier today for **${selected_deal.get('cheaper_deal_price', 0):,.0f}**.")
                        st.link_button("👉 View Cheaper Listing Instead", selected_deal.get('cheaper_deal_url', '#'))
                    elif p_status == "BEST_OF_DAY":
                        st.success("🏆 **TODAY'S LOW:** This is the cheapest price recorded for this model in the last 24h!")
                    
                    st.markdown("#### 📱 Details")
                    was = f" ~~${val(selected_deal.get('previous_price')):,.0f}~~" if val(selected_deal.get('previous_price')) else ""
                    st.markdown(f"**Listed Price:** :green[**${selected_deal['price']:,.0f}**]{was}")
                    st.markdown(f"**Listed on FB:** {when(selected_deal.get('listed_at'))} · **Scraped:** {when(selected_deal.get('created_at'))}")
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Market value", f"${val(selected_deal.get('market_value')):,.0f}", help=f"Source: {selected_deal.get('comp_source') or 'n/a'}")
                    m2.metric("Est. repair", f"${val(selected_deal.get('estimated_repair_cost')):,.0f}")
                    m3.metric("Proj. profit", f"${val(selected_deal.get('projected_profit')):,.0f}")
                    st.markdown(f"**Condition:** {selected_deal.get('condition_grade') or 'n/a'} · **Scam risk:** {selected_deal.get('scam_risk') or 'n/a'} · **Found by:** {selected_deal.get('found_by') or 'n/a'}")
                    st.markdown(f"**Flaws:** {selected_deal.get('defect_summary') or 'None'}")
                    st.markdown(f"**Status:** {selected_deal.get('outreach_log', 'New')}")
                    if isinstance(selected_deal.get('description'), str) and selected_deal['description']:
                        with st.expander("Seller's description"):
                            st.write(selected_deal['description'])
                    st.link_button("🔗 Open on FB Marketplace", selected_deal['url'], width="stretch")

                    if pd.notna(selected_deal.get("image_url")):
                        st.image(selected_deal["image_url"], width="stretch")
                        if st.button("🔍 AI Hardware Scan", width="stretch"):
                            with st.spinner("Scanning..."):
                                marked_img, report = inspect_hardware_with_ai(selected_deal["image_url"])
                                st.image(marked_img, caption="Hardware Diagnosis Overlay")
                                st.write(report.get('condition_summary', ''))

                    c1, c2, c3 = st.columns(3)
                    if c1.button("💬 Contacted", width="stretch", type="primary"):
                        supabase.table("listings").update({"status": "CONTACTED", "contacted_by": "Manual",
                                                           "contacted_at": pd.Timestamp.now(tz="UTC").isoformat()}).eq("id", selected_deal['id']).execute()
                        st.rerun()
                    if c2.button("✅ Purchased", width="stretch"):
                        supabase.table("listings").update({"status": "PURCHASED"}).eq("id", selected_deal['id']).execute()
                        st.rerun()
                    if c3.button("❌ Ignore", width="stretch"):
                        supabase.table("listings").update({"status": "IGNORED"}).eq("id", selected_deal['id']).execute()
                        st.rerun()

                with col_right:
                    st.markdown("#### 🤖 AI Negotiation Draft")
                    st.code(selected_deal.get('auto_message_text', "Is this still available?"), wrap_lines=True)

with tab2:
    st.subheader("Pipeline Tracking")
    pipeline_cols = {
        "status": st.column_config.TextColumn("Status"),
        "title": st.column_config.TextColumn("Listing"),
        "price": st.column_config.NumberColumn("Price", format="$%d"),
        "listed_at": st.column_config.DatetimeColumn("Listed on FB", format="MMM D, h:mm a"),
        "created_at": st.column_config.DatetimeColumn("Scraped", format="MMM D, h:mm a"),
        "contacted_at": st.column_config.DatetimeColumn("Messaged", format="MMM D, h:mm a"),
        "contacted_by": st.column_config.TextColumn("By"),
        "url": st.column_config.LinkColumn("Link", display_text="Open ↗"),
    }

    def pipeline_table(statuses: list[str], empty_msg: str):
        res = supabase.table("listings").select("status,title,price,listed_at,created_at,contacted_at,contacted_by,url") \
            .in_("status", statuses).order("created_at", desc=True).execute()
        if not res.data:
            st.info(empty_msg)
            return
        table = pd.DataFrame(res.data)
        for col in ("listed_at", "created_at", "contacted_at"):
            table[col] = pd.to_datetime(table[col], utc=True, errors="coerce").dt.tz_convert(settings.TIMEZONE)
        st.dataframe(table, column_config=pipeline_cols, hide_index=True, width="stretch")

    st.markdown("### ⏳ Contacted & Queued")
    pipeline_table(["CONTACTED", "PENDING_OUTREACH"], "No active queue.")
    st.markdown("### ✅ Purchased")
    pipeline_table(["PURCHASED"], "No inventory.")

with tab3:
    st.subheader("⚙️ Global Scanner Settings")
    if bot_config:
        state = bot_config['is_active']
        if state:
            st.success("🟢 SCANNER LIVE")
        else:
            st.error("🔴 SCANNER PAUSED")
        
        c_on, c_off = st.columns(2)
        if c_on.button("▶️ Start System", type="primary", disabled=state, width="stretch"):
            supabase.table("bot_settings").update({"is_active": True}).eq("id", 1).execute(); st.rerun()
        if c_off.button("⏸️ Pause System", disabled=not state, width="stretch"):
            supabase.table("bot_settings").update({"is_active": False}).eq("id", 1).execute(); st.rerun()
            
        st.markdown("---")
        st.markdown("#### 📊 Today")
        since = pd.Timestamp.now(tz=settings.TIMEZONE).normalize().tz_convert('UTC').isoformat()
        def count_today(column="created_at", **filters):
            q = supabase.table("listings").select("id", count="exact").gte(column, since)
            for col, v in filters.items():
                q = q.eq(col, v)
            return q.limit(1).execute().count or 0
        checked, hidden = count_today(), count_today(status="TRASH")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Listings checked", checked)
        s2.metric("Filtered out (hidden)", hidden)
        s3.metric("Deals found", checked - hidden)
        s4.metric("Messages sent", count_today("contacted_at"))
        st.caption(f"Safety limits per Facebook account: {settings.MAX_SEARCHES_PER_HOUR} searches/hour, {settings.MAX_MESSAGES_PER_DAY} messages/day "
                   f"(at least {settings.MIN_MINUTES_BETWEEN_MESSAGES} min apart), active {settings.ACTIVE_START_HOUR}:00-{settings.ACTIVE_END_HOUR}:00 {settings.TIMEZONE} time.")
        for acc_id, info in load_account_state().items():
            st.warning(f"⛔ {acc_id} is paused: Facebook showed {info['problem']} ({info['since']}). Log it in again with run.bat setup, then restart the scraper.")
        st.markdown("---")
        auto_dm = st.toggle(f"Auto-message Elite/Prime deals (each account max {settings.MAX_MESSAGES_PER_DAY}/day)", value=bot_config.get('auto_message_enabled', False))
        if auto_dm != bot_config.get('auto_message_enabled'):
            supabase.table("bot_settings").update({"auto_message_enabled": auto_dm}).eq("id", 1).execute(); st.rerun()

    with st.form("settings_form"):
        city = st.text_input("Target City", value=bot_config['target_city'] if bot_config else "toronto")
        kw = st.text_input("Keywords", value=bot_config['keywords'] if bot_config else "iPhone")
        min_p = st.number_input("Min $", value=bot_config['min_price'] if bot_config else 50)
        max_p = st.number_input("Max $", value=bot_config['max_price'] if bot_config else 2500)
        if st.form_submit_button("Deploy Changes"):
            supabase.table("bot_settings").update({"target_city": city, "keywords": kw, "min_price": min_p, "max_price": max_p}).eq("id", 1).execute()
            st.success("Changes deployed to database.")