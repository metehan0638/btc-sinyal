"""Genel skor (Dip Skoru), nihai etiket, skor geçmişi ve ileri getiri tabloları için çevrimdışı testler."""
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.indicators import (BOTTOM, EXTREME, NEUTRAL, OVERHEATED, POINTS, STRONG_BOTTOM, Indicator, RSIIndicator,
                             build_indicators)
from core.scoring import bucket_table, evaluate, final_label, forward_returns, overall_score, score_history
from test_core import make_fg, make_mvrv_frame, synthetic


def labels_of(*names):
    return {f"i{n}": name for n, name in enumerate(names)}


def test_spec_scenarios():
    # Senaryo 1: yalnızca RSI, Güçlü Dip
    r = overall_score({"rsi": STRONG_BOTTOM})
    assert r["score"] == 100 and r["label"] == STRONG_BOTTOM and r["active"] == r["total"] == 1
    # Senaryo 2: 3 gösterge: (100 + 50 + 0) / 3
    assert overall_score(labels_of(STRONG_BOTTOM, BOTTOM, NEUTRAL))["score"] == 50
    # Senaryo 3: 5 gösterge: (100 + 50 + 0 + 100 - 50) / 5
    assert overall_score(labels_of(STRONG_BOTTOM, BOTTOM, NEUTRAL, STRONG_BOTTOM, OVERHEATED))["score"] == 40
    # Senaryo 4: 10 gösterge: 5 Güçlü Dip, 2 Dip, 1 Nötr, 1 Şişmiş, 1 Aşırı Şişmiş: (500 + 100 + 0 - 50 - 100) / 10
    ten = labels_of(*[STRONG_BOTTOM] * 5, *[BOTTOM] * 2, NEUTRAL, OVERHEATED, EXTREME)
    r = overall_score(ten)
    assert len(ten) == 10 and r["score"] == 45 and r["label"] == BOTTOM


def test_score_is_always_within_bounds_and_hits_the_extremes():
    assert overall_score(labels_of(*[STRONG_BOTTOM] * 10))["score"] == 100
    assert overall_score(labels_of(*[EXTREME] * 10))["score"] == -100
    for combo in itertools.product((STRONG_BOTTOM, BOTTOM, NEUTRAL, OVERHEATED, EXTREME), repeat=3):
        assert -100 <= overall_score(labels_of(*combo))["score"] <= 100


def test_indicators_without_data_are_excluded_not_neutral():
    r = overall_score({"rsi": STRONG_BOTTOM, "x": None})
    assert r["score"] == 100 and r["active"] == 1 and r["total"] == 2, "veri olmayan gösterge nötr (0) sayılmamalı"
    r = overall_score({"rsi": BOTTOM, "x": None, "y": EXTREME})
    assert r["score"] == -25 and r["active"] == 2
    r = overall_score({"rsi": None})
    assert np.isnan(r["score"]) and r["label"] is None and r["active"] == 0
    assert np.isnan(overall_score({})["score"])
    try:
        overall_score({"rsi": "Süper Dip"})
        assert False
    except ValueError:
        pass


def test_final_label_boundaries_do_not_overlap():
    expected = [(100, STRONG_BOTTOM), (60, STRONG_BOTTOM), (59.99, BOTTOM), (20, BOTTOM), (19.99, NEUTRAL), (0, NEUTRAL),
                (-19.99, NEUTRAL), (-20, OVERHEATED), (-59.99, OVERHEATED), (-60, EXTREME), (-100, EXTREME)]
    for score, label in expected:
        assert final_label(score) == label, (score, final_label(score), label)
    assert final_label(float("nan")) is None and final_label(None) is None
    # her skor tam olarak bir etikete düşer (aralık ve boşluk yok)
    for score in np.linspace(-100, 100, 4001):
        assert final_label(float(score)) is not None


def test_evaluate_and_history_agree_and_never_look_ahead():
    df = synthetic(900, seed=3)
    indicators = build_indicators(None)
    history = score_history(df, indicators)
    assert list(history.columns) == ["rsi", "score", "active", "label"] and history.index.equals(df.index)
    assert history["score"].iloc[:14].isna().all() and history["active"].iloc[:14].eq(0).all()
    assert history["score"].dropna().between(-100, 100).all()
    for k in (200, 500, 900):
        now = evaluate(df.iloc[:k], indicators)
        assert now["score"] == history["score"].iloc[k - 1] and now["label"] == history["label"].iloc[k - 1], k
        assert now["date"] == df.index[k - 1] and now["labels"]["rsi"] == RSIIndicator().classify(now["values"]["rsi"])
    pd.testing.assert_frame_equal(score_history(df.iloc[:400], indicators), history.iloc[:400])
    # tek gösterge varken genel skor RSI etiketinin puanı ile aynı
    assert set(history["score"].dropna().unique()) <= {-100.0, -50.0, 0.0, 50.0, 100.0}


def test_rsi_and_bollinger_combine_as_equal_votes():
    df = synthetic(900, seed=3)
    indicators = build_indicators({"rsi": {}, "bollinger": {}})
    history = score_history(df, indicators)
    assert list(history.columns) == ["rsi", "bollinger", "score", "active", "label"]
    warm = history.iloc[14:19]  # RSI 14. mumda, Bollinger 19. mumda başlar: aradaki mumlarda yalnızca RSI oy kullanır
    assert (warm["active"] == 1).all() and warm["score"].equals(warm["rsi"]) and warm["bollinger"].isna().all()
    assert (history["active"].iloc[:14] == 0).all() and history["score"].iloc[:14].isna().all()
    both = history.iloc[19:]
    assert (both["active"] == 2).all() and np.allclose(both["score"], (both["rsi"] + both["bollinger"]) / 2)
    assert set(both["score"].unique()) <= {-100.0, -75.0, -50.0, -25.0, 0.0, 25.0, 50.0, 75.0, 100.0}
    for k in (100, 400, 900):
        now = evaluate(df.iloc[:k], indicators)
        assert now["score"] == history["score"].iloc[k - 1] and now["label"] == history["label"].iloc[k - 1], k
        assert now["active"] == now["total"] == 2 and list(now["names"].values()) == ["RSI(14)", "Bollinger %B(20, 2)"]
        assert now["score"] == (POINTS[now["labels"]["rsi"]] + POINTS[now["labels"]["bollinger"]]) / 2
    pd.testing.assert_frame_equal(score_history(df.iloc[:500], indicators), history.iloc[:500])  # gelecek verisi sızmaz


FIVE = {"rsi": {}, "bollinger": {}, "mfi": {}, "fear_greed": {}, "mvrv": {}}


def five_sources():
    return {"fear_greed": lambda: make_fg("2017-12-01", 1500, seed=3), "mvrv": lambda: make_mvrv_frame("2017-01-01", 2500)}


def test_five_indicators_combine_as_equal_votes_for_every_timeframe():
    df = synthetic(900, seed=3)  # 2018-01-01 .. 2020-06-18 (günlük)
    weekly = df.resample("W-MON", label="left", closed="left").agg(
        dict(open="first", high="max", low="min", close="last", volume="sum")).dropna()
    monthly = df.resample("MS", label="left", closed="left").agg(
        dict(open="first", high="max", low="min", close="last", volume="sum")).dropna()
    for tf, frame, warm in (("1d", df, 19), ("1w", weekly, 19), ("1M", monthly, 19)):  # Bollinger 20. mumda başlar
        indicators = build_indicators(FIVE, five_sources())
        history = score_history(frame, indicators, tf)
        votes = history[list(FIVE)]
        assert list(history.columns) == [*FIVE, "score", "active", "label"], tf
        ready = history.iloc[warm:-1]  # sonuncu parçalı dönem olabilir
        assert (ready["active"] == 5).all(), (tf, ready["active"].value_counts().to_dict())
        np.testing.assert_allclose(ready["score"], votes.loc[ready.index].mean(axis=1))
        assert set(ready["score"].unique()) <= {float(v) for v in range(-100, 101, 10)}, "5 oyun ortalaması 10'un katlarıdır"
        for k in (len(frame) // 2, len(frame) - 2):
            now = evaluate(frame.iloc[:k], indicators, tf)
            assert now["score"] == history["score"].iloc[k - 1] and now["label"] == history["label"].iloc[k - 1], (tf, k)
            assert list(now["names"]) == list(FIVE) and now["total"] == 5
        pd.testing.assert_frame_equal(score_history(frame.iloc[:len(frame) // 2], indicators, tf), history.iloc[:len(frame) // 2])
        if tf == "1w":  # F&G haftalık ortalaması etiketi bağımsız hesaplanan ortalamayla aynı olmalı
            fg = make_fg("2017-12-01", 1500, seed=3)
            week = ready.index[10]
            average = fg.loc[week:week + pd.Timedelta(days=6)].mean()
            assert votes.loc[week, "fear_greed"] == POINTS[indicators[3].classify(average)]


def test_a_failing_external_source_is_excluded_not_neutral():
    df = synthetic(300, seed=3)
    def broken():
        raise RuntimeError("kaynak çöktü")
    good = build_indicators(FIVE, five_sources())
    partial = build_indicators(FIVE, {"fear_greed": five_sources()["fear_greed"], "mvrv": broken})
    history = score_history(df, partial, "1d")
    assert history["mvrv"].isna().all()
    ready = history.iloc[19:]
    assert (ready["active"] == 4).all()
    four = good[:4]
    expected = score_history(df, four, "1d")
    np.testing.assert_allclose(ready["score"], expected["score"].iloc[19:])
    now = evaluate(df, partial, "1d")
    assert now["active"] == 4 and now["total"] == 5 and now["labels"]["mvrv"] is None


def test_all_indicators_agreeing_gives_the_extremes():
    both = build_indicators({"rsi": {}, "bollinger": {}})
    idx = lambda n: pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    flat = [100.0] * 60
    surge = evaluate(pd.DataFrame({"close": flat + [130.0]}, index=idx(61)), both)
    assert surge["labels"] == {"rsi": EXTREME, "bollinger": EXTREME} and surge["score"] == -100 and surge["label"] == EXTREME
    crash = evaluate(pd.DataFrame({"close": flat + [70.0]}, index=idx(61)), both)
    assert crash["labels"] == {"rsi": STRONG_BOTTOM, "bollinger": STRONG_BOTTOM} and crash["score"] == 100
    assert crash["label"] == STRONG_BOTTOM
    # kısmi uyum: doğrusal düşüşte RSI 0 (Güçlü Dip +100) ama %B ≈ 0.088 (Dip +50) -> (100 + 50) / 2 = +75
    falling = evaluate(pd.DataFrame({"close": np.linspace(300, 100, 300)}, index=idx(300)), both)
    assert falling["labels"] == {"rsi": STRONG_BOTTOM, "bollinger": BOTTOM} and falling["score"] == 75
    assert falling["label"] == STRONG_BOTTOM


def test_history_with_a_late_starting_indicator_uses_only_active_ones():
    class Late(Indicator):
        key, name = "late", "Geç"

        def compute(self, df, tf=None):
            return pd.Series(np.where(np.arange(len(df)) >= 50, 1.0, np.nan), index=df.index)

        def classify(self, value):
            return None if pd.isna(value) else EXTREME

    df = synthetic(120, seed=8)
    history = score_history(df, [RSIIndicator(), Late()])
    early = history.iloc[20:50]
    assert (early["active"] == 1).all() and early["score"].equals(early["rsi"]), "geç başlayan gösterge hesaba girmemeli"
    late = history.iloc[60:]
    assert (late["active"] == 2).all() and np.allclose(late["score"], (late["rsi"] - 100) / 2)


def test_bucket_table_forward_returns_and_episodes():
    close = pd.Series([100, 200, 100, 50, 25, 110, 110], dtype=float)
    labels = pd.Series([STRONG_BOTTOM, STRONG_BOTTOM, EXTREME, EXTREME, NEUTRAL, NEUTRAL, NEUTRAL], dtype=object)
    pd.testing.assert_series_equal(forward_returns(close, 1).iloc[:6], pd.Series([1.0, -0.5, -0.5, -0.5, 3.4, 0.0]))
    t = bucket_table(close, labels, 1)
    assert list(t.index) == [STRONG_BOTTOM, BOTTOM, NEUTRAL, OVERHEATED, EXTREME, "Tüm mumlar (kıyas)"]
    sb, ex, ne = t.loc[STRONG_BOTTOM], t.loc[EXTREME], t.loc[NEUTRAL]
    assert sb["n"] == 2 and np.isclose(sb["mean"], 0.25) and sb["pos_rate"] == 0.5 and sb["episodes"] == 1
    assert ex["n"] == 2 and np.isclose(ex["mean"], -0.5) and ex["pos_rate"] == 0.0
    assert ne["n"] == 2 and np.isclose(ne["mean"], 1.7) and ne["episodes"] == 1, "son mumun ileri getirisi yok"
    assert t.loc[BOTTOM, "n"] == 0 and np.isnan(t.loc[BOTTOM, "mean"]) and t.loc[BOTTOM, "episodes"] == 0
    assert t.loc["Tüm mumlar (kıyas)", "n"] == 6 and np.isclose(t.iloc[:5]["share"].sum(), 1.0)
    # aynı etikete iki ayrı giriş = 2 dönem
    again = bucket_table(pd.Series([1.0] * 6), pd.Series([BOTTOM, NEUTRAL, BOTTOM, BOTTOM, NEUTRAL, BOTTOM], dtype=object), 1)
    assert again.loc[BOTTOM, "episodes"] == 3
    # etiketi olmayan mumlar tabloya girmez
    gap = bucket_table(pd.Series([1.0, 2.0, 4.0]), pd.Series([None, BOTTOM, BOTTOM], dtype=object), 1)
    assert gap.loc[BOTTOM, "n"] == 1 and gap.loc["Tüm mumlar (kıyas)", "n"] == 1


def test_all():
    test_five_indicators_combine_as_equal_votes_for_every_timeframe()
    test_a_failing_external_source_is_excluded_not_neutral()
    test_spec_scenarios()
    test_score_is_always_within_bounds_and_hits_the_extremes()
    test_indicators_without_data_are_excluded_not_neutral()
    test_final_label_boundaries_do_not_overlap()
    test_evaluate_and_history_agree_and_never_look_ahead()
    test_rsi_and_bollinger_combine_as_equal_votes()
    test_all_indicators_agreeing_gives_the_extremes()
    test_history_with_a_late_starting_indicator_uses_only_active_ones()
    test_bucket_table_forward_returns_and_episodes()


if __name__ == "__main__":
    test_all()
    print("Skor testleri geçti ✔")
