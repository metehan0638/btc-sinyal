"""Trend/Aşırılık skor geçmişi ve ileri getiri tabloları için çevrimdışı testler. Çalıştır: python tests/test_scores.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.scores import bucket_table, forward_returns, levels, matrix_table, score_history
from core.zones import compute_metrics, latest_rows, split_rows, summarize
from test_core import synthetic


def make_fg(df, seed=5):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.uniform(5, 95, len(df)), index=df.index.normalize())


def report_scores(df_prefix, fg, params=None):
    """Raporun (check_signals) kullandığı yol: son mum için gösterge satırları -> gruplar -> skor."""
    raw = compute_metrics(df_prefix, params)
    day = df_prefix.index[-1].normalize()
    info = dict(value=float(fg.loc[day]), label="test", yesterday=None, week=None, month=None)
    groups = split_rows(latest_rows(raw, params, "1d", info), raw)
    return {k: summarize(v)["mean"] * 50 for k, v in groups.items()}


def test_history_matches_report_logic_and_never_looks_ahead():
    df, fg = synthetic(1500, seed=3), make_fg(synthetic(1500, seed=3))
    history = score_history(compute_metrics(df), "1d", {}, fg)
    assert list(history.columns) == ["trend", "stretch"] and history.index.equals(df.index)
    assert history.dropna().abs().max().max() <= 100
    for k in (700, 1000, 1300, 1500):
        expected = report_scores(df.iloc[:k], fg)
        for key in ("trend", "stretch"):
            np.testing.assert_allclose(history[key].iloc[k - 1], expected[key], atol=1e-6,
                                       err_msg=f"{key} k={k}: tarihsel skor rapor mantığıyla aynı olmalı")
    # kesilmiş veriyle hesaplanan skor, tam veriyle hesaplananın aynı mumdaki değeriyle aynı olmalı (gelecek verisi sızmaz)
    cut = score_history(compute_metrics(df.iloc[:900]), "1d", {}, fg)
    pd.testing.assert_frame_equal(cut, history.iloc[:900])


def test_oscillators_count_as_one_vote():
    rows = [dict(key=k, grade=2.0) for k in ("rsi", "stochrsi", "bollinger", "mfi")]
    rows += [dict(key="ma_ratio", grade=-2.0), dict(key="drawdown", grade=-2.0)]
    raw = pd.DataFrame({"ma_ratio": [1.5]})
    stretch = split_rows(rows, raw)["stretch"]
    assert [r["key"] for r in stretch] == ["oscillators", "ma_ratio", "drawdown"]
    # tek oy: (2 - 2 - 2) / 3 = -0.667 -> -33.3 (dört ayrı oy olsaydı (8 - 4) / 6 = +0.667 çıkardı)
    np.testing.assert_allclose(summarize(stretch)["mean"] * 50, -100 / 3)
    # geçersiz osilatörler oya girmez; hiçbiri geçerli değilse oylanmaz
    partial = split_rows([dict(key="rsi", grade=1.0), dict(key="mfi", grade=np.nan)], raw)["stretch"]
    assert partial[0]["grade"] == 1.0
    assert np.isnan(split_rows([dict(key="rsi", grade=np.nan)], raw)["stretch"][0]["grade"])


def test_levels_use_same_boundaries_as_report_labels():
    score = pd.Series([100, 50, 49.9, 15, 14.9, 0, -14.9, -15, -49.9, -50, -100, np.nan])
    expected = [2, 2, 1, 1, 0, 0, 0, -1, -1, -2, -2, np.nan]
    pd.testing.assert_series_equal(levels(score), pd.Series(expected, dtype=float))


def test_bucket_table_forward_returns_and_episodes():
    close = pd.Series([100, 200, 100, 50, 25, 110, 110], dtype=float)
    score = pd.Series([60, 60, -60, -60, 0, 0, 0], dtype=float)
    pd.testing.assert_series_equal(forward_returns(close, 1).iloc[:6], pd.Series([1.0, -0.5, -0.5, -0.5, 3.4, 0.0]))
    t = bucket_table(close, score, 1)
    assert t.loc[2, "n"] == 2 and np.isclose(t.loc[2, "mean"], 0.25) and t.loc[2, "pos_rate"] == 0.5 and t.loc[2, "episodes"] == 1
    assert t.loc[-2, "n"] == 2 and np.isclose(t.loc[-2, "mean"], -0.5) and t.loc[-2, "pos_rate"] == 0.0
    assert t.loc[0, "n"] == 2 and np.isclose(t.loc[0, "mean"], 1.7) and t.loc[0, "episodes"] == 1, "son mumun ileri getirisi yok"
    assert t.loc[1, "n"] == 0 and np.isnan(t.loc[1, "mean"]) and t.loc[1, "episodes"] == 0
    assert t.loc["all", "n"] == 6 and np.isclose(t.loc[[-2, -1, 0, 1, 2], "share"].sum(), 1.0)
    # aynı bölgeye iki ayrı giriş = 2 dönem
    again = bucket_table(pd.Series([1.0] * 6), pd.Series([60, 0, 60, 60, 0, 60], dtype=float), 1)
    assert again.loc[2, "episodes"] == 3


def test_matrix_table_cells():
    close = pd.Series([100, 110, 121, 60.5, 60.5], dtype=float)
    trend = pd.Series([60, 60, -60, 0, 0], dtype=float)
    stretch = pd.Series([-60, -60, 60, 0, 0], dtype=float)
    mean, count = matrix_table(close, trend, stretch, 1)
    assert count.loc["Yükseliş", "Isınmış"] == 2 and np.isclose(mean.loc["Yükseliş", "Isınmış"], 0.1)
    assert count.loc["Düşüş", "Ucuz"] == 1 and np.isclose(mean.loc["Düşüş", "Ucuz"], -0.5)
    assert count.loc["Yatay", "Dengeli"] == 1 and mean.loc["Yatay", "Dengeli"] == 0
    assert count.to_numpy().sum() == 4 and np.isnan(mean.loc["Yükseliş", "Ucuz"])


def test_weekly_and_monthly_have_no_fear_greed_and_short_history_is_nan():
    df = synthetic(300, seed=9)
    weekly = score_history(compute_metrics(df), "1w", {})
    assert weekly["trend"].isna().iloc[:50].all() and weekly["stretch"].dropna().abs().max() <= 100
    params = {"ma_long": 24, "cross": (6, 18), "ichimoku": (9, 26, 52)}
    monthly = score_history(compute_metrics(synthetic(60, seed=15), params), "1M", params)
    assert monthly.index.equals(synthetic(60, seed=15).index)


def test_all():
    test_history_matches_report_logic_and_never_looks_ahead()
    test_oscillators_count_as_one_vote()
    test_levels_use_same_boundaries_as_report_labels()
    test_bucket_table_forward_returns_and_episodes()
    test_matrix_table_cells()
    test_weekly_and_monthly_have_no_fear_greed_and_short_history_is_nan()


if __name__ == "__main__":
    test_all()
    print("Skor geçmişi testleri geçti ✔")
