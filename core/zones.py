"""Kripto varlık göstergeleri. Her gösterge, değerine göre 5 dereceden birine yerleştirilir:

   +2 GÜÇLÜ AL · +1 AL · 0 NÖTR · -1 SAT · -2 GÜÇLÜ SAT      (NaN = yetersiz veri)

AL tarafı = fiyat ucuz/dip tarafında (şişmiş değil), SAT tarafı = fiyat şişmiş tarafında.
Bunlar göstergelerin değerlerine bakan MEKANİK derecelerdir; emir ya da tavsiye değildir.
Genel derece, geçerli göstergelerin puan ortalamasından çıkar. Hesaplar yalnızca geçmiş mumları kullanır.
Aralıklar config.yaml içinden değiştirilebilir (varsayılanlar aşağıdadır)."""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_PARAMS = dict(rsi_period=14, stoch=(14, 3, 3), bb_period=20, bb_std=2.0, mfi_period=14, ma_long=200,
                      cross=(50, 200))  # cross: golden/dead cross için (hızlı, yavaş) SMA periyotları

# gösterge -> 4 eşik (t1 < t2 < t3 < t4):
#   değer <= t1 GÜÇLÜ AL · <= t2 AL · t2 ile t3 arası NÖTR · >= t3 SAT · >= t4 GÜÇLÜ SAT
DEFAULT_BANDS = {
    "rsi": (20, 30, 70, 80),
    "stochrsi": (10, 20, 80, 90),
    "bollinger": (0.0, 0.10, 0.90, 1.0),    # %B: 0 = alt bant, 1 = üst bant
    "mfi": (10, 20, 80, 90),
    "ma_ratio": (0.8, 1.0, 2.0, 2.4),       # fiyat / uzun SMA (günlük: Mayer Multiple)
    "drawdown": (-65, -50, -10, -3),        # ATH'den uzaklık, %
    "fear_greed": (10, 24, 75, 90),         # alternative.me: 0-24 aşırı korku, 75-100 aşırı açgözlülük
}
# zaman dilimine özel varsayılanlar (config'teki `bands` bunların üzerine yazar)
TF_BANDS = {"1w": {"ma_ratio": (1.0, 1.3, 2.5, 3.5)},
            "1M": {"ma_ratio": (1.0, 1.3, 2.5, 3.5)}}  # haftalık/aylıkta fiyat / uzun ortalama

# (AL yönündeki ifade, SAT yönündeki ifade)
NOTES = {
    "rsi": ("aşırı satım", "aşırı alım"),
    "stochrsi": ("aşırı satım", "aşırı alım"),
    "bollinger": ("alt banda yakın/altında", "üst banda yakın/üstünde"),
    "mfi": ("aşırı satım (para çıkışı)", "aşırı alım (para girişi)"),
    "ma_ratio": ("uzun ortalamanın altında/yakınında", "uzun ortalamadan çok uzakta"),
    "drawdown": ("zirveden derin düşüş", "zirveye çok yakın"),
    "fear_greed": ("", ""),  # etiket (Aşırı korku vb.) zaten metinde
}
METRIC_KEYS = ["rsi", "stochrsi", "bollinger", "mfi", "ma_ratio", "drawdown"]
# bundan kısa geçmişle değerler güvenilmez (yedek borsaya düşülmüş olabilir)
MIN_BARS = {"1d": 1500, "1w": 250, "1M": 36}
UNIT = {"1d": "günlük", "1w": "haftalık", "1M": "aylık"}
BAR_NAME = {"1d": "gün", "1w": "hafta", "1M": "ay"}
FRESH_BARS = {"1d": 14, "1w": 4, "1M": 2}  # son kesişim bu kadar mum içindeyse "yeni" sayılır (cross +-2 puan)

LEVELS = {2: ("🟢🟢", "GÜÇLÜ AL"), 1: ("🟢", "AL"), 0: ("⚪", "NÖTR"), -1: ("🔴", "SAT"), -2: ("🔴🔴", "GÜÇLÜ SAT")}
LEVEL_TITLE = {2: "Güçlü al", 1: "Al", 0: "Nötr", -1: "Sat", -2: "Güçlü sat"}
RATING_KEYS = {2: "strong_buy", 1: "buy", 0: "neutral", -1: "sell", -2: "strong_sell"}
RATING_TEXT = {**{RATING_KEYS[g]: v for g, v in LEVELS.items()}, "nodata": ("❔", "YETERSİZ VERİ")}


# ---------- hesaplar ----------
def _rma(s: pd.Series, n: int) -> pd.Series:  # Wilder yumuşatma
    return s.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    out = 100 - 100 / (1 + _rma(up, n) / _rma(dn, n))
    out.iloc[:n] = np.nan  # ısınma
    return out


def _params(params: dict | None) -> dict:
    return {**DEFAULT_PARAMS, **{k: v for k, v in (params or {}).items() if k not in ("bands", "zones")}}


def compute_metrics(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Her mum için ham gösterge değerleri (kapanmış mumlar varsayılır)."""
    p = _params(params)
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    raw = pd.DataFrame(index=df.index)
    raw["close"] = c

    raw["rsi"] = rsi(c, p["rsi_period"])

    n, ks, _ = p["stoch"]
    r = rsi(c, p["rsi_period"])
    rmin, rmax = r.rolling(n).min(), r.rolling(n).max()
    raw["stochrsi"] = ((r - rmin) / (rmax - rmin) * 100).rolling(ks).mean().replace([np.inf, -np.inf], np.nan)

    mid, std = c.rolling(p["bb_period"]).mean(), c.rolling(p["bb_period"]).std()
    lower, upper = mid - p["bb_std"] * std, mid + p["bb_std"] * std
    raw["bollinger"] = ((c - lower) / (upper - lower)).replace([np.inf, -np.inf], np.nan)

    tp = (h + l + c) / 3
    mf = tp * v
    pos = mf.where(tp > tp.shift(), 0.0).rolling(p["mfi_period"]).sum()
    neg = mf.where(tp < tp.shift(), 0.0).rolling(p["mfi_period"]).sum()
    mfi = 100 - 100 / (1 + pos / neg)
    mfi.iloc[: p["mfi_period"]] = np.nan
    raw["mfi"] = mfi

    raw["ma_ratio"] = c / c.rolling(p["ma_long"]).mean()

    raw["ath"] = h.cummax()
    raw["drawdown"] = (c / raw["ath"] - 1) * 100

    # Ichimoku 9/26/52: bulut değerleri 26 mum ötelendiğinden geçmiş veriye bakılır.
    tenkan_n, kijun_n, span_b_n = p.get("ichimoku", (9, 26, 52))
    midpoint = lambda n: (h.rolling(n).max() + l.rolling(n).min()) / 2
    tenkan, kijun = midpoint(tenkan_n), midpoint(kijun_n)
    raw["ichimoku_tenkan"], raw["ichimoku_kijun"] = tenkan, kijun
    raw["ichimoku_span_a"] = ((tenkan + kijun) / 2).shift(kijun_n)
    raw["ichimoku_span_b"] = midpoint(span_b_n).shift(kijun_n)
    top = pd.concat([raw["ichimoku_span_a"], raw["ichimoku_span_b"]], axis=1).max(axis=1)
    bottom = pd.concat([raw["ichimoku_span_a"], raw["ichimoku_span_b"]], axis=1).min(axis=1)
    valid_cloud = raw[["ichimoku_span_a", "ichimoku_span_b"]].notna().all(axis=1)
    above, below = c > top, c < bottom
    bullish = above & (tenkan > kijun) & (raw["ichimoku_span_a"] > raw["ichimoku_span_b"])
    bearish = below & (tenkan < kijun) & (raw["ichimoku_span_a"] < raw["ichimoku_span_b"])
    raw["ichimoku_grade"] = np.select([bullish, bearish, above & valid_cloud, below & valid_cloud], [2, -2, 1, -1], default=0)
    raw.loc[~valid_cloud, "ichimoku_grade"] = np.nan

    # golden / dead cross: hızlı SMA yavaş SMA'yı yukarı keserse +1 (golden), aşağı keserse -1 (dead)
    fast_n, slow_n = p["cross"]
    if not fast_n < slow_n:
        raise ValueError(f"cross periyotları hatalı: hızlı ({fast_n}) yavaştan ({slow_n}) küçük olmalı.")
    raw["ma_fast"], raw["ma_slow"] = c.rolling(fast_n).mean(), c.rolling(slow_n).mean()
    diff = raw["ma_fast"] - raw["ma_slow"]
    raw["cross_state"] = np.sign(diff)  # +1 golden cross yapısı (hızlı > yavaş), -1 dead cross yapısı
    prev = diff.shift()
    event = pd.Series(0.0, index=df.index)
    event[(prev <= 0) & (diff > 0)] = 1.0    # dün hızlı <= yavaş, bugün hızlı > yavaş
    event[(prev >= 0) & (diff < 0)] = -1.0   # dün hızlı >= yavaş, bugün hızlı < yavaş
    raw["cross_event"] = event               # kesişimin yaşandığı mum
    return raw


# ---------- dereceler ----------
def bands_for(params: dict | None = None, tf: str = "1d") -> dict:
    """Gösterge -> (t1, t2, t3, t4). Öncelik: config `bands` > zaman dilimi varsayılanı > genel varsayılan."""
    if (params or {}).get("zones"):
        raise ValueError("config'te 'zones' yerine 'bands' kullanılmalı ve her gösterge için 4 eşik verilmelidir "
                         "(güçlü al, al, sat, güçlü sat sınırları).")
    b = {**DEFAULT_BANDS, **TF_BANDS.get(tf, {})}
    for k, v in ((params or {}).get("bands") or {}).items():
        b[k] = tuple(v)
    for k, t in b.items():
        if len(t) != 4 or not (t[0] < t[1] < t[2] < t[3]):
            raise ValueError(f"'{k}' için 4 artan eşik gerekir (güçlü al < al < sat < güçlü sat), verilen: {list(t)}")
    return b


def grade_value(value, bands) -> float:
    """+2 güçlü al, +1 al, 0 nötr, -1 sat, -2 güçlü sat, NaN veri yok."""
    if value is None or pd.isna(value):
        return np.nan
    t1, t2, t3, t4 = bands
    if value <= t1:
        return 2
    if value <= t2:
        return 1
    if value >= t4:
        return -2
    if value >= t3:
        return -1
    return 0


def grade_series(values: pd.Series, bands) -> pd.Series:
    return values.map(lambda v: grade_value(v, bands)).astype(float)


def grade_votes(raw: pd.DataFrame, params: dict | None = None, tf: str = "1d") -> pd.DataFrame:
    b = bands_for(params, tf)
    return pd.DataFrame({k: grade_series(raw[k], b[k]) for k in METRIC_KEYS}, index=raw.index)


def _row(key, name, value, text, bands):
    g = grade_value(value, bands[key])
    if pd.isna(g):
        return dict(key=key, name=name, value=np.nan, text="-", grade=np.nan, note="")
    note = NOTES[key][0] if g > 0 else (NOTES[key][1] if g < 0 else "")
    return dict(key=key, name=name, value=float(value), text=text, grade=int(g), note=note)


def latest_rows(raw: pd.DataFrame, params: dict | None, tf: str, fg_info: dict | None = None) -> list[dict]:
    """Son mum için gösterge satırları (derecesiyle). Fear & Greed yalnızca verilirse (günlükte) eklenir."""
    p = _params(params)
    tenkan_n, kijun_n, span_b_n = p.get("ichimoku", (9, 26, 52))
    b, last = bands_for(params, tf), raw.iloc[-1]
    unit, ma = UNIT[tf], last["ma_ratio"]
    rows = [
        _row("rsi", f"RSI({p['rsi_period']})", last["rsi"], f"{last['rsi']:.1f}", b),
        _row("stochrsi", "Stochastic RSI", last["stochrsi"], f"%K={last['stochrsi']:.0f}", b),
        _row("bollinger", "Bollinger %B", last["bollinger"], f"%B={last['bollinger']:.2f}", b),
        _row("mfi", f"MFI({p['mfi_period']})", last["mfi"], f"{last['mfi']:.1f}", b),
        _row("ma_ratio", f"Fiyat / {p['ma_long']} {unit} ortalama", ma,
             f"{ma:.2f} (ortalamanın %{abs(ma - 1) * 100:.0f} {'üstünde' if ma >= 1 else 'altında'})" if pd.notna(ma) else "-", b),
        _row("drawdown", "ATH'den uzaklık", last["drawdown"], f"%{last['drawdown']:.1f} (ATH {last['ath']:,.0f})", b),
    ]
    span_a, span_b = last["ichimoku_span_a"], last["ichimoku_span_b"]
    if pd.notna(span_a) and pd.notna(span_b) and pd.notna(last["ichimoku_grade"]):
        cloud_top, cloud_bottom = max(span_a, span_b), min(span_a, span_b)
        close = last["close"]
        grade = int(last["ichimoku_grade"])
        position = "bulut üstünde" if close > cloud_top else "bulut altında" if close < cloud_bottom else "bulut içinde"
        cross = "Tenkan > Kijun" if last["ichimoku_tenkan"] > last["ichimoku_kijun"] else "Tenkan < Kijun" if last["ichimoku_tenkan"] < last["ichimoku_kijun"] else "Tenkan = Kijun"
        cloud = "bulut yükseliş yönlü" if last["ichimoku_span_a"] > last["ichimoku_span_b"] else "bulut düşüş yönlü"
        rows.append(dict(key="ichimoku", name=f"Ichimoku ({tenkan_n}/{kijun_n}/{span_b_n})", value=float(grade),
                         text=f"{position} · {cross} · {cloud}", grade=grade, note=""))
    else:
        rows.append(dict(key="ichimoku", name=f"Ichimoku ({tenkan_n}/{kijun_n}/{span_b_n})", value=np.nan, text="-", grade=np.nan, note=""))
    if fg_info:
        hist = " · ".join(f"{n} {fg_info[k]:.0f}" for n, k in (("dün", "yesterday"), ("7g", "week"), ("30g", "month")) if fg_info.get(k) is not None)
        rows.append(_row("fear_greed", "Fear & Greed", fg_info["value"],
                         f"{fg_info['value']:.0f} — {fg_info['label']}" + (f" ({hist})" if hist else ""), b))
    rows.append(cross_row(trend_info(raw, params, tf)))
    return rows


def summarize(rows: list[dict], thresholds=(0.3, 1.0)) -> dict:
    """Geçerli göstergelerin puan ortalaması (-2..+2) -> genel derece.
    thresholds = (AL/SAT sınırı, GÜÇLÜ sınırı): ortalama >= 1.0 güçlü al, >= 0.3 al, -0.3..0.3 nötr, <= -0.3 sat, <= -1.0 güçlü sat."""
    t_mid, t_strong = thresholds
    if not 0 < t_mid < t_strong <= 2:
        raise ValueError("rating_thresholds için 0 < al/sat sınırı < güçlü sınırı <= 2 olmalı.")
    valid = [r["grade"] for r in rows if not pd.isna(r["grade"])]
    counts = {g: sum(v == g for v in valid) for g in LEVELS}
    if not valid:
        return dict(n=0, counts=counts, mean=np.nan, level=np.nan, key="nodata")
    mean = round(sum(valid) / len(valid), 9)
    level = 2 if mean >= t_strong else 1 if mean >= t_mid else -2 if mean <= -t_strong else -1 if mean <= -t_mid else 0
    return dict(n=len(valid), counts=counts, mean=mean, level=level, key=RATING_KEYS[level])


# ---------- golden / dead cross ----------
def cross_events(raw: pd.DataFrame, since: pd.Timestamp | None = None) -> list[tuple[pd.Timestamp, int]]:
    """Kesişim olayları [(mum zamanı, +1 golden / -1 dead)], eskiden yeniye. `since` verilirse yalnızca ondan sonrakiler."""
    ev = raw["cross_event"][raw["cross_event"] != 0]
    return [(ts, int(k)) for ts, k in ev.items() if since is None or ts > since]


def trend_info(raw: pd.DataFrame, params: dict | None, tf: str) -> dict:
    """Son mumdaki trend yapısı: golden cross yapısı (hızlı > yavaş) ya da dead cross yapısı (hızlı < yavaş)."""
    fast_n, slow_n = _params(params)["cross"]
    st = raw["cross_state"].iloc[-1]
    if pd.isna(st):
        return dict(state=np.nan, fast=fast_n, slow=slow_n, event=None, fresh=False,
                    text=f"yetersiz veri (SMA{slow_n} henüz hesaplanamıyor)", last_text="")
    state = int(st)
    events = cross_events(raw)
    event = None
    if events:
        ts, kind = events[-1]
        event = dict(ts=ts, kind=kind, bars_ago=len(raw) - 1 - raw.index.get_loc(ts))
    fresh = event is not None and event["bars_ago"] <= FRESH_BARS[tf]
    name = {1: "Golden cross yapısı", -1: "Dead cross yapısı", 0: "Ortalamalar eşit"}[state]
    sign = {1: ">", -1: "<", 0: "="}[state]
    if event:
        kind_txt = "golden" if event["kind"] == 1 else "dead"
        last_text = f"Son kesişim: {event['ts']:%d.%m.%Y} ({event['bars_ago']} {BAR_NAME[tf]} önce, {kind_txt} cross)"
    else:
        last_text = "Son kesişim: eldeki veri kapsamında kesişim yok"
    return dict(state=state, fast=fast_n, slow=slow_n, event=event, fresh=fresh,
                text=f"{name} (SMA{fast_n} {sign} SMA{slow_n})", last_text=last_text)


def cross_row(trend: dict) -> dict:
    """Cross de bir gösterge satırıdır: golden yapısı +1 (AL), dead yapısı -1 (SAT); kesişim yeniyse (FRESH_BARS içinde) +-2."""
    name = f"Golden/Dead cross (SMA{trend['fast']}/{trend['slow']})"
    if trend["state"] != trend["state"]:  # NaN
        return dict(key="cross", name=name, value=np.nan, text="-", grade=np.nan, note="")
    grade = trend["state"] * (2 if trend["fresh"] else 1)
    text = trend["text"] + " · " + trend["last_text"].replace("Son kesişim: ", "son kesişim ") + (" 🆕" if trend["fresh"] else "")
    return dict(key="cross", name=name, value=float(trend["state"]), text=text, grade=int(grade), note="")
