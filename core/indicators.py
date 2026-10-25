"""10 indikatör -> her biri için oy: +1 (AL), -1 (SAT), 0 (NÖTR), NaN (yetersiz veri).
Hepsi pandas/numpy ile elle yazıldı (ek kütüphane gerekmez)."""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULTS = dict(
    ema_fast=50, ema_slow=200,
    macd=(12, 26, 9),
    st_period=10, st_mult=3.0,
    adx_period=14, adx_min=20,
    ichi=(9, 26, 52),
    rsi_period=14, rsi_band=(45, 55),
    stoch=(14, 3, 3),
    bb_period=20, bb_std=2.0,
    obv_ema=20,
    mfi_period=14, mfi_band=(45, 55),
    atr_period=14,
    fg_band=(47, 54),   # Fear&Greed: <47 korku -> AL, >54 açgözlülük -> SAT, arası NÖTR
)

# (anahtar, görünen ad, kategori)
INDICATORS = [
    ("ema", "EMA kesişimi", "Trend"),
    ("macd", "MACD", "Trend"),
    ("supertrend", "Supertrend", "Trend"),
    ("adx", "ADX / DI", "Trend"),
    ("ichimoku", "Ichimoku bulutu", "Trend"),
    ("rsi", "RSI", "Momentum"),
    ("stochrsi", "Stochastic RSI", "Momentum"),
    ("bollinger", "Bollinger orta bant", "Volatilite"),
    ("obv", "OBV", "Hacim"),
    ("mfi", "MFI", "Hacim"),
    ("feargreed", "Fear & Greed (ters)", "Duyarlılık"),  # yalnızca fg verilirse hesaplanır
]


# ---------- yardımcı fonksiyonlar ----------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rma(s, n):  # Wilder yumuşatma
    return s.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    rs = rma(up, n) / rma(dn, n)
    return 100 - 100 / (1 + rs)


def atr(df, n=14):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return rma(tr, n)


def supertrend(df, n=10, mult=3.0):
    a = atr(df, n).values
    hl2 = ((df["high"] + df["low"]) / 2).values
    c = df["close"].values
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = ub.copy(), lb.copy()
    direction = np.full(len(df), np.nan)
    valid = np.where(~np.isnan(a))[0]
    if len(valid) == 0:
        return pd.Series(direction, index=df.index)
    s = valid[0]
    direction[s] = 1
    for i in range(s + 1, len(df)):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if direction[i - 1] == 1 and c[i] < flb[i]:
            direction[i] = -1
        elif direction[i - 1] == -1 and c[i] > fub[i]:
            direction[i] = 1
        else:
            direction[i] = direction[i - 1]
    return pd.Series(direction, index=df.index)


def adx(df, n=14):
    up, dn = df["high"].diff(), -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    pdi, mdi = 100 * rma(plus_dm, n) / a, 100 * rma(minus_dm, n) / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return rma(dx, n), pdi, mdi


def _vote(bull, bear, *sources):
    v = pd.Series(np.where(bull, 1.0, np.where(bear, -1.0, 0.0)), index=bull.index)
    v[pd.concat(sources, axis=1).isna().any(axis=1)] = np.nan
    return v


# ---------- ana fonksiyon ----------
def compute_indicators(df: pd.DataFrame, params: dict | None = None, fg: pd.Series | None = None):
    """Döndürür: (votes DataFrame [10 sütun; fg verilirse 11], raw DataFrame [ham indikatör değerleri]).
    fg: mum indeksiyle hizalanmış Fear & Greed serisi (bkz. core.sentiment.align_fear_greed)."""
    p = {**DEFAULTS, **(params or {})}
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    raw = pd.DataFrame(index=df.index)
    raw["close"] = c
    votes = pd.DataFrame(index=df.index)

    # 1) EMA kesişimi
    raw["ema_fast"], raw["ema_slow"] = ema(c, p["ema_fast"]), ema(c, p["ema_slow"])
    raw.iloc[: p["ema_slow"] - 1, raw.columns.get_indexer(["ema_fast", "ema_slow"])] = np.nan  # ısınma
    votes["ema"] = _vote(raw.ema_fast > raw.ema_slow, raw.ema_fast < raw.ema_slow, raw.ema_fast, raw.ema_slow)

    # 2) MACD
    f, s, sg = p["macd"]
    raw["macd"] = ema(c, f) - ema(c, s)
    raw["macd_signal"] = ema(raw["macd"], sg)
    raw.iloc[: s + sg, raw.columns.get_indexer(["macd", "macd_signal"])] = np.nan
    votes["macd"] = _vote(raw.macd > raw.macd_signal, raw.macd < raw.macd_signal, raw.macd, raw.macd_signal)

    # 3) Supertrend
    raw["supertrend"] = supertrend(df, p["st_period"], p["st_mult"])
    votes["supertrend"] = _vote(raw.supertrend == 1, raw.supertrend == -1, raw.supertrend)

    # 4) ADX: trend yeterince güçlüyse DI yönüne oy, değilse nötr
    raw["adx"], raw["plus_di"], raw["minus_di"] = adx(df, p["adx_period"])
    strong = raw.adx >= p["adx_min"]
    votes["adx"] = _vote(strong & (raw.plus_di > raw.minus_di), strong & (raw.plus_di < raw.minus_di),
                         raw.adx, raw.plus_di, raw.minus_di)

    # 5) Ichimoku: fiyat bulutun üstünde/altında/içinde (bulut 26 bar ileri kaydırılmış = bugünkü bulut)
    t, k, b = p["ichi"]
    tenkan = (h.rolling(t).max() + l.rolling(t).min()) / 2
    kijun = (h.rolling(k).max() + l.rolling(k).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(k)
    span_b = ((h.rolling(b).max() + l.rolling(b).min()) / 2).shift(k)
    raw["cloud_top"], raw["cloud_bottom"] = pd.concat([span_a, span_b], axis=1).max(axis=1, skipna=False), \
        pd.concat([span_a, span_b], axis=1).min(axis=1, skipna=False)
    votes["ichimoku"] = _vote(c > raw.cloud_top, c < raw.cloud_bottom, raw.cloud_top, raw.cloud_bottom)

    # 6) RSI (45-55 arası nötr bölge)
    lo, hi = p["rsi_band"]
    raw["rsi"] = rsi(c, p["rsi_period"])
    raw.iloc[: p["rsi_period"], raw.columns.get_loc("rsi")] = np.nan
    votes["rsi"] = _vote(raw.rsi > hi, raw.rsi < lo, raw.rsi)

    # 7) Stochastic RSI: %K, %D'yi yukarı/aşağı geçmiş durumda
    n, ks, ds = p["stoch"]
    r = rsi(c, p["rsi_period"])
    rmin, rmax = r.rolling(n).min(), r.rolling(n).max()
    raw["stoch_k"] = ((r - rmin) / (rmax - rmin) * 100).rolling(ks).mean()
    raw["stoch_d"] = raw["stoch_k"].rolling(ds).mean()
    votes["stochrsi"] = _vote(raw.stoch_k > raw.stoch_d, raw.stoch_k < raw.stoch_d, raw.stoch_k, raw.stoch_d)

    # 8) Bollinger: fiyat orta bandın üstünde mi
    raw["bb_mid"] = c.rolling(p["bb_period"]).mean()
    std = c.rolling(p["bb_period"]).std()
    raw["bb_upper"], raw["bb_lower"] = raw.bb_mid + p["bb_std"] * std, raw.bb_mid - p["bb_std"] * std
    votes["bollinger"] = _vote(c > raw.bb_mid, c < raw.bb_mid, raw.bb_mid)

    # 9) OBV: kendi EMA'sının üstünde mi
    raw["obv"] = (np.sign(c.diff()).fillna(0) * v).cumsum()
    raw["obv_ema"] = ema(raw["obv"], p["obv_ema"])
    raw.iloc[: p["obv_ema"], raw.columns.get_indexer(["obv", "obv_ema"])] = np.nan
    votes["obv"] = _vote(raw.obv > raw.obv_ema, raw.obv < raw.obv_ema, raw.obv, raw.obv_ema)

    # 10) MFI (45-55 arası nötr bölge)
    lo, hi = p["mfi_band"]
    tp = (h + l + c) / 3
    mf = tp * v
    pos = mf.where(tp > tp.shift(), 0.0).rolling(p["mfi_period"]).sum()
    neg = mf.where(tp < tp.shift(), 0.0).rolling(p["mfi_period"]).sum()
    raw["mfi"] = 100 - 100 / (1 + pos / neg)
    votes["mfi"] = _vote(raw.mfi > hi, raw.mfi < lo, raw.mfi)

    # 11) Fear & Greed (ters): korku -> AL, açgözlülük -> SAT
    if fg is not None:
        lo, hi = p["fg_band"]
        raw["fear_greed"] = fg.reindex(df.index)
        votes["feargreed"] = _vote(raw.fear_greed < lo, raw.fear_greed > hi, raw.fear_greed)

    raw["atr"] = atr(df, p["atr_period"])
    return votes[[k for k, _, _ in INDICATORS if k in votes.columns]], raw


def latest_table(votes: pd.DataFrame, raw: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Son mum için indikatör durum tablosu (arayüzde gösterilir)."""
    p = {**DEFAULTS, **(params or {})}
    r, vt = raw.iloc[-1], votes.iloc[-1]
    txt = {
        "ema": f"EMA{p['ema_fast']}={r.ema_fast:,.0f} | EMA{p['ema_slow']}={r.ema_slow:,.0f}",
        "macd": f"MACD={r.macd:,.0f} | sinyal={r.macd_signal:,.0f}",
        "supertrend": "Yükseliş modu" if r.supertrend == 1 else "Düşüş modu",
        "adx": f"ADX={r.adx:.1f} | +DI={r.plus_di:.1f} | -DI={r.minus_di:.1f}",
        "ichimoku": f"Bulut: {r.cloud_bottom:,.0f} – {r.cloud_top:,.0f} | Fiyat={r.close:,.0f}",
        "rsi": f"RSI={r.rsi:.1f}",
        "stochrsi": f"%K={r.stoch_k:.1f} | %D={r.stoch_d:.1f}",
        "bollinger": f"Orta bant={r.bb_mid:,.0f} | Fiyat={r.close:,.0f}",
        "obv": f"OBV {'>' if r.obv > r.obv_ema else '<'} OBV-EMA{p['obv_ema']}",
        "mfi": f"MFI={r.mfi:.1f}",
    }
    if "fear_greed" in raw.columns:
        from core.sentiment import classify
        txt["feargreed"] = f"Endeks={r.fear_greed:.0f} ({classify(r.fear_greed)})"
    label = {1.0: "AL", -1.0: "SAT", 0.0: "NÖTR"}
    rows = [dict(İndikatör=name, Kategori=cat, Değer=txt[k] if pd.notna(vt[k]) else "-",
                 Oy=label.get(vt[k], "Yetersiz veri")) for k, name, cat in INDICATORS if k in votes.columns]
    return pd.DataFrame(rows)
