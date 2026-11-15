"""Dış günlük verinin mumlara hizalanması ve süreç içi önbellek testleri (internet gerekmez)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import external
from core.external import align_daily, bar_end, infer_timeframe, memoize


def utc(*args):
    return pd.Timestamp(*args, tz="UTC")


def daily_series(start="2024-01-01", days=120, values=None):
    index = pd.date_range(start, periods=days, freq="D", tz="UTC")
    return pd.Series(np.arange(days, dtype=float) if values is None else values, index=index)


def week_bars(start="2024-01-01", n=8):
    return pd.date_range(start, periods=n, freq="W-MON", tz="UTC")


def month_bars(start="2024-01-01", n=3):
    return pd.date_range(start, periods=n, freq="MS", tz="UTC")


def test_infer_timeframe_and_bar_end():
    assert infer_timeframe(pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")) == "1d"
    assert infer_timeframe(week_bars(n=5)) == "1w" and infer_timeframe(month_bars(n=5)) == "1M"
    assert infer_timeframe(pd.date_range("2024-01-01", periods=1, tz="UTC")) == "1d"
    assert bar_end(utc("2024-01-01"), "1d") == utc("2024-01-01") and bar_end(utc("2024-01-01"), "1w") == utc("2024-01-07")
    assert bar_end(utc("2024-02-01"), "1M") == utc("2024-02-29") and bar_end(utc("2023-02-01"), "1M") == utc("2023-02-28")
    assert bar_end(utc("2024-12-01"), "1M") == utc("2024-12-31")
    try:
        bar_end(utc("2024-01-01"), "4h")
        assert False
    except ValueError:
        pass


def test_daily_alignment():
    daily = daily_series(days=10).drop(utc("2024-01-04"))
    bars = pd.date_range("2024-01-01", periods=12, freq="D", tz="UTC")
    out = align_daily(daily, bars, "1d")
    assert out.index.equals(bars)
    assert out.iloc[0] == 0 and out.iloc[8] == 8 and out.iloc[9] == 9 and np.isnan(out.iloc[3]), "eksik gün NaN"
    assert out.iloc[10:].isna().all(), "veri bitince değer yok"


def test_weekly_and_monthly_averages_require_complete_periods():
    daily = daily_series(days=90)  # 2024-01-01 (Pzt) .. 2024-03-30
    weeks = align_daily(daily, week_bars(n=9), "1w", "mean")
    assert weeks.iloc[0] == 3.0 and weeks.iloc[1] == 10.0, "ilk hafta (0..6) ortalaması 3, ikinci (7..13) 10"
    assert align_daily(daily, week_bars(n=9), "1w", "last").iloc[0] == 6.0
    assert weeks.iloc[:-1].notna().all() and weeks.iloc[-1] == 3.0 + 7 * 8, "son tam hafta"
    # 2024 artık yıl: Şubat 29 gün; ocak ortalaması 0..30 -> 15, şubat 31..59 -> 45
    months = align_daily(daily, month_bars(n=3), "1M", "mean")
    assert months.iloc[0] == 15.0 and months.iloc[1] == 45.0
    assert np.isnan(months.iloc[2]), "mart henüz bitmedi (30 Mart'a kadar veri var): değer üretilmemeli"
    assert align_daily(daily, month_bars(n=2), "1M", "last").iloc[1] == 59.0
    # eksik günü olan dönem NaN, komşu dönemler etkilenmez
    gap = daily.drop(utc("2024-01-10"))
    gapped = align_daily(gap, week_bars(n=4), "1w")
    assert np.isnan(gapped.iloc[1]) and gapped.iloc[0] == 3.0 and gapped.iloc[2] == 17.0
    # veri haftanın ortasında başlıyorsa o hafta eksik sayılır
    late = daily.loc["2024-01-04":]
    first = align_daily(late, week_bars(n=3), "1w")
    assert np.isnan(first.iloc[0]) and first.iloc[1] == 10.0
    assert np.isnan(align_daily(late, month_bars(n=2), "1M").iloc[0])


def test_stale_carry_forward_is_limited_and_causal():
    daily = daily_series(days=10)  # 2024-01-01 .. 2024-01-10
    bars = pd.date_range("2024-01-08", periods=7, freq="D", tz="UTC")
    carried = align_daily(daily, bars, "1d", max_stale_days=2)
    assert list(carried.iloc[:3]) == [7.0, 8.0, 9.0] and list(carried.iloc[3:5]) == [9.0, 9.0], "en fazla 2 gün taşınır"
    assert carried.iloc[5:].isna().all()
    assert align_daily(daily, bars, "1d", max_stale_days=0).iloc[3:].isna().all()
    # veri 2 gün eksik biten haftalık mum: taşıma, haftanın son günlerini doldurursa geçerli olur
    ends_friday = daily_series(days=12).iloc[:11]  # 2024-01-01 .. 2024-01-11 (Perşembe)
    assert np.isnan(align_daily(ends_friday, week_bars(n=2), "1w", max_stale_days=0).iloc[1])
    filled = align_daily(ends_friday, week_bars(n=2), "1w", max_stale_days=3)
    assert filled.iloc[1] == np.mean([7, 8, 9, 10, 10, 10, 10]), "Cmt-Paz son değerle dolar"
    # gelecek verisi geçmişi değiştirmez
    full = daily_series(days=80)
    bars8 = week_bars(n=8)
    cut = align_daily(full.iloc[:56], bars8, "1w")  # 8 haftalık veri
    pd.testing.assert_series_equal(cut, align_daily(full, bars8, "1w"))


def test_invalid_arguments_and_empty_inputs():
    for args in (dict(how="median"), dict(tf="4h")):
        try:
            align_daily(daily_series(days=10), week_bars(n=1), args.get("tf", "1w"), args.get("how", "mean"))
            assert False, args
        except ValueError:
            pass
    empty = align_daily(pd.Series(dtype=float), week_bars(n=3), "1w")
    assert empty.isna().all() and empty.index.equals(week_bars(n=3))
    assert len(align_daily(daily_series(days=10), pd.DatetimeIndex([], tz="UTC"), "1d")) == 0
    dup = pd.concat([daily_series(days=5), daily_series(days=1, values=[99.0])])  # tekrarlı gün: sonuncusu geçerli
    assert align_daily(dup, pd.date_range("2024-01-01", periods=1, tz="UTC"), "1d").iloc[0] == 99.0


def test_memoize_caches_values_and_errors():
    now = {"t": 0.0}
    calls = {"n": 0}

    def source():
        calls["n"] += 1
        return calls["n"]
    cached = memoize(source, ttl=100, error_ttl=10, clock=lambda: now["t"])
    assert cached() == 1 and cached() == 1 and calls["n"] == 1
    now["t"] = 99
    assert cached() == 1
    now["t"] = 101
    assert cached() == 2, "süre dolunca yeniden alınır"
    cached.clear()
    assert cached() == 3

    state = {"fail": True, "n": 0}

    def flaky():
        state["n"] += 1
        if state["fail"]:
            raise RuntimeError("kaynak çöktü")
        return "ok"
    guarded = memoize(flaky, ttl=100, error_ttl=10, clock=lambda: now["t"])
    now["t"] = 0
    for _ in range(3):
        try:
            guarded()
            assert False
        except RuntimeError as e:
            assert "çöktü" in str(e)
    assert state["n"] == 1, "hata kısa süre hatırlanır; her çağrıda yeniden (bekleyerek) denenmez"
    state["fail"] = False
    now["t"] = 11
    assert guarded() == "ok" and state["n"] == 2, "hata süresi dolunca yeniden denenir"


def test_all():
    test_infer_timeframe_and_bar_end()
    test_daily_alignment()
    test_weekly_and_monthly_averages_require_complete_periods()
    test_stale_carry_forward_is_limited_and_causal()
    test_invalid_arguments_and_empty_inputs()
    test_memoize_caches_values_and_errors()


if __name__ == "__main__":
    test_all()
    print("Dış veri hizalama testleri geçti ✔")
