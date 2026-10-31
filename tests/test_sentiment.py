"""Fear & Greed testleri; internet gerekmez."""
import sys, tempfile, time, urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import synthetic
from core import sentiment
from core.zones import compute_metrics, latest_rows, summarize

time.sleep = lambda s: None
days = pd.date_range("2018-02-01", periods=1000, freq="D", tz="UTC")
rng = np.random.default_rng(1)
fg = pd.Series(np.clip(50 + rng.normal(0, 25, len(days)), 1, 99).round(), index=days)

# 1) API yanıtı ayrıştırma (alternative.me biçimi: yeni -> eski sıralı, string değerler)
payload = {"data": [{"value": str(int(v)), "value_classification": "x", "timestamp": str(int(d.timestamp()))}
                    for d, v in list(fg.items())[::-1]]}
parsed = sentiment._parse(payload)
assert parsed.index.is_monotonic_increasing and len(parsed) == len(fg) and parsed.iloc[-1] == fg.iloc[-1]

# 2) sınıflar ve özet
assert [sentiment.classify(v) for v in (10, 30, 50, 60, 90)] == ["Aşırı korku", "Korku", "Nötr", "Açgözlülük", "Aşırı açgözlülük"]
info = sentiment.summary(parsed)
assert info["value"] == parsed.iloc[-1] and info["yesterday"] == parsed.iloc[-2] and info["month"] == parsed.iloc[-31]

# 3) PUANA DAHİL: korku düşük değer = ucuz bölge, açgözlülük = şişmiş; sınıf sınırlarıyla uyumlu
raw = compute_metrics(synthetic(1500, seed=2))
zone_of = lambda v: [r for r in latest_rows(raw, {}, "1d", dict(info, value=float(v), label=sentiment.classify(v)))
                     if r["key"] == "fear_greed"][0]["zone"]
assert [zone_of(v) for v in (10, 24, 25, 50, 74, 75, 90)] == [1, 1, 0, 0, 0, -1, -1]
assert sentiment.classify(24) == "Aşırı korku" and sentiment.classify(25) == "Korku" and sentiment.classify(75) == "Aşırı açgözlülük"
assert all(r["key"] != "fear_greed" for r in latest_rows(raw, {}, "1w")), "haftalıkta F&G olmamalı"
assert summarize(latest_rows(raw, {}, "1d", info))["n"] == 7 and summarize(latest_rows(raw, {}, "1d"))["n"] == 6

# 4) API çökerse: yeniden dene -> taze önbellek -> yoksa hata
def boom(*a, **k): raise OSError("bağlantı yok")
urllib.request.urlopen = boom
with tempfile.TemporaryDirectory() as tmp:
    sentiment.CACHE_FILE = Path(tmp) / "fear_greed.csv"
    try: sentiment.fetch_fear_greed(); assert False
    except RuntimeError as e: assert "alınamadı" in str(e)
    fresh = parsed.copy(); fresh.index = pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=len(fresh), freq="D")
    fresh.rename_axis("date").to_csv(sentiment.CACHE_FILE, header=["value"])
    assert len(sentiment.fetch_fear_greed()) == len(fresh)             # taze önbellek kabul
    old = fresh.copy(); old.index = old.index - pd.Timedelta(days=10); old.rename_axis("date").to_csv(sentiment.CACHE_FILE, header=["value"])
    try: sentiment.fetch_fear_greed(); assert False                     # bayat önbellek reddedilir
    except RuntimeError: pass
print("Tüm Fear & Greed testleri geçti ✔")
