"""Arayüzdeki gösterge satırlarının grafik serilerini hazırlar."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.zones import FRESH_BARS, METRIC_KEYS, bands_for


def indicator_series(raw: pd.DataFrame, key: str, tf: str, params: dict | None = None,
                     fear_greed: pd.Series | None = None):
    """(seri, eşikler, eksen aralığı) döndürür; Ichimoku derecesini gerektiğinde eski raw'dan üretir."""
    if key in METRIC_KEYS:
        return raw[key], bands_for(params, tf)[key], None
    if key == "fear_greed":
        if fear_greed is None:
            raise ValueError("Fear & Greed grafiği için geçmiş seri gerekli.")
        values = fear_greed.reindex(raw.index.normalize()).to_numpy()
        return pd.Series(values, index=raw.index), bands_for(params, "1d")["fear_greed"], [0, 100]
    if key == "cross":
        values = raw["cross_state"].copy()
        for pos in np.flatnonzero(raw["cross_event"].to_numpy()):
            end = min(pos + FRESH_BARS[tf] + 1, len(raw))
            values.iloc[pos:end] = raw["cross_state"].iloc[pos] * 2
        return values, (-2, -1, 0, 1, 2), [-2.2, 2.2]
    if key == "ichimoku":
        if "ichimoku_grade" in raw:
            values = raw["ichimoku_grade"]
        else:  # sıcak yeniden yüklemede eski compute_metrics sonucu korunmuşsa
            sa, sb = raw["ichimoku_span_a"], raw["ichimoku_span_b"]
            top = pd.concat([sa, sb], axis=1).max(axis=1)
            bottom = pd.concat([sa, sb], axis=1).min(axis=1)
            valid = sa.notna() & sb.notna()
            above, below = raw["close"] > top, raw["close"] < bottom
            bullish = above & (raw["ichimoku_tenkan"] > raw["ichimoku_kijun"]) & (sa > sb)
            bearish = below & (raw["ichimoku_tenkan"] < raw["ichimoku_kijun"]) & (sa < sb)
            values = pd.Series(np.select([bullish, bearish, above & valid, below & valid], [2, -2, 1, -1], default=0).astype(float), index=raw.index)
            values.loc[~valid] = np.nan
        return values, (-2, -1, 0, 1, 2), [-2.2, 2.2]
    raise KeyError(f"Grafiği desteklenmeyen gösterge: {key}")
