"""Sentetik veriyle çekirdek mantığı test eder (internet gerekmez). Çalıştır: python tests/test_core.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.backtest import run_backtest
from core.consensus import build_signals
from core.indicators import compute_indicators, latest_table


def synthetic(n=1500, seed=1):
    rng = np.random.default_rng(seed)
    close = 1000 * np.exp(np.cumsum(rng.normal(0.0008, 0.03, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.02, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.02, n))
    vol = rng.uniform(1000, 5000, n)
    idx = pd.date_range("2018-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(dict(open=open_, high=high, low=low, close=close, volume=vol), index=idx)


def test_all():
    df = synthetic()
    votes, raw = compute_indicators(df, dict(ema_fast=50, ema_slow=200))
    assert votes.shape[1] == 10
    assert set(np.unique(votes.dropna().values)) <= {-1.0, 0.0, 1.0}
    assert votes.iloc[:100].isna().any(axis=1).all(), "ısınma döneminde NaN olmalı"
    assert votes.iloc[-1].notna().all()
    # RSI 0-100 aralığında olmalı
    assert raw.rsi.dropna().between(0, 100).all()
    sig = build_signals(votes, 8)
    assert (sig.bull + sig.bear + sig.neutral).eq(10).all()
    assert not ((sig.state == 1) & (sig.state == -1)).any()
    bt = run_backtest(df, sig, 8, 4, 0.001, 365)
    print(latest_table(votes, raw).to_string())
    print("\nSon sinyal:", sig.iloc[-1].to_dict())
    print("\nBacktest:", {k: round(v, 2) for k, v in bt["metrics"].items()})
    print("Al-tut  :", {k: round(v, 2) for k, v in bt["bh_metrics"].items() if k != "İşlem sayısı"})
    print("\nİşlem sayısı:", len(bt["trades"]), "| sinyal olayı:", int((sig.event != 0).sum()))
    # lookahead kontrolü: son mumu silince önceki sinyaller değişmemeli
    v2, _ = compute_indicators(df.iloc[:-30], dict(ema_fast=50, ema_slow=200))
    s2 = build_signals(v2, 8)
    assert s2.state.equals(sig.state.iloc[:-30]), "Geleceği gören hesaplama var!"
    print("\nTüm testler geçti ✔")


if __name__ == "__main__":
    test_all()
