"""Gösterge derecelerini sentetik veriyle test eder (internet gerekmez). Çalıştır: python tests/test_core.py"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.zones import (METRIC_KEYS, bands_for, compute_metrics, cross_events, cross_row, grade_series, grade_value,
                        grade_votes, latest_rows, summarize, trend_info)


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
    votes = grade_votes(raw)
    assert list(votes.columns) == METRIC_KEYS and set(np.unique(votes.dropna().values)) <= {-2.0, -1.0, 0.0, 1.0, 2.0}

    # Aylık SOL geçmişi 9/26/52 Ichimoku bulutunun 78 mumluk ısınma süresinden kısa olabilir.
    monthly_params = {"ma_long": 24, "cross": (6, 18), "ichimoku": (9, 26, 52)}
    short_monthly_raw = compute_metrics(synthetic(60, seed=5), monthly_params)
    short_monthly_rows = latest_rows(short_monthly_raw, monthly_params, "1M")
    short_ichimoku = next(row for row in short_monthly_rows if row["key"] == "ichimoku")
    assert pd.isna(short_ichimoku["grade"]) and short_ichimoku["text"] == "-"

    # 2) GELECEĞE BAKMAMA: son mumları silince önceki değerler değişmemeli (cross sütunları dahil)
    for cut in (600, 1000, 1400):
        a, b = raw.iloc[:cut], compute_metrics(df.iloc[:cut])
        assert ((a == b) | (a.isna() & b.isna())).all().all(), f"{cut}: geleceği gören hesap var!"

    # 3) bilinen cevaplar: sürekli düşen fiyat -> AL tarafı, sürekli yükselen fiyat -> SAT tarafı
    down = latest_rows(compute_metrics(trend(100 * 0.99 ** np.arange(400))), {}, "1d")
    g = {r["key"]: r["grade"] for r in down}
    assert g["rsi"] == g["mfi"] == g["ma_ratio"] == g["drawdown"] == 2, g
    assert g["cross"] == -1, "dead cross yapısı (eski kesişim, yeni değil) = SAT (-1)"
    s = summarize(down)
    assert s["key"] == "buy" and s["level"] == 1 and s["n"] == 7, s      # Ichimoku kendi derecesiyle ayrı satırdır
    up = latest_rows(compute_metrics(trend(100 * 1.01 ** np.arange(400))), {}, "1d")
    g = {r["key"]: r["grade"] for r in up}
    assert g["rsi"] == g["mfi"] == g["drawdown"] == -2 and g["ma_ratio"] == -1 and g["cross"] == 1, g
    assert summarize(up)["key"] == "sell"

    # 4) eşik sınırları: (güçlü al <= t1, al <= t2, nötr, sat >= t3, güçlü sat >= t4)
    def grade_at(key, tf="1d", **vals):
        r = raw.copy()
        for k, v in vals.items():
            r.loc[r.index[-1], k] = v
        return {x["key"]: x["grade"] for x in latest_rows(r, {}, tf)}[key]
    cases = {"rsi": [(20, 2), (20.1, 1), (30, 1), (30.1, 0), (69.9, 0), (70, -1), (79.9, -1), (80, -2)],
             "stochrsi": [(10, 2), (10.5, 1), (20, 1), (20.5, 0), (79.5, 0), (80, -1), (89.9, -1), (90, -2)],
             "bollinger": [(-0.1, 2), (0.0, 2), (0.05, 1), (0.10, 1), (0.5, 0), (0.9, -1), (0.99, -1), (1.0, -2)],
             "mfi": [(10, 2), (20, 1), (50, 0), (80, -1), (90, -2)],
             "drawdown": [(-80, 2), (-65, 2), (-64.9, 1), (-50, 1), (-49.9, 0), (-10.1, 0), (-10, -1), (-3.1, -1), (-3, -2), (0, -2)]}
    for key, cs in cases.items():
        for v, want in cs:
            assert grade_at(key, **{key: v}) == want, f"{key}={v}: {grade_at(key, **{key: v})} != {want}"
    # fiyat/uzun ortalama: günlük ve haftalık ölçekleri farklı
    for v, want in [(0.75, 2), (0.9, 1), (1.19, 0), (2.0, -1), (2.4, -2)]:
        assert grade_at("ma_ratio", "1d", ma_ratio=v) == want, ("1d", v)
    for v, want in [(0.9, 2), (1.19, 1), (1.5, 0), (2.5, -1), (3.5, -2)]:
        assert grade_at("ma_ratio", "1w", ma_ratio=v) == want, ("1w", v)

    # 5) aralık ayarları: geçersiz girişler reddedilir, config varsayılanın üstüne yazar
    assert bands_for({})["rsi"] == (20, 30, 70, 80)
    assert bands_for({"bands": {"rsi": [15, 25, 75, 85]}})["rsi"] == (15, 25, 75, 85)
    assert bands_for({}, "1w")["ma_ratio"] == (1.0, 1.3, 2.5, 3.5) and bands_for({"bands": {"ma_ratio": [1, 2, 3, 4]}}, "1w")["ma_ratio"] == (1, 2, 3, 4)
    for bad in ({"bands": {"rsi": [30, 20, 70, 80]}}, {"bands": {"rsi": [30, 70]}}, {"zones": {"rsi": [30, 70]}}):
        try:
            bands_for(bad); assert False, bad
        except ValueError:
            pass
    assert grade_value(np.nan, (1, 2, 3, 4)) != grade_value(np.nan, (1, 2, 3, 4)), "veri yoksa NaN"
    assert grade_series(pd.Series([5, 25, 50, 75, 95]), (10, 24, 75, 90)).tolist() == [2, 0, 0, -1, -2]

    # 6) genel derece: puan ortalaması ve sınırlar (>=1.0 güçlü al, >=0.3 al, -0.3..0.3 nötr, <=-0.3 sat, <=-1.0 güçlü sat)
    mk = lambda *gs: [dict(grade=v) for v in gs]
    cases = [((2, 2, 0, 0), "strong_buy"),                  # 1.0
             ((2, 1, 0, 0, 0), "buy"),                      # 0.6
             ((1, 1, 1, 0, 0, 0, 0, 0, 0, 0), "buy"),       # 0.3 tam sınır
             ((1, 1, 0, 0, 0, 0, 0), "neutral"),            # 0.2857
             ((2, -2), "neutral"),
             ((-1, -1, -1, 0, 0, 0, 0, 0, 0, 0), "sell"),   # -0.3
             ((-2, -2, 0, 0), "strong_sell"),               # -1.0
             ((-2, -1, 0, 0, 0), "sell"),                   # -0.6
             ((2, 2, 2, np.nan, np.nan), "strong_buy")]     # veri olmayanlar paydaya girmez
    for gs, want in cases:
        assert summarize(mk(*gs))["key"] == want, (gs, summarize(mk(*gs)))
    assert summarize(mk(np.nan, np.nan))["key"] == "nodata" and summarize(mk(2, 1, 0, -1, -2, 0))["counts"] == {2: 1, 1: 1, 0: 2, -1: 1, -2: 1}
    assert summarize(mk(1, 0, 0, 0), thresholds=(0.2, 0.5))["key"] == "buy" and summarize(mk(2, 0, 0, 0), thresholds=(0.2, 0.5))["key"] == "strong_buy"
    for bad in ((0, 1), (1.0, 0.5), (0.3, 3)):
        try:
            summarize(mk(1), thresholds=bad); assert False, bad
        except ValueError:
            pass

    # 7) Fear & Greed satırı yalnızca verilirse eklenir; korku (düşük) = AL tarafı
    fg = dict(value=15.0, label="Aşırı korku", yesterday=18.0, week=30.0, month=None)
    with_fg = latest_rows(raw, {}, "1d", fg)
    fgrow = [r for r in with_fg if r["key"] == "fear_greed"][0]
    assert fgrow["grade"] == 1 and "dün 18" in fgrow["text"] and with_fg[-1]["key"] == "cross"
    assert len(latest_rows(raw, {}, "1d")) == len(with_fg) - 1 == 8
    assert [r for r in latest_rows(raw, {}, "1d", dict(fg, value=95.0, label="Aşırı açgözlülük")) if r["key"] == "fear_greed"][0]["grade"] == -2
    assert "haftalık" in [r for r in latest_rows(raw, {"ma_long": 50}, "1w") if r["key"] == "ma_ratio"][0]["name"]

    # 8) golden / dead cross: önce düşüş (dead yapısı), sonra güçlü yükseliş (golden), sonra sert düşüş (dead)
    price = np.r_[np.linspace(120, 100, 300), np.linspace(100, 200, 150), np.linspace(200, 60, 300)]
    craw = compute_metrics(trend(price))
    evs = cross_events(craw)
    assert [k for _, k in evs] == [1, -1], f"beklenen golden sonra dead: {evs}"
    assert craw.cross_state.dropna().iloc[0] == -1 and craw.cross_state.iloc[-1] == -1
    g_ts, d_ts = evs[0][0], evs[1][0]
    assert craw.cross_state.loc[g_ts:d_ts].iloc[1:-1].eq(1).all(), "iki kesişim arasında golden yapısı sürmeli"
    assert cross_events(craw, since=g_ts) == [evs[1]], "since sonrasını vermeli"
    ti = trend_info(craw.loc[:g_ts + pd.Timedelta(days=3)], {}, "1d")      # golden'dan 3 gün sonra: yeni
    assert ti["state"] == 1 and ti["fresh"] and "Golden cross yapısı" in ti["text"] and "3 gün önce" in ti["last_text"], ti
    assert cross_row(ti)["grade"] == 2 and "🆕" in cross_row(ti)["text"], "yeni golden cross = GÜÇLÜ AL (+2)"
    ti = trend_info(craw.loc[:g_ts + pd.Timedelta(days=60)], {}, "1d")     # 60 gün sonra: eski
    assert ti["state"] == 1 and not ti["fresh"] and cross_row(ti)["grade"] == 1, "eski golden yapısı = AL (+1)"
    ti = trend_info(craw.loc[:d_ts + pd.Timedelta(days=2)], {}, "1d")
    assert cross_row(ti)["grade"] == -2, "yeni dead cross = GÜÇLÜ SAT (-2)"
    ti = trend_info(craw, {}, "1d")                                         # dead'den uzun süre sonra
    assert ti["state"] == -1 and not ti["fresh"] and cross_row(ti)["grade"] == -1 and "Dead cross yapısı" in ti["text"]
    nan_ti = trend_info(craw.iloc[:100], {}, "1d")
    assert nan_ti["state"] != nan_ti["state"] and cross_row(nan_ti)["grade"] != cross_row(nan_ti)["grade"], "SMA200 hazır değilken NaN"
    assert "hafta önce" in trend_info(craw.loc[:g_ts + pd.Timedelta(days=21)], {}, "1w")["last_text"]
    assert cross_events(compute_metrics(trend(price), {"cross": (20, 50)})), "20/50 de kesişim bulmalı"
    try:
        compute_metrics(trend(price), {"cross": (200, 50)}); assert False
    except ValueError:
        pass

    print(pd.DataFrame(latest_rows(raw, {}, "1d", fg))[["name", "text", "grade", "note"]].to_string(index=False))
    print("\nTüm testler geçti ✔")


if __name__ == "__main__":
    test_all()
