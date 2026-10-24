"""BTC uzun vade sinyal paneli.  Çalıştır:  streamlit run app.py"""
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml
from plotly.subplots import make_subplots

from core.backtest import run_backtest
from core.consensus import build_signals
from core.data import get_candles
from core.indicators import compute_indicators, latest_table

CFG = yaml.safe_load(open(Path(__file__).parent / "config.yaml", encoding="utf-8"))
TF_LABEL = {"1d": "Günlük", "1w": "Haftalık"}
BARS_PER_YEAR = {"1d": 365, "1w": 52}
STATE_TXT = {1: "AL", -1: "SAT", 0: "NÖTR"}

st.set_page_config(page_title="BTC Uzun Vade Sinyal", page_icon="₿", layout="wide")

# ---------------- kenar çubuğu ----------------
if "nonce" not in st.session_state:
    st.session_state.nonce = 0
with st.sidebar:
    st.header("Ayarlar")
    entry = st.slider("Sinyal eşiği (10 indikatörden kaçı onaylasın)", 6, 10, CFG["entry_threshold"])
    exit_ = st.slider("Backtest: AL onayı bu sayıya düşünce çık", 0, 6, CFG["exit_threshold"])
    fee = st.number_input("Komisyon (işlem başına)", 0.0, 0.01, float(CFG["fee"]), 0.0005, format="%.4f")
    if st.button("🔄 Veriyi yenile"):
        st.session_state.nonce += 1
    st.caption("Veri 1 saat önbelleğe alınır. Sinyaller yalnızca KAPANMIŞ mumlara göre üretilir.")
    st.warning("Bu araç yatırım tavsiyesi değildir; geçmiş performans geleceği garanti etmez.")


@st.cache_data(ttl=3600, show_spinner="Mum verisi alınıyor...")
def load_candles(symbol, tf, exchanges, since, nonce):
    return get_candles(symbol, tf, exchanges, since)


def analyze(tf, threshold):
    df = load_candles(CFG["symbol"], tf, tuple(CFG["exchanges"]), CFG["since"], st.session_state.nonce)
    params = CFG["timeframes"].get(tf, {})
    votes, raw = compute_indicators(df, params)
    sig = build_signals(votes, threshold)
    return df, votes, raw, sig, params


try:
    data = {tf: analyze(tf, entry) for tf in TF_LABEL}
except Exception as e:
    st.error(f"Veri alınamadı: {e}")
    st.info("İnternet bağlantını ve borsa erişimini kontrol et. Gerekirse config.yaml içindeki `exchanges` listesini değiştir.")
    st.stop()

# ---------------- başlık + özet ----------------
st.title(f"₿ {CFG['symbol']} — Uzun Vade Sinyal Paneli")
cols = st.columns(3)
for col, tf in zip(cols[:2], TF_LABEL):
    df, votes, raw, sig, _ = data[tf]
    last = sig.iloc[-1]
    if not last.valid:
        col.metric(f"{TF_LABEL[tf]} sinyal", "Yetersiz veri")
        continue
    col.metric(f"{TF_LABEL[tf]} sinyal ({df.index[-1].date()} kapanışı)", STATE_TXT[int(last.state)],
               f"{int(last.bull)} AL · {int(last.bear)} SAT · {int(last.neutral)} nötr", delta_color="off")

sd, sw = data["1d"][3].iloc[-1].state, data["1w"][3].iloc[-1].state
if sd == 1 and sw == 1:
    combo = "🟢 Günlük + haftalık AL: güçlü yükseliş uyumu"
elif sd == -1 and sw == -1:
    combo = "🔴 Günlük + haftalık SAT: güçlü düşüş uyumu"
elif sw == 1:
    combo = "🟡 Haftalık AL, günlük henüz onaylamadı"
elif sw == -1:
    combo = "🟡 Haftalık SAT, günlük henüz onaylamadı"
else:
    combo = "⚪ Net uyum yok, beklemede"
cols[2].metric("Birleşik yorum", combo.split(":")[0][2:].strip())
st.caption(combo)

# ---------------- zaman dilimi seçimi ----------------
tf = st.radio("Zaman dilimi", list(TF_LABEL), format_func=TF_LABEL.get, horizontal=True)
df, votes, raw, sig, params = data[tf]
tab1, tab2, tab3, tab4 = st.tabs(["📊 Güncel durum", "📈 Grafik", "🧪 Backtest", "🕘 Sinyal geçmişi"])

# ---------------- sekme 1: güncel durum ----------------
with tab1:
    table = latest_table(votes, raw, params) if sig.iloc[-1].valid else None
    if table is None:
        st.info("Bu zaman diliminde tüm indikatörler için yeterli veri henüz yok.")
    else:
        def color(v):
            return {"AL": "background-color:#1b5e20;color:white", "SAT": "background-color:#b71c1c;color:white",
                    "NÖTR": "background-color:#555;color:white"}.get(v, "")
        st.dataframe(table.style.map(color, subset=["Oy"]), hide_index=True, use_container_width=True)
        last = raw.iloc[-1]
        c1, c2, c3 = st.columns(3)
        c1.metric("Son kapanış", f"{last.close:,.0f}")
        c2.metric(f"ATR({14})", f"{last.atr:,.0f}")
        c3.metric("ATR tabanlı stop önerisi (3×ATR)", f"{last.close - 3 * last.atr:,.0f}")
        st.caption("Stop seviyesi sadece bir referanstır; pozisyon büyüklüğünü ve riski kendi planına göre belirle.")

# ---------------- sekme 2: grafik ----------------
with tab2:
    c1, c2 = st.columns(2)
    span = c1.selectbox("Aralık", ["Tümü", "Son 4 yıl", "Son 2 yıl", "Son 1 yıl"], index=1)
    log_y = c2.checkbox("Logaritmik fiyat ekseni", value=True)
    cut = {"Tümü": None, "Son 4 yıl": 4, "Son 2 yıl": 2, "Son 1 yıl": 1}[span]
    start = df.index[-1] - pd.DateOffset(years=cut) if cut else df.index[0]
    d, r, s = df.loc[start:], raw.loc[start:], sig.loc[start:]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close, name="BTC"), 1, 1)
    fig.add_trace(go.Scatter(x=r.index, y=r.ema_fast, name=f"EMA{params.get('ema_fast', 50)}", line=dict(width=1.3)), 1, 1)
    fig.add_trace(go.Scatter(x=r.index, y=r.ema_slow, name=f"EMA{params.get('ema_slow', 200)}", line=dict(width=1.3)), 1, 1)
    buys, sells = s[s.event == 1], s[s.event == -1]
    fig.add_trace(go.Scatter(x=buys.index, y=d.loc[buys.index, "low"] * 0.93, mode="markers", name="AL sinyali",
                             marker=dict(symbol="triangle-up", size=13, color="#00c853")), 1, 1)
    fig.add_trace(go.Scatter(x=sells.index, y=d.loc[sells.index, "high"] * 1.07, mode="markers", name="SAT sinyali",
                             marker=dict(symbol="triangle-down", size=13, color="#ff1744")), 1, 1)
    fig.add_trace(go.Bar(x=s.index, y=s.bull, name="AL oyu", marker_color="#00c853"), 2, 1)
    fig.add_trace(go.Bar(x=s.index, y=-s.bear, name="SAT oyu", marker_color="#ff1744"), 2, 1)
    fig.add_hline(y=entry, line_dash="dot", row=2, col=1)
    fig.add_hline(y=-entry, line_dash="dot", row=2, col=1)
    fig.update_layout(height=720, barmode="relative", xaxis_rangeslider_visible=False,
                      margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"))
    fig.update_yaxes(title_text="Fiyat", row=1, col=1, type="log" if log_y else "linear")
    fig.update_yaxes(title_text="İndikatör oyu", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)

# ---------------- sekme 3: backtest ----------------
with tab3:
    if not sig.valid.any():
        st.info("Backtest için yeterli veri yok.")
    else:
        bt = run_backtest(df, sig, entry, exit_, fee, BARS_PER_YEAR[tf])
        m, bm = bt["metrics"], bt["bh_metrics"]
        st.subheader(f"Strateji: AL onayı ≥ {entry} → gir, ≤ {exit_} → çık ({TF_LABEL[tf]})")
        k = st.columns(4)
        k[0].metric("Toplam getiri", f"{m['Toplam getiri %']:,.0f}%", f"Al-tut: {bm['Toplam getiri %']:,.0f}%", delta_color="off")
        k[1].metric("Maks. düşüş", f"{m['Maks. düşüş %']:.0f}%", f"Al-tut: {bm['Maks. düşüş %']:.0f}%", delta_color="off")
        k[2].metric("Sharpe", f"{m['Sharpe']:.2f}", f"Al-tut: {bm['Sharpe']:.2f}", delta_color="off")
        k[3].metric("İşlem / kazanma oranı", f"{m['İşlem sayısı']} / {m['Kazanma oranı %']:.0f}%" if m["İşlem sayısı"] else "0",
                    f"Piyasada kalma: {m['Piyasada kalma %']:.0f}%", delta_color="off")

        f2 = go.Figure()
        f2.add_trace(go.Scatter(x=bt["equity"].index, y=bt["equity"], name="Strateji"))
        f2.add_trace(go.Scatter(x=bt["buy_hold"].index, y=bt["buy_hold"], name="Al-tut (BTC)"))
        f2.update_layout(height=420, yaxis_type="log", yaxis_title="Büyüme (başlangıç = 1)", margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(f2, use_container_width=True)

        st.subheader("Eşik karşılaştırması (çıkış eşiği sabit)")
        rows = []
        for th in range(6, 11):
            s_th = build_signals(votes, th)
            if not s_th.valid.any():
                continue
            res = run_backtest(df, s_th, th, exit_, fee, BARS_PER_YEAR[tf])["metrics"]
            rows.append({"Eşik": th, **{k_: res[k_] for k_ in ["İşlem sayısı", "Toplam getiri %", "Maks. düşüş %", "Sharpe", "Kazanma oranı %", "Piyasada kalma %"]}})
        st.dataframe(pd.DataFrame(rows).round(2), hide_index=True, use_container_width=True)
        st.caption("Dikkat: Az sayıda işlem istatistiksel olarak güvenilir değildir. Sonuçlar geçmişe uyarlanmış (overfit) olabilir; "
                   "eşiği sadece en iyi sonuca bakarak seçme.")

        if len(bt["trades"]):
            st.subheader("İşlemler")
            st.dataframe(bt["trades"].round(2), hide_index=True, use_container_width=True)

# ---------------- sekme 4: sinyal geçmişi ----------------
with tab4:
    ev = sig[sig.event != 0].copy()
    if ev.empty:
        st.info(f"Seçilen eşikte ({entry}/10) hiç sinyal oluşmadı. Eşiği düşürmeyi dene.")
    else:
        ev["Sinyal"] = ev.event.map(STATE_TXT)
        ev["Kapanış"] = df.loc[ev.index, "close"]
        # sinyalden sonra +N bar getiri
        nxt = {n: (df["close"].shift(-n) / df["close"] - 1).loc[ev.index] * 100 for n in (4, 12)}
        ev[f"+4 bar %"], ev[f"+12 bar %"] = nxt[4], nxt[12]
        out = ev[["Sinyal", "Kapanış", "bull", "bear", "+4 bar %", "+12 bar %"]].rename(columns={"bull": "AL oyu", "bear": "SAT oyu"})
        st.dataframe(out.sort_index(ascending=False).round(2), use_container_width=True)
        st.caption("'+N bar %': sinyal mumundan N mum sonraki fiyat değişimi (henüz oluşmadıysa boş).")
