"""Göstergeleri (RSI, Bollinger %B ve etiketleme) sentetik veriyle test eder (internet gerekmez). Çalıştır: python tests/test_core.py"""
import contextlib
import io
import math
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import indicators as ind
from core import mvrv as mvrv_module
from core.external import align_daily
from core.indicators import (BOTTOM, EXTREME, LABELS, NEUTRAL, OVERHEATED, POINTS, STRONG_BOTTOM, BollingerPercentBIndicator,
                             FearGreedIndicator, Indicator, MFIIndicator, MVRVZScoreIndicator, RSIIndicator, bollinger_percent_b,
                             build_indicators, default_sources, money_flow_index, wilder_rsi)


def synthetic(n=1500, seed=1):
    rng = np.random.default_rng(seed)
    close = 1000 * np.exp(np.cumsum(rng.normal(0.0008, 0.03, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.02, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.02, n))
    vol = rng.uniform(1000, 5000, n)
    idx = pd.date_range("2018-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(dict(open=open_, high=high, low=low, close=close, volume=vol), index=idx)


# Wilder'ın yayınlanmış RSI örneği (kapanışlar ve yayınlanan RSI değerleri; yayınlanan fiyatlar yuvarlanmış olduğundan 0.1 pay)
WILDER_CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
                 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
                 43.42, 42.66, 43.13]
WILDER_RSI = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46, 41.87,
              45.46, 37.30, 33.08, 37.77]


def test_rsi_matches_published_wilder_example():
    rsi = wilder_rsi(pd.Series(WILDER_CLOSES), 14)
    assert rsi.iloc[:14].isna().all() and rsi.iloc[14:].notna().all(), "ilk 14 mum ısınma (NaN) olmalı"
    np.testing.assert_allclose(rsi.iloc[14:].to_numpy(), WILDER_RSI, atol=0.1)


def test_rsi_edge_cases():
    up = wilder_rsi(pd.Series(np.arange(1.0, 60.0)), 14)
    down = wilder_rsi(pd.Series(np.arange(60.0, 1.0, -1.0)), 14)
    assert up.dropna().eq(100).all() and down.dropna().eq(0).all()
    assert wilder_rsi(pd.Series([5.0] * 40), 14).isna().all(), "hiç hareket yoksa RSI tanımsız"
    assert wilder_rsi(pd.Series([1.0, 2.0, 3.0]), 14).isna().all(), "mum sayısı yetersizse hepsi NaN"
    r = wilder_rsi(synthetic(800)["close"], 14).dropna()
    assert r.between(0, 100).all() and r.min() < 35 and r.max() > 65, "sentetik veride RSI geniş aralıkta gezinmeli"


def test_rsi_never_looks_ahead():
    close = synthetic(900, seed=4)["close"]
    full = wilder_rsi(close, 14)
    pd.testing.assert_series_equal(wilder_rsi(close.iloc[:500], 14), full.iloc[:500])


def test_rsi_labels_follow_the_rule_table_exactly():
    rsi = RSIIndicator()
    cases = [(0, STRONG_BOTTOM), (29.99, STRONG_BOTTOM), (30, BOTTOM), (32, BOTTOM), (35, BOTTOM), (35.01, NEUTRAL),
             (50, NEUTRAL), (64.99, NEUTRAL), (65, OVERHEATED), (68, OVERHEATED), (70, OVERHEATED), (70.01, EXTREME),
             (100, EXTREME)]
    for value, expected in cases:
        assert rsi.classify(value) == expected, (value, rsi.classify(value), expected)
    assert rsi.classify(float("nan")) is None and rsi.classify(None) is None, "değeri olmayan etiket üretmemeli"
    assert set(LABELS) == set(POINTS) and [POINTS[label] for label in LABELS] == [100, 50, 0, -50, -100]


def reference_percent_b(close, period=20, mult=2.0):
    """Bağımsız (saf Python) referans: statistics.pstdev ile pencere pencere hesaplar."""
    out = []
    for i in range(len(close)):
        if i + 1 < period:
            out.append(float("nan"))
            continue
        window = list(close[i + 1 - period:i + 1])
        mid, sd = statistics.fmean(window), statistics.pstdev(window)
        lower, upper = mid - mult * sd, mid + mult * sd
        out.append((close[i] - lower) / (upper - lower) if upper > lower else float("nan"))
    return out


def test_bollinger_percent_b_matches_independent_reference():
    close = synthetic(400, seed=7)["close"]
    got = bollinger_percent_b(close, 20, 2.0)
    assert got.iloc[:19].isna().all() and got.iloc[19:].notna().all(), "ilk 19 mum ısınma (NaN) olmalı"
    np.testing.assert_allclose(got.to_numpy(), reference_percent_b(list(close), 20, 2.0), atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(bollinger_percent_b(close, 10, 1.5).to_numpy(), reference_percent_b(list(close), 10, 1.5),
                               atol=1e-12, equal_nan=True)
    # elle hesaplanmış örnek: 1..20, bantlar 10.5 ± 2 * sqrt(33.25) (popülasyon sapması)
    sd = math.sqrt(33.25)
    lower, upper = 10.5 - 2 * sd, 10.5 + 2 * sd
    assert math.isclose(bollinger_percent_b(pd.Series(np.arange(1.0, 21.0))).iloc[-1], (20 - lower) / (upper - lower), rel_tol=1e-12)


def test_bollinger_percent_b_geometry_and_edge_cases():
    # 19 sabit mum + tek sıçrama: %B = 0.5 + sqrt(19) / 4 (> 1), tek düşüş: 0.5 - sqrt(19) / 4 (< 0)
    base = [100.0] * 30
    up, down = pd.Series(base + [130.0]), pd.Series(base + [70.0])
    assert math.isclose(bollinger_percent_b(up).iloc[-1], 0.5 + math.sqrt(19) / 4, rel_tol=1e-12)
    assert math.isclose(bollinger_percent_b(down).iloc[-1], 0.5 - math.sqrt(19) / 4, rel_tol=1e-12)
    # fiyat orta banda eşitken %B = 0.5 (simetrik pencere: son değer ortalamaya eşit)
    window = [90.0, 110.0] * 9 + [95.0, 105.0]
    assert math.isclose(bollinger_percent_b(pd.Series(window + [100.0])).iloc[-1], 0.5, abs_tol=0.02)
    assert bollinger_percent_b(pd.Series([100.0] * 40)).isna().all(), "bant genişliği 0 ise %B tanımsız"
    assert bollinger_percent_b(pd.Series([1.0, 2.0, 3.0])).isna().all(), "mum sayısı yetersizse hepsi NaN"
    # çarpan büyüdükçe bantlar genişler, %B ortaya (0.5) yaklaşır
    close = synthetic(200, seed=2)["close"]
    narrow, wide = bollinger_percent_b(close, 20, 1.0).dropna(), bollinger_percent_b(close, 20, 3.0).dropna()
    assert ((wide - 0.5).abs() < (narrow - 0.5).abs()).all()


def test_bollinger_never_looks_ahead():
    close = synthetic(900, seed=5)["close"]
    full = bollinger_percent_b(close, 20, 2.0)
    pd.testing.assert_series_equal(bollinger_percent_b(close.iloc[:500], 20, 2.0), full.iloc[:500])


def test_bollinger_labels_follow_the_rule_table_exactly():
    bb = BollingerPercentBIndicator()
    cases = [(-1.5, STRONG_BOTTOM), (-0.0001, STRONG_BOTTOM), (0, BOTTOM), (0.05, BOTTOM), (0.10, BOTTOM), (0.1000001, NEUTRAL),
             (0.5, NEUTRAL), (0.8999999, NEUTRAL), (0.90, OVERHEATED), (0.95, OVERHEATED), (1, OVERHEATED), (1.0000001, EXTREME),
             (2.4, EXTREME)]
    for value, expected in cases:
        assert bb.classify(value) == expected, (value, bb.classify(value), expected)
    assert bb.classify(float("nan")) is None and bb.classify(None) is None
    assert bb.key == "bollinger" and bb.name == "Bollinger %B(20, 2)" and BollingerPercentBIndicator(10, 1.5).name == "Bollinger %B(10, 1.5)"
    assert [v for v, _ in bb.levels()] == [0.0, 0.10, 0.90, 1.0] and bb.text(0.456) == "0.46"
    # gösterge nesnesi uçtan uca: ısınma NaN -> etiket yok; sıçrama -> Aşırı Şişmiş, düşüş -> Güçlü Dip
    df = pd.DataFrame({"close": [100.0] * 30 + [130.0]})
    labels = bb.labels(bb.compute(df))
    assert labels.iloc[:19].isna().all() and labels.iloc[-1] == EXTREME
    assert bb.labels(bb.compute(pd.DataFrame({"close": [100.0] * 30 + [70.0]}))).iloc[-1] == STRONG_BOTTOM
    for bad_args in ({"period": 1}, {"mult": 0}, {"mult": -1}):
        try:
            BollingerPercentBIndicator(**bad_args)
            assert False, bad_args
        except ValueError:
            pass


def test_labels_series_marks_missing_data_as_none():
    df = synthetic(300, seed=2)
    rsi = RSIIndicator()
    values = rsi.compute(df)
    labels = rsi.labels(values)
    assert labels.iloc[:14].isna().all() and labels.iloc[14:].notna().all()
    assert set(labels.dropna()) <= set(LABELS) and labels.index.equals(df.index)


def test_build_indicators_and_extensibility():
    default = build_indicators(None)
    assert [i.key for i in default] == ["rsi"] and default[0].period == 14 and default[0].name == "RSI(14)"
    assert build_indicators({"rsi": {"period": 7}})[0].period == 7 and build_indicators({"rsi": None})[0].period == 14
    both = build_indicators({"rsi": {}, "bollinger": {"period": 10, "mult": 1.5}})
    assert [i.key for i in both] == ["rsi", "bollinger"] and both[1].period == 10 and both[1].mult == 1.5
    assert build_indicators({"bollinger": None})[0].period == 20 and build_indicators({"bollinger": None})[0].mult == 2.0
    # gerçek config.yaml: RSI ve Bollinger %B tanımlı olmalı
    real = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))
    built = build_indicators(real["indicators"])
    assert [i.key for i in built] == ["rsi", "bollinger", "mfi", "fear_greed", "mvrv"]
    assert [type(i) for i in built] == [RSIIndicator, BollingerPercentBIndicator, MFIIndicator, FearGreedIndicator, MVRVZScoreIndicator]
    assert [i.name for i in built] == ["RSI(14)", "Bollinger %B(20, 2)", "MFI(14)", "Fear & Greed", "MVRV Z-Score"]
    for bad, message in (({}, "en az bir"), ({"macd": {}}, "Bilinmeyen"), ({"rsi": {"period": 1}}, "en az 2")):
        try:
            build_indicators(bad)
            assert False, bad
        except ValueError as e:
            assert message in str(e), (bad, str(e))

    class Dummy(Indicator):  # yeni gösterge: yalnızca sınıf yazılır ve kaydedilir
        key, name = "dummy", "Dummy"

        def compute(self, df, tf=None):
            return df["close"] * 0 + 20.0

        def classify(self, value):
            return None if pd.isna(value) else BOTTOM

    ind.REGISTRY["dummy"] = Dummy
    try:
        built = build_indicators({"rsi": {}, "dummy": {}})
        assert [i.key for i in built] == ["rsi", "dummy"]
    finally:
        del ind.REGISTRY["dummy"]


def reference_mfi(high, low, close, volume, period=14):
    """Bağımsız (saf Python) referans: pencere pencere pozitif/negatif para akışını toplar."""
    typical = [(h + l + c) / 3 for h, l, c in zip(high, low, close)]
    volume = list(volume)
    out = [float("nan")] * len(typical)
    for i in range(period, len(typical)):
        up = down = 0.0
        for j in range(i - period + 1, i + 1):
            change, flow = typical[j] - typical[j - 1], typical[j] * volume[j]
            if change > 0:
                up += flow
            elif change < 0:
                down += flow
        if down > 0:
            out[i] = 100 - 100 / (1 + up / down)
        elif up > 0:
            out[i] = 100.0
    return out


def test_mfi_matches_independent_reference_and_hand_example():
    df = synthetic(500, seed=11)
    got = money_flow_index(df["high"], df["low"], df["close"], df["volume"], 14)
    assert got.iloc[:14].isna().all() and got.iloc[14:].notna().all(), "ilk 14 mum ısınma (NaN) olmalı"
    np.testing.assert_allclose(got.to_numpy(), reference_mfi(df["high"], df["low"], df["close"], df["volume"], 14),
                               atol=1e-9, equal_nan=True)
    assert got.dropna().between(0, 100).all() and got.min() < 30 and got.max() > 70
    # elle hesaplanmış örnek (periyot 2): tipik fiyatlar 10, 11, 10, 10, 12; hacimler 100, 100, 200, 100, 50
    tp = pd.Series([10.0, 11.0, 10.0, 10.0, 12.0])
    vol = pd.Series([100.0, 100.0, 200.0, 100.0, 50.0])
    hand = money_flow_index(tp, tp, tp, vol, 2)
    assert hand.iloc[:2].isna().all()
    assert math.isclose(hand.iloc[2], 100 - 100 / (1 + 1100 / 2000), rel_tol=1e-12), "i=2: pozitif 11*100, negatif 10*200"
    assert hand.iloc[3] == 0, "i=3: tipik fiyat eşit olan mum hiçbir akışa girmez; yalnızca negatif akış kalır"
    assert hand.iloc[4] == 100, "i=4: negatif akış penceren çıktı"


def test_mfi_edge_cases_and_no_look_ahead():
    up = pd.Series(np.arange(1.0, 41.0))
    vol = pd.Series([10.0] * 40)
    assert money_flow_index(up, up, up, vol).dropna().eq(100).all(), "hiç negatif akış yoksa 100"
    down = pd.Series(np.arange(40.0, 0.0, -1.0))
    assert money_flow_index(down, down, down, vol).dropna().eq(0).all()
    flat = pd.Series([5.0] * 40)
    assert money_flow_index(flat, flat, flat, vol).isna().all(), "fiyat hiç değişmezse akış yok: tanımsız"
    assert money_flow_index(up, up, up, pd.Series([0.0] * 40)).isna().all(), "hacim 0 ise akış yok: tanımsız"
    assert money_flow_index(up.iloc[:5], up.iloc[:5], up.iloc[:5], vol.iloc[:5]).isna().all(), "mum sayısı yetersiz"
    # hacim ağırlığı: aynı fiyat hareketi, farklı hacimle farklı MFI verir (RSI'dan farkı)
    df = synthetic(300, seed=2)
    heavy_up = df["volume"].where(df["close"].diff() > 0, 1.0)
    assert not np.allclose(money_flow_index(df["high"], df["low"], df["close"], df["volume"]).dropna(),
                           money_flow_index(df["high"], df["low"], df["close"], heavy_up).dropna())
    full = money_flow_index(df["high"], df["low"], df["close"], df["volume"])
    cut = money_flow_index(df["high"].iloc[:200], df["low"].iloc[:200], df["close"].iloc[:200], df["volume"].iloc[:200])
    pd.testing.assert_series_equal(cut, full.iloc[:200])


def test_mfi_labels_follow_the_rule_table_exactly():
    mfi = MFIIndicator()
    cases = [(0, STRONG_BOTTOM), (19.99, STRONG_BOTTOM), (20, BOTTOM), (25, BOTTOM), (30, BOTTOM), (30.01, NEUTRAL), (50, NEUTRAL),
             (69.99, NEUTRAL), (70, OVERHEATED), (75, OVERHEATED), (80, OVERHEATED), (80.01, EXTREME), (100, EXTREME)]
    for value, expected in cases:
        assert mfi.classify(value) == expected, (value, mfi.classify(value), expected)
    assert mfi.classify(float("nan")) is None and mfi.classify(None) is None
    assert mfi.key == "mfi" and mfi.name == "MFI(14)" and MFIIndicator(7).name == "MFI(7)" and mfi.value_range == (0, 100)
    assert [v for v, _ in mfi.levels()] == [20, 30, 70, 80] and mfi.text(55.5) == "55.5"
    df = synthetic(120, seed=6)
    labels = mfi.labels(mfi.compute(df))
    assert labels.iloc[:14].isna().all() and labels.iloc[14:].notna().all() and set(labels.dropna()) <= set(LABELS)
    try:
        MFIIndicator(1)
        assert False
    except ValueError:
        pass


def test_fear_greed_labels_match_the_table_for_every_integer():
    fg = FearGreedIndicator()
    table = [(range(0, 21), STRONG_BOTTOM), (range(21, 35), BOTTOM), (range(35, 70), NEUTRAL), (range(70, 80), OVERHEATED),
             (range(80, 101), EXTREME)]
    seen = set()
    for values, label in table:
        for value in values:
            assert fg.classify(float(value)) == label, (value, fg.classify(float(value)), label)
            seen.add(value)
    assert seen == set(range(0, 101)), "tablo 0-100'ün tamamını kapsamalı"
    # haftalık/aylık ortalamalar tam sayı olmayabilir: en yakın tam sayıya yuvarlanıp (x,5 yukarı) tabloya uygulanır
    for value, label in ((20.4, STRONG_BOTTOM), (20.5, BOTTOM), (20.9, BOTTOM), (34.4, BOTTOM), (34.5, NEUTRAL), (69.4, NEUTRAL),
                         (69.5, OVERHEATED), (79.4, OVERHEATED), (79.5, EXTREME), (99.9, EXTREME), (0.2, STRONG_BOTTOM)):
        assert fg.classify(value) == label, (value, fg.classify(value), label)
        assert fg.classify(value) == fg.classify(float(int(value + 0.5))), "ara değer, yuvarlanmış tam sayıyla aynı etiketi almalı"
    assert fg.classify(float("nan")) is None and fg.classify(None) is None
    assert fg.key == "fear_greed" and fg.name == "Fear & Greed" and fg.text(34.0) == "34" and fg.text(34.5) == "34.5"
    assert [v for v, _ in fg.levels()] == [20.5, 34.5, 69.5, 79.5]


def test_mvrv_labels_follow_the_rule_table_exactly():
    z = MVRVZScoreIndicator()
    cases = [(-2.5, STRONG_BOTTOM), (-0.0001, STRONG_BOTTOM), (0, BOTTOM), (0.5, BOTTOM), (0.9999, BOTTOM), (1, NEUTRAL), (2, NEUTRAL),
             (2.9999, NEUTRAL), (3, OVERHEATED), (5, OVERHEATED), (6.9999, OVERHEATED), (7, EXTREME), (9.5, EXTREME)]
    for value, expected in cases:
        assert z.classify(value) == expected, (value, z.classify(value), expected)
    assert z.classify(float("nan")) is None and z.classify(None) is None
    assert z.key == "mvrv" and z.name == "MVRV Z-Score" and [v for v, _ in z.levels()] == [0, 1, 3, 7] and z.max_stale_days == 2


def make_fg(start="2023-11-01", days=200, seed=3):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.integers(0, 101, days).astype(float), index=pd.date_range(start, periods=days, freq="D", tz="UTC"))


def make_mvrv_frame(start="2020-01-01", days=1500):
    index = pd.date_range(start, periods=days, freq="D", tz="UTC")
    rng = np.random.default_rng(9)
    mcap = 1e11 * np.exp(np.cumsum(rng.normal(0.001, 0.02, days)))
    return pd.DataFrame({"mcap": mcap, "mvrv": 1.5 + 0.5 * np.sin(np.arange(days) / 90)}, index=index)


def test_external_indicators_align_to_asset_bars():
    fg_daily = make_fg()
    daily_df = synthetic(60, seed=1).set_axis(pd.date_range("2023-12-01", periods=60, freq="D", tz="UTC"))
    fg = FearGreedIndicator(source=lambda: fg_daily)
    pd.testing.assert_series_equal(fg.compute(daily_df, "1d"), fg_daily.reindex(daily_df.index), check_freq=False)
    assert fg.compute(daily_df).equals(fg.compute(daily_df, "1d")), "tf verilmezse mum aralığından tahmin edilir"
    weekly_df = synthetic(8, seed=1).set_axis(pd.date_range("2023-12-04", periods=8, freq="W-MON", tz="UTC"))
    weekly = fg.compute(weekly_df, "1w")
    assert math.isclose(weekly.iloc[0], fg_daily.loc["2023-12-04":"2023-12-10"].mean()), "haftalık = haftanın 7 günlük ortalaması"
    assert fg.compute(weekly_df).equals(weekly)
    last = FearGreedIndicator(aggregate="last", source=lambda: fg_daily).compute(weekly_df, "1w")
    assert last.iloc[0] == fg_daily.loc["2023-12-10"]
    monthly_df = synthetic(4, seed=1).set_axis(pd.date_range("2023-12-01", periods=4, freq="MS", tz="UTC"))
    month = fg.compute(monthly_df, "1M")
    assert math.isclose(month.iloc[0], fg_daily.loc["2023-12-01":"2023-12-31"].mean()) and month.iloc[:2].notna().all()
    # MVRV: Z-Score günlük serisi aynı kurallarla hizalanır; yayın gecikmesinde son değer en fazla 2 gün taşınır
    frame = make_mvrv_frame()
    z = mvrv_module.zscore(frame)
    df = synthetic(30, seed=2).set_axis(pd.date_range(frame.index[-1] - pd.Timedelta(days=26), periods=30, freq="D", tz="UTC"))
    out = MVRVZScoreIndicator(source=lambda: frame).compute(df, "1d")
    np.testing.assert_allclose(out.iloc[:27].to_numpy(), z.iloc[-27:].to_numpy())
    assert out.iloc[27] == z.iloc[-1] and out.iloc[28] == z.iloc[-1] and np.isnan(out.iloc[29]), "son değer en fazla 2 gün taşınır"
    strict = MVRVZScoreIndicator(max_stale_days=0, source=lambda: frame).compute(df, "1d")
    assert strict.iloc[27:].isna().all()
    for bad in ({"aggregate": "median"}, {"max_stale_days": -1}):
        try:
            FearGreedIndicator(**bad)
            assert False, bad
        except ValueError:
            pass


def test_external_indicators_never_raise_when_the_source_fails():
    from core import indicators as ind
    df = synthetic(30, seed=3).set_axis(pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC"))
    def broken():
        raise RuntimeError("kaynak çöktü")
    ind._WARNED.clear()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        a = FearGreedIndicator(source=broken).compute(df, "1d")
        b = FearGreedIndicator(source=broken).compute(df, "1d")  # ikinci çağrıda aynı uyarı tekrarlanmaz
        c = MVRVZScoreIndicator(source=broken).compute(df, "1d")
        d = FearGreedIndicator(source=None).compute(df, "1d")
    assert a.isna().all() and b.isna().all() and c.isna().all() and d.isna().all() and a.index.equals(df.index)
    assert err.getvalue().count("Fear & Greed verisi alınamadı") == 1 and "MVRV Z-Score verisi alınamadı" in err.getvalue()
    assert "kaynağı bağlı değil" in err.getvalue()
    # bozuk (ör. boş) veri de hata fırlatmadan "değer yok" olur
    assert FearGreedIndicator(source=lambda: pd.Series(dtype=float)).compute(df, "1d").isna().all()
    assert FearGreedIndicator(source=broken).labels(FearGreedIndicator(source=broken).compute(df, "1d")).isna().all()


def test_build_indicators_wires_sources():
    fg_fn, mv_fn = (lambda: None), (lambda: None)
    built = build_indicators({"fear_greed": {}, "mvrv": {"aggregate": "last"}}, {"fear_greed": fg_fn, "mvrv": mv_fn})
    assert built[0].source is fg_fn and built[1].source is mv_fn and built[1].aggregate == "last" and built[1].max_stale_days == 2
    defaults = build_indicators({"fear_greed": {}, "mvrv": {}})
    assert defaults[0].source is default_sources()["fear_greed"] and defaults[1].source is default_sources()["mvrv"]
    assert build_indicators({"rsi": {}})[0].period == 14, "dış veri kullanmayan göstergelere kaynak verilmez"
    assert build_indicators({"fear_greed": {"max_stale_days": 1}})[0].max_stale_days == 1


def test_all():
    test_mfi_matches_independent_reference_and_hand_example()
    test_mfi_edge_cases_and_no_look_ahead()
    test_mfi_labels_follow_the_rule_table_exactly()
    test_fear_greed_labels_match_the_table_for_every_integer()
    test_mvrv_labels_follow_the_rule_table_exactly()
    test_external_indicators_align_to_asset_bars()
    test_external_indicators_never_raise_when_the_source_fails()
    test_build_indicators_wires_sources()
    test_rsi_matches_published_wilder_example()
    test_rsi_edge_cases()
    test_rsi_never_looks_ahead()
    test_rsi_labels_follow_the_rule_table_exactly()
    test_bollinger_percent_b_matches_independent_reference()
    test_bollinger_percent_b_geometry_and_edge_cases()
    test_bollinger_never_looks_ahead()
    test_bollinger_labels_follow_the_rule_table_exactly()
    test_labels_series_marks_missing_data_as_none()
    test_build_indicators_and_extensibility()


if __name__ == "__main__":
    test_all()
    print("Gösterge testleri geçti ✔")
