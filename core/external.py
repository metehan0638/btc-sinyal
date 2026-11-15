"""Dış (günlük) veri serilerini varlığın mumlarına hizalama ve süreç içi önbellek.

Fear & Greed ve MVRV gibi veriler GÜNLÜK gelir; haftalık/aylık mum için o dönemin günlük değerlerinin ortalaması (veya son değeri) alınır.
Bir dönem ancak TÜM günleri varsa geçerlidir: eksik günlü dönem için değer üretilmez (NaN), yarım veriden sahte ortalama çıkmaz.
"""
from __future__ import annotations

import time

import pandas as pd

RULES = {"1w": "W-MON", "1M": "MS"}  # Binance haftalık mumu Pazartesi 00:00 UTC, aylık mum ayın 1'i 00:00 UTC açılır
HOW = ("mean", "last")


def infer_timeframe(index: pd.DatetimeIndex) -> str:
    """Mum aralığından zaman dilimini tahmin eder (tf verilmediğinde yedek): ~1 gün, ~1 hafta veya ~1 ay."""
    if len(index) < 2:
        return "1d"
    days = pd.Series(index).diff().dropna().dt.total_seconds().median() / 86400
    return "1d" if days < 3 else ("1w" if days < 15 else "1M")


def bar_end(open_time: pd.Timestamp, tf: str) -> pd.Timestamp:
    """Mumun SON gününün (UTC gün başı) zamanı."""
    if tf == "1d":
        return open_time
    if tf == "1w":
        return open_time + pd.Timedelta(days=6)
    if tf == "1M":
        return open_time + pd.offsets.MonthEnd(0)
    raise ValueError(f"Desteklenmeyen zaman dilimi: {tf}")


def align_daily(daily: pd.Series, bars: pd.DatetimeIndex, tf: str, how: str = "mean", max_stale_days: int = 0) -> pd.Series:
    """Günlük seriyi `bars` (mum AÇILIŞ zamanları, UTC) indeksine hizalar.

    1d: mumun günündeki değer. 1w / 1M: mumun tüm günlerinin ortalaması (how="mean") veya son günün değeri (how="last").
    max_stale_days: günlük seride eksik günler için son bilinen değerin en fazla kaç gün ileri taşınacağı (0 = taşıma yok).
    Yalnızca GEÇMİŞ değer ileri taşınır, gelecekten değer alınmaz. Eksik günü olan haftalık/aylık mum için NaN döner."""
    if how not in HOW:
        raise ValueError(f"how 'mean' veya 'last' olmalı, verilen: {how}")
    if tf not in ("1d", *RULES):
        raise ValueError(f"Desteklenmeyen zaman dilimi: {tf}")
    if len(bars) == 0 or daily is None or len(daily) == 0:
        return pd.Series(float("nan"), index=bars, dtype=float)
    daily = daily[~daily.index.duplicated(keep="last")].sort_index().astype(float)
    start = min(bars[0], daily.index[0]).floor("D")
    full = daily.reindex(pd.date_range(start, bar_end(bars[-1], tf), freq="D", tz="UTC"))
    if max_stale_days:
        full = full.ffill(limit=int(max_stale_days))
    if tf == "1d":
        return full.reindex(bars).astype(float)
    grouped = full.resample(RULES[tf], label="left", closed="left")
    value = grouped.mean() if how == "mean" else grouped.last()
    expected = pd.Series([(bar_end(t, tf) - t).days + 1 for t in value.index], index=value.index)
    return value.where(grouped.count() == expected).reindex(bars).astype(float)


def memoize(fn, ttl: float = 3600.0, error_ttl: float = 300.0, clock=time.monotonic):
    """Argümansız `fn` sonucunu `ttl` saniye hatırlar. Hata da `error_ttl` saniye hatırlanır: kaynak çökmüşken her çağrıda
    yeniden (bekleyerek) denenmez, aynı hata yeniden fırlatılır. `.clear()` önbelleği boşaltır."""
    state: dict = {}

    def wrapper():
        now = clock()
        if "at" in state:
            age = now - state["at"]
            if "error" in state and age < error_ttl:
                raise state["error"]
            if "value" in state and age < ttl:
                return state["value"]
        state.clear()
        try:
            value = fn()
        except Exception as e:  # noqa: BLE001
            state.update(at=now, error=e)
            raise
        state.update(at=now, value=value)
        return value

    wrapper.clear = state.clear
    return wrapper
