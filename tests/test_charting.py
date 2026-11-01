"""UI indicator/chart parity checks (offline)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.charting import indicator_series
from core.zones import METRIC_KEYS, compute_metrics, latest_rows
from test_core import synthetic

df = synthetic(1500, seed=8)
raw = compute_metrics(df)
rows = latest_rows(raw, {}, "1d", dict(value=40, label="Korku"))
expected = {r["key"] for r in rows if r["key"] != "fear_greed"} | {"fear_greed"}
assert expected == set(METRIC_KEYS) | {"ichimoku", "cross", "fear_greed"}
fg = pd.Series(np.linspace(10, 90, len(df)), index=df.index)
for key in expected:
    series, bands, axis_range = indicator_series(raw, key, "1d", {}, fg if key == "fear_greed" else None)
    assert len(series) == len(raw) and series.index.equals(raw.index), key

# Simulate a hot-reloaded older calculation frame with Ichimoku components but no derived grade.
legacy_raw = raw.drop(columns="ichimoku_grade")
series, _, _ = indicator_series(legacy_raw, "ichimoku", "1d", {})
assert series.notna().any() and series.dropna().isin([-2, -1, 0, 1, 2]).all()
print("Grafik/güncel durum gösterge uyumu geçti ✔")
