import streamlit as st
import yfinance as yf
import pandas as pd
from sec_api import QueryApi
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from transformers import pipeline
import io
import re

# --- CONFIGURATION ---
queryApi = QueryApi(api_key="YOUR_API_KEY")

st.set_page_config(page_title="INSIGHT ALPHA", layout="wide", page_icon="🧠")

st.markdown("""
    <style>
    .stApp { background-color: #0e1117; color: #ffffff; }
    div[data-testid="stMetricValue"] { font-size: 24px; color: #ffffff; }
    .stButton button { width: 100%; }
    .stAlert { background-color: #2b1c1c; color: #ffaaaa; border: 1px solid #ff4444; }
    /* Link Styles */
    a { text-decoration: none !important; color: #58a6ff; }
    a:hover { text-decoration: underline !important; color: #ff9900; }
    .news-card {
        padding: 10px;
        margin-bottom: 10px;
        background-color: #161b22;
        border-radius: 5px;
        border-left: 3px solid #ff9900;
    }
    </style>
""", unsafe_allow_html=True)


# --- 1. LOAD AI MODEL ---
@st.cache_resource
def load_sentiment_model():
    return pipeline("sentiment-analysis", model="ProsusAI/finbert")


sentiment_pipe = load_sentiment_model()


# --- 2. LOGIC FUNCTIONS ---
def validate_ticker(ticker):
    if not ticker:
        return False, "Ticker field cannot be empty."
    if not re.match(r'^[A-Z]+$', ticker):
        return False, "⚠️ Use English letters only (e.g., NVDA, AAPL)."
    if len(ticker) > 6:
        return False, "⚠️ Ticker is too long."
    return True, ""


def calculate_rsi(data, window=14):
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_dcf(info):
    try:
        fcf, shares = info.get('freeCashflow'), info.get('sharesOutstanding')
        if not fcf or fcf < 0 or not shares: return None
        growth, discount, terminal = 0.12, 0.10, 0.03
        future_fcf = [fcf * (1 + growth) ** i for i in range(1, 6)]
        pv_fcf = sum([f / (1 + discount) ** i for i, f in enumerate(future_fcf, 1)])
        tv = (future_fcf[-1] * (1 + terminal)) / (discount - terminal)
        pv_tv = tv / (1 + discount) ** 5
        return (pv_fcf + pv_tv) / shares
    except:
        return None


def get_data(ticker):
    stock = yf.Ticker(ticker)
    try:
        info = stock.info
        if 'currentPrice' not in info: return None, None, None
        hist = stock.history(period="2y")
        if hist.empty: return None, None, None

        hist['RSI'] = calculate_rsi(hist['Close'])
        summary = info.get('longBusinessSummary', 'Description not available.')

        data = {
            "Ticker": ticker,
            "Price": info.get('currentPrice', 0),
            "RevGrowth": info.get('revenueGrowth', 0),
            "FCF": info.get('freeCashflow', 0),
            "Margin": info.get('profitMargins', 0),
            "Target": info.get('targetMeanPrice', 0),
            "DCF": calculate_dcf(info),
            "RSI": hist['RSI'].iloc[-1] if not hist['RSI'].empty else 50,
            "Description": summary
        }
        clean_hist = hist.dropna(subset=['RSI'])
        return data, clean_hist, stock
    except:
        return None, None, None


def get_report(data):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        summary = pd.DataFrame({
            "Metric": ["Ticker", "Current Price", "DCF Fair Value"],
            "Value": [
                data['Ticker'],
                f"${data['Price']}",
                f"${data['DCF']:.2f}" if data['DCF'] else "N/A"
            ]
        })

        sheet_name = 'Summary'
        summary.to_excel(writer, sheet_name=sheet_name, index=False)

        workbook = writer.book
        worksheet = writer.sheets[sheet_name]

        header_fmt = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'bg_color': '#D3D3D3', 'border': 1
        })
        cell_fmt = workbook.add_format({'align': 'center', 'valign': 'vcenter'})

        for col_num, value in enumerate(summary.columns.values):
            worksheet.write(0, col_num, value, header_fmt)

        worksheet.set_column('A:B', 30, cell_fmt)

    return output.getvalue()


st.title("🧠 INSIGHT ALPHA: Intelligent Investment Platform")

if 'search_triggered' not in st.session_state:
    st.session_state.search_triggered = False


def trigger_search():
    st.session_state.search_triggered = True


col_in, _, col_bt = st.columns([6, 0.5, 2])
with col_in:
    raw_input = st.text_input("Enter Ticker:", "NVDA", on_change=trigger_search)
    ticker_input = raw_input.upper().strip()
with col_bt:
    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
    st.button("RUN DEEP ANALYSIS 🚀", on_click=trigger_search)

# EXECUTION LOGIC
if st.session_state.search_triggered:
    is_valid, error_msg = validate_ticker(ticker_input)
    if not is_valid:
        st.error(error_msg)
    else:
        with st.spinner(f"AI scanning {ticker_input} protocols..."):
            data, hist, stock = get_data(ticker_input)

            if not data:
                st.error(f"❌ Ticker '{ticker_input}' not found. Please check the symbol.")
            else:
                news_list = stock.news
                scores = []
                titles_found = []
                seen_titles = set()
                display_news = []

                if news_list:
                    for n in news_list:
                        if len(titles_found) >= 20:
                            break

                        title = n.get('title') or (
                            n.get('content', {}).get('title') if isinstance(n.get('content'), dict) else None)

                        # --- SAFE LINK RETRIEVAL ---
                        link = n.get('link')
                        content = n.get('content')

                        if not link and isinstance(content, dict):
                            ct_url = content.get('clickThroughUrl')
                            if isinstance(ct_url, dict):
                                link = ct_url.get('url')

                            if not link:
                                can_url = content.get('canonicalUrl')
                                if isinstance(can_url, dict):
                                    link = can_url.get('url')

                        if title and title not in seen_titles:
                            seen_titles.add(title)
                            titles_found.append(title)

                            final_link = link if link else f"https://www.google.com/search?q={title}"

                            if len(display_news) < 5:
                                display_news.append({"title": title, "link": final_link})

                            try:
                                res = sentiment_pipe(title)[0]
                                scores.append(
                                    1 if res['label'] == 'positive' else -1 if res['label'] == 'negative' else 0)
                            except:
                                continue

                if not scores:
                    sent_val, sent_text = 0, "No News Data"
                else:
                    sent_val = sum(scores) / len(scores)
                    sent_text = f"Positive ({sent_val:.2f})" if sent_val > 0.15 else f"Negative ({sent_val:.2f})" if sent_val < -0.15 else "Neutral / Mixed"

                # === BLOCK 1: METRICS ===
                st.divider()
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Price", f"${data['Price']}", delta=f"Target: ${data['Target']}")
                m2.metric("DCF Value", f"${data['DCF']:.2f}" if data['DCF'] else "N/A")
                m3.metric("AI Sentiment", sent_text)
                m4.metric("RSI (14d)", f"{data['RSI']:.1f}")

                # === TABS FOR BETTER UX ===
                tab1, tab2 = st.tabs(["📈 Analysis", "🏢 Company Info"])

                with tab1:
                    # === BLOCK 3: CHARTS ===
                    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1, row_heights=[0.7, 0.3])
                    fig.add_trace(go.Scatter(x=hist.index, y=hist['Close'], name="Price", line=dict(color='#58a6ff')), row=1, col=1)
                    fig.add_trace(
                        go.Scatter(x=hist.index, y=hist['RSI'], name="RSI (Psychology)", line=dict(color='orange')), row=2,
                        col=1)
                    fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
                    fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)
                    fig.update_layout(template="plotly_dark", height=500, margin=dict(b=20, t=20), hovermode="x unified")
                    st.plotly_chart(fig, use_container_width=True)

                with tab2:
                    st.subheader("Company Profile")
                    st.write(data['Description'])


                # === BLOCK 4: LATEST NEWS ===
                st.write("")
                st.subheader("📰 Latest Market News")
                if not display_news:
                    st.info("No recent news found.")
                else:
                    for news in display_news[:5]:
                        st.markdown(f"""
                        <div class="news-card">
                            <a href="{news['link']}" target="_blank" style="color: white; font-size: 16px;">{news['title']}</a>
                        </div>
                        """, unsafe_allow_html=True)

                # === BLOCK 5: VERDICT ===
                # NOTE: This block is currently under requirement testing for experimental features.
                # st.divider()
                # v, color, reason = "HOLD", "orange", "Mixed signals from market and fundamentals."
                # if data['DCF'] and data['DCF'] > data['Price'] and data['RSI'] < 45:
                #     v, color, reason = "STRONG BUY 🚀", "#00ff00", "Undervalued + Market fear (Low RSI)."
                # elif data['DCF'] and data['DCF'] < data['Price'] and data['RSI'] > 65:
                #     v, color, reason = "SELL / OVERHEATED 🔻", "#ff0000", "Overvalued + Market euphoria (High RSI)."
                #
                # st.markdown(
                #     f"<div style='background-color:{color}20; border:2px solid {color}; padding:20px; border-radius:10px; text-align:center;'><h1 style='color:{color};'>{v}</h1><p>{reason}</p></div>",
                #     unsafe_allow_html=True)

                st.divider()
                st.download_button("📥 DOWNLOAD REPORT (.xlsx)", get_report(data),
                                   f"{ticker_input}_Report.xlsx")