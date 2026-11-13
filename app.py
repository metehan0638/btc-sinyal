"""Kripto varlıklar için Dip Skoru paneli.  Çalıştır:  streamlit run app.py"""
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
from core.backtest import OVERALL_KEY, run_backtests
from core.indicators import BAR_NAME, ICONS, MIN_BARS, POINTS, build_indicators
from core.scoring import HORIZONS, MILD_FROM, STRONG_FROM, bucket_table, evaluate, score_history
from core.news import IST, analyze_with_history as analyze_news
from core.sentiment import EMOJI as FG_EMOJI, fetch_fear_greed, summary as fg_summary

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
INDICATOR_SPEC = CFG.get("indicators")  # None: varsayılan (RSI)
INDICATORS = build_indicators(INDICATOR_SPEC)
INDICATOR_BY_KEY = {indicator.key: indicator for indicator in INDICATORS}
names = {indicator.key: indicator.name for indicator in INDICATORS}
SCORE_STEPS = [(-100, -STRONG_FROM, "#b71c1c"), (-STRONG_FROM, -MILD_FROM, "#ef5350"), (-MILD_FROM, MILD_FROM, "#9e9e9e"),
               (MILD_FROM, STRONG_FROM, "#66bb6a"), (STRONG_FROM, 100, "#1b5e20")]
SCORE_LINES = [(STRONG_FROM, "#1b5e20"), (MILD_FROM, "#66bb6a"), (-MILD_FROM, "#ef5350"), (-STRONG_FROM, "#b71c1c")]

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
               "Skora girecek göstergeleri config.yaml içindeki `indicators:` bölümünden belirlersin.")


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


@st.cache_data(ttl=3600, show_spinner="Skor geçmişi hesaplanıyor...")
def load_history(df, spec):
    return score_history(df, build_indicators(spec))


@st.cache_data(ttl=3600, show_spinner="Gösterge backtestleri hesaplanıyor...")
def load_backtests(df, spec, tf, keys, start, target_pct, fee_pct, ticker):
    return run_backtests(df, build_indicators(spec), tf, keys=keys, start=start, target_pct=target_pct,
                         fee_pct=fee_pct, ticker=ticker)


fg_series, fg_info = None, None
if CFG.get("fear_greed", True):
    try:
        fg_series = load_fg(st.session_state.nonce)
        fg_info = fg_summary(fg_series)
    except Exception as e:  # alınamazsa panel yine çalışır; Fear & Greed zaten skora girmez
        st.info(f"Fear & Greed endeksi alınamadı ({e}). Bu bilgi satırı gösterilmeyecek; Dip Skoru etkilenmez.")


def analyze(tf, asset):
    df = load_candles(asset["symbol"], tf, tuple(CFG["exchanges"]), asset["since"], st.session_state.nonce)
    return df, load_history(df, INDICATOR_SPEC), evaluate(df, INDICATORS)


try:
    data = {tf: analyze(tf, asset) for tf in TF_LABEL}
except Exception as e:
    st.error(f"Veri alınamadı: {e}")
    st.info("İnternet bağlantını ve borsa erişimini kontrol et. Gerekirse config.yaml içindeki `exchanges` listesini değiştir.")
    st.stop()


def score_text(result):
    if pd.isna(result["score"]):
        return "Yetersiz veri", None
    return f"{result['score']:+.0f}", f"{ICONS[result['label']]} {result['label']}"


# ---------------- başlık + özet ----------------
st.title("Kripto Takip Paneli")
st.caption(f"{asset['name']} · {asset['symbol']}")
cols = st.columns(4)
for col, tf_ in zip(cols[:3], TF_LABEL):
    value, label = score_text(data[tf_][2])
    col.metric(f"{TF_LABEL[tf_]} Dip Skoru", value, label, delta_color="off")
if fg_info:
    cols[3].metric("Fear & Greed (bilgi)", f"{fg_info['value']:.0f} · {fg_info['label']}",
                   f"dün {fg_info['yesterday']:.0f}" if fg_info["yesterday"] is not None else None, delta_color="off")
else:
    cols[3].metric("Fear & Greed (bilgi)", "-")
for tf_, (df_, *_rest) in data.items():
    if len(df_) < MIN_BARS[tf_]:
        st.warning(f"{TF_LABEL[tf_]}: yalnızca {len(df_)} mum alındı (en az {MIN_BARS[tf_]} beklenir). "
                   "Binance'e erişilemiyor, yedek borsanın geçmişi kısa olabilir; bu zaman diliminin değerleri güvenilir olmayabilir.")

# ---------------- zaman dilimi seçimi ----------------
tf = st.radio("Zaman dilimi", list(TF_LABEL), format_func=TF_LABEL.get, horizontal=True)
df, history, result = data[tf]
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Güncel durum", "📈 Grafik", "🔥 Haber / Hype", "📉 Geçmiş performans",
                                         "🎯 Skor geçmişi"])

# ---------------- sekme 1: güncel durum ----------------
with tab1:
    st.subheader(f"{TF_LABEL[tf]}: Dip Skoru")
    score = result["score"]
    left, right = st.columns([1, 2])
    gauge = go.Figure(go.Indicator(
        mode="gauge+number", value=None if pd.isna(score) else round(score, 1),
        number=dict(valueformat="+.0f"),
        gauge=dict(axis=dict(range=[-100, 100], tickvals=[-100, -60, -20, 0, 20, 60, 100]),
                   bar=dict(color="#263238", thickness=0.25),
                   steps=[dict(range=[lo, hi], color=color) for lo, hi, color in SCORE_STEPS])))
    gauge.update_layout(height=260, margin=dict(l=20, r=20, t=30, b=10))
    left.plotly_chart(gauge, width="stretch", alt=f"{TF_LABEL[tf]} Dip Skoru göstergesi (-100 aşırı şişmiş, +100 güçlü dip)")
    if pd.isna(score):
        right.warning("Hiçbir gösterge henüz veri üretmedi, skor hesaplanamadı.")
    else:
        right.markdown(f"### {ICONS[result['label']]} {result['label']}")
        total_points = sum(result["points"].values())
        right.write(f"Genel skor = toplam puan / aktif gösterge sayısı = **{total_points:+d} / {result['active']} = {score:+.1f}**")
        if result["active"] != result["total"]:
            right.caption(f"{result['total'] - result['active']} gösterge henüz veri üretmediği için hesaba katılmadı (nötr sayılmaz).")
    right.caption("Etiket sınırları: ≥ +60 Güçlü Dip · ≥ +20 Dip · -20 ile +20 arası Nötr · ≤ -20 Şişmiş · ≤ -60 Aşırı Şişmiş. "
                  "Skor gerçek dip veya tepe garantisi değildir.")

    table = pd.DataFrame([{
        "Gösterge": result["names"][key],
        "Değer": INDICATOR_BY_KEY[key].text(value) if pd.notna(value) else "-",
        "Etiket": f"{ICONS[result['labels'][key]]} {result['labels'][key]}" if result["labels"][key] else "❔ Yetersiz veri",
        "Puan": POINTS[result["labels"][key]] if result["labels"][key] else None,
    } for key, value in result["values"].items()])

    def color(v):
        for prefix, css in (("🟢🟢", "#1b5e20"), ("🟢", "#388e3c"), ("🔴🔴", "#b71c1c"), ("🔴", "#e64a19"), ("⚪", "#555")):
            if str(v).startswith(prefix):
                return f"background-color:{css};color:white"
        return ""

    st.dataframe(table.style.map(color, subset=["Etiket"]), hide_index=True, width="stretch")
    st.caption("Her gösterge yalnızca 5 etiketten birini üretir; etiketler ortak puana çevrilir "
               "(Güçlü Dip +100 · Dip +50 · Nötr 0 · Şişmiş -50 · Aşırı Şişmiş -100) ve eşit ağırlıkla ortalanır.")

    c1, c2 = st.columns(2)
    c1.metric("Son kapanmış mum", f"{result['date']:%d.%m.%Y}")
    c2.metric("Son kapanış (USDT)", f"{result['close']:,.8g}")

    if fg_series is not None:
        st.subheader("Piyasa duyarlılığı: Fear & Greed (bilgi)")
        st.write(f"{FG_EMOJI.get(fg_info['label'], '')} **{fg_info['value']:.0f} — {fg_info['label']}** "
                 + " · ".join(f"{n} {fg_info[k]:.0f}" for n, k in (("dün", "yesterday"), ("7 gün önce", "week"), ("30 gün önce", "month")) if fg_info[k] is not None))
        recent = fg_series.loc[fg_series.index[-1] - pd.DateOffset(years=1):]
        f3 = go.Figure(go.Scatter(x=recent.index, y=recent.values, mode="lines", name="Fear & Greed"))
        f3.update_layout(height=280, yaxis=dict(range=[0, 100], title="0 = aşırı korku · 100 = aşırı açgözlülük"),
                         margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
        st.plotly_chart(f3, width="stretch", alt="Son bir yılın Fear & Greed endeksi")
        st.caption("Kaynak: alternative.me. Yalnızca bilgi amaçlıdır; Dip Skoru'na dahil değildir.")

# ---------------- sekme 2: grafik ----------------
with tab2:
    key = st.selectbox("Alt grafikte gösterilecek gösterge", list(names), format_func=names.get)
    indicator = INDICATOR_BY_KEY[key]
    c1, c2 = st.columns(2)
    span = c1.selectbox("Aralık", ["Tümü", "Son 4 yıl", "Son 2 yıl", "Son 1 yıl"], index=1)
    log_y = c2.checkbox("Logaritmik fiyat ekseni", value=True)
    cut = {"Tümü": None, "Son 4 yıl": 4, "Son 2 yıl": 2, "Son 1 yıl": 1}[span]
    start = df.index[-1] - pd.DateOffset(years=cut) if cut else df.index[0]
    d, h = df.loc[start:], history.loc[start:]
    values = indicator.compute(df).loc[start:]

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.04)
    fig.add_trace(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close, name=asset["symbol"]), 1, 1)
    fig.add_trace(go.Scatter(x=values.index, y=values.values, name=names[key], line=dict(width=1.5)), 2, 1)
    for y, color_ in zip([level for level, _ in indicator.levels()], ("#1b5e20", "#66bb6a", "#ef5350", "#b71c1c")):
        fig.add_hline(y=y, line_dash="dot", line_color=color_, row=2, col=1)
    fig.add_trace(go.Scatter(x=h.index, y=h["score"], name="Dip Skoru", line=dict(width=1.5, shape="hv")), 3, 1)
    for y, color_ in SCORE_LINES:
        fig.add_hline(y=y, line_dash="dot", line_color=color_, row=3, col=1)
    fig.update_layout(height=760, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"))
    fig.update_yaxes(title_text="Fiyat", row=1, col=1, type="log" if log_y else "linear")
    fig.update_yaxes(title_text=names[key], row=2, col=1, range=indicator.value_range)
    fig.update_yaxes(title_text="Dip Skoru", row=3, col=1, range=[-105, 105])
    st.plotly_chart(fig, width="stretch", alt=f"{asset['ticker']} fiyatı, {names[key]} ve Dip Skoru")
    levels_text = " · ".join(text for _, text in indicator.levels())
    st.caption(f"Orta grafik gösterge değeridir; noktalı çizgiler etiket eşikleridir ({levels_text}). "
               "Alt grafik genel Dip Skoru'dur: +100 güçlü dip, -100 aşırı şişmiş; çizgiler ±20 ve ±60 etiket sınırlarıdır.")

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
    st.caption("Her gösterge ve genel skor ayrı ayrı, kendi başına %100 başlangıç bakiyesiyle simüle edilir.")
    st.info("Kural: Gösterge etiketi Nötr veya şişmiş tarafından Dip / Güçlü Dip'e (puan > 0), genel skor ise +20'nin altından +20 ve üstüne yeni geçtiğinde, sonraki mum açılışında alım yapılır. "
            "Mumun en yüksek fiyatı hedefe ulaştığında satış yapılır; mum hedefin üstünde açılırsa açılış fiyatı alınır. "
            "Zarar kes yoktur. Dönem sonunda açık işlem son kapanışta kapatılır; bu işlem hedefe ulaşmış sayılmaz. "
            "Komisyon uygulanır; spread ve fiyat kayması hesaba katılmaz.")

    controls = st.columns(3)
    target_pct = controls[0].number_input("Kâr hedefi (%)", min_value=1.0, max_value=500.0, value=30.0, step=1.0, key="bt_target")
    fee_pct = controls[1].number_input("Komisyon / işlem yönü (%)", min_value=0.0, max_value=5.0, value=0.1, step=0.05, key="bt_fee")
    span = controls[2].selectbox("Geçmiş aralığı", ["Tümü", "Son 5 yıl", "Son 3 yıl", "Son 1 yıl"], key="bt_span")
    years = {"Tümü": None, "Son 5 yıl": 5, "Son 3 yıl": 3, "Son 1 yıl": 1}[span]
    start = df.index[-1] - pd.DateOffset(years=years) if years else df.index[0]

    available_keys = tuple(indicator.key for indicator in INDICATORS) + (OVERALL_KEY,)
    display_names = {**names, OVERALL_KEY: "Genel skor (Dip Skoru ≥ +20)"}
    try:
        report = load_backtests(df, INDICATOR_SPEC, tf, available_keys, start, target_pct, fee_pct, asset["ticker"])
    except Exception as e:
        st.error(f"Geçmiş performans hesaplanamadı: {e}")
    else:
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
                   "Tablo getiriye göre sıralıdır. "
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


# ---------------- sekme 5: Dip Skoru geçmişi ----------------
with tab5:
    st.subheader(f"{TF_LABEL[tf]}: Dip Skorunun geçmişi")
    st.caption("Skor, Telegram raporuyla aynı kurallarla her mum için hesaplanır; bir mumun skoru yalnızca o muma kadarki veriyi "
               "kullanır. Tablo \"skor bu etikette iken sonraki N mumda ne oldu\" sorusunu yanıtlar. Gelecek için garanti değildir.")
    unit = BAR_NAME[tf]
    controls = st.columns(3)
    horizon = controls[0].selectbox("İleri bakış", HORIZONS[tf], index=1, format_func=lambda h_: f"{h_} {unit}", key="sc_horizon")
    span = controls[1].selectbox("Grafik aralığı", ["Tümü", "Son 4 yıl", "Son 2 yıl", "Son 1 yıl"], index=1, key="sc_span")
    log_y = controls[2].checkbox("Logaritmik fiyat ekseni", value=True, key="sc_log")
    cut = {"Tümü": None, "Son 4 yıl": 4, "Son 2 yıl": 2, "Son 1 yıl": 1}[span]
    begin = df.index[-1] - pd.DateOffset(years=cut) if cut else df.index[0]
    shown = history.loc[begin:]

    fig_sc = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.04)
    fig_sc.add_trace(go.Scatter(x=shown.index, y=df["close"].loc[shown.index], name=asset["symbol"], line=dict(width=1.5)), 1, 1)
    fig_sc.add_trace(go.Scatter(x=shown.index, y=shown["score"], name="Dip Skoru", line=dict(width=1.3, shape="hv")), 2, 1)
    for y, color_ in SCORE_LINES:
        fig_sc.add_hline(y=y, line_dash="dot", line_color=color_, row=2, col=1)
    fig_sc.update_layout(height=560, margin=dict(l=10, r=10, t=25, b=10), legend=dict(orientation="h"), hovermode="x unified")
    fig_sc.update_yaxes(title_text="Fiyat", row=1, col=1, type="log" if log_y else "linear")
    fig_sc.update_yaxes(title_text="Dip Skoru", row=2, col=1, range=[-105, 105])
    st.plotly_chart(fig_sc, width="stretch", alt=f"{asset['ticker']} fiyatı ve Dip Skoru geçmişi")
    st.caption("Noktalı çizgiler ±20 ve ±60 etiket sınırlarıdır. +100 güçlü dip (alım fırsatı yönünde), -100 aşırı şişmiş.")

    table_sc = bucket_table(df["close"], history["label"], horizon)
    view = pd.DataFrame({"Etiket": [f"{ICONS[i]} {i}" if i in ICONS else i for i in table_sc.index],
                         "Mum": table_sc["n"], "Zamanın payı": table_sc["share"], "Ayrı dönem": table_sc["episodes"],
                         f"Ort. getiri ({horizon} {unit})": table_sc["mean"], "Medyan": table_sc["median"],
                         "Getirisi pozitif": table_sc["pos_rate"]}).reset_index(drop=True)
    st.markdown(f"##### Dip Skoru etiketine göre sonraki {horizon} {unit}lık getiri")
    st.dataframe(view, hide_index=True, width="stretch", alt="Dip Skoru etiketlerine göre ileri getiri istatistikleri",
                 column_config={name: st.column_config.NumberColumn(format="percent")
                                for name in ("Zamanın payı", f"Ort. getiri ({horizon} {unit})", "Medyan", "Getirisi pozitif")})
    st.info("Dikkat: (1) Bitişik mumların ileri getiri pencereleri birbirinin neredeyse aynısıdır; güvenilirliği 'Mum' değil "
            "**'Ayrı dönem'** sütunu (etikete ayrı giriş sayısı) belirler. (2) Kripto geçmişi yalnızca birkaç boğa/ayı döngüsünden oluşur; "
            "bir etiketin sonuçları çoğu zaman bir iki döngüye aittir. (3) Getiriler kapanıştan kapanışa, komisyonsuzdur ve al-tut "
            "getirisiyle (kıyas satırı) karşılaştırılmalıdır. (4) Geçmişte işleyen bir etiket gelecekte de işleyecek demek değildir. "
            "(5) İstatistikler seçili zaman diliminin tüm geçmişinden hesaplanır; grafik aralığı yalnızca görünümü değiştirir.")
