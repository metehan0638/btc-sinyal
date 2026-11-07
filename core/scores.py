"""Trend ve Aşırılık skorlarının TARİHSEL serisi ve geçmişte ne anlama geldiklerinin ölçümü.

Skorlar `core.zones.split_rows` ile AYNI kuralları kullanır (aynı oylar, aynı ağırlıklar), ama her mum için hesaplanır.
Yalnızca geçmiş mumlar kullanılır: bir mumun skoru, o mumdan sonraki hiçbir veriye bakmaz.
Buradaki tablolar "skor X iken sonraki N mumda ne oldu" sorusunu yanıtlar; gelecek hakkında garanti vermez.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.backtest import grades_for_backtest
from core.zones import OSCILLATOR_KEYS

THRESHOLDS = (0.3, 1.0)  # summarize ile aynı: ortalama >= 0.3 orta, >= 1.0 güçlü (skorda +-15 / +-50)
# Skor ortalamaları için ileri bakış ufukları (mum cinsinden): günlük 30/90/180 gün, haftalık 4/13/26 hafta, aylık 1/3/6 ay
HORIZONS = {"1d": (30, 90, 180), "1w": (4, 13, 26), "1M": (1, 3, 6)}
TREND_NAMES = {-2: "Güçlü düşüş", -1: "Düşüş", 0: "Yatay", 1: "Yükseliş", 2: "Güçlü yükseliş"}
STRETCH_NAMES = {-2: "Aşırı ısınmış", -1: "Isınmış", 0: "Dengeli", 1: "Ucuz", 2: "Dip bölgesi"}
LEVELS_ORDER = (-2, -1, 0, 1, 2)


def score_history(raw: pd.DataFrame, tf: str, params: dict | None = None,
                  fear_greed: pd.Series | None = None) -> pd.DataFrame:
    """Her mum için Trend ve Aşırılık skoru (-100..+100; o mumda hiç geçerli oy yoksa NaN).

    Trend    = ortalama(Ichimoku, cross, fiyatın uzun ortalamaya göre yönü) x 50
    Aşırılık = ortalama(osilatörler [RSI, StochRSI, Bollinger, MFI ortalaması], fiyat/ortalama, ATH'den uzaklık,
               [günlükte Fear & Greed]) x 50
    """
    grades = grades_for_backtest(raw, tf, params, fear_greed)
    trend_votes = pd.DataFrame({"ichimoku": grades["ichimoku"], "cross": grades["cross"],
                                "ma_trend": np.sign(raw["ma_ratio"] - 1)}, index=raw.index)
    stretch = {"oscillators": grades[list(OSCILLATOR_KEYS)].mean(axis=1),
               "ma_ratio": grades["ma_ratio"], "drawdown": grades["drawdown"]}
    if "fear_greed" in grades:
        stretch["fear_greed"] = grades["fear_greed"]
    return pd.DataFrame({"trend": trend_votes.mean(axis=1) * 50,
                         "stretch": pd.DataFrame(stretch, index=raw.index).mean(axis=1) * 50}, index=raw.index)


def levels(score: pd.Series, thresholds=THRESHOLDS) -> pd.Series:
    """Skoru 5 derece etiketine çevirir (-2..+2); raporda görünen etiketle birebir aynı sınırlar."""
    t_mid, t_strong = thresholds
    mean = (score / 50).round(9)
    out = np.select([mean >= t_strong, mean >= t_mid, mean <= -t_strong, mean <= -t_mid], [2, 1, -2, -1], default=0)
    return pd.Series(out, index=score.index, dtype=float).where(score.notna())


def forward_returns(close: pd.Series, horizon: int) -> pd.Series:
    """Her mumun kapanışından `horizon` mum sonraki kapanışa getiri (son `horizon` mum için NaN)."""
    return close.shift(-horizon) / close - 1


def _episodes(level: pd.Series, value: int) -> int:
    """Skorun bu bölgeye girip çıktığı ayrı dönem sayısı (art arda gelen mumlar tek dönem sayılır)."""
    inside = level == value
    return int((inside & ~inside.shift(fill_value=False)).sum())


def bucket_table(close: pd.Series, score: pd.Series, horizon: int, thresholds=THRESHOLDS) -> pd.DataFrame:
    """Skor bölgesine göre `horizon` mum sonraki getiri istatistikleri (+ tüm mumlar için kıyas satırı).

    Sütunlar: n (getirisi ölçülebilen mum), share (zamanın payı), episodes (bölgeye ayrı giriş sayısı),
    mean/median (ileri getiri), pos_rate (getirisi pozitif olanların oranı). Komisyon yoktur.
    """
    level, forward = levels(score, thresholds), forward_returns(close, horizon)
    valid = level.notna() & forward.notna()
    total = int(valid.sum())

    def row(label, mask, episodes):
        r = forward[mask]
        return dict(level=label, n=int(mask.sum()), share=mask.sum() / total if total else np.nan, episodes=episodes,
                    mean=r.mean() if len(r) else np.nan, median=r.median() if len(r) else np.nan,
                    pos_rate=(r > 0).mean() if len(r) else np.nan)

    rows = [row(v, valid & (level == v), _episodes(level, v)) for v in LEVELS_ORDER]
    rows.append(row("all", valid, np.nan))
    return pd.DataFrame(rows).set_index("level")


def matrix_table(close: pd.Series, trend: pd.Series, stretch: pd.Series, horizon: int,
                 thresholds=THRESHOLDS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trend (satır) x Aşırılık (sütun) 3x3 tablosu: her hücre için ortalama ileri getiri ve mum sayısı.

    Trend: düşüş / yatay / yükseliş. Aşırılık: ısınmış / dengeli / ucuz (güçlü/orta ayrımı kaldırılır; hücre başına
    yeterli örnek kalsın diye)."""
    t = np.sign(levels(trend, thresholds))
    s = np.sign(levels(stretch, thresholds))
    forward = forward_returns(close, horizon)
    valid = t.notna() & s.notna() & forward.notna()
    rows, cols = {-1: "Düşüş", 0: "Yatay", 1: "Yükseliş"}, {-1: "Isınmış", 0: "Dengeli", 1: "Ucuz"}
    mean = pd.DataFrame(index=list(rows.values()), columns=list(cols.values()), dtype=float)
    count = pd.DataFrame(0, index=list(rows.values()), columns=list(cols.values()), dtype=int)
    for tv, tname in rows.items():
        for sv, sname in cols.items():
            mask = valid & (t == tv) & (s == sv)
            count.loc[tname, sname] = int(mask.sum())
            mean.loc[tname, sname] = forward[mask].mean() if mask.any() else np.nan
    return mean, count
