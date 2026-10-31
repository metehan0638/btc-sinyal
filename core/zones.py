"""Fiyat dipte mi, şişmiş mi?  Her gösterge için ham değer + bölge:

   +1  ucuz / dip bölgesi   (aşırı satım, derin düşüş, korku ...)  -> fiyat şişmiş DEĞİL
    0  normal
   -1  şişmiş / pahalı bölge (aşırı alım, tepeye yakın, aşırı açgözlülük ...)
  NaN  yetersiz veri

AL/SAT sinyali ÜRETMEZ, yalnızca durum raporu verir. Hesaplar sadece geçmiş mumları kullanır.
Eşikler config.yaml içinden değiştirilebilir (varsayılanlar aşağıdadır)."""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_PARAMS = dict(rsi_period=14, stoch=(14, 3, 3), bb_period=20, bb_std=2.0, mfi_period=14, ma_long=200,
                      cross=(50, 200))  # cross: golden/dead cross için (hızlı, yavaş) SMA periyotları

# gösterge -> (ucuz eşiği, şişmiş eşiği): değer <= ucuz eşiği ise UCUZ, değer >= şişmiş eşiği ise ŞİŞMİŞ
DEFAULT_ZONES = {
    "rsi": (30, 70),
    "stochrsi": (20, 80),
    "bollinger": (0.10, 0.90),     # %B: 0 = alt bant, 1 = üst bant
    "mfi": (20, 80),
    "ma_ratio": (0.85, 2.0),       # fiyat / uzun hareketli ortalama
    "drawdown": (-50, -10),        # ATH'den uzaklık, %
    "fear_greed": (24, 75),        # alternative.me: 0-24 aşırı korku, 75-100 aşırı açgözlülük
}

# (ucuz yönündeki ifade, şişmiş yönündeki ifade)
NOTES = {
    "rsi": ("aşırı satım", "aşırı alım"),
    "stochrsi": ("aşırı satım", "aşırı alım"),
    "bollinger": ("alt banda yakın/altında", "üst banda yakın/üstünde"),
    "mfi": ("aşırı satım (para çıkışı)", "aşırı alım (para girişi)"),
    "ma_ratio": ("uzun ortalamanın altında/yakınında", "uzun ortalamadan çok uzakta"),
    "drawdown": ("zirveden derin düşüş", "zirveye çok yakın"),
    "fear_greed": ("aşırı korku", "aşırı açgözlülük"),
}
# bundan kısa geçmişle değerler güvenilmez (yedek borsaya düşülmüş olabilir)
MIN_BARS = {"1d": 1500, "1w": 250}
METRIC_KEYS = ["rsi", "stochrsi", "bollinger", "mfi", "ma_ratio", "drawdown"]
UNIT = {"1d": "günlük", "1w": "haftalık"}
BAR_NAME = {"1d": "gün", "1w": "hafta"}
FRESH_BARS = {"1d": 14, "1w": 4}  # son kesişim bu kadar mum içindeyse "yeni" sayılır
# Trend yönü (golden/dead cross): ucuz/şişmiş ekseninden BAĞIMSIZ, olumlu/olumsuz ekseni
TREND_TEXT = {1: ("🟢", "OLUMLU"), -1: ("🔴", "OLUMSUZ"), 0: ("⚪", "NÖTR")}
ZONE_TEXT = {"dip": ("🟢", "DİP BÖLGESİ"), "normal": ("⚪", "NORMAL"),
             "hot": ("🔴", "ŞİŞMİŞ BÖLGE"), "nodata": ("❔", "YETERSİZ VERİ")}


# ---------- hesaplar ----------
def _rma(s: pd.Series, n: int) -> pd.Series:  # Wilder yumuşatma
    return s.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    out = 100 - 100 / (1 + _rma(up, n) / _rma(dn, n))
    out.iloc[:n] = np.nan  # ısınma
    return out


def compute_metrics(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Her mum için ham gösterge değerleri (kapanmış mumlar varsayılır)."""
    p = {**DEFAULT_PARAMS, **{k: v for k, v in (params or {}).items() if k != "zones"}}
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


# ---------- bölgeler ----------
def zones_for(params: dict | None = None) -> dict:
    z = dict(DEFAULT_ZONES)
    for k, v in ((params or {}).get("zones") or {}).items():
        z[k] = tuple(v)
    for k, (cheap, hot) in z.items():
        if not cheap < hot:
            raise ValueError(f"'{k}' bölge eşikleri hatalı: ucuz eşiği ({cheap}) şişmiş eşiğinden ({hot}) küçük olmalı.")
    return z


def classify(values: pd.Series, cheap: float, hot: float) -> pd.Series:
    """+1 ucuz (değer <= cheap), -1 şişmiş (değer >= hot), 0 normal, NaN veri yok."""
    out = pd.Series(0.0, index=values.index)
    out[values <= cheap] = 1.0
    out[values >= hot] = -1.0
    out[values.isna()] = np.nan
    return out


def zone_votes(raw: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    z = zones_for(params)
    return pd.DataFrame({k: classify(raw[k], *z[k]) for k in METRIC_KEYS}, index=raw.index)


def _row(key, name, value, text, z):
    if value is None or pd.isna(value):
        return dict(key=key, name=name, value=np.nan, text="-", zone=np.nan, note="")
    zone = 1 if value <= z[key][0] else (-1 if value >= z[key][1] else 0)
    return dict(key=key, name=name, value=float(value), text=text, zone=zone,
                note=NOTES[key][0] if zone == 1 else (NOTES[key][1] if zone == -1 else ""))


def latest_rows(raw: pd.DataFrame, params: dict | None, tf: str, fg_info: dict | None = None) -> list[dict]:
    """Son mum için gösterge satırları. fg_info verilirse Fear & Greed da (yalnızca günlükte) eklenir."""
    p = {**DEFAULT_PARAMS, **{k: v for k, v in (params or {}).items() if k != "zones"}}
    z, last = zones_for(params), raw.iloc[-1]
    unit = UNIT[tf]
    ma = last["ma_ratio"]
    rows = [
        _row("rsi", f"RSI({p['rsi_period']})", last["rsi"], f"{last['rsi']:.1f}", z),
        _row("stochrsi", "Stochastic RSI", last["stochrsi"], f"%K={last['stochrsi']:.0f}", z),
        _row("bollinger", "Bollinger %B", last["bollinger"], f"%B={last['bollinger']:.2f}", z),
        _row("mfi", f"MFI({p['mfi_period']})", last["mfi"], f"{last['mfi']:.1f}", z),
        _row("ma_ratio", f"Fiyat / {p['ma_long']} {unit} ortalama", ma,
             f"{ma:.2f} (ortalamanın %{abs(ma - 1) * 100:.0f} {'üstünde' if ma >= 1 else 'altında'})" if pd.notna(ma) else "-", z),
        _row("drawdown", "ATH'den uzaklık", last["drawdown"], f"%{last['drawdown']:.1f} (ATH {last['ath']:,.0f})", z),
    ]
    if fg_info:
        hist = " · ".join(f"{n} {fg_info[k]:.0f}" for n, k in (("dün", "yesterday"), ("7g", "week"), ("30g", "month")) if fg_info.get(k) is not None)
        row = _row("fear_greed", "Fear & Greed", fg_info["value"],
                   f"{fg_info['value']:.0f} — {fg_info['label']}" + (f" ({hist})" if hist else ""), z)
        row["note"] = ""  # etiket (Aşırı korku vb.) zaten metinde
        rows.append(row)
    return rows


def summarize(rows: list[dict], ratio: float = 0.5) -> dict:
    """Göstergelerin en az `ratio` kadarı ucuz bölgedeyse DİP, şişmiş bölgedeyse ŞİŞMİŞ; aksi halde NORMAL."""
    valid = [r for r in rows if not pd.isna(r["zone"])]
    n = len(valid)
    cheap, hot = sum(r["zone"] == 1 for r in valid), sum(r["zone"] == -1 for r in valid)
    if n == 0:
        zone = "nodata"
    elif cheap / n >= ratio and cheap > hot:
        zone = "dip"
    elif hot / n >= ratio and hot > cheap:
        zone = "hot"
    else:
        zone = "normal"
    return dict(n=n, cheap=cheap, normal=n - cheap - hot, hot=hot, zone=zone)


# ---------- trend yönü: golden / dead cross ----------
def cross_events(raw: pd.DataFrame, since: pd.Timestamp | None = None) -> list[tuple[pd.Timestamp, int]]:
    """Kesişim olayları [(mum zamanı, +1 golden / -1 dead)], eskiden yeniye. `since` verilirse yalnızca ondan sonrakiler."""
    ev = raw["cross_event"][raw["cross_event"] != 0]
    return [(ts, int(k)) for ts, k in ev.items() if since is None or ts > since]


def trend_info(raw: pd.DataFrame, params: dict | None, tf: str) -> dict:
    """Son mumdaki trend yönü: golden cross yapısı = olumlu, dead cross yapısı = olumsuz (kesişimden sonra tersi olana kadar)."""
    p = {**DEFAULT_PARAMS, **{k: v for k, v in (params or {}).items() if k != "zones"}}
    fast_n, slow_n = p["cross"]
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
