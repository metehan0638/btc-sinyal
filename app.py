"""BTC gösterge dereceleri paneli (güçlü al ... güçlü sat).  Çalıştır:  streamlit run app.py"""
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml
from plotly.subplots import make_subplots

from core.data import get_candles
from core.news import IST, analyze as analyze_news
from core.sentiment import EMOJI as FG_EMOJI, fetch_fear_greed, summary as fg_summary
from core.zones import (LEVELS, LEVEL_TITLE, METRIC_KEYS, MIN_BARS, RATING_TEXT, bands_for, compute_metrics, latest_rows,
                        summarize, trend_info)

CFG = yaml.safe_load(open(Path(__file__).parent / "config.yaml", encoding="utf-8"))
TF_LABEL = {"1d": "Günlük", "1w": "Haftalık", "1M": "Aylık"}
GRADE_NAME = {g: f"{icon} {LEVEL_TITLE[g]}" for g, (icon, _) in LEVELS.items()}
RATING_TH = tuple(CFG.get("rating_thresholds") or (0.3, 1.0))
BAND_COLORS = ["#1b5e20", "#66bb6a", "#ef5350", "#b71c1c"]  # t1 güçlü al, t2 al, t3 sat, t4 güçlü sat

st.set_page_config(page_title="Kripto Takip Paneli", page_icon="₿", layout="wide")

# ---------------- kenar çubuğu ----------------
if "nonce" not in st.session_state:
    st.session_state.nonce = 0
with st.sidebar:
    st.header("Ayarlar")
    if st.button("🔄 Veriyi yenile"):
        st.session_state.nonce += 1
    st.caption("Veri 1 saat önbelleğe alınır. Hesaplar yalnızca KAPANMIŞ mumlara göre yapılır. "
               "Derece aralıklarını config.yaml içinden değiştirebilirsin.")


@st.cache_data(ttl=3600, show_spinner="Mum verisi alınıyor...")
def load_candles(symbol, tf, exchanges, since, nonce):
    return get_candles(symbol, tf, exchanges, since)


@st.cache_data(ttl=1800, show_spinner="Haberler taranıyor...")
def load_news(nonce):
    state_file = Path(__file__).parent / "state.json"
    hist = json.loads(state_file.read_text(encoding="utf-8")).get("news_hist", {}) if state_file.exists() else {}
    feeds = (CFG.get("news") or {}).get("feeds")
    return analyze_news(feeds, hist)


@st.cache_data(ttl=3600, show_spinner="Fear & Greed endeksi alınıyor...")
def load_fg(nonce):
    return fetch_fear_greed()


fg_series, fg_info = None, None
if CFG.get("fear_greed", True):
    try:
        fg_series = load_fg(st.session_state.nonce)
        fg_info = fg_summary(fg_series)
    except Exception as e:  # alınamazsa panel yine çalışır, günlük puan Fear & Greed'siz hesaplanır
        st.info(f"Fear & Greed endeksi alınamadı ({e}). Günlük puan bu gösterge olmadan hesaplandı.")


def analyze(tf):
    df = load_candles(CFG["symbol"], tf, tuple(CFG["exchanges"]), CFG["since"], st.session_state.nonce)
    params = CFG["timeframes"].get(tf, {})
    raw = compute_metrics(df, params)
    rows = latest_rows(raw, params, tf, fg_info if tf == "1d" else None)
    return df, raw, rows, summarize(rows, RATING_TH), params, trend_info(raw, params, tf)


try:
    data = {tf: analyze(tf) for tf in TF_LABEL}
except Exception as e:
    st.error(f"Veri alınamadı: {e}")
    st.info("İnternet bağlantını ve borsa erişimini kontrol et. Gerekirse config.yaml içindeki `exchanges` listesini değiştir.")
    st.stop()


def rating_delta(summ):
    c = summ["counts"]
    return f"ort. puan {summ['mean']:+.2f} · {c[2] + c[1]} al · {c[0]} nötr · {c[-1] + c[-2]} sat" if summ["n"] else None


# ---------------- başlık + özet ----------------
st.title("Kripto Takip Paneli")
cols = st.columns(4)
for col, tf in zip(cols[:3], TF_LABEL):
    df, raw, rows, summ, _, _trend = data[tf]
    icon, label = RATING_TEXT[summ["key"]]
    col.metric(f"{TF_LABEL[tf]} ({df.index[-1].date()} kapanışı)", f"{icon} {label}", rating_delta(summ), delta_color="off")
if fg_info:
    cols[3].metric("Fear & Greed", f"{fg_info['value']:.0f} · {fg_info['label']}",
                   f"dün {fg_info['yesterday']:.0f}" if fg_info["yesterday"] is not None else None, delta_color="off")
else:
    cols[3].metric("Fear & Greed", "-")
for tf_, (df_, *_rest) in data.items():
    if len(df_) < MIN_BARS[tf_]:
        st.warning(f"{TF_LABEL[tf_]}: yalnızca {len(df_)} mum alındı (en az {MIN_BARS[tf_]} beklenir). "
                   "Binance'e erişilemiyor, yedek borsanın geçmişi kısa olabilir; bu zaman diliminin değerleri güvenilir olmayabilir.")

# ---------------- zaman dilimi seçimi ----------------
tf = st.radio("Zaman dilimi", list(TF_LABEL), format_func=TF_LABEL.get, horizontal=True)
df, raw, rows, summ, params, trend = data[tf]
tab1, tab2, tab3 = st.tabs(["📊 Güncel durum", "📈 Grafik", "🔥 Haber / Hype"])

# ---------------- sekme 1: güncel durum ----------------
with tab1:
    icon, label = RATING_TEXT[summ["key"]]
    st.subheader(f"{TF_LABEL[tf]}: {icon} {label}")
    if summ["n"]:
        c = summ["counts"]
        st.caption(f"{summ['n']} göstergenin ortalama puanı {summ['mean']:+.2f} (−2 güçlü sat … +2 güçlü al): "
                   f"{c[2]} güçlü al · {c[1]} al · {c[0]} nötr · {c[-1]} sat · {c[-2]} güçlü sat. "
                   f"Genel derece sınırları: ≥{RATING_TH[1]:g} güçlü al, ≥{RATING_TH[0]:g} al, ≤−{RATING_TH[0]:g} sat, ≤−{RATING_TH[1]:g} güçlü sat.")

    def color(v):
        for prefix, css in (("🟢🟢", "#1b5e20"), ("🟢", "#388e3c"), ("🔴🔴", "#b71c1c"), ("🔴", "#e64a19"), ("⚪", "#555")):
            if v.startswith(prefix):
                return f"background-color:{css};color:white"
        return ""

    table = pd.DataFrame([{"Gösterge": r["name"], "Değer": r["text"], "Derece": GRADE_NAME.get(r["grade"], "❔ Yetersiz veri"),
                           "Yorum": r["note"]} for r in rows])
    st.dataframe(table.style.map(color, subset=["Derece"]), hide_index=True, use_container_width=True)
    if trend["state"] == trend["state"]:
        st.caption(f"Cross: {trend['text']}. {trend['last_text']}. Golden yapısı +1, dead yapısı −1; kesişim yeniyse ±2 puan. "
                   "Kesişim gecikmeli bir onay sinyalidir.")

    last = raw.iloc[-1]
    c1, c2, c3 = st.columns(3)
    c1.metric("Son kapanış", f"{last.close:,.0f}")
    c2.metric("ATH (en yüksek)", f"{last.ath:,.0f}")
    c3.metric("ATH'den uzaklık", f"%{last.drawdown:.1f}")

    if fg_series is not None:
        st.subheader("Piyasa duyarlılığı: Fear & Greed")
        st.write(f"{FG_EMOJI.get(fg_info['label'], '')} **{fg_info['value']:.0f} — {fg_info['label']}** "
                 + " · ".join(f"{n} {fg_info[k]:.0f}" for n, k in (("dün", "yesterday"), ("7 gün önce", "week"), ("30 gün önce", "month")) if fg_info[k] is not None))
        recent = fg_series.loc[fg_series.index[-1] - pd.DateOffset(years=1):]
        f3 = go.Figure(go.Scatter(x=recent.index, y=recent.values, mode="lines", name="Fear & Greed"))
        for y, col_ in zip(bands_for(CFG["timeframes"].get("1d", {}), "1d")["fear_greed"], BAND_COLORS):
            f3.add_hline(y=y, line_dash="dot", line_width=1, line_color=col_)
        f3.update_layout(height=280, yaxis=dict(range=[0, 100], title="0 = aşırı korku · 100 = aşırı açgözlülük"),
                         margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
        st.plotly_chart(f3, use_container_width=True)
        st.caption("Kaynak: alternative.me. Düşük değer (korku) AL tarafı, yüksek değer (açgözlülük) SAT tarafı sayılır; günlük puana dahildir.")

# ---------------- sekme 2: grafik ----------------
with tab2:
    names = {r["key"]: r["name"] for r in rows if r["key"] in METRIC_KEYS}
    key = st.selectbox("Alt grafikte gösterilecek gösterge", list(names), format_func=names.get)
    c1, c2 = st.columns(2)
    span = c1.selectbox("Aralık", ["Tümü", "Son 4 yıl", "Son 2 yıl", "Son 1 yıl"], index=1)
    log_y = c2.checkbox("Logaritmik fiyat ekseni", value=True)
    cut = {"Tümü": None, "Son 4 yıl": 4, "Son 2 yıl": 2, "Son 1 yıl": 1}[span]
    start = df.index[-1] - pd.DateOffset(years=cut) if cut else df.index[0]
    d, r = df.loc[start:], raw.loc[start:]
    fast_n, slow_n = trend["fast"], trend["slow"]
    bands = bands_for(params, tf)[key]
    events = r[r["cross_event"] != 0]
    golden, dead = events[events["cross_event"] > 0], events[events["cross_event"] < 0]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.04)
    fig.add_trace(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close, name="BTC"), 1, 1)
    fig.add_trace(go.Scatter(x=r.index, y=r["ma_fast"], name=f"SMA{fast_n}", line=dict(width=1.3)), 1, 1)
    fig.add_trace(go.Scatter(x=r.index, y=r["ma_slow"], name=f"SMA{slow_n}", line=dict(width=1.3)), 1, 1)
    fig.add_trace(go.Scatter(x=golden.index, y=golden["ma_slow"], mode="markers", name="Golden cross",
                             marker=dict(symbol="triangle-up", size=13, color="#00c853")), 1, 1)
    fig.add_trace(go.Scatter(x=dead.index, y=dead["ma_slow"], mode="markers", name="Dead cross",
                             marker=dict(symbol="triangle-down", size=13, color="#ff1744")), 1, 1)
    fig.add_trace(go.Scatter(x=r.index, y=r[key], name=names[key], line=dict(width=1.5)), 2, 1)
    for y, col_ in zip(bands, BAND_COLORS):
        fig.add_hline(y=y, line_dash="dot", line_color=col_, row=2, col=1)
    fig.update_layout(height=700, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"))
    fig.update_yaxes(title_text="Fiyat", row=1, col=1, type="log" if log_y else "linear")
    fig.update_yaxes(title_text=names[key], row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Çizgiler: koyu yeşil {bands[0]:g} (güçlü al sınırı) · açık yeşil {bands[1]:g} (al sınırı) · açık kırmızı {bands[2]:g} (sat sınırı) · "
               f"koyu kırmızı {bands[3]:g} (güçlü sat sınırı). Değer yeşil çizgilerin altındaysa AL tarafında, kırmızıların üstündeyse SAT tarafındadır.")

# ---------------- sekme 3: haber / hype ----------------
with tab3:
    try:
        articles, hype = load_news(st.session_state.nonce)
    except Exception as e:
        st.error(f"Haberler alınamadı: {e}")
        articles, hype = [], None
    if hype:
        st.caption(f"Son 24 saatte {hype['ok']}/{hype['total']} kaynaktan {hype['n24']} haber tarandı. "
                   + ("" if hype["use_trend"] else "Trend (olağana göre artış), bu bilgisayarda yeterli geçmiş olmadığı için gösterilmiyor; "
                      "Telegram raporu GitHub'daki birikmiş geçmişi kullanır."))
        if hype["failed"]:
            st.caption("Ulaşılamayan kaynaklar: " + ", ".join(hype["failed"]))

        def news_table(rows_):
            return pd.DataFrame([{"Konu": r["label"], "Haber (24s)": r["n"], "Kaynak": len(r["sources"]),
                                  "Trend ×": round(r["trend"], 1) if r["trend"] is not None else None,
                                  "Skor": round(r["score"], 1),
                                  "Örnek başlık": f"{r['example'].title} ({r['example'].source})"} for r in rows_])

        n1, n2 = st.columns(2)
        n1.subheader("Öne çıkan kategoriler")
        n1.dataframe(news_table(hype["cats"]), hide_index=True, use_container_width=True)
        n2.subheader("Öne çıkan coin / projeler")
        n2.dataframe(news_table(hype["coins"]), hide_index=True, use_container_width=True)

        st.subheader("Son haberler")
        latest = sorted(articles, key=lambda a: a.ts, reverse=True)[:60]
        st.dataframe(pd.DataFrame([{"Zaman (TR)": a.ts.astimezone(IST).strftime("%d.%m %H:%M"),
                                    "Kaynak": a.source, "Başlık": a.title,
                                    "Kategori": ", ".join(a.cats), "Coin": ", ".join(a.coins)} for a in latest]),
                     hide_index=True, use_container_width=True)
        st.caption("Kategori ve coin eşleştirmesi anahtar kelimeye dayanır, yanılabilir. Yatırım tavsiyesi değildir.")
