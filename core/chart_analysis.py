"""Grafik Yorumu: kural tabanlı (LLM/harici yapay zeka YOK) "grafik şu an ne gösteriyor" özeti.

Sistem fiyat TAHMİNİ yapmaz; yalnızca trendi, en güçlü destek/direnci ve (varsa) belirgin bir formasyonu tarif eder.
Fonksiyonlar saftır: ağ çağrısı ve yan etki yoktur, girdi DataFrame'i değiştirilmez.

    analyze(df, timeframe, params=None) -> dict      hesaplama (yapılandırılmış sonuç)
    build_comment(result, ...) -> str                tek zaman dilimi için Türkçe metin
    build_report(results, ...) -> str                "Grafik Yorumu" bölümü (birden çok zaman dilimi + not)

Bağımlılık: yalnızca pandas ve numpy (scipy gerekmez; pivot ve regresyon numpy ile yazıldı).
Tüm eşikler aşağıdaki DEFAULT_PARAMS içindedir; config.yaml'daki `chart_comment.params` ile ezilebilir.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- eşikler (kodda gömülü sayı yok)
DEFAULT_PARAMS: dict = {
    # --- veri ---
    "min_candles": 250,                 # EMA200'ün oturması için gereken en az mum sayısı
    "ema_fast": 50,
    "ema_slow": 200,
    "structure_window": 200,            # destek/direnç ve tepe-dip dizilimi için bakılan son mum sayısı
    "pivot_order": 5,                   # pivot: her iki yanındaki bu kadar mumdan yüksek (tepe) / düşük (dip)
    # --- trend (4 sinyal oylanır) ---
    "trend_regression_window": 60,      # lineer regresyon eğimi için son kaç mum
    "trend_slope_min_pct": 2.0,         # pencere boyunca regresyonla toplam değişim bu % ten büyükse yön sayılır
    "ema_gap_min_pct": 0.3,             # EMA50-EMA200 farkı bu %'den küçükse sinyal "belirsiz"
    "structure_tolerance_pct": 0.1,      # iki tepe/dip bu %'den yakınsa "eşit" sayılır (sinyal belirsiz)
    "trend_min_agree": 3,               # 4 sinyalden en az kaçı aynı yönü göstermeli
    "trend_max_conflict": 0,            # ters yönü gösteren sinyal sayısı en çok bu kadar olabilir
    # --- destek / direnç ---
    "sr_tolerance_pct": 0.5,            # bu %'den yakın pivotlar aynı seviye kümesine girer
    "sr_min_touches": 2,                # bir kümenin seviye sayılması için en az dokunuş
    "sr_touch_cap": 5,                  # dokunuş skoru bu sayıda 1'e ulaşır
    "sr_volume_cap": 2.0,               # pivot hacmi / ortalama hacim bu oranda skor 1'e ulaşır
    "sr_weights": [0.5, 0.3, 0.2],      # güç skoru ağırlıkları: dokunuş, yakınlık (yenilik), hacim
    "sr_strong_score": 0.5,             # güç skoru bunun altındaysa metinde "güçlü" denmez
    "sr_max_distance_pct": 25.0,        # fiyattan bu %'den uzak seviyeler raporlanmaz (çok uzak seviye güncel bir bilgi değildir)
    # --- formasyon ---
    "pattern_window": 90,               # formasyon aranan son mum sayısı
    "pattern_pivot_order": 3,           # formasyon pivotları için (trend pivotlarından hassas)
    "pattern_min_span": 25,             # formasyonun en az kaç mum sürmesi gerekir
    "pattern_min_pivots_per_line": 3,   # her çizgi (tepeler / dipler) için en az pivot (2 nokta her zaman mükemmel doğru verir, 3 gerekir)
    "pattern_min_total_pivots": 6,      # iki çizgide toplam en az pivot
    "pattern_pivot_target": 8,          # pivot skoru bu toplamda 1'e ulaşır
    "pattern_line_tolerance_pct": 0.7,  # pivotların regresyon doğrusuna ortalama uzaklığı için ölçek (%)
    "pattern_flat_change_pct": 1.5,     # bir çizgi, süre boyunca bu %'den az değişirse "yatay"
    "pattern_slope_min_change_pct": 3.0,  # bir çizgi, süre boyunca en az bu % değişirse "eğimli"
    "pattern_converge_min": 0.15,       # son/ilk aralık oranı bundan küçükse çizgiler neredeyse kesişmiş (geç)
    "pattern_converge_max": 0.7,        # oran bundan büyükse yeterince daralmamış (üçgen/kama değil)
    "pattern_breakout_tolerance_pct": 0.5,  # fiyat kanalın bu %'den fazla dışındaysa formasyon bitmiş sayılır
    "pattern_containment_min": 0.95,    # formasyon süresince mumların en az bu oranı iki çizginin İÇİNDE kalmalı (kanal içerme)
    "pattern_containment_tol_pct": 0.5,  # içerme kontrolünde çizgilere tanınan pay (% fiyat)
    "double_tolerance_pct": 1.0,        # çift tepe/dip: iki zirvenin birbirine en çok uzaklığı (%)
    "double_min_gap": 12,               # iki zirve arası en az mum
    "double_min_depth_pct": 3.0,        # zirveler ile aradaki çukur/tepe arası en az fark (%)
    "double_prior_lookback": 40,        # çift tepe/dipten önce, ilk zirveye gelen hareketi ölçmek için bakılan mum sayısı
    "double_min_prior_move_pct": 6.0,   # ilk zirveye gelen hareket (yükseliş/düşüş) en az bu % olmalı: yatay aralık çift formasyon sayılmaz
    "pattern_min_score": 0.72,          # güven skoru (0-1) bunun altındaki formasyon raporlanmaz
}

PATTERNS = {   # anahtar: (Türkçe ad, klasik yön)
    "ascending_triangle": ("Yükselen üçgen", "bullish"),
    "descending_triangle": ("Alçalan üçgen", "bearish"),
    "symmetrical_triangle": ("Simetrik üçgen", "nötr"),
    "rising_wedge": ("Yükselen kama", "bearish"),
    "falling_wedge": ("Düşen kama", "bullish"),
    "double_top": ("Çift tepe", "bearish"),
    "double_bottom": ("Çift dip", "bullish"),
}
TIMEFRAME_LABELS = {"1h": "1 saatlik", "2h": "2 saatlik", "4h": "4 saatlik", "6h": "6 saatlik", "12h": "12 saatlik",
                    "1d": "günlük", "1w": "haftalık", "1M": "aylık"}
HEADING = "📐 Grafik Yorumu"
DISCLAIMER = "ℹ️ Teknik analiz kesinlik taşımaz, yatırım tavsiyesi değildir."
UNAVAILABLE = "Grafik yorumu şu an üretilemedi."
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def merge_params(params: dict | None = None) -> dict:
    """Varsayılan eşikleri verilenlerle birleştirir. Bilinmeyen anahtar (yazım hatası) ValueError verir."""
    params = dict(params or {})
    unknown = set(params) - set(DEFAULT_PARAMS)
    if unknown:
        raise ValueError(f"Bilinmeyen grafik yorumu eşiği: {', '.join(sorted(unknown))}")
    merged = {**DEFAULT_PARAMS, **params}
    if len(merged["sr_weights"]) != 3 or sum(merged["sr_weights"]) <= 0:
        raise ValueError("sr_weights üç pozitif ağırlıktan oluşmalı (dokunuş, yakınlık, hacim).")
    return merged


# --------------------------------------------------------------------------- yardımcılar
def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Sıralar, tekrarları atar, NaN/sonsuz/geçersiz satırları çıkarır. Girdiyi değiştirmez."""
    out = df[list(REQUIRED_COLUMNS)].astype(float)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.replace([np.inf, -np.inf], np.nan).dropna()
    valid = (out[["open", "high", "low", "close"]] > 0).all(axis=1) & (out["high"] >= out["low"]) & (out["volume"] >= 0)
    return out[valid]


def _pivots(values: np.ndarray, order: int, kind: str) -> list[int]:
    """Yerel tepe ("high") veya dip ("low") indeksleri. Sağ tarafında `order` mum yoksa pivot henüz doğrulanmamıştır, sayılmaz."""
    found = []
    for i in range(order, len(values) - order):
        window_left, window_right, value = values[i - order:i], values[i + 1:i + order + 1], values[i]
        if kind == "high" and value > window_left.max() and value >= window_right.max():
            found.append(i)
        if kind == "low" and value < window_left.min() and value <= window_right.min():
            found.append(i)
    return found


def _pct(a: float, b: float) -> float:
    """a'nın b'ye göre yüzde farkı (b = 0 ise 0)."""
    return (a - b) / b * 100.0 if b else 0.0


# --------------------------------------------------------------------------- 1) trend
def _trend(df: pd.DataFrame, p: dict) -> dict:
    """Trend: 4 sinyal oylanır (EMA ilişkisi, fiyatın EMA'lara göre yeri, tepe/dip dizilimi, regresyon eğimi).
    Sinyaller çelişiyorsa yön VERİLMEZ ("yatay", conflict=True)."""
    close = df["close"]
    price = float(close.iloc[-1])
    fast = float(close.ewm(span=p["ema_fast"], adjust=False).mean().iloc[-1])
    slow = float(close.ewm(span=p["ema_slow"], adjust=False).mean().iloc[-1])
    signals, notes = {}, {}

    gap = _pct(fast, slow)  # 1) EMA50 / EMA200
    signals["ema"] = 1 if gap > p["ema_gap_min_pct"] else -1 if gap < -p["ema_gap_min_pct"] else 0
    notes["ema"] = {1: f"EMA{p['ema_fast']} > EMA{p['ema_slow']}", -1: f"EMA{p['ema_fast']} < EMA{p['ema_slow']}",
                    0: f"EMA{p['ema_fast']} ile EMA{p['ema_slow']} birbirine yakın"}[signals["ema"]]

    top, bottom = max(fast, slow), min(fast, slow)  # 2) fiyatın EMA'lara göre yeri
    signals["price"] = 1 if price > top else -1 if price < bottom else 0
    notes["price"] = {1: "fiyat EMA'ların üzerinde", -1: "fiyat EMA'ların altında", 0: "fiyat EMA'ların arasında"}[signals["price"]]

    tail = df.iloc[-p["structure_window"]:]  # 3) son pivotlarda tepe/dip dizilimi (HH/HL, LH/LL)
    highs = _pivots(tail["high"].to_numpy(), p["pivot_order"], "high")
    lows = _pivots(tail["low"].to_numpy(), p["pivot_order"], "low")
    signals["structure"], notes["structure"] = 0, "tepe/dip dizilimi belirsiz"
    if len(highs) >= 2 and len(lows) >= 2:
        h1, h2 = tail["high"].iloc[highs[-2]], tail["high"].iloc[highs[-1]]
        l1, l2 = tail["low"].iloc[lows[-2]], tail["low"].iloc[lows[-1]]
        tol = p["structure_tolerance_pct"]
        higher_high, lower_high = _pct(h2, h1) > tol, _pct(h2, h1) < -tol
        higher_low, lower_low = _pct(l2, l1) > tol, _pct(l2, l1) < -tol
        if higher_high and higher_low:
            signals["structure"], notes["structure"] = 1, "daha yüksek tepeler ve dipler"
        elif lower_high and lower_low:
            signals["structure"], notes["structure"] = -1, "daha düşük tepeler ve dipler"

    window = close.iloc[-p["trend_regression_window"]:].to_numpy()  # 4) lineer regresyon eğimi (log fiyat)
    slope = float(np.polyfit(np.arange(len(window)), np.log(window), 1)[0]) if len(window) >= 3 else 0.0
    change = (math.exp(slope * (len(window) - 1)) - 1) * 100.0
    signals["slope"] = 1 if change > p["trend_slope_min_pct"] else -1 if change < -p["trend_slope_min_pct"] else 0
    notes["slope"] = {1: "regresyon eğimi yukarı", -1: "regresyon eğimi aşağı", 0: "regresyon eğimi yatay"}[signals["slope"]]

    up, down = sum(v == 1 for v in signals.values()), sum(v == -1 for v in signals.values())
    conflict = up > 0 and down > 0
    if up >= p["trend_min_agree"] and down <= p["trend_max_conflict"]:
        direction, wanted = "yükselen", 1
    elif down >= p["trend_min_agree"] and up <= p["trend_max_conflict"]:
        direction, wanted = "düşen", -1
    else:
        direction, wanted = "yatay", 0
    if wanted:
        reasons = [notes[k] for k, v in signals.items() if v == wanted]
    elif conflict:
        reasons = ["sinyaller birbiriyle çelişiyor"]
    else:
        reasons = ["belirgin bir yön sinyali yok"]
    return dict(direction=direction, conflict=conflict and not wanted, reasons=reasons, signals=signals,
                ema_fast=fast, ema_slow=slow, slope_change_pct=change)


# --------------------------------------------------------------------------- 2) destek / direnç
def _support_resistance(df: pd.DataFrame, p: dict) -> tuple[dict | None, dict | None]:
    """Pivot tepe/diplerini fiyat yüzdesiyle kümeler; kümeyi dokunuş sayısı, yenilik ve hacimle skorlar.
    Fiyatın altındaki en güçlü küme destek, üstündeki en güçlü küme dirençtir (yoksa None)."""
    tail = df.iloc[-p["structure_window"]:]
    n, price = len(tail), float(tail["close"].iloc[-1])
    volume = tail["volume"].to_numpy()
    mean_volume = float(volume.mean()) if n else 0.0
    points = []
    for kind, column in (("high", "high"), ("low", "low")):
        values = tail[column].to_numpy()
        for i in _pivots(values, p["pivot_order"], kind):
            local = volume[max(i - 1, 0):i + 2].mean()
            points.append((float(values[i]), i, float(local / mean_volume) if mean_volume > 0 else 1.0))
    clusters: list[list[tuple]] = []
    for point in sorted(points):
        if clusters:
            level = float(np.mean([q[0] for q in clusters[-1]]))
            if abs(point[0] - level) / level * 100.0 <= p["sr_tolerance_pct"]:
                clusters[-1].append(point)
                continue
        clusters.append([point])
    w_touch, w_recent, w_volume = (w / sum(p["sr_weights"]) for w in p["sr_weights"])
    levels = []
    for cluster in clusters:
        touches = len(cluster)
        if touches < p["sr_min_touches"]:
            continue
        recency = 1.0 - (n - 1 - max(q[1] for q in cluster)) / max(n - 1, 1)
        volume_norm = min(float(np.mean([q[2] for q in cluster])) / p["sr_volume_cap"], 1.0)
        strength = (w_touch * min(touches / p["sr_touch_cap"], 1.0) + w_recent * recency + w_volume * volume_norm)
        levels.append(dict(price=float(np.mean([q[0] for q in cluster])), touches=touches, strength=round(float(strength), 4)))
    margin = p["sr_tolerance_pct"] / 2.0 / 100.0  # fiyata tolerans/2'den yakın seviye ne destek ne direnç sayılır
    reach = p["sr_max_distance_pct"] / 100.0
    below = [lv for lv in levels if price * (1 - reach) <= lv["price"] < price * (1 - margin)]
    above = [lv for lv in levels if price * (1 + margin) < lv["price"] <= price * (1 + reach)]
    support = max(below, key=lambda lv: (lv["strength"], lv["price"]), default=None)
    resistance = max(above, key=lambda lv: (lv["strength"], -lv["price"]), default=None)
    return support, resistance


# --------------------------------------------------------------------------- 3) formasyonlar
def _line(idx: list[int], prices: list[float]) -> tuple[float, float]:
    """Pivotlara lineer regresyonla doğru uydurur: (eğim, kesişim)."""
    slope, intercept = np.polyfit(np.asarray(idx, float), np.asarray(prices, float), 1)
    return float(slope), float(intercept)


def _line_quality(idx: list[int], prices: list[float], slope: float, intercept: float, mean_price: float, tol_pct: float) -> float | None:
    """Pivotların doğruya yakınlığı (0-1). Herhangi biri 2x toleransın dışındaysa None (çizgi güvenilmez)."""
    resid = np.abs(np.asarray(prices) - (slope * np.asarray(idx, float) + intercept)) / mean_price * 100.0
    if resid.max() > 2 * tol_pct:
        return None
    return float(max(0.0, 1.0 - resid.mean() / tol_pct))


def _converging_patterns(tail: pd.DataFrame, highs: list[int], lows: list[int], p: dict) -> list[dict]:
    """Üçgen ve kama adayları. Formasyon TEK bir başlangıç anıyla tanımlanır: o andan sonraki tüm tepeler ve tüm dipler ayrı ayrı doğruya
    oturtulur (çizgi başına farklı pivot seçilip "tesadüfen" yakınsayan doğru aranmaz). Eğim, yakınsama, kanal içerme ve pivot sayısına göre sınıflanır."""
    n, end = len(tail), len(tail) - 1
    mean_price = float(tail["close"].mean())
    high_values, low_values = tail["high"].to_numpy(), tail["low"].to_numpy()
    price = float(tail["close"].iloc[-1])
    flat, steep = p["pattern_flat_change_pct"], p["pattern_slope_min_change_pct"]
    candidates = []
    for start in sorted(set(highs + lows)):  # başlangıç adayları pivotların kendi konumlarıdır
        h_idx, l_idx = [i for i in highs if i >= start], [i for i in lows if i >= start]
        kh, kl = len(h_idx), len(l_idx)
        if min(kh, kl) < p["pattern_min_pivots_per_line"] or kh + kl < p["pattern_min_total_pivots"]:
            continue
        if end - start < p["pattern_min_span"]:
            continue
        h_prices, l_prices = [float(high_values[i]) for i in h_idx], [float(low_values[i]) for i in l_idx]
        hs, hb = _line(h_idx, h_prices)
        ls, lb = _line(l_idx, l_prices)
        hq = _line_quality(h_idx, h_prices, hs, hb, mean_price, p["pattern_line_tolerance_pct"])
        lq = _line_quality(l_idx, l_prices, ls, lb, mean_price, p["pattern_line_tolerance_pct"])
        if hq is None or lq is None:
            continue  # pivotlar doğrunun üzerinde değil: çizgi güvenilmez
        upper0, upper1 = hs * start + hb, hs * end + hb
        lower0, lower1 = ls * start + lb, ls * end + lb
        width0, width1 = upper0 - lower0, upper1 - lower1
        if width0 <= 0 or width1 <= 0:
            continue  # çizgiler kesişmiş
        ratio = width1 / width0  # son aralık / ilk aralık: 1'den küçükse yakınsıyor
        if not (p["pattern_converge_min"] <= ratio <= p["pattern_converge_max"]):
            continue
        slack = price * p["pattern_breakout_tolerance_pct"] / 100.0
        if not (lower1 - slack <= price <= upper1 + slack):
            continue  # fiyat kanalın dışında: formasyon bitmiş (kırılmış)
        xs = np.arange(start, end + 1)  # gerçek bir kanalda fiyat çizgilerin içinde kalır; rastgele dalgalanma çizgileri sık deler
        margin = p["pattern_containment_tol_pct"] / 100.0
        inside = (high_values[start:] <= (hs * xs + hb) * (1 + margin)) & (low_values[start:] >= (ls * xs + lb) * (1 - margin))
        if float(inside.mean()) < p["pattern_containment_min"]:
            continue
        up_chg, low_chg = (upper1 - upper0) / mean_price * 100.0, (lower1 - lower0) / mean_price * 100.0  # süre boyunca % değişim
        kind = None
        if abs(up_chg) <= flat and low_chg >= steep:
            kind = "ascending_triangle"      # direnç yatay, dipler yükseliyor
        elif abs(low_chg) <= flat and up_chg <= -steep:
            kind = "descending_triangle"     # destek yatay, tepeler alçalıyor
        elif up_chg <= -steep and low_chg >= steep:
            kind = "symmetrical_triangle"    # tepeler alçalıyor, dipler yükseliyor
        elif up_chg >= steep and low_chg >= steep:
            kind = "rising_wedge"            # ikisi de yükseliyor ama daralıyor
        elif up_chg <= -steep and low_chg <= -steep:
            kind = "falling_wedge"           # ikisi de düşüyor ama daralıyor
        if kind is None:
            continue

        def shape(change: float, is_flat: bool) -> float:  # çizginin ideal şekle uyumu (0-1)
            return max(0.0, 1.0 - abs(change) / flat) if is_flat else min(1.0, abs(change) / (2 * steep))
        geometry = (shape(up_chg, kind == "ascending_triangle") + shape(low_chg, kind == "descending_triangle")) / 2
        convergence = (p["pattern_converge_max"] - ratio) / (p["pattern_converge_max"] - p["pattern_converge_min"])
        pivots_score = min(1.0, (kh + kl) / p["pattern_pivot_target"])
        recency = max(0.0, 1.0 - (end - max(h_idx[-1], l_idx[-1])) / (n * 0.5))
        score = 0.35 * (hq + lq) / 2 + 0.2 * pivots_score + 0.2 * geometry + 0.15 * convergence + 0.1 * recency
        candidates.append(dict(name=kind, score=round(float(score), 4), status="forming",
                               levels=dict(upper=float(upper1), lower=float(lower1)), pivots=dict(highs=kh, lows=kl),
                               span=int(end - start), width_ratio=round(float(ratio), 3)))
    return candidates


def _double_patterns(tail: pd.DataFrame, highs: list[int], lows: list[int], p: dict) -> list[dict]:
    """Çift tepe (iki benzer zirve + aradaki çukur) ve çift dip (iki benzer çukur + aradaki tepe) adayları.
    Zirve seviyesi pencerede TAM İKİ kez test edilmiş olmalı: üçüncü bir dokunuş yatay bir aralığı (range) gösterir, çift formasyon değil.
    İlk zirveye belirgin bir yükseliş/düşüş gelmiş olmalı (yatay salınım çift formasyon sayılmaz)."""
    n = len(tail)
    price = float(tail["close"].iloc[-1])
    tol = p["double_tolerance_pct"] / 100.0
    candidates = []
    for kind, pivots, values, opposite in (("double_top", highs, tail["high"].to_numpy(), tail["low"].to_numpy()),
                                           ("double_bottom", lows, tail["low"].to_numpy(), tail["high"].to_numpy())):
        top = kind == "double_top"
        extreme = values.max() if top else values.min()  # zirveler pencerenin en uç bölgesinde olmalı
        for a in range(len(pivots)):
            for b in range(a + 1, len(pivots)):
                i, j = pivots[a], pivots[b]
                v1, v2 = float(values[i]), float(values[j])
                mean = (v1 + v2) / 2
                if j - i < p["double_min_gap"] or abs(v1 - v2) / mean > tol:
                    continue
                if (top and extreme > max(v1, v2) * (1 + tol)) or (not top and extreme < min(v1, v2) * (1 - tol)):
                    continue
                if sum(abs(float(values[k]) - mean) / mean <= tol for k in pivots) != 2:
                    continue  # aynı seviyede 3+ zirve: yatay aralık
                before = opposite[max(0, i - p["double_prior_lookback"]):i]  # ilk zirveye gelen hareket (trend bağlamı)
                if len(before) < 5:
                    continue
                prior = (v1 - float(before.min())) / v1 * 100.0 if top else (float(before.max()) - v1) / float(before.max()) * 100.0
                if prior < p["double_min_prior_move_pct"]:
                    continue  # önceden belirgin yükseliş/düşüş yok: yatay salınım
                neck = float(opposite[i:j + 1].min() if top else opposite[i:j + 1].max())
                depth = abs(mean - neck) / mean * 100.0
                if depth < p["double_min_depth_pct"]:
                    continue
                if (top and price > max(v1, v2) * (1 + tol)) or (not top and price < min(v1, v2) * (1 - tol)):
                    continue  # fiyat zirvelerin ötesine geçmiş: formasyon geçersiz
                broken = price < neck * (1 - tol) if top else price > neck * (1 + tol)
                similarity = max(0.0, 1.0 - abs(v1 - v2) / mean / tol)
                depth_score = min(1.0, depth / (2 * p["double_min_depth_pct"]))
                gap_score = min(1.0, (j - i) / (2 * p["double_min_gap"]))
                recency = max(0.0, 1.0 - (n - 1 - j) / (n * 0.5))
                score = 0.4 * similarity + 0.25 * depth_score + 0.15 * gap_score + 0.2 * recency
                candidates.append(dict(name=kind, score=round(float(score), 4), status="confirmed" if broken else "forming",
                                       levels=dict(peak=float(mean), neckline=neck), pivots=dict(first=int(i), second=int(j)),
                                       span=int(n - 1 - i)))
    return candidates


def _pattern(df: pd.DataFrame, p: dict) -> dict | None:
    """En yüksek güven skorlu formasyon (skor eşiğin altındaysa veya aday yoksa None; formasyon uydurulmaz)."""
    tail = df.iloc[-p["pattern_window"]:]
    highs = _pivots(tail["high"].to_numpy(), p["pattern_pivot_order"], "high")
    lows = _pivots(tail["low"].to_numpy(), p["pattern_pivot_order"], "low")
    candidates = _converging_patterns(tail, highs, lows, p) + _double_patterns(tail, highs, lows, p)
    candidates = [c for c in candidates if c["score"] >= p["pattern_min_score"]]
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c["score"])
    best["label"], best["bias"] = PATTERNS[best["name"]]
    return best


# --------------------------------------------------------------------------- dış arayüz: hesaplama
def analyze(df: pd.DataFrame, timeframe: str, params: dict | None = None) -> dict:
    """Bir zaman dilimi için grafik analizi. HİÇBİR durumda veri kaynaklı istisna fırlatmaz (bozuk/eksik/kısa veri için status döner).

    Dönen sözlük: timeframe, status ("ok" | "insufficient" | "invalid"), reason, price, as_of,
    trend{direction, conflict, reasons, signals, ...}, support/resistance{price, touches, strength} | None, pattern{...} | None."""
    p = merge_params(params)  # yapılandırma hatası (bilinmeyen eşik) bilerek fırlatılır; veri hatası değil
    base = dict(timeframe=timeframe, status="invalid", reason=None, price=None, as_of=None, trend=None,
                support=None, resistance=None, pattern=None)
    try:
        if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0:
            return {**base, "reason": "veri yok"}
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            return {**base, "reason": f"eksik sütun: {', '.join(missing)}"}
        clean = _clean(df)
        if len(clean) < p["min_candles"]:
            return {**base, "status": "insufficient", "reason": f"yeterli mum yok ({len(clean)} < {p['min_candles']})"}
        price = float(clean["close"].iloc[-1])
        support, resistance = _support_resistance(clean, p)
        return {**base, "status": "ok", "price": price, "as_of": str(clean.index[-1]), "trend": _trend(clean, p),
                "support": support, "resistance": resistance, "pattern": _pattern(clean, p)}
    except Exception as e:  # noqa: BLE001  beklenmeyen hesaplama hatası: rapor bozulmasın
        return {**base, "reason": f"hesaplama hatası: {type(e).__name__}: {e}"}


# --------------------------------------------------------------------------- 5) metin üretimi (şablonlar)
def format_price(value: float | None) -> str:
    """3 anlamlı basamağa yuvarlar, Türkçe biçimler: 94213 -> '94.200', 4567.8 -> '4.570', 12.345 -> '12,3', 0.0345 -> '0,0345'."""
    if value is None or not math.isfinite(value) or value <= 0:
        return "-"
    decimals = max(0, 2 - math.floor(math.log10(value)))
    rounded = round(value, decimals) if decimals else float(round(value, 2 - math.floor(math.log10(value))))
    if rounded >= 1000:
        return f"{rounded:,.0f}".replace(",", ".")
    return f"{rounded:.{decimals}f}".replace(".", ",")


def _trend_phrase(trend: dict, variant: int, compact: bool) -> str:
    reasons = ", ".join(trend["reasons"])
    detail = "" if compact else f" ({reasons})"
    if trend["direction"] == "yükselen":
        return ("yükselen trend içinde" if variant % 2 == 0 else "yükseliş trendinde ilerliyor") + detail
    if trend["direction"] == "düşen":
        return ("düşen trend içinde" if variant % 2 == 0 else "düşüş trendinde ilerliyor") + detail
    if trend["conflict"]:
        return "kararsız bir görünümde" + ("" if compact else " (sinyaller birbiriyle çelişiyor)")
    return "yatay seyrediyor" + ("" if compact else f" ({reasons})")


def _pattern_sentence(pattern: dict, variant: int, compact: bool) -> str:
    name, label, bias, lv = pattern["name"], pattern["label"], pattern["bias"], pattern["levels"]
    lead = f"{label} formasyonu oluşuyor ({bias})" if variant % 2 == 0 else f"Grafikte {label.lower()} formasyonu şekilleniyor ({bias})"
    if compact:
        return lead + "."
    detail = {
        "ascending_triangle": f"direnç yatay ({format_price(lv.get('upper'))}), dipler yükseliyor",
        "descending_triangle": f"destek yatay ({format_price(lv.get('lower'))}), tepeler alçalıyor",
        "symmetrical_triangle": "tepeler alçalıyor, dipler yükseliyor; kırılım yönü belirsiz",
        "rising_wedge": "tepeler ve dipler yükseliyor ama aralık daralıyor",
        "falling_wedge": "tepeler ve dipler düşüyor ama aralık daralıyor",
    }.get(name)
    if name in ("double_top", "double_bottom"):
        kind = "tepe" if name == "double_top" else "dip"
        detail = f"iki {kind} {format_price(lv['peak'])} civarında, boyun çizgisi {format_price(lv['neckline'])}"
        if pattern["status"] == "confirmed":
            detail += " ve boyun çizgisi kırıldı"
    return f"{lead}: {detail}."


def _levels_sentence(support: dict | None, resistance: dict | None, strong_score: float) -> str:
    def name(level: dict, word: str) -> str:
        return f"güçlü {word}" if level["strength"] >= strong_score else word
    if support and resistance:
        return (f"{name(support, 'destek').capitalize()}: {format_price(support['price'])}, "
                f"{name(resistance, 'direnç')}: {format_price(resistance['price'])}.")
    if support:
        return f"{name(support, 'destek').capitalize()}: {format_price(support['price'])}; üstte belirgin bir direnç seviyesi tespit edilmedi."
    if resistance:
        return f"{name(resistance, 'direnç').capitalize()}: {format_price(resistance['price'])}; altta belirgin bir destek seviyesi tespit edilmedi."
    return "Belirgin bir destek/direnç seviyesi tespit edilmedi."


def build_comment(result: dict, asset_name: str = "Bitcoin", variant: int = 0, compact: bool = False,
                  strong_score: float | None = None) -> str:
    """Tek zaman dilimi sonucunu kısa, akıcı Türkçe metne çevirir. `variant` ifade çeşitliliği için (aynı girdi = aynı metin).
    compact=True: gerekçe ve ayrıntıları atan kısa sürüm (mesaj uzunluk sınırı için)."""
    label = TIMEFRAME_LABELS.get(result.get("timeframe"), str(result.get("timeframe")))
    if result.get("status") != "ok":
        return f"{label.capitalize()} grafik için yorum üretilemedi ({result.get('reason') or 'veri yetersiz'})."
    strong = DEFAULT_PARAMS["sr_strong_score"] if strong_score is None else strong_score
    opening = f"{asset_name} şu an {label} grafikte {_trend_phrase(result['trend'], variant, compact)}."
    pattern = (_pattern_sentence(result["pattern"], variant, compact) if result["pattern"]
               else "Belirgin bir formasyon tespit edilmedi.")
    return " ".join([opening, pattern, _levels_sentence(result["support"], result["resistance"], strong)])


def build_report(results: list[dict], asset_name: str = "Bitcoin", compact: bool = False, strong_score: float | None = None) -> str:
    """"Grafik Yorumu" bölümü: başlık + her zaman dilimi için ayrı paragraf + tek satır not.
    Hiçbir zaman dilimi üretilemediyse yalnızca 'Grafik yorumu şu an üretilemedi.' satırı döner."""
    if not results or not any(r.get("status") == "ok" for r in results):
        return f"{HEADING}\n{UNAVAILABLE}"
    paragraphs = [build_comment(r, asset_name, variant=i, compact=compact, strong_score=strong_score) for i, r in enumerate(results)]
    return "\n\n".join([HEADING, *paragraphs, DISCLAIMER])
