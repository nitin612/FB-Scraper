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
from deal_pipeline import save_bot_config, load_bot_config

logging.getLogger("google.genai").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
load_dotenv()

# Global Rotation Settings for Hardware Inspector
api_keys = [os.getenv("GEMINI_API_KEY_1"), os.getenv("GEMINI_API_KEY_2"), os.getenv("GEMINI_API_KEY_3"), os.getenv("GEMINI_API_KEY")]
valid_keys = [k for k in api_keys if k]
models_list = ['gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-flash-lite-latest', 'gemini-3.1-flash-lite-preview', 'gemini-3-flash-preview', 'gemini-2.5-flash', 'gemini-3.5-flash']

def get_config(name: str) -> str:
    return os.getenv(name) or st.secrets[name]

def val(x) -> float:
    return float(x) if x is not None and pd.notna(x) else 0.0

def when(ts) -> str:
    if ts is None or pd.isna(ts) or ts == "" or str(ts).lower() == "none":
        return "—"
    try:
        dt = pd.to_datetime(ts, utc=True)
        local_dt = dt.tz_convert(settings.TIMEZONE)
        now = pd.Timestamp.now(tz=settings.TIMEZONE)
        diff_sec = (now - local_dt).total_seconds()
        if 0 <= diff_sec < 60:
            return "just now"
        elif 60 <= diff_sec < 3600:
            return f"{int(diff_sec // 60)}m ago"
        elif 3600 <= diff_sec < 86400 and local_dt.date() == now.date():
            return f"Today, {local_dt.strftime('%I:%M %p')}"
        elif local_dt.date() == (now - pd.Timedelta(days=1)).date():
            return f"Yesterday, {local_dt.strftime('%I:%M %p')}"
        return local_dt.strftime("%b %d, %I:%M %p")
    except Exception:
        return "—"

@st.cache_resource
def init_connection():
    return create_client(get_config("SUPABASE_URL"), get_config("SUPABASE_KEY"))

supabase = init_connection()

st.set_page_config(
    page_title="Arbitrage Terminal",
    page_icon=":material/analytics:",
    layout="wide",
)

bot_config = load_bot_config()
is_active = bot_config.get("is_active", False) if bot_config else False
status_cls = "active" if is_active else "paused"
status_lbl = "SCANNER ACTIVE" if is_active else "SCANNER PAUSED"
sync_ts = pd.Timestamp.now(tz=settings.TIMEZONE).strftime("%I:%M %p")

# Compact spacing CSS overrides & Top Navbar
st.markdown(f"""
<style>
/* 1. Completely hide Streamlit header */
header[data-testid="stHeader"],
[data-testid="stHeader"],
div[data-testid="stHeader"] {{
    display: none !important;
    height: 0px !important;
    min-height: 0px !important;
    padding: 0px !important;
    margin: 0px !important;
    opacity: 0 !important;
    pointer-events: none !important;
    visibility: hidden !important;
}}

/* 2. Flush container to the very top */
[data-testid="stMainBlockContainer"],
.stMainBlockContainer,
.block-container,
div[data-testid="stMainBlockContainer"] {{
    padding-top: 0.25rem !important;
    padding-bottom: 1.5rem !important;
    padding-left: 1.25rem !important;
    padding-right: 1.25rem !important;
    max-width: 1520px !important;
}}

/* 3. Remove default top margin on first element container */
[data-testid="stVerticalBlock"] > div:first-child {{
    margin-top: 0 !important;
    padding-top: 0 !important;
}}

/* 4. Hide autorefresh iframe and avoid extra whitespace */
.st-key-data_refresh,
div[data-testid="stElementContainer"]:has(.st-key-data_refresh) {{
    display: none !important;
    height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
}}

/* Custom navbar */
.terminal-navbar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.35rem 0.25rem 0.75rem 0.25rem;
    border-bottom: 1px solid #272A34;
    margin-bottom: 0.65rem;
}}
.terminal-title-row {{
    display: flex;
    align-items: center;
    gap: 10px;
}}
.terminal-title {{
    font-size: 1.55rem;
    font-weight: 700;
    letter-spacing: -0.025em;
    color: #F3F4F6;
    line-height: 1.2;
}}
.terminal-badge {{
    background: #1B1D26;
    border: 1px solid #2E3345;
    color: #818CF8;
    font-size: 0.76rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 5px;
    letter-spacing: 0.03em;
}}
.terminal-meta {{
    font-size: 0.85rem;
    color: #94A3B8;
    margin-top: 3px;
}}
.terminal-status-container {{
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 3px;
}}
.status-pill {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.78rem;
    font-weight: 600;
    padding: 4px 12px;
    border-radius: 9999px;
    letter-spacing: 0.02em;
}}
.status-pill.active {{
    background: rgba(16, 185, 129, 0.12);
    color: #34D399;
    border: 1px solid rgba(16, 185, 129, 0.25);
}}
.status-pill.paused {{
    background: rgba(239, 68, 68, 0.12);
    color: #F87171;
    border: 1px solid rgba(239, 68, 68, 0.25);
}}
.status-dot {{
    width: 7px;
    height: 7px;
    border-radius: 50%;
}}
.status-dot.active {{
    background: #10B981;
    box-shadow: 0 0 6px #10B981;
}}
.status-dot.paused {{
    background: #EF4444;
}}
.sync-time {{
    font-size: 0.76rem;
    color: #94A3B8;
}}

/* Tighten tab bar */
div[data-testid="stTabs"] {{
    margin-top: -0.25rem !important;
}}
button[data-baseweb="tab"] {{
    font-size: 0.85rem !important;
    font-weight: 500 !important;
    padding: 6px 14px !important;
}}

/* Metric box styling */
div[data-testid="stMetric"] {{
    background: #16181F !important;
    border: 1px solid #272A34 !important;
    border-radius: 6px !important;
    padding: 8px 12px !important;
}}
div[data-testid="stMetricLabel"] {{
    font-size: 0.75rem !important;
    color: #94A3B8 !important;
}}
div[data-testid="stMetricValue"] {{
    font-size: 1.25rem !important;
    font-weight: 700 !important;
    color: #F3F4F6 !important;
}}
</style>

<div class="terminal-navbar">
    <div>
        <div class="terminal-title-row">
            <span class="terminal-title">Marketplace Arbitrage Terminal</span>
            <span class="terminal-badge">PRO v2.4</span>
        </div>
        <div class="terminal-meta">Autonomous secondary market surveillance • {bot_config.get('target_city', 'Toronto').title()}</div>
    </div>
    <div class="terminal-status-container">
        <div class="status-pill {status_cls}">
            <span class="status-dot {status_cls}"></span>
            {status_lbl}
        </div>
        <div class="sync-time">Sync: {sync_ts} • {settings.TIMEZONE}</div>
    </div>
</div>
""", unsafe_allow_html=True)

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
        if response_text:
            break
            
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
            text = f"Defect: {defect['label']}"
            draw.rectangle([x0, max(0, y0-15), x0+len(text)*6, y0], fill=color)
            draw.text((x0+2, max(0, y0-15)), text, fill="white")
        except Exception:
            pass
            
    return img, data

tab1, tab2, tab3 = st.tabs([
    ":material/radar: Deals Watchlist",
    ":material/inventory_2: Pipeline Tracking",
    ":material/tune: Scanner Settings"
])

# ---------------------------------------------------------
# TAB 1: DEALS WATCHLIST
# ---------------------------------------------------------
with tab1:
    response = supabase.table("listings").select("*").in_("status", ["NEW", "PENDING_OUTREACH"]).order("created_at", desc=True).execute()
    deals = response.data
    
    if not deals:
        with st.container(border=True):
            st.info("Watchlist is clear. No unprocessed opportunities require attention.")
    else:
        df = pd.DataFrame(deals)
        if 'created_at' in df.columns:
            df['created_at'] = pd.to_datetime(df['created_at']).dt.tz_convert(settings.TIMEZONE)

        col_search, col_tier, col_price = st.columns([3, 2, 1])
        search_query = col_search.text_input("Filter listings", placeholder="Search by model, brand, or location...", label_visibility="collapsed")
        tier_filter = col_tier.pills("Tier filter", ["All", "Elite Flips", "Prime Flips", "Good Deals"], default="All", label_visibility="collapsed")
        max_p = col_price.number_input("Max asking price", value=5000, step=100, label_visibility="collapsed")
            
        filtered_df = df[df['price'] <= max_p]
        if search_query:
            filtered_df = filtered_df[filtered_df['title'].str.contains(search_query, case=False, na=False)]

        if tier_filter == "Elite Flips":
            filtered_df = filtered_df[filtered_df['deal_tier'] == "ELITE_FLIP"]
        elif tier_filter == "Prime Flips":
            filtered_df = filtered_df[filtered_df['deal_tier'] == "PRIME_FLIP"]
        elif tier_filter == "Good Deals":
            filtered_df = filtered_df[filtered_df['deal_tier'] == "GOOD_DEAL"]

        if filtered_df.empty:
            st.info("No deals match the selected filter criteria.")
        else:
            def format_deal(x):
                tier = x.get('deal_tier') or 'TRASH'
                tier_tag = "ELITE" if tier == 'ELITE_FLIP' else ("PRIME" if tier == 'PRIME_FLIP' else "GOOD")
                status_tag = " [Queued]" if x.get('status') == 'PENDING_OUTREACH' else ""
                profit = val(x.get('projected_profit'))
                profit_str = f"+${profit:,.0f}" if profit > 0 else f"${profit:,.0f}"
                return f"[{tier_tag}]{status_tag} {x['title'][:55]}  —  ${x['price']:,.0f} CAD  (Net: {profit_str})"

            selected_deal = st.selectbox("Select deal to inspect:", filtered_df.to_dict('records'), format_func=format_deal, label_visibility="collapsed")

            if selected_deal:
                # Executive Dossier Container
                with st.container(border=True):
                    col_info, col_tags = st.columns([3, 2])
                    with col_info:
                        st.markdown(f"### {selected_deal['title']}")
                        meta_items = []
                        if selected_deal.get('location'):
                            meta_items.append(f":material/location_on: {selected_deal['location']}")
                        meta_items.append(f":material/schedule: Listed {when(selected_deal.get('listed_at'))}")
                        meta_items.append(f":material/radar: Scraped {when(selected_deal.get('created_at'))}")
                        st.caption(" • ".join(meta_items))

                    with col_tags:
                        c_b1, c_b2, c_b3 = st.columns(3)
                        b_tier = selected_deal.get('deal_tier')
                        if b_tier == 'ELITE_FLIP':
                            c_b1.badge("Elite Flip", icon=":material/bolt:", color="red")
                        elif b_tier == 'PRIME_FLIP':
                            c_b1.badge("Prime Flip", icon=":material/star:", color="orange")
                        else:
                            c_b1.badge("Good Deal", icon=":material/trending_up:", color="blue")
                        
                        cond = selected_deal.get('condition_grade', 'UNKNOWN')
                        c_b2.badge(f"{cond}", color="green" if cond in ("MINT", "GOOD") else "yellow")
                        
                        if selected_deal.get('status') == 'PENDING_OUTREACH':
                            c_b3.badge("Queued for DM", icon=":material/schedule_send:", color="violet")
                        else:
                            c_b3.badge("New Lead", icon=":material/fiber_new:", color="blue")

                    # Intraday Price Alerts
                    p_status = selected_deal.get("price_status")
                    if p_status == "OVERPRICED":
                        st.warning(f"**High Price Alert:** An active listing for this model was found earlier today for **${selected_deal.get('cheaper_deal_price', 0):,.0f} CAD**.")
                        st.link_button("View Cheaper Listing", selected_deal.get('cheaper_deal_url', '#'), icon=":material/open_in_new:")
                    elif p_status == "BEST_OF_DAY":
                        st.success("🏆 **24-Hour Low:** This is the lowest recorded asking price for this model today.")

                    # Financial Metrics Strip
                    m1, m2, m3, m4 = st.columns(4)
                    was_price = val(selected_deal.get('previous_price'))
                    price_delta = f"-${was_price - selected_deal['price']:,.0f}" if was_price and was_price > selected_deal['price'] else None
                    m1.metric("Asking Price", f"${selected_deal['price']:,.0f} CAD", delta=price_delta, delta_color="inverse" if price_delta else "off", border=True)
                    
                    comp_src = selected_deal.get('comp_source') or 'estimate'
                    m2.metric("Market Valuation", f"${val(selected_deal.get('market_value')):,.0f} CAD", help=f"Source: {comp_src}", border=True)
                    
                    repair = val(selected_deal.get('estimated_repair_cost'))
                    m3.metric("Est. Repair Cost", f"${repair:,.0f} CAD", border=True)
                    
                    profit = val(selected_deal.get('projected_profit'))
                    margin_pct = (profit / selected_deal['price'] * 100) if selected_deal['price'] > 0 else 0
                    m4.metric("Projected Profit", f"+${profit:,.0f} CAD" if profit > 0 else f"${profit:,.0f} CAD", delta=f"{margin_pct:.1f}% Margin", border=True)

                # Two-Column Detail Cards
                col_left, col_right = st.columns([1, 1], gap="medium")

                with col_left:
                    with st.container(border=True):
                        st.markdown("#### Hardware & Condition Dossier")
                        
                        s_r1, s_r2 = st.columns(2)
                        s_r1.caption("Scam Risk Assessment")
                        risk = selected_deal.get('scam_risk', 'LOW')
                        risk_color = "green" if risk == "LOW" else ("orange" if risk == "MEDIUM" else "red")
                        s_r1.markdown(f":{risk_color}[**{risk} RISK**]")
                        
                        s_r2.caption("Sourced By")
                        s_r2.markdown(f"**{selected_deal.get('found_by', 'Account_1')}**")
                        
                        st.caption("Reported Flaws & Defects")
                        flaws = selected_deal.get('defect_summary') or 'None detected'
                        st.markdown(f"*{flaws}*")
                        
                        if selected_deal.get('description'):
                            with st.expander("Seller's Original Listing Description", expanded=False):
                                st.write(selected_deal['description'])
                                
                        st.link_button("Open on Facebook Marketplace", selected_deal['url'], icon=":material/open_in_new:", width="stretch")
                        
                        if pd.notna(selected_deal.get("image_url")):
                            st.image(selected_deal["image_url"], width="stretch")
                            if st.button("Run AI Hardware Diagnostic Scan", icon=":material/document_scanner:", width="stretch"):
                                with st.spinner("Analyzing high-resolution listing photo..."):
                                    marked_img, report = inspect_hardware_with_ai(selected_deal["image_url"])
                                    st.image(marked_img, caption="Computer Vision Hardware Diagnosis")
                                    if report.get('condition_summary'):
                                        st.info(report.get('condition_summary'))

                with col_right:
                    with st.container(border=True):
                        st.markdown("#### Outreach & Seller Negotiation")
                        st.caption("AI-generated conversation starter tailored for fast seller response:")
                        
                        draft_text = selected_deal.get('auto_message_text', "Hey, is this still available?")
                        st.text_area("Starter Message", value=draft_text, height=100, label_visibility="collapsed")
                        
                        st.caption("Dispatch Status:")
                        dispatch_log = selected_deal.get('outreach_log') or 'Not Triggered'
                        st.info(f"{dispatch_log}")
                        
                        st.divider()
                        st.markdown("##### Pipeline Quick Actions")
                        act1, act2 = st.columns(2)
                        if act1.button("Mark as Contacted", icon=":material/chat:", type="primary", width="stretch"):
                            supabase.table("listings").update({
                                "status": "CONTACTED",
                                "contacted_by": "Manual Operator",
                                "contacted_at": pd.Timestamp.now(tz="UTC").isoformat()
                            }).eq("id", selected_deal['id']).execute()
                            st.success("Moved to Contacted pipeline.")
                            time.sleep(0.4)
                            st.rerun()
                            
                        if act2.button("Mark as Purchased", icon=":material/verified:", width="stretch"):
                            supabase.table("listings").update({"status": "PURCHASED"}).eq("id", selected_deal['id']).execute()
                            st.success("Added to Purchased inventory.")
                            time.sleep(0.4)
                            st.rerun()
                            
                        if st.button("Archive / Pass on Deal", icon=":material/archive:", width="stretch"):
                            supabase.table("listings").update({"status": "IGNORED"}).eq("id", selected_deal['id']).execute()
                            st.info("Deal archived.")
                            time.sleep(0.4)
                            st.rerun()

# ---------------------------------------------------------
# TAB 2: PIPELINE TRACKING
# ---------------------------------------------------------
with tab2:
    st.markdown("### Inventory & Negotiation Pipeline")
    
    q_contacted = supabase.table("listings").select("id", count="exact").eq("status", "CONTACTED").execute().count or 0
    q_pending = supabase.table("listings").select("id", count="exact").eq("status", "PENDING_OUTREACH").execute().count or 0
    q_purchased = supabase.table("listings").select("id", count="exact").eq("status", "PURCHASED").execute().count or 0
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Active Negotiations", f"{q_contacted} units", border=True)
    c2.metric("Queued for Outreach", f"{q_pending} units", border=True)
    c3.metric("Purchased Inventory", f"{q_purchased} units", border=True)
    
    pipeline_view = st.segmented_control("Pipeline Stage", ["Active Outreach & Queue", "Purchased Inventory"], default="Active Outreach & Queue", label_visibility="collapsed")
    
    pipeline_cols = {
        "status": st.column_config.TextColumn("Status"),
        "title": st.column_config.TextColumn("Listing Title"),
        "price": st.column_config.NumberColumn("Asking Price", format="$%d CAD"),
        "listed_at": st.column_config.DatetimeColumn("Listed", format="MMM D, h:mm a"),
        "created_at": st.column_config.DatetimeColumn("Scraped", format="MMM D, h:mm a"),
        "contacted_at": st.column_config.DatetimeColumn("Messaged", format="MMM D, h:mm a"),
        "contacted_by": st.column_config.TextColumn("Account"),
        "url": st.column_config.LinkColumn("Direct Link", display_text="Open ↗"),
    }

    def render_pipeline(statuses: list[str], empty_msg: str):
        res = supabase.table("listings").select("status,title,price,listed_at,created_at,contacted_at,contacted_by,url") \
            .in_("status", statuses).order("created_at", desc=True).execute()
        if not res.data:
            with st.container(border=True):
                st.info(empty_msg)
            return
        table = pd.DataFrame(res.data)
        for col in ("listed_at", "created_at", "contacted_at"):
            table[col] = pd.to_datetime(table[col], utc=True, errors="coerce").dt.tz_convert(settings.TIMEZONE)
        st.dataframe(table, column_config=pipeline_cols, hide_index=True, width="stretch")

    if pipeline_view == "Active Outreach & Queue":
        render_pipeline(["CONTACTED", "PENDING_OUTREACH"], "No items currently in outreach queue or active conversation.")
    else:
        render_pipeline(["PURCHASED"], "No items marked as purchased yet.")

# ---------------------------------------------------------
# TAB 3: SCANNER SETTINGS
# ---------------------------------------------------------
with tab3:
    st.markdown("### Scanner Engine Command Center")
    if bot_config:
        state = bot_config.get('is_active', False)
        
        with st.container(border=True):
            status_col1, status_col2 = st.columns([3, 2])
            with status_col1:
                if state:
                    st.badge("ENGINE ACTIVE", icon=":material/check_circle:", color="green")
                    st.markdown("#### Autonomous Surveillance Running")
                    st.caption(f"Continuous scanner is polling Facebook Marketplace in **{bot_config.get('target_city', 'Toronto').title()}**.")
                else:
                    st.badge("ENGINE PAUSED", icon=":material/pause_circle:", color="red")
                    st.markdown("#### Scanner On Standby")
                    st.caption("Scraping engine and auto-messaging are paused. Facebook accounts are resting.")
            with status_col2:
                btn_start, btn_pause = st.columns(2)
                if btn_start.button("Start Engine", type="primary", icon=":material/play_arrow:", disabled=state, width="stretch"):
                    save_bot_config({"is_active": True})
                    st.rerun()
                if btn_pause.button("Pause Engine", icon=":material/pause:", disabled=not state, width="stretch"):
                    save_bot_config({"is_active": False})
                    st.rerun()
                    
        st.markdown("#### Performance Metrics Today")
        since = pd.Timestamp.now(tz=settings.TIMEZONE).normalize().tz_convert('UTC').isoformat()
        def count_today(column="created_at", **filters):
            q = supabase.table("listings").select("id", count="exact").gte(column, since)
            for col, v in filters.items():
                q = q.eq(col, v)
            return q.limit(1).execute().count or 0
            
        checked, hidden = count_today(), count_today(status="TRASH")
        deals_found = checked - hidden
        msgs_sent = count_today("contacted_at")
        
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Listings Scanned", f"{checked:,}", border=True)
        s2.metric("Filtered Out Trash", f"{hidden:,}", border=True)
        s3.metric("Deals Discovered", f"{deals_found:,}", delta=f"{(deals_found/checked*100):.1f}% Yield" if checked > 0 else None, border=True)
        s4.metric("Messages Dispatched", f"{msgs_sent} / {settings.MAX_MESSAGES_PER_DAY}", border=True)
        
        st.caption(f"Safety Limits: Max {settings.MAX_SEARCHES_PER_HOUR} searches/hr • Max {settings.MAX_MESSAGES_PER_DAY} messages/day (min {settings.MIN_MINUTES_BETWEEN_MESSAGES} min gap) • Active hours: {settings.ACTIVE_START_HOUR}:00 - {settings.ACTIVE_END_HOUR}:00.")

        # Account Health Status
        acc_states = load_account_state()
        if acc_states:
            with st.container(border=True):
                st.markdown("##### Account Attention Required")
                for acc_id, info in acc_states.items():
                    st.error(f":material/error: **{acc_id}** is paused: Facebook indicated **{info['problem']}** ({info['since']}). Log in via `./run.sh setup` to resume.")
                
        # Configuration Card
        with st.container(border=True):
            st.markdown("##### Global Surveillance Parameters")
            auto_dm = st.toggle(
                f"Auto-message Elite & Prime deals (Safety cap: max {settings.MAX_MESSAGES_PER_DAY}/day per account)",
                value=bot_config.get('auto_message_enabled', False)
            )
            if auto_dm != bot_config.get('auto_message_enabled'):
                save_bot_config({"auto_message_enabled": auto_dm})
                st.rerun()

            with st.form("settings_form"):
                city = st.text_input("Target City / Metro Area", value=bot_config['target_city'] if bot_config else "toronto")
                kw = st.text_input("Search Keywords (comma-separated)", value=bot_config['keywords'] if bot_config else "iPhone")
                p_col1, p_col2 = st.columns(2)
                min_p = p_col1.number_input("Minimum Asking Price ($ CAD)", value=bot_config['min_price'] if bot_config else 50, step=25)
                max_p = p_col2.number_input("Maximum Asking Price ($ CAD)", value=bot_config['max_price'] if bot_config else 2500, step=100)
                
                if st.form_submit_button("Deploy Changes to Engine", type="primary", icon=":material/bolt:"):
                    save_bot_config({"target_city": city, "keywords": kw, "min_price": min_p, "max_price": max_p})
                    st.success("Configuration deployed. Active scraper updated in real-time.")
                    time.sleep(0.5)
                    st.rerun()

# 30-second background autorefresh at the bottom of the page
st_autorefresh(interval=30000, limit=None, key="data_refresh")