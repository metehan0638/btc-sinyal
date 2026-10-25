"""Fear & Greed (yalnızca bilgi amaçlı) testleri; internet gerekmez."""
import sys, tempfile, time, urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import synthetic
from core import sentiment
from core.indicators import compute_indicators
import check_signals as cs

time.sleep = lambda s: None
days = pd.date_range("2018-02-01", periods=1000, freq="D", tz="UTC")
rng = np.random.default_rng(1)
fg = pd.Series(np.clip(50 + rng.normal(0, 25, len(days)), 1, 99).round(), index=days)

# 1) API yanıtı ayrıştırma (alternative.me biçimi: yeni -> eski sıralı, string değerler)
payload = {"data": [{"value": str(int(v)), "value_classification": "x", "timestamp": str(int(d.timestamp()))}
                    for d, v in list(fg.items())[::-1]]}
parsed = sentiment._parse(payload)
assert parsed.index.is_monotonic_increasing and len(parsed) == len(fg) and parsed.iloc[-1] == fg.iloc[-1]

# 2) sınıflar, özet ve rapor satırları
assert [sentiment.classify(v) for v in (10, 30, 50, 60, 90)] == ["Aşırı korku", "Korku", "Nötr", "Açgözlülük", "Aşırı açgözlülük"]
info = sentiment.summary(parsed)
assert info["value"] == parsed.iloc[-1] and info["yesterday"] == parsed.iloc[-2] and info["month"] == parsed.iloc[-31]
txt = "\n".join(sentiment.lines(info))
assert "Fear & Greed" in txt and "sinyaline dahil değil" in txt and "dün" in txt

# 3) KONSENSÜSE KATILMAZ: indikatör sayısı hâlâ 10
votes, raw = compute_indicators(synthetic(1500, seed=2), {"ema_fast": 50, "ema_slow": 200})
assert votes.shape[1] == 10 and "feargreed" not in votes.columns

# 4) API çökerse: yeniden dene -> taze önbellek -> yoksa hata
def boom(*a, **k): raise OSError("bağlantı yok")
urllib.request.urlopen = boom
with tempfile.TemporaryDirectory() as tmp:
    sentiment.CACHE_FILE = Path(tmp) / "fear_greed.csv"
    try: sentiment.fetch_fear_greed(); assert False
    except RuntimeError as e: assert "alınamadı" in str(e)
    fresh = parsed.copy(); fresh.index = pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=len(fresh), freq="D")
    fresh.rename_axis("date").to_csv(sentiment.CACHE_FILE, header=["value"])
    assert len(sentiment.fetch_fear_greed()) == len(fresh)
    old = fresh.copy(); old.index = old.index - pd.Timedelta(days=10); old.rename_axis("date").to_csv(sentiment.CACHE_FILE, header=["value"])
    try: sentiment.fetch_fear_greed(); assert False
    except RuntimeError: pass

# 5) bot entegrasyonu
cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))
cfg.update(entry_threshold=7, exit_threshold=4, news_report=False)
df = synthetic(1800, seed=5)
fetch = lambda sym, tf, ex, since: df if tf == "1d" else df.resample("W-MON", label="left", closed="left").agg(
    dict(open="first", high="max", low="min", close="last", volume="sum")).dropna()
with tempfile.TemporaryDirectory() as tmp:
    sent = []
    base = cs.run({**cfg, "fear_greed": False}, sent.append, state_path=Path(tmp) / "a.json", fetch=fetch)
    m = cs.run(cfg, sent.append, state_path=Path(tmp) / "b.json", fetch=fetch, fetch_fg=lambda: parsed)
    assert "PİYASA DUYARLILIĞI" in m[0] and "(10 indikatörden)" in m[0]
    assert "PİYASA DUYARLILIĞI" not in base[0], "fear_greed: false ise satır görünmemeli"
    # sinyal sonucu F&G ile değişmez
    import re
    without_fg = re.sub(r"━━ PİYASA.*?\n\n", "", m[0], flags=re.S)
    assert without_fg.split("\n", 1)[1] == base[0].split("\n", 1)[1]
    # API yoksa rapor yine gider, durum yazılır
    def down(): raise RuntimeError("Fear & Greed endeksi alınamadı: test")
    st = Path(tmp) / "c.json"
    m2 = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=down)
    assert m2 and "PİYASA DUYARLILIĞI" not in m2[0] and st.exists()
    print(m[0][m[0].index("━━ PİYASA"):])
print("Tüm Fear & Greed testleri geçti ✔")
