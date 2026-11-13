"""Bağımsız gösterge backtest kuralları için çevrimdışı testler."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.backtest import OVERALL_KEY, run_backtests, signals_for_backtest, simulate_indicator
from core.indicators import RSIIndicator, build_indicators
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
    points = pd.Series(100.0, index=df.index)
    points.iloc[0] = 0.0  # sinyal zaten AL tarafında: seçilen aralığın başında yapay AL sinyali oluşmamalı
    history = pd.DataFrame({"rsi": points, "score": points, "active": 1, "label": "Güçlü Dip"}, index=df.index)
    start = df.index[2]
    report = run_backtests(df, [], "1d", keys=("rsi",), start=start, target_pct=30, fee_pct=0, history=history)
    assert report["grades"].index[0] == start
    assert report["results"]["rsi"]["trades"] == 0


def test_signals_use_indicator_points_and_overall_buy_side():
    index = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    history = pd.DataFrame({"rsi": [np.nan, 0, 50, 0, 100], "score": [np.nan, 0, 19.99, 20, 100],
                            "active": [0, 1, 1, 1, 1], "label": [None, "Nötr", "Nötr", "Dip", "Güçlü Dip"]}, index=index)
    signals = signals_for_backtest(history)
    assert list(signals.columns) == ["rsi", OVERALL_KEY], "yalnızca gösterge puanları ve genel skor sinyali olmalı"
    pd.testing.assert_series_equal(signals["rsi"], history["rsi"])
    pd.testing.assert_series_equal(signals[OVERALL_KEY], pd.Series([np.nan, 0.0, 0.0, 1.0, 1.0], index=index),
                                   check_names=False)


def test_real_rsi_backtest_and_overall_match_when_rsi_is_the_only_indicator():
    df = synthetic(900, seed=21)
    report = run_backtests(df, build_indicators(None), "1d", target_pct=20, fee_pct=0.1, ticker="TST")
    assert set(report["results"]) == {"rsi", OVERALL_KEY, "buy_hold"}
    assert report["results"]["rsi"]["available"] and report["results"]["rsi"]["trades"] > 0
    assert report["results"]["rsi"]["trades"] == report["results"][OVERALL_KEY]["trades"]
    assert np.isclose(report["results"]["rsi"]["total_return"], report["results"][OVERALL_KEY]["total_return"])


def test_indicator_with_no_valid_history_is_marked_unavailable():
    df = synthetic(10, seed=15)  # RSI(14) için yetersiz geçmiş
    report = run_backtests(df, [RSIIndicator()], "1M", keys=("rsi",))
    result = report["results"]["rsi"]
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
    test_signals_use_indicator_points_and_overall_buy_side()
    test_real_rsi_backtest_and_overall_match_when_rsi_is_the_only_indicator()
    test_indicator_with_no_valid_history_is_marked_unavailable()
    test_signal_can_reenter_only_after_a_fresh_buy_transition()


if __name__ == "__main__":
    test_all()
    print("Backtest senaryolari gecti.")
