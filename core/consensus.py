"""Tüm indikatörlerin (10 ya da Fear&Greed ile 11) oylarından konsensüs sinyali üretir."""
from __future__ import annotations

import pandas as pd


def build_signals(votes: pd.DataFrame, threshold: int = 8) -> pd.DataFrame:
    """
    state : +1 (>=threshold indikatör AL), -1 (>=threshold SAT), 0 (karışık/nötr)
    event : state'in 0 veya ters yönden +1/-1'e GEÇTİĞİ mum (yeni sinyal anı)
    valid : tüm indikatörler için yeterli veri var mı
    """
    n = votes.shape[1]
    threshold = max(int(threshold), n // 2 + 1)  # eşik çoğunluğun altındaysa AL ve SAT aynı anda oluşabilir
    out = pd.DataFrame(index=votes.index)
    out["valid"] = votes.notna().all(axis=1)
    out["bull"] = (votes == 1).sum(axis=1)
    out["bear"] = (votes == -1).sum(axis=1)
    out["neutral"] = n - out["bull"] - out["bear"]
    out["score"] = out["bull"] - out["bear"]

    state = pd.Series(0, index=votes.index)
    state[out["bull"] >= threshold] = 1
    state[out["bear"] >= threshold] = -1
    state[~out["valid"]] = 0
    out["state"] = state

    changed = state != state.shift(fill_value=0)
    out["event"] = state.where(changed & (state != 0), 0)
    return out
