"""Göstergeleri (RSI ve etiketleme) sentetik veriyle test eder (internet gerekmez). Çalıştır: python tests/test_core.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import indicators as ind
from core.indicators import (BOTTOM, EXTREME, LABELS, NEUTRAL, OVERHEATED, POINTS, STRONG_BOTTOM, Indicator, RSIIndicator,
                             build_indicators, wilder_rsi)


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
    for bad, message in (({}, "en az bir"), ({"macd": {}}, "Bilinmeyen"), ({"rsi": {"period": 1}}, "en az 2")):
        try:
            build_indicators(bad)
            assert False, bad
        except ValueError as e:
            assert message in str(e), (bad, str(e))

    class Dummy(Indicator):  # yeni gösterge: yalnızca sınıf yazılır ve kaydedilir
        key, name = "dummy", "Dummy"

        def compute(self, df):
            return df["close"] * 0 + 20.0

        def classify(self, value):
            return None if pd.isna(value) else BOTTOM

    ind.REGISTRY["dummy"] = Dummy
    try:
        built = build_indicators({"rsi": {}, "dummy": {}})
        assert [i.key for i in built] == ["rsi", "dummy"]
    finally:
        del ind.REGISTRY["dummy"]


def test_all():
    test_rsi_matches_published_wilder_example()
    test_rsi_edge_cases()
    test_rsi_never_looks_ahead()
    test_rsi_labels_follow_the_rule_table_exactly()
    test_labels_series_marks_missing_data_as_none()
    test_build_indicators_and_extensibility()


if __name__ == "__main__":
    test_all()
    print("Gösterge testleri geçti ✔")
