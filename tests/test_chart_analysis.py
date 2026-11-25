"""Grafik Yorumu (core/chart_analysis.py) testleri: ağ bağlantısı gerektirmez. Çalıştır: python tests/test_chart_analysis.py
(Fonksiyonlar `test_` ile başladığı için `pytest tests/test_chart_analysis.py` ile de çalışır.)"""
import copy
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import chart_analysis as ca
from core.chart_analysis import DEFAULT_PARAMS, analyze, build_comment, build_report, format_price, merge_params


# ------------------------------------------------------------------ sentetik veri üreticileri (belirli tohumla, tekrarlanabilir)
def make_ohlcv(close, seed=0, wick=0.002, freq="4h", volume=None):
    rng = np.random.default_rng(seed)
    close = np.asarray(close, float)
    n = len(close)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + wick * rng.uniform(0.2, 1.0, n))
    low = np.minimum(open_, close) * (1 - wick * rng.uniform(0.2, 1.0, n))
    vol = np.abs(1000 * (1 + 0.3 * rng.standard_normal(n))) if volume is None else np.asarray(volume, float)
    index = pd.date_range("2025-01-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(dict(open=open_, high=high, low=low, close=close, volume=vol), index=index)


def trend_df(drift, n=400, seed=1, amplitude=0.012):
    """Düz yükselen (drift > 0), düz düşen (drift < 0) veya yatay (drift = 0): üstel gidiş + küçük dalgalanma."""
    i = np.arange(n)
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(drift * i) * (1 + amplitude * np.sin(2 * np.pi * i / 40)) * (1 + 0.001 * rng.standard_normal(n))
    return make_ohlcv(close, seed)


def sideways_df(n=400, seed=3):
    i = np.arange(n)
    rng = np.random.default_rng(seed)
    return make_ohlcv(100 * (1 + 0.02 * np.sin(2 * np.pi * i / 30)) * (1 + 0.001 * rng.standard_normal(n)), seed)


def formation_df(upper, lower, n_pre=310, n_form=90, period=15, seed=5, pre="flat"):
    """Son n_form mum, (upper) ve (lower) doğruları arasında salınır: zirveler üst çizgiye, dipler alt çizgiye değer."""
    rng = np.random.default_rng(seed)
    t = np.arange(n_form)
    up, lo = np.interp(t, [0, n_form - 1], upper), np.interp(t, [0, n_form - 1], lower)
    form = (up + lo) / 2 + (up - lo) / 2 * np.sin(2 * np.pi * t / period + 0.5)
    pre_i = np.arange(n_pre)
    wave = 1 + 0.004 * np.sin(2 * np.pi * pre_i / 23)
    pre_close = {"flat": form[0] * (1 + 0.003 * np.sin(2 * np.pi * pre_i / 23)),
                 "up": form[0] * np.exp(np.linspace(-0.25, 0, n_pre)) * wave,
                 "down": form[0] * np.exp(np.linspace(0.25, 0, n_pre)) * wave}[pre]
    close = np.r_[pre_close, form] * (1 + 0.0005 * rng.standard_normal(n_pre + n_form))
    return make_ohlcv(close, seed)


def double_top_df(n=400, seed=7, peak=110.0, valley=100.0, tail=0.975):
    rng = np.random.default_rng(seed)
    pre = 100 * (1 + 0.004 * np.sin(2 * np.pi * np.arange(n - 70) / 25))
    leg = np.interp(np.arange(70), [0, 15, 35, 55, 69], [100.5, peak, valley + 3, peak * 1.002, peak * tail])
    return make_ohlcv(np.r_[pre, leg] * (1 + 0.0008 * rng.standard_normal(n)), seed)


def double_bottom_df(n=400, seed=8, low=100.0, mid=110.0):
    rng = np.random.default_rng(seed)
    pre = 111 * (1 + 0.004 * np.sin(2 * np.pi * np.arange(n - 70) / 25))
    leg = np.interp(np.arange(70), [0, 15, 35, 55, 69], [110.5, low, mid - 3, low * 0.998, low * 1.025])
    return make_ohlcv(np.r_[pre, leg] * (1 + 0.0008 * rng.standard_normal(n)), seed)


def random_walk_df(seed, n=400, vol=0.006, drift=0.0):
    rng = np.random.default_rng(seed)
    return make_ohlcv(100 * np.exp(np.cumsum(rng.normal(drift, vol, n))), seed)


def range_df(low=100.0, high=120.0, n=400, period=20, seed=4, volume=None):
    i = np.arange(n)
    close = (low + high) / 2 + (high - low) / 2 * np.sin(2 * np.pi * i / period)
    return make_ohlcv(close * (1 + 0.0004 * np.random.default_rng(seed).standard_normal(n)), seed, volume=volume)


PATTERN_CASES = {
    "ascending_triangle": (lambda s: formation_df([110, 110], [100, 108.5], seed=s), "bullish"),
    "descending_triangle": (lambda s: formation_df([110, 101.5], [100, 100], seed=s), "bearish"),
    "symmetrical_triangle": (lambda s: formation_df([112, 106], [98, 102], seed=s), "nötr"),
    "rising_wedge": (lambda s: formation_df([106, 113], [100, 111], seed=s, pre="up"), "bearish"),
    "falling_wedge": (lambda s: formation_df([120, 108], [110, 104], seed=s, pre="down"), "bullish"),
    "double_top": (lambda s: double_top_df(seed=s), "bearish"),
    "double_bottom": (lambda s: double_bottom_df(seed=s), "bullish"),
}


# ------------------------------------------------------------------ 1) trend
def test_trend_direction_on_known_synthetic_data():
    up, down, flat = analyze(trend_df(0.0015), "4h"), analyze(trend_df(-0.0015, seed=2), "4h"), analyze(sideways_df(), "4h")
    for result in (up, down, flat):
        assert result["status"] == "ok" and result["reason"] is None and result["timeframe"] == "4h"
    assert up["trend"]["direction"] == "yükselen" and not up["trend"]["conflict"]
    assert "EMA50 > EMA200" in up["trend"]["reasons"] and "daha yüksek tepeler ve dipler" in up["trend"]["reasons"]
    assert down["trend"]["direction"] == "düşen" and "EMA50 < EMA200" in down["trend"]["reasons"]
    assert "regresyon eğimi aşağı" in down["trend"]["reasons"] and "fiyat EMA'ların altında" in down["trend"]["reasons"]
    # son iki dip birbirine çok yakınsa (eşit dip) tepe/dip dizilimi sinyali bilerek "belirsiz" kalır; yön yine de 3 sinyalle verilir
    assert down["trend"]["signals"]["structure"] in (-1, 0) and 1 not in down["trend"]["signals"].values()
    assert flat["trend"]["direction"] == "yatay" and not flat["trend"]["conflict"]
    assert all(v == 1 for v in up["trend"]["signals"].values())
    assert up["trend"]["slope_change_pct"] > 2 > -2 > down["trend"]["slope_change_pct"]
    # yön için en az 3 sinyal aynı yönde ve hiçbiri ters olmamalı
    assert sum(v == 1 for v in up["trend"]["signals"].values()) >= DEFAULT_PARAMS["trend_min_agree"]


def conflicting_df():
    """Uzun yükseliş, ardından 12 mumda %35 sert düşüş: EMA50 hâlâ EMA200 üstünde (+1) ama fiyat EMA'ların altında (-1) ve eğim aşağı (-1)."""
    n, drop_len, drop_pct = 400, 12, 0.35
    i = np.arange(n)
    start = n - drop_len
    close = 100 * np.exp(0.003 * np.minimum(i, start)) * np.where(i >= start, np.exp(np.log(1 - drop_pct) * (i - start + 1) / drop_len), 1.0)
    return make_ohlcv(close * (1 + 0.001 * np.random.default_rng(9).standard_normal(n)))


def test_conflicting_signals_are_reported_as_undecided_not_forced():
    result = analyze(conflicting_df(), "4h")
    trend = result["trend"]
    assert trend["signals"]["ema"] == 1 and trend["signals"]["price"] == -1 and trend["signals"]["slope"] == -1, trend["signals"]
    assert trend["direction"] == "yatay" and trend["conflict"] is True and trend["reasons"] == ["sinyaller birbiriyle çelişiyor"]
    assert "kararsız bir görünümde (sinyaller birbiriyle çelişiyor)" in build_comment(result)
    assert "kararsız bir görünümde." in build_comment(result, compact=True)
    # çelişki toleransı gevşetilirse yön verilebilir (eşikler gerçekten kullanılıyor): 2 sinyal aşağı, 1 yukarı
    loose = analyze(conflicting_df(), "4h", {"trend_min_agree": 2, "trend_max_conflict": 1})["trend"]
    assert loose["direction"] == "düşen" and not loose["conflict"]


# ------------------------------------------------------------------ 2) destek / direnç
def test_support_and_resistance_found_in_a_range():
    result = analyze(range_df(100, 120), "4h")
    price, support, resistance = result["price"], result["support"], result["resistance"]
    assert support and resistance and support["price"] < price < resistance["price"]
    assert abs(support["price"] - 100) / 100 < 0.015 and abs(resistance["price"] - 120) / 120 < 0.015
    for level in (support, resistance):
        assert level["touches"] >= DEFAULT_PARAMS["sr_min_touches"] and 0.0 <= level["strength"] <= 1.0
    comment = build_comment(result)
    assert "destek" in comment.lower() and format_price(support["price"]) in comment and format_price(resistance["price"]) in comment


def test_missing_levels_return_none_and_the_text_says_so():
    result = analyze(trend_df(0.003, seed=11, amplitude=0.004), "4h")  # sert yükseliş: fiyat zirvede, üstte seviye yok
    assert result["resistance"] is None
    text = build_comment(result)
    assert "üstte belirgin bir direnç seviyesi tespit edilmedi" in text or "Belirgin bir destek/direnç seviyesi tespit edilmedi" in text
    both_missing = dict(result, support=None, resistance=None)
    assert "Belirgin bir destek/direnç seviyesi tespit edilmedi." in build_comment(both_missing)


def test_far_away_levels_are_not_reported():
    # eski bir dip 40'ta iki kez test edilmiş; fiyat sonra düzenli yükselip 100'e gelmiş (arada tekrar eden başka seviye yok)
    close = np.r_[np.interp(np.arange(121), [0, 30, 60, 90, 120], [60, 40, 70, 40, 80]), np.linspace(80, 100, 330)[1:]]  # plato yok: düzenli yükseliş
    df = make_ohlcv(close * (1 + 0.0005 * np.random.default_rng(2).standard_normal(len(close))), seed=2)
    default = analyze(df, "1w", {"structure_window": 450})
    assert default["support"] is None, "fiyatın %60 altındaki eski seviye raporlanmamalı"
    far = analyze(df, "1w", {"structure_window": 450, "sr_max_distance_pct": 1000.0})["support"]
    assert far and abs(far["price"] - 40) < 2 and far["touches"] >= 2, "eşik gevşetilince aynı seviye bulunur"
    assert "altta belirgin bir destek seviyesi tespit edilmedi" in build_comment(default) or default["resistance"] is None
    for pct in (5.0, 25.0):  # hiçbir raporlanan seviye eşikten uzak olamaz
        result = analyze(range_df(100, 140), "4h", {"sr_max_distance_pct": pct})
        for level in (result["support"], result["resistance"]):
            assert level is None or abs(level["price"] - result["price"]) / result["price"] <= pct / 100 + 1e-9
    assert DEFAULT_PARAMS["sr_max_distance_pct"] == 25.0


def test_level_strength_uses_touches_recency_and_volume():
    # iki destek kümesi: 95 (3 dokunuş, YÜKSEK hacim, eski) ve 98 (3 dokunuş, normal hacim); güncel fiyat ~104
    knots = [104, 110, 95, 110, 95, 110, 95, 110, 98, 110, 98, 110, 98, 104]
    path = np.interp(np.arange((len(knots) - 1) * 14 + 1), np.arange(len(knots)) * 14, knots)
    n_pre = 250
    close = np.r_[np.full(n_pre, 104.0) * (1 + 0.002 * np.sin(np.arange(n_pre) / 5)), path]
    volume = np.full(len(close), 1000.0)
    for k in (2, 4, 6):  # 95 seviyesine inen pivotlarda hacim 5 kat
        volume[n_pre + k * 14 - 2:n_pre + k * 14 + 3] = 5000.0
    df = make_ohlcv(close, seed=3, volume=volume)
    only_volume = analyze(df, "4h", {"sr_weights": [0.0, 0.0, 1.0]})["support"]
    only_recency = analyze(df, "4h", {"sr_weights": [0.0, 1.0, 0.0]})["support"]
    assert only_volume and abs(only_volume["price"] - 95) < 1.0, only_volume
    assert only_recency and abs(only_recency["price"] - 98) < 1.0, only_recency
    # tolerans büyütülünce yakın seviyeler tek kümede birleşir (dokunuş sayısı artar)
    merged = analyze(df, "4h", {"sr_tolerance_pct": 4.0})["support"]
    assert merged is None or merged["touches"] >= only_volume["touches"]


# ------------------------------------------------------------------ 3) formasyonlar
def test_every_supported_pattern_is_detected_with_the_right_direction():
    for name, (make, bias) in PATTERN_CASES.items():
        for seed in (1, 2, 3):
            result = analyze(make(seed), "4h")
            pattern = result["pattern"]
            assert pattern and pattern["name"] == name, (name, seed, pattern and pattern["name"])
            assert pattern["bias"] == bias and pattern["label"] == ca.PATTERNS[name][0]
            assert DEFAULT_PARAMS["pattern_min_score"] <= pattern["score"] <= 1.0
            assert "Belirgin bir formasyon tespit edilmedi" not in build_comment(result)


def test_pattern_levels_and_texts():
    asc = analyze(formation_df([110, 110], [100, 108.5]), "4h")["pattern"]
    assert abs(asc["levels"]["upper"] - 110) / 110 < 0.015 and asc["status"] == "forming" and asc["pivots"]["highs"] >= 3
    top = analyze(double_top_df(), "4h")["pattern"]
    assert abs(top["levels"]["peak"] - 110) / 110 < 0.015 and 98 < top["levels"]["neckline"] < 106 and top["status"] == "forming"
    texts = {name: build_comment(analyze(make(1), "4h")) for name, (make, _) in PATTERN_CASES.items()}
    assert "Yükselen üçgen formasyonu oluşuyor (bullish): direnç yatay" in texts["ascending_triangle"]
    assert "Alçalan üçgen formasyonu oluşuyor (bearish): destek yatay" in texts["descending_triangle"]
    assert "Simetrik üçgen formasyonu oluşuyor (nötr)" in texts["symmetrical_triangle"] and "kırılım yönü belirsiz" in texts["symmetrical_triangle"]
    assert "Yükselen kama formasyonu oluşuyor (bearish)" in texts["rising_wedge"]
    assert "Düşen kama formasyonu oluşuyor (bullish)" in texts["falling_wedge"]
    assert "Çift tepe formasyonu oluşuyor (bearish): iki tepe" in texts["double_top"] and "boyun çizgisi" in texts["double_top"]
    assert "Çift dip formasyonu oluşuyor (bullish): iki dip" in texts["double_bottom"]


def test_no_pattern_is_invented_on_clean_and_random_data():
    for df in (trend_df(0.0015), trend_df(-0.0015, seed=2), sideways_df(), range_df(100, 120)):
        assert analyze(df, "4h")["pattern"] is None
    assert "Belirgin bir formasyon tespit edilmedi." in build_comment(analyze(sideways_df(), "4h"))
    hits = sum(analyze(random_walk_df(seed), "4h")["pattern"] is not None for seed in range(200))
    assert hits / 200 <= 0.06, f"rastgele yürüyüşte yanlış pozitif oranı çok yüksek: {hits}/200"
    drift_hits = sum(analyze(random_walk_df(seed, drift=0.0008), "4h")["pattern"] is not None for seed in range(150))
    assert drift_hits / 150 <= 0.08, drift_hits


def test_broken_or_invalid_formations_are_not_reported():
    # üçgenin üst çizgisi sert kırılmışsa (fiyat kanalın dışında) formasyon yok
    base = formation_df([110, 110], [100, 108.5])
    broken = base.copy()
    for column in ("open", "high", "low", "close"):
        broken.iloc[-3:, broken.columns.get_loc(column)] *= 1.06
    assert analyze(base, "4h")["pattern"]["name"] == "ascending_triangle"
    after = analyze(broken, "4h")["pattern"]
    assert after is None or after["name"] != "ascending_triangle"
    # çift tepe: fiyat zirvelerin ötesine çıktıysa geçersiz
    top = double_top_df(tail=1.08)
    pattern = analyze(top, "4h")["pattern"]
    assert pattern is None or pattern["name"] != "double_top"
    # çift tepe: boyun çizgisi kırıldıysa "confirmed" ve metinde belirtilir
    confirmed = analyze(double_top_df(tail=0.90), "4h")  # fiyat boyun çizgisinin (~103) %1 toleransının altına iner
    still_forming = analyze(double_top_df(tail=0.93), "4h")["pattern"]
    assert still_forming["status"] == "forming", "boyun çizgisine henüz inmediyse onaylanmış sayılmaz"
    assert confirmed["pattern"]["name"] == "double_top" and confirmed["pattern"]["status"] == "confirmed"
    assert "boyun çizgisi kırıldı" in build_comment(confirmed)
    # en az pivot sayısı: gerekli pivot sayısı yükseltilirse formasyon bulunmaz
    assert analyze(base, "4h", {"pattern_min_pivots_per_line": 99})["pattern"] is None


def test_the_highest_scoring_candidate_wins_and_scores_are_bounded():
    for name, (make, _) in PATTERN_CASES.items():
        pattern = analyze(make(1), "4h")["pattern"]
        assert 0.0 < pattern["score"] <= 1.0
        stricter = analyze(make(1), "4h", {"pattern_min_score": min(pattern["score"] + 0.01, 1.0)})["pattern"]
        assert stricter is None, "skor eşiği yükselince formasyon raporlanmamalı"
        assert analyze(make(1), "4h", {"pattern_min_score": pattern["score"] - 0.01})["pattern"]["name"] == name


# ------------------------------------------------------------------ eşikler / yapılandırma
def test_thresholds_are_configurable_and_validated():
    assert merge_params(None) == DEFAULT_PARAMS and merge_params({"sr_tolerance_pct": 1.0})["sr_tolerance_pct"] == 1.0
    assert DEFAULT_PARAMS["sr_tolerance_pct"] == 0.5 and DEFAULT_PARAMS["min_candles"] == 250 and DEFAULT_PARAMS["pattern_min_score"] > 0
    for bad in ({"sr_tolerans": 1}, {"patern_min_score": 0.5}):
        try:
            analyze(range_df(), "4h", bad)
            assert False, bad
        except ValueError as e:
            assert "Bilinmeyen grafik yorumu eşiği" in str(e)
    for weights in ([1, 1], [0, 0, 0]):
        try:
            merge_params({"sr_weights": weights})
            assert False
        except ValueError:
            pass
    assert DEFAULT_PARAMS["sr_weights"] == [0.5, 0.3, 0.2] and DEFAULT_PARAMS == copy.deepcopy(DEFAULT_PARAMS)
    before = copy.deepcopy(DEFAULT_PARAMS)
    analyze(range_df(), "4h", {"sr_tolerance_pct": 9.0})
    assert DEFAULT_PARAMS == before, "varsayılan eşikler çağrılar arasında değişmemeli"
    # min_candles eşiği sınırda: 249 yetersiz, 250 yeterli
    df = range_df(n=400)
    assert analyze(df.iloc[-249:], "4h")["status"] == "insufficient" and analyze(df.iloc[-250:], "4h")["status"] == "ok"
    assert analyze(df.iloc[-100:], "4h", {"min_candles": 100})["status"] == "ok"


# ------------------------------------------------------------------ uç durumlar: hiçbiri istisna fırlatmamalı
def test_edge_cases_never_raise():
    good = range_df()
    cases = {
        "None": None, "boş": good.iloc[:0], "çok kısa": good.iloc[:10], "metin": "veri", "sözlük": {},
        "eksik sütun": good.drop(columns=["volume"]), "yalnızca close": good[["close"]],
    }
    for name, df in cases.items():
        result = analyze(df, "4h")
        assert result["status"] in ("invalid", "insufficient") and result["reason"], name
        assert result["trend"] is None and result["pattern"] is None and result["support"] is None
        text = build_comment(result)
        assert "yorum üretilemedi" in text and "4 saatlik" in text.lower() or "4 saatlik" in text, name
    nan_df = good.copy()
    nan_df.iloc[50:55, nan_df.columns.get_loc("close")] = np.nan
    assert analyze(nan_df, "4h")["status"] == "ok", "birkaç NaN satır atılır, analiz sürer"
    assert analyze(good.assign(close=np.nan), "4h")["status"] == "insufficient"
    inf_df = good.copy()
    inf_df.iloc[10, inf_df.columns.get_loc("high")] = np.inf
    assert analyze(inf_df, "4h")["status"] == "ok"
    bad_rows = good.copy()
    bad_rows.iloc[20, bad_rows.columns.get_loc("close")] = -5
    bad_rows.iloc[21, bad_rows.columns.get_loc("low")] = bad_rows.iloc[21]["high"] * 2  # high < low
    assert analyze(bad_rows, "4h")["status"] == "ok"
    flat = make_ohlcv(np.full(300, 100.0), seed=1, wick=0.0)
    flat_result = analyze(flat, "4h")
    assert flat_result["status"] == "ok" and flat_result["trend"]["direction"] == "yatay" and not flat_result["trend"]["conflict"]
    assert flat_result["pattern"] is None and flat_result["support"] is None and flat_result["resistance"] is None
    assert "yatay seyrediyor" in build_comment(flat_result)
    zero_volume = good.assign(volume=0.0)
    assert analyze(zero_volume, "4h")["status"] == "ok"
    ints = good.round(0).astype(int)
    assert analyze(ints.assign(high=ints["high"] + 1), "4h")["status"] in ("ok", "invalid")
    shuffled = good.sample(frac=1, random_state=1)
    duplicated = pd.concat([good, good.iloc[-5:]])
    base = analyze(good, "4h")
    for variant in (shuffled, duplicated):
        again = analyze(variant, "4h")
        assert again["price"] == base["price"] and again["trend"] == base["trend"] and again["support"] == base["support"]
    plain_index = good.reset_index(drop=True)
    assert analyze(plain_index, "4h")["status"] == "ok", "tarih olmayan indeks de çalışır"


def test_functions_are_pure_deterministic_and_serializable():
    df = formation_df([110, 110], [100, 108.5])
    snapshot = df.copy(deep=True)
    first, second = analyze(df, "4h"), analyze(df, "4h")
    assert df.equals(snapshot) and first == second, "girdi değişmemeli, aynı girdi aynı sonuç vermeli"
    assert json.loads(json.dumps(first)) == first, "sonuç JSON'a çevrilebilmeli (numpy tipleri yok)"
    assert build_comment(first) == build_comment(second) and build_report([first]) == build_report([second])
    start = time.time()
    for _ in range(5):
        analyze(trend_df(0.0015, n=700), "4h")
    assert (time.time() - start) / 5 < 1.0, "analiz makul sürede bitmeli"


# ------------------------------------------------------------------ 5) metin üretimi
def test_price_formatting():
    cases = {94213: "94.200", 98499.6: "98.500", 150000: "150.000", 4567.8: "4.570", 999.4: "999", 12.345: "12,3", 1.0: "1,00",
             0.0345: "0,0345", 0.5: "0,500"}
    for value, expected in cases.items():
        assert format_price(value) == expected, (value, format_price(value), expected)
    for bad in (None, 0, -5, float("nan"), float("inf")):
        assert format_price(bad) == "-"


def test_comment_text_is_natural_turkish_and_deterministic():
    result = analyze(formation_df([110, 110], [100, 108.5], pre="up"), "4h")
    full, variant, compact = build_comment(result), build_comment(result, variant=1), build_comment(result, compact=True)
    assert full.startswith("Bitcoin şu an 4 saatlik grafikte ") and "(" in full and len(full) > len(compact)
    assert full != variant and "Grafikte yükselen üçgen formasyonu şekilleniyor (bullish)" in variant
    assert build_comment(result, asset_name="Ethereum").startswith("Ethereum şu an 4 saatlik grafikte")
    assert build_comment(result) == full, "aynı girdi + aynı variant = aynı metin"
    assert "EMA" not in compact and "regresyon" not in compact, "kısa sürümde gerekçe parantezleri yok"
    for text in (full, variant, compact):
        assert text.count(".") >= 2 and text.endswith(".") and "  " not in text
    daily = build_comment(dict(result, timeframe="1d"))
    assert "günlük grafikte" in daily and "Bitcoin şu an günlük grafikte" in daily


def test_level_wording_depends_on_strength():
    base = dict(timeframe="4h", status="ok", price=96000.0, as_of="x", pattern=None,
                trend=dict(direction="yükselen", conflict=False, reasons=["EMA50 > EMA200"], signals={}))
    strong = dict(base, support=dict(price=94213.0, touches=4, strength=0.8), resistance=dict(price=98500.0, touches=3, strength=0.7))
    weak = dict(base, support=dict(price=94213.0, touches=2, strength=0.3), resistance=dict(price=98500.0, touches=2, strength=0.2))
    assert "Güçlü destek: 94.200, güçlü direnç: 98.500." in build_comment(strong)
    assert "Destek: 94.200, direnç: 98.500." in build_comment(weak) and "güçlü" not in build_comment(weak).split("Belirgin")[-1]
    assert "Güçlü destek: 94.200; üstte belirgin bir direnç seviyesi tespit edilmedi." in build_comment(dict(strong, resistance=None))
    assert "Güçlü direnç: 98.500; altta belirgin bir destek seviyesi tespit edilmedi." in build_comment(dict(strong, support=None))
    assert "Destek: 94.200, güçlü direnç" not in build_comment(strong)
    assert "Güçlü destek: 94.200, direnç: 98.500." in build_comment(strong, strong_score=0.75)  # eşik ayarlanabilir
    assert "Bitcoin şu an 4 saatlik grafikte yükselen trend içinde (EMA50 > EMA200)." in build_comment(strong)


def test_report_section_structure():
    ok4, ok1d = analyze(formation_df([110, 110], [100, 108.5]), "4h"), analyze(trend_df(-0.0015, seed=2, n=400), "1d")
    report = build_report([ok4, ok1d])
    parts = report.split("\n\n")
    assert parts[0] == "📐 Grafik Yorumu" and parts[-1] == ca.DISCLAIMER == "ℹ️ Teknik analiz kesinlik taşımaz, yatırım tavsiyesi değildir."
    assert len(parts) == 4 and "4 saatlik" in parts[1] and "günlük" in parts[2], "her periyot ayrı paragraf"
    assert report.count("Teknik analiz kesinlik taşımaz") == 1 and len(report) < 1500, "kısa ve tek not satırı"
    assert len(build_report([ok4, ok1d], compact=True)) < len(report)
    failed = analyze(None, "1d")
    mixed = build_report([ok4, failed])
    assert "4 saatlik grafikte" in mixed and "Günlük grafik için yorum üretilemedi (veri yok)." in mixed and mixed.endswith(ca.DISCLAIMER)
    for nothing in ([], [failed], [failed, analyze(None, "4h")]):
        assert build_report(nothing) == "📐 Grafik Yorumu\nGrafik yorumu şu an üretilemedi."
    assert "\r" not in report and "\t" not in report


def test_all():
    test_trend_direction_on_known_synthetic_data()
    test_conflicting_signals_are_reported_as_undecided_not_forced()
    test_support_and_resistance_found_in_a_range()
    test_missing_levels_return_none_and_the_text_says_so()
    test_far_away_levels_are_not_reported()
    test_level_strength_uses_touches_recency_and_volume()
    test_every_supported_pattern_is_detected_with_the_right_direction()
    test_pattern_levels_and_texts()
    test_no_pattern_is_invented_on_clean_and_random_data()
    test_broken_or_invalid_formations_are_not_reported()
    test_the_highest_scoring_candidate_wins_and_scores_are_bounded()
    test_thresholds_are_configurable_and_validated()
    test_edge_cases_never_raise()
    test_functions_are_pure_deterministic_and_serializable()
    test_price_formatting()
    test_comment_text_is_natural_turkish_and_deterministic()
    test_level_wording_depends_on_strength()
    test_report_section_structure()


if __name__ == "__main__":
    test_all()
    print("Grafik yorumu testleri geçti ✔")
