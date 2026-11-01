"""Kripto varlıklar için gösterge dereceleri paneli.  Çalıştır:  streamlit run app.py"""
import json
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st
import yaml
from plotly.subplots import make_subplots

from core.data import get_candles
from core.charting import indicator_series
from core.backtest import run_backtests
from core.news import IST, analyze_with_history as analyze_news
from core.sentiment import EMOJI as FG_EMOJI, fetch_fear_greed, summary as fg_summary
from core.zones import (FRESH_BARS, LEVELS, LEVEL_TITLE, METRIC_KEYS, MIN_BARS, RATING_TEXT, bands_for, compute_metrics, latest_rows,
                        trend_info)

CFG = yaml.safe_load(open(Path(__file__).parent / "config.yaml", encoding="utf-8"))
ASSETS = CFG.get("assets") or [{"ticker": CFG.get("symbol", "BTC/USDT").split("/")[0],
                                 "name": "Bitcoin", "symbol": CFG.get("symbol", "BTC/USDT"),
                                 "since": CFG.get("since", "2017-08-17")}]
ASSETS = [{"ticker": asset.get("ticker", asset["symbol"].split("/")[0]),
           "name": asset.get("name", asset.get("ticker", asset["symbol"])),
           "symbol": asset["symbol"], "since": asset.get("since", CFG.get("since", "2017-08-17"))}
          for asset in ASSETS]
ASSET_BY_SYMBOL = {asset["symbol"]: asset for asset in ASSETS}
TF_LABEL = {"1d": "Günlük", "1w": "Haftalık", "1M": "Aylık"}
GRADE_NAME = {g: f"{icon} {LEVEL_TITLE[g]}" for g, (icon, _) in LEVELS.items()}
BAND_COLORS = ["#1b5e20", "#66bb6a", "#ef5350", "#b71c1c"]  # t1 güçlü al, t2 al, t3 sat, t4 güçlü sat

st.set_page_config(page_title="Kripto Takip Paneli", page_icon="🪙", layout="wide")

# ---------------- kenar çubuğu ----------------
if "nonce" not in st.session_state:
    st.session_state.nonce = 0
with st.sidebar:
    st.header("Ayarlar")
    selected_symbol = st.selectbox("Kripto varlık", list(ASSET_BY_SYMBOL),
                                   format_func=lambda value: f"{ASSET_BY_SYMBOL[value]['name']} ({value})",
                                   key="selected_asset")
    asset = ASSET_BY_SYMBOL[selected_symbol]
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
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    hist = state.get("news_hist", {})
    feeds = (CFG.get("news") or {}).get("feeds")
    articles, hype, updated_hist = analyze_news(feeds, hist, now=datetime.now(timezone.utc))
    if updated_hist != hist:
        state["news_hist"] = updated_hist
        temp_file = state_file.with_name(state_file.name + ".tmp")
        try:
            temp_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
            temp_file.replace(state_file)
        except OSError as e:
            print(f"[haber] UI geçmişi kaydedilemedi: {e}")
    return articles, hype


@st.cache_data(ttl=3600, show_spinner="Fear & Greed endeksi alınıyor...")
def load_fg(nonce):
    return fetch_fear_greed()


@st.cache_data(ttl=3600, show_spinner="Bağımsız gösterge backtestleri hesaplanıyor...")
def load_backtests(df, raw, tf, params, fear_greed, keys, start, target_pct, fee_pct, ticker):
    return run_backtests(df, raw, tf, params, fear_greed, keys, start, target_pct, fee_pct, ticker)


fg_series, fg_info = None, None
if CFG.get("fear_greed", True):
    try:
        fg_series = load_fg(st.session_state.nonce)
        fg_info = fg_summary(fg_series)
    except Exception as e:  # alınamazsa panel yine çalışır, günlük puan Fear & Greed'siz hesaplanır
        st.info(f"Fear & Greed endeksi alınamadı ({e}). Günlük puan bu gösterge olmadan hesaplandı.")


def analyze(tf, asset):
    df = load_candles(asset["symbol"], tf, tuple(CFG["exchanges"]), asset["since"], st.session_state.nonce)
    params = CFG["timeframes"].get(tf, {})
    raw = compute_metrics(df, params)
    # Eski/önceden yüklenmiş core.zones modülü Ichimoku çizgilerini üretip
    # derece sütununu üretmiyor olabilir; grafik ve tablo aynı kuralı kullansın.
    if "ichimoku_grade" not in raw:
        span_a, span_b = raw["ichimoku_span_a"], raw["ichimoku_span_b"]
        top = pd.concat([span_a, span_b], axis=1).max(axis=1)
        bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)
        valid = span_a.notna() & span_b.notna()
        above, below = raw["close"] > top, raw["close"] < bottom
        bullish = above & (raw["ichimoku_tenkan"] > raw["ichimoku_kijun"]) & (span_a > span_b)
        bearish = below & (raw["ichimoku_tenkan"] < raw["ichimoku_kijun"]) & (span_a < span_b)
        raw["ichimoku_grade"] = np.select([bullish, bearish, above & valid, below & valid], [2, -2, 1, -1], default=0).astype(float)
        raw.loc[~valid, "ichimoku_grade"] = np.nan
    rows = latest_rows(raw, params, tf, fg_info if tf == "1d" else None)
    return df, raw, rows, params, trend_info(raw, params, tf)


try:
    data = {tf: analyze(tf, asset) for tf in TF_LABEL}
except Exception as e:
    st.error(f"Veri alınamadı: {e}")
    st.info("İnternet bağlantını ve borsa erişimini kontrol et. Gerekirse config.yaml içindeki `exchanges` listesini değiştir.")
    st.stop()


# ---------------- başlık + özet ----------------
st.title("Kripto Takip Paneli")
st.caption(f"{asset['name']} · {asset['symbol']}")
cols = st.columns(4)
for col, tf in zip(cols[:3], TF_LABEL):
    df, raw, rows, _, _trend = data[tf]
    col.metric(f"{TF_LABEL[tf]} mum kapanışı", str(df.index[-1].date()))
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
df, raw, rows, params, trend = data[tf]
tab1, tab2, tab3, tab4 = st.tabs(["📊 Güncel durum", "📈 Grafik", "🔥 Haber / Hype", "📉 Geçmiş performans"])

# ---------------- sekme 1: güncel durum ----------------
with tab1:
    st.subheader(f"{TF_LABEL[tf]}: bağımsız gösterge dereceleri")

    def color(v):
        for prefix, css in (("🟢🟢", "#1b5e20"), ("🟢", "#388e3c"), ("🔴🔴", "#b71c1c"), ("🔴", "#e64a19"), ("⚪", "#555")):
            if v.startswith(prefix):
                return f"background-color:{css};color:white"
        return ""

    table = pd.DataFrame([{"Gösterge": r["name"], "Değer": r["text"], "Derece": GRADE_NAME.get(r["grade"], "❔ Yetersiz veri"),
                           "Yorum": r["note"]} for r in rows])
    st.dataframe(table.style.map(color, subset=["Derece"]), hide_index=True, width="stretch")
    if trend["state"] == trend["state"]:
        st.caption(f"Cross: {trend['text']}. {trend['last_text']}. Golden yapısı +1, dead yapısı −1; kesişim yeniyse ±2 puan. "
                   "Kesişim gecikmeli bir onay sinyalidir.")

    last = raw.iloc[-1]
    c1, c2, c3 = st.columns(3)
    c1.metric("Son kapanış (USDT)", f"{last.close:,.8g}")
    c2.metric("ATH (en yüksek, mevcut geçmiş)", f"{last.ath:,.8g}")
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
        st.plotly_chart(f3, width="stretch")
        st.caption("Kaynak: alternative.me. Bu genel piyasa duyarlılığı göstergesi, seçili varlığın günlük derecesinde "
                   "değerlendirilir: düşük değer (korku) AL, yüksek değer (açgözlülük) SAT tarafıdır.")

# ---------------- sekme 2: grafik ----------------
with tab2:
    chart_keys = set(METRIC_KEYS) | {"cross", "ichimoku", "fear_greed"}
    names = {r["key"]: r["name"] for r in rows if r["key"] in chart_keys}
    key = st.selectbox("Alt grafikte gösterilecek gösterge", list(names), format_func=names.get)
    c1, c2 = st.columns(2)
    span = c1.selectbox("Aralık", ["Tümü", "Son 4 yıl", "Son 2 yıl", "Son 1 yıl"], index=1)
    log_y = c2.checkbox("Logaritmik fiyat ekseni", value=True)
    cut = {"Tümü": None, "Son 4 yıl": 4, "Son 2 yıl": 2, "Son 1 yıl": 1}[span]
    start = df.index[-1] - pd.DateOffset(years=cut) if cut else df.index[0]
    d, r = df.loc[start:], raw.loc[start:]
    fast_n, slow_n = trend["fast"], trend["slow"]
    series, bands, value_range = indicator_series(r, key, tf, params, fg_series if key == "fear_greed" else None)
    events = r[r["cross_event"] != 0]
    golden, dead = events[events["cross_event"] > 0], events[events["cross_event"] < 0]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.04)
    fig.add_trace(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close,
                                 name=asset["symbol"]), 1, 1)
    fig.add_trace(go.Scatter(x=r.index, y=r["ma_fast"], name=f"SMA{fast_n}", line=dict(width=1.3)), 1, 1)
    fig.add_trace(go.Scatter(x=r.index, y=r["ma_slow"], name=f"SMA{slow_n}", line=dict(width=1.3)), 1, 1)
    fig.add_trace(go.Scatter(x=golden.index, y=golden["ma_slow"], mode="markers", name="Golden cross",
                             marker=dict(symbol="triangle-up", size=13, color="#00c853")), 1, 1)
    fig.add_trace(go.Scatter(x=dead.index, y=dead["ma_slow"], mode="markers", name="Dead cross",
                             marker=dict(symbol="triangle-down", size=13, color="#ff1744")), 1, 1)
    if key == "ichimoku":
        fig.add_trace(go.Scatter(x=r.index, y=r["ichimoku_span_a"], name="Senkou Span A", line=dict(width=1)), 1, 1)
        fig.add_trace(go.Scatter(x=r.index, y=r["ichimoku_span_b"], name="Senkou Span B", line=dict(width=1),
                                 fill="tonexty", fillcolor="rgba(76,175,80,0.14)"), 1, 1)
        fig.add_trace(go.Scatter(x=r.index, y=r["ichimoku_tenkan"], name="Tenkan-sen", line=dict(width=1)), 1, 1)
        fig.add_trace(go.Scatter(x=r.index, y=r["ichimoku_kijun"], name="Kijun-sen", line=dict(width=1)), 1, 1)
    fig.add_trace(go.Scatter(x=r.index, y=series.loc[r.index], name=names[key], line=dict(width=1.5, shape="hv" if key in ("cross", "ichimoku") else "linear")), 2, 1)
    if key in METRIC_KEYS or key == "fear_greed":
        for y, col_ in zip(bands, BAND_COLORS):
            fig.add_hline(y=y, line_dash="dot", line_color=col_, row=2, col=1)
    else:
        for y in (-2, -1, 0, 1, 2):
            fig.add_hline(y=y, line_dash="dot", line_color="#777", row=2, col=1)
    fig.update_layout(height=700, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"))
    fig.update_yaxes(title_text="Fiyat", row=1, col=1, type="log" if log_y else "linear")
    fig.update_yaxes(title_text=names[key], row=2, col=1, range=value_range)
    st.plotly_chart(fig, width="stretch")
    if key in METRIC_KEYS or key == "fear_greed":
        st.caption(f"Çizgiler: koyu yeşil {bands[0]:g} · açık yeşil {bands[1]:g} · açık kırmızı {bands[2]:g} · koyu kırmızı {bands[3]:g} (derece eşikleri).")
    elif key == "ichimoku":
        st.caption("Üst grafikte fiyat, Ichimoku bulutu ve Tenkan/Kijun çizgileri; alt grafikte aynı göstergenin bağımsız −2…+2 derecesi gösterilir.")
    else:
        st.caption("Alt grafik kesişim derecesini gösterir: yeni kesişim +2/−2, eski golden/dead yapısı +1/−1, eşit ortalamalar 0.")

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
                      "haber geçmişi panel ziyaretleriyle birikecek."))
        if hype["failed"]:
            st.caption("Ulaşılamayan kaynaklar: " + ", ".join(hype["failed"]))

        def news_table(rows_):
            return pd.DataFrame([{"Konu": r["label"], "Haber (24s)": r["n"], "Kaynak": len(r["sources"]),
                                  "Trend ×": round(r["trend"], 1) if r["trend"] is not None else None,
                                  "Skor": round(r["score"], 1),
                                  "Örnek başlık": f"{r['example'].title} ({r['example'].source})"} for r in rows_])

        n1, n2 = st.columns(2)
        n1.subheader("Öne çıkan kategoriler")
        n1.dataframe(news_table(hype["cats"]), hide_index=True, width="stretch")
        n2.subheader("Öne çıkan coin / projeler")
        n2.dataframe(news_table(hype["coins"]), hide_index=True, width="stretch")

        st.subheader("Son haberler")
        latest = sorted(articles, key=lambda a: a.ts, reverse=True)[:60]
        st.dataframe(pd.DataFrame([{"Zaman (TR)": a.ts.astimezone(IST).strftime("%d.%m %H:%M"),
                                    "Kaynak": a.source, "Başlık": a.title,
                                    "Kategori": ", ".join(a.cats), "Coin": ", ".join(a.coins)} for a in latest]),
                     hide_index=True, width="stretch")
        st.caption("Kategori ve coin eşleştirmesi anahtar kelimeye dayanır, yanılabilir. Yatırım tavsiyesi değildir.")

# ---------------- sekme 4: bağımsız geçmiş performans ----------------
with tab4:
    st.subheader(f"{TF_LABEL[tf]}: gösterge bazında geçmiş performans")
    st.caption("Her gösterge ayrı ve kendi başına %100 başlangıç bakiyesiyle simüle edilir. Ortak/ortalama sinyal puanı kullanılmaz.")
    st.info("Kural: Derece 0 veya altından AL tarafına (+1/+2) yeni geçtiğinde, sonraki mum açılışında alım yapılır. "
            "Mumun en yüksek fiyatı hedefe ulaştığında satış yapılır; mum hedefin üstünde açılırsa açılış fiyatı alınır. "
            "Zarar kes yoktur. Dönem sonunda açık işlem son kapanışta kapatılır; bu işlem hedefe ulaşmış sayılmaz. "
            "Komisyon uygulanır; spread ve fiyat kayması hesaba katılmaz.")

    controls = st.columns(3)
    target_pct = controls[0].number_input("Kâr hedefi (%)", min_value=1.0, max_value=500.0, value=30.0, step=1.0, key="bt_target")
    fee_pct = controls[1].number_input("Komisyon / işlem yönü (%)", min_value=0.0, max_value=5.0, value=0.1, step=0.05, key="bt_fee")
    span = controls[2].selectbox("Geçmiş aralığı", ["Tümü", "Son 5 yıl", "Son 3 yıl", "Son 1 yıl"], key="bt_span")
    years = {"Tümü": None, "Son 5 yıl": 5, "Son 3 yıl": 3, "Son 1 yıl": 1}[span]
    start = df.index[-1] - pd.DateOffset(years=years) if years else df.index[0]

    available_keys = tuple(r["key"] for r in rows if r["key"] != "fear_greed" or (tf == "1d" and fg_series is not None))
    try:
        report = load_backtests(df, raw, tf, params, fg_series, available_keys, start, target_pct, fee_pct,
                                asset["ticker"])
    except Exception as e:
        st.error(f"Geçmiş performans hesaplanamadı: {e}")
    else:
        display_names = {r["key"]: r["name"] for r in rows if r["key"] in available_keys}
        summary_rows = []
        for key in available_keys:
            result = report["results"][key]
            summary_rows.append({"Gösterge": display_names.get(key, key),
                                 "Veri durumu": "Yeterli" if result.get("available", True) else "Yetersiz veri",
                                 "Toplam getiri": result["total_return"], "Yıllık bileşik getiri": result["cagr"],
                                 "En büyük düşüş": result["max_drawdown"], "İşlem": result["trades"],
                                 "Hedef satışı": result["target_hits"], "Kazançlı işlem oranı": result["win_rate"],
                                 "Kâr faktörü": result["profit_factor"], "Ort. elde tutma (gün)": result["avg_days"],
                                 "Piyasada geçen süre": result["exposure"]})
        buy_hold = report["results"]["buy_hold"]
        summary_rows.append({"Gösterge": f"{asset['ticker']} al-tut (kıyas)", "Veri durumu": "Kıyas",
                             "Toplam getiri": buy_hold["total_return"], "Yıllık bileşik getiri": buy_hold["cagr"],
                             "En büyük düşüş": buy_hold["max_drawdown"], "İşlem": np.nan,
                             "Hedef satışı": np.nan, "Kazançlı işlem oranı": np.nan,
                             "Kâr faktörü": np.nan, "Ort. elde tutma (gün)": np.nan,
                             "Piyasada geçen süre": 1.0})
        summary = pd.DataFrame(summary_rows).sort_values("Toplam getiri", ascending=False, na_position="last")
        st.caption(f"Simülasyon aralığı: {report['prices'].index[0]:%d.%m.%Y} – {report['prices'].index[-1]:%d.%m.%Y}. "
                   "Tablo getiriye göre sıralıdır; bu sıralama birleşik bir gösterge puanı değildir. "
                   "Kazançlı işlem oranına dönem sonu kapatılan işlemler de dahildir.")
        st.dataframe(summary, hide_index=True, width="stretch",
                     alt=f"Her göstergenin bağımsız backtest ölçümleri ve {asset['ticker']} al-tut karşılaştırması",
                     column_config={
                         "Toplam getiri": st.column_config.NumberColumn(format="percent"),
                         "Yıllık bileşik getiri": st.column_config.NumberColumn(format="percent"),
                         "En büyük düşüş": st.column_config.NumberColumn(format="percent"),
                         "Kazançlı işlem oranı": st.column_config.NumberColumn(format="percent"),
                         "Piyasada geçen süre": st.column_config.NumberColumn(format="percent"),
                         "Kâr faktörü": st.column_config.NumberColumn(format="%.2f"),
                         "Ort. elde tutma (gün)": st.column_config.NumberColumn(format="%.1f"),
                     })

        performance_keys = tuple(key for key in available_keys if report["results"][key].get("available", True))
        chosen_key = st.selectbox("İşlem ve özkaynak ayrıntısı", performance_keys,
                                  format_func=lambda key: display_names.get(key, key), key="bt_indicator")
        chosen = report["results"][chosen_key]
        metrics = st.columns(4)
        metrics[0].metric("Toplam getiri", f"%{chosen['total_return'] * 100:,.1f}")
        metrics[1].metric("Yıllık bileşik getiri", f"%{chosen['cagr'] * 100:,.1f}" if pd.notna(chosen["cagr"]) else "-")
        metrics[2].metric("En büyük düşüş", f"%{chosen['max_drawdown'] * 100:,.1f}")
        metrics[3].metric("Hedef satış / işlem", f"{chosen['target_hits']} / {chosen['trades']}")

        equity = chosen["equity"]
        fig_bt = go.Figure()
        fig_bt.add_trace(go.Scatter(x=equity.index, y=equity * 100, mode="lines",
                                    name=display_names.get(chosen_key, f"{asset['ticker']} al-tut")))
        fig_bt.add_trace(go.Scatter(x=buy_hold["equity"].index, y=buy_hold["equity"] * 100,
                                    mode="lines", name=f"{asset['ticker']} al-tut",
                                    line=dict(color="#9e9e9e", dash="dot")))
        fig_bt.update_layout(height=420, margin=dict(l=10, r=10, t=25, b=10),
                             yaxis_title="Özkaynak endeksi (başlangıç = 100)", xaxis_title="Tarih",
                             legend=dict(orientation="h"), hovermode="x unified")
        chosen_name = display_names.get(chosen_key, f"{asset['ticker']} al-tut")
        st.plotly_chart(fig_bt, width="stretch",
                        alt=f"{chosen_name} ve {asset['ticker']} al-tut özkaynak karşılaştırması")

        if chosen_key != "buy_hold":
            trades = chosen["trades_detail"]
            st.subheader("İşlem geçmişi")
            if trades:
                trade_table = pd.DataFrame([{
                    "Alış tarihi": trade["entry"].strftime("%d.%m.%Y"),
                    "Satış tarihi": trade["exit"].strftime("%d.%m.%Y"),
                    "Alış fiyatı": trade["entry_price"], "Satış fiyatı": trade["exit_price"],
                    "Net işlem sonucu": trade["net_return"], "Çıkış nedeni": trade["exit_reason"],
                    "Elde tutma (gün)": trade["days"],
                } for trade in trades])
                st.dataframe(trade_table, hide_index=True, width="stretch", alt="Seçili gösterge simülasyonundaki işlemler",
                             column_config={
                                 "Alış fiyatı": st.column_config.NumberColumn(format="%.2f"),
                                 "Satış fiyatı": st.column_config.NumberColumn(format="%.2f"),
                                 "Net işlem sonucu": st.column_config.NumberColumn(format="percent"),
                                 "Elde tutma (gün)": st.column_config.NumberColumn(format="%.1f"),
                             })
            else:
                st.caption("Bu aralıkta yeni AL geçişi oluşmadığı için işlem açılmadı.")
