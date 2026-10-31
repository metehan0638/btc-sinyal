"""BTC dip / şişkinlik paneli.  Çalıştır:  streamlit run app.py"""
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
from core.zones import DEFAULT_PARAMS, METRIC_KEYS, MIN_BARS, ZONE_TEXT, compute_metrics, latest_rows, summarize, zones_for

CFG = yaml.safe_load(open(Path(__file__).parent / "config.yaml", encoding="utf-8"))
TF_LABEL = {"1d": "Günlük", "1w": "Haftalık"}
ZONE_NAME = {1: "🟢 Ucuz / dip", 0: "⚪ Normal", -1: "🔴 Şişmiş"}
ZONE_RATIO = CFG.get("zone_ratio", 0.5)

st.set_page_config(page_title="BTC Dip / Şişkinlik", page_icon="₿", layout="wide")

# ---------------- kenar çubuğu ----------------
if "nonce" not in st.session_state:
    st.session_state.nonce = 0
with st.sidebar:
    st.header("Ayarlar")
    if st.button("🔄 Veriyi yenile"):
        st.session_state.nonce += 1
    st.caption("Veri 1 saat önbelleğe alınır. Hesaplar yalnızca KAPANMIŞ mumlara göre yapılır. "
               "Bölge eşiklerini config.yaml içinden değiştirebilirsin.")
    st.warning("Bu araç yatırım tavsiyesi değildir. Aşırı satım, dipte olunduğunu garanti etmez; "
               "düşüş trendinde uzun süre sürebilir.")


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
    return df, raw, rows, summarize(rows, ZONE_RATIO), params


try:
    data = {tf: analyze(tf) for tf in TF_LABEL}
except Exception as e:
    st.error(f"Veri alınamadı: {e}")
    st.info("İnternet bağlantını ve borsa erişimini kontrol et. Gerekirse config.yaml içindeki `exchanges` listesini değiştir.")
    st.stop()

# ---------------- başlık + özet ----------------
st.title(f"₿ {CFG['symbol']} — Dip / Şişkinlik Paneli")
cols = st.columns(3)
for col, tf in zip(cols[:2], TF_LABEL):
    df, raw, rows, summ, _ = data[tf]
    icon, label = ZONE_TEXT[summ["zone"]]
    col.metric(f"{TF_LABEL[tf]} ({df.index[-1].date()} kapanışı)", f"{icon} {label}",
               f"{summ['cheap']} ucuz · {summ['normal']} normal · {summ['hot']} şişmiş", delta_color="off")
if fg_info:
    cols[2].metric("Fear & Greed", f"{fg_info['value']:.0f} · {fg_info['label']}",
                   f"dün {fg_info['yesterday']:.0f}" if fg_info["yesterday"] is not None else None, delta_color="off")
else:
    cols[2].metric("Fear & Greed", "-")
st.caption("🟢 = fiyat ucuz/dip tarafında (şişmiş değil) · 🔴 = şişmiş tarafında. "
           f"Genel karar için göstergelerin en az %{ZONE_RATIO * 100:.0f}'i aynı tarafta olmalı.")

for tf_, (df_, *_rest) in data.items():
    if len(df_) < MIN_BARS[tf_]:
        st.warning(f"{TF_LABEL[tf_]}: yalnızca {len(df_)} mum alındı (en az {MIN_BARS[tf_]} beklenir). "
                   "Binance'e erişilemiyor, yedek borsanın geçmişi kısa olabilir; bu zaman diliminin değerleri güvenilir olmayabilir.")

# ---------------- zaman dilimi seçimi ----------------
tf = st.radio("Zaman dilimi", list(TF_LABEL), format_func=TF_LABEL.get, horizontal=True)
df, raw, rows, summ, params = data[tf]
tab1, tab2, tab3 = st.tabs(["📊 Güncel durum", "📈 Grafik", "🔥 Haber / Hype"])

# ---------------- sekme 1: güncel durum ----------------
with tab1:
    icon, label = ZONE_TEXT[summ["zone"]]
    st.subheader(f"{TF_LABEL[tf]}: {icon} {label}")
    st.caption(f"{summ['n']} göstergeden {summ['cheap']} tanesi ucuz, {summ['normal']} normal, {summ['hot']} şişmiş bölgede.")

    def color(v):
        return {"🟢": "background-color:#1b5e20;color:white", "🔴": "background-color:#b71c1c;color:white",
                "⚪": "background-color:#555;color:white"}.get(v[:1], "")

    table = pd.DataFrame([{"Gösterge": r["name"], "Değer": r["text"], "Bölge": ZONE_NAME.get(r["zone"], "❔ Yetersiz veri"),
                           "Yorum": r["note"]} for r in rows])
    st.dataframe(table.style.map(color, subset=["Bölge"]), hide_index=True, use_container_width=True)

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
        for y in (24, 75):
            f3.add_hline(y=y, line_dash="dot", line_width=1)
        f3.update_layout(height=280, yaxis=dict(range=[0, 100], title="0 = aşırı korku · 100 = aşırı açgözlülük"),
                         margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
        st.plotly_chart(f3, use_container_width=True)
        st.caption("Kaynak: alternative.me. Düşük değer (korku) fiyatın şişmediğine işaret sayılır; günlük puana dahildir.")

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
    ma_n = {**DEFAULT_PARAMS, **{k: v for k, v in params.items() if k != "zones"}}["ma_long"]
    sma = raw["close"].rolling(ma_n).mean().loc[start:]
    cheap, hot = zones_for(params)[key]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.04)
    fig.add_trace(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close, name="BTC"), 1, 1)
    fig.add_trace(go.Scatter(x=sma.index, y=sma, name=f"{ma_n} ortalama", line=dict(width=1.3)), 1, 1)
    fig.add_trace(go.Scatter(x=r.index, y=r[key], name=names[key], line=dict(width=1.5)), 2, 1)
    fig.add_hline(y=cheap, line_dash="dot", line_color="#00c853", row=2, col=1)
    fig.add_hline(y=hot, line_dash="dot", line_color="#ff1744", row=2, col=1)
    fig.update_layout(height=700, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"))
    fig.update_yaxes(title_text="Fiyat", row=1, col=1, type="log" if log_y else "linear")
    fig.update_yaxes(title_text=names[key], row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Yeşil çizgi: ucuz bölge eşiği ({cheap:g}), kırmızı çizgi: şişmiş bölge eşiği ({hot:g}). Değer yeşilin altındaysa ucuz, kırmızının üstündeyse şişmiş sayılır.")

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
