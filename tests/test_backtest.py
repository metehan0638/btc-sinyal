"""Bağımsız gösterge backtest kuralları için çevrimdışı testler."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.backtest import grades_for_backtest, run_backtests, simulate_indicator
from core.zones import compute_metrics
from test_core import synthetic


def candles(opens, highs, closes):
    index = pd.date_range("2024-01-01", periods=len(opens), freq="D", tz="UTC")
    return pd.DataFrame({"open": opens, "high": highs, "low": np.minimum(opens, closes),
                         "close": closes, "volume": np.ones(len(opens))}, index=index)


def test_entry_is_next_open_and_target_sale_includes_both_fees():
    df = candles([100, 120, 150, 140], [100, 130, 200, 200], [100, 125, 160, 140])
    grades = pd.Series([1, 1, 0, 1], index=df.index, dtype=float)
    result = simulate_indicator(df, grades, "1d", target_pct=30, fee_pct=0.1)

    trade = result["trades_detail"][0]
    assert trade["entry"] == df.index[1]       # mum-0 sinyali, mum-1 açılışından alış
    assert trade["entry_price"] == 120
    assert trade["exit"] == df.index[2]
    assert trade["exit_price"] == 156       # gün içi yüksek hedefe değdi
    assert trade["target_hit"] is True
    np.testing.assert_allclose(trade["net_return"], 1.3 * 0.999**2 - 1)
    assert result["target_hits"] == 1 and result["trades"] == 1


def test_gap_above_target_exits_at_open_and_stays_long_for_positive_signal():
    df = candles([100, 120, 170, 180, 190], [100, 130, 180, 190, 200], [100, 125, 175, 185, 195])
    grades = pd.Series([1, 1, 1, 1, 1], index=df.index, dtype=float)
    result = simulate_indicator(df, grades, "1d", target_pct=30, fee_pct=0)

    assert len(result["trades_detail"]) == 1  # AL derecesi pozitif kalırken yeniden girmez
    assert result["trades_detail"][0]["exit_price"] == 170  # hedef 156, açılışta fiyat boşluğu var


def test_period_end_liquidation_is_not_a_target_hit_and_counts_in_win_rate():
    df = candles([100, 100, 90], [100, 101, 95], [100, 100, 90])
    grades = pd.Series([0, 1, 1], index=df.index, dtype=float)
    result = simulate_indicator(df, grades, "1d", target_pct=30, fee_pct=0)

    trade = result["trades_detail"][0]
    assert trade["entry"] == df.index[2]  # son sinyal, sonraki mum olmadığı için işleme dönüşmez
    assert trade["exit_reason"] == "Dönem sonu"
    assert trade["target_hit"] is False
    assert result["target_hits"] == 0 and result["trades"] == 1
    assert result["win_rate"] == 0


def test_selected_window_uses_prior_grade_to_avoid_fake_buy_at_start():
    df = synthetic(400, seed=19)
    raw = compute_metrics(df)
    raw["rsi"] = 50.0
    raw.loc[df.index[1]:, "rsi"] = 20.0
    start = df.index[2]
    report = run_backtests(df, raw, "1d", params={}, keys=("rsi",), start=start,
                           target_pct=30, fee_pct=0)
    assert report["grades"].index[0] == start
    assert report["results"]["rsi"]["trades"] == 0


def test_daily_fear_greed_is_available_but_not_added_to_other_timeframes():
    df = synthetic(400, seed=7)
    raw = compute_metrics(df)
    fg = pd.Series(np.linspace(5, 95, len(df)), index=df.index.normalize())
    daily = grades_for_backtest(raw, "1d", {}, fg)
    monthly = grades_for_backtest(raw, "1M", {}, fg)
    assert "fear_greed" in daily
    assert "fear_greed" not in monthly
    assert daily["fear_greed"].dropna().isin([-2, -1, 0, 1, 2]).all()
    assert set(daily.columns) == {"rsi", "stochrsi", "bollinger", "mfi", "ma_ratio", "drawdown",
                                 "cross", "ichimoku", "fear_greed"}


def test_indicator_with_no_valid_history_is_marked_unavailable():
    params = {"ma_long": 24, "cross": (6, 18), "ichimoku": (9, 26, 52)}
    df = synthetic(60, seed=15)
    raw = compute_metrics(df, params)
    report = run_backtests(df, raw, "1M", params, keys=("ichimoku",))
    result = report["results"]["ichimoku"]
    assert result["available"] is False and result["equity"] is None
    assert np.isnan(result["total_return"]) and result["trades"] == 0


def test_signal_can_reenter_only_after_a_fresh_buy_transition():
    df = candles([100] * 7, [100, 100, 100, 140, 100, 100, 100], [100, 100, 100, 130, 100, 100, 100])
    grades = pd.Series([0, 1, 1, 1, 0, 1, 1], index=df.index, dtype=float)
    result = simulate_indicator(df, grades, "1d", target_pct=30, fee_pct=0)
    assert len(result["trades_detail"]) == 2
    assert result["trades_detail"][0]["entry"] == df.index[2]
    assert result["trades_detail"][0]["exit"] == df.index[3]
    assert result["trades_detail"][1]["entry"] == df.index[6]


def test_all():
    test_entry_is_next_open_and_target_sale_includes_both_fees()
    test_gap_above_target_exits_at_open_and_stays_long_for_positive_signal()
    test_period_end_liquidation_is_not_a_target_hit_and_counts_in_win_rate()
    test_selected_window_uses_prior_grade_to_avoid_fake_buy_at_start()
    test_daily_fear_greed_is_available_but_not_added_to_other_timeframes()
    test_indicator_with_no_valid_history_is_marked_unavailable()
    test_signal_can_reenter_only_after_a_fresh_buy_transition()


if __name__ == "__main__":
    test_all()
    print("Backtest senaryolari gecti.")
