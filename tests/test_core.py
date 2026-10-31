"""Dip/şişkinlik göstergelerini sentetik veriyle test eder (internet gerekmez). Çalıştır: python tests/test_core.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.zones import (METRIC_KEYS, classify, compute_metrics, latest_rows, summarize, zone_votes, zones_for)


def synthetic(n=1500, seed=1):
    rng = np.random.default_rng(seed)
    close = 1000 * np.exp(np.cumsum(rng.normal(0.0008, 0.03, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.02, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.02, n))
    vol = rng.uniform(1000, 5000, n)
    idx = pd.date_range("2018-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(dict(open=open_, high=high, low=low, close=close, volume=vol), index=idx)


def trend(close):
    close = np.asarray(close, float)
    idx = pd.date_range("2020-01-01", periods=len(close), freq="D", tz="UTC")
    return pd.DataFrame(dict(open=np.r_[close[0], close[:-1]], high=close * 1.001, low=close * 0.999,
                             close=close, volume=np.full(len(close), 1000.0)), index=idx)


def test_all():
    # 1) değer aralıkları
    df = synthetic()
    raw = compute_metrics(df)
    assert raw.rsi.dropna().between(0, 100).all() and raw.mfi.dropna().between(0, 100).all()
    assert raw.stochrsi.dropna().between(0, 100).all()
    assert (raw.drawdown.dropna() <= 0).all() and (raw.ma_ratio.dropna() > 0).all()
    assert raw.iloc[:100].rsi.isna().sum() >= 14 and raw.iloc[:100].ma_ratio.isna().all(), "ısınma döneminde NaN olmalı"
    votes = zone_votes(raw)
    assert list(votes.columns) == METRIC_KEYS and set(np.unique(votes.dropna().values)) <= {-1.0, 0.0, 1.0}

    # 2) GELECEĞE BAKMAMA: son mumları silince önceki değerler değişmemeli
    for cut in (600, 1000, 1400):
        part = compute_metrics(df.iloc[:cut])
        a, b = raw.iloc[:cut], part
        assert ((a == b) | (a.isna() & b.isna())).all().all(), f"{cut}: geleceği gören hesap var!"

    # 3) bilinen cevaplar: sürekli düşen fiyat -> dip, sürekli yükselen fiyat -> şişmiş
    down = latest_rows(compute_metrics(trend(100 * 0.99 ** np.arange(400))), {}, "1d")
    z = {r["key"]: r["zone"] for r in down}
    assert z["rsi"] == z["mfi"] == z["ma_ratio"] == z["drawdown"] == 1, z
    assert summarize(down)["zone"] == "dip"
    up = latest_rows(compute_metrics(trend(100 * 1.01 ** np.arange(400))), {}, "1d")
    z = {r["key"]: r["zone"] for r in up}
    assert z["rsi"] == z["mfi"] == z["ma_ratio"] == z["drawdown"] == -1, z
    assert summarize(up)["zone"] == "hot"
    # orta değerler -> normal; eşik sınırları dahil (<= ucuz, >= şişmiş)
    def with_last(**vals):
        r = raw.copy()
        for k, v in vals.items():
            r.loc[r.index[-1], k] = v
        return {x["key"]: x["zone"] for x in latest_rows(r, {}, "1d")}
    mid = dict(rsi=50, stochrsi=50, bollinger=0.5, mfi=50, ma_ratio=1.0, drawdown=-30)
    assert set(with_last(**mid).values()) == {0}
    assert summarize(latest_rows(raw, {}, "1d"))["n"] == 6
    assert with_last(**{**mid, "rsi": 30})["rsi"] == 1 and with_last(**{**mid, "rsi": 30.1})["rsi"] == 0
    assert with_last(**{**mid, "rsi": 70})["rsi"] == -1 and with_last(**{**mid, "rsi": 69.9})["rsi"] == 0
    assert with_last(**{**mid, "drawdown": -50})["drawdown"] == 1 and with_last(**{**mid, "drawdown": -10})["drawdown"] == -1

    # 4) eşik mantığı (<= ucuz, >= şişmiş) ve hatalı ayar
    s = pd.Series([10, 30, 31, 69, 70, 95, np.nan])
    assert classify(s, 30, 70).tolist()[:6] == [1, 1, 0, 0, -1, -1] and np.isnan(classify(s, 30, 70).iloc[6])
    try:
        zones_for({"zones": {"rsi": [70, 30]}}); assert False
    except ValueError:
        pass
    assert zones_for({"zones": {"ma_ratio": [1.2, 3.5]}})["ma_ratio"] == (1.2, 3.5)
    assert zones_for({})["rsi"] == (30, 70), "belirtilmeyen eşikler varsayılan kalmalı"

    # 5) özet: çoğunluk kararı, beraberlikte normal, veri yoksa nodata
    mk = lambda *zs: [dict(zone=v) for v in zs]
    assert summarize(mk(1, 1, 1, 0, 0, 0))["zone"] == "dip"          # 3/6 = 0.5
    assert summarize(mk(1, 1, 0, 0, 0, 0))["zone"] == "normal"       # 2/6
    assert summarize(mk(-1, -1, -1, 1, 0, 0))["zone"] == "hot"
    assert summarize(mk(1, 1, -1, -1))["zone"] == "normal", "ucuz = şişmiş ise normal"
    assert summarize(mk(np.nan, np.nan))["zone"] == "nodata"
    assert summarize(mk(1, 1, 1, np.nan, np.nan, np.nan))["n"] == 3, "veri olmayanlar paydaya girmemeli"

    # 6) Fear & Greed satırı yalnızca verilirse eklenir; düşük endeks (korku) = ucuz
    fg = dict(value=15.0, label="Aşırı korku", yesterday=18.0, week=30.0, month=None)
    with_fg = latest_rows(raw, {}, "1d", fg)
    assert with_fg[-1]["key"] == "fear_greed" and with_fg[-1]["zone"] == 1 and "dün 18" in with_fg[-1]["text"]
    assert len(latest_rows(raw, {}, "1d")) == len(with_fg) - 1
    assert latest_rows(raw, {}, "1d", dict(fg, value=80.0, label="Aşırı açgözlülük"))[-1]["zone"] == -1
    assert "haftalık" in [r for r in latest_rows(raw, {"ma_long": 50}, "1w") if r["key"] == "ma_ratio"][0]["name"]

    print(pd.DataFrame(latest_rows(raw, {}, "1d", fg))[["name", "text", "zone", "note"]].to_string(index=False))
    print("\nTüm testler geçti ✔")


if __name__ == "__main__":
    test_all()
