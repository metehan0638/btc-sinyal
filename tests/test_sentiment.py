"""Fear & Greed (11. indikatör) testleri; internet gerekmez."""
import io, json, sys, tempfile, time, urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import synthetic
from core import sentiment
from core.backtest import run_backtest
from core.consensus import build_signals
from core.indicators import compute_indicators, latest_table
import check_signals as cs

time.sleep = lambda s: None
df = synthetic(1800, seed=5)                                   # 2018-01-01 -> ~2022-11
days = pd.date_range("2017-12-01", periods=2000, freq="D", tz="UTC")
rng = np.random.default_rng(1)
fg = pd.Series(np.clip(50 + np.cumsum(rng.normal(0, 4, len(days))) % 100 - 50 + rng.normal(0, 5, len(days)), 1, 99), index=days)

# 1) API yanıtı ayrıştırma (alternative.me biçimi: yeni -> eski sıralı, string değerler)
payload = {"data": [{"value": str(int(v)), "value_classification": "x", "timestamp": str(int(d.timestamp()))}
                    for d, v in list(fg.items())[::-1]]}
parsed = sentiment._parse(payload)
assert parsed.index.is_monotonic_increasing and len(parsed) == len(fg) and parsed.index.tz is not None
assert abs(parsed.iloc[0] - int(fg.iloc[0])) < 1e-9

# 2) hizalama: günlük = aynı gün, haftalık = +6 gün (ileriye bakma yok)
al_d = sentiment.align_fear_greed(parsed, df.index, "1d")
t = df.index[100]
assert al_d.loc[t] == parsed.loc[t.normalize()]
wk = df.resample("W-MON", label="left", closed="left").first().dropna().index
al_w = sentiment.align_fear_greed(parsed, wk, "1w")
assert al_w.iloc[10] == parsed.loc[wk[10].normalize() + pd.Timedelta(days=6)]

# 3) 11 indikatör ve oy mantığı: korku -> AL, açgözlülük -> SAT, orta -> NÖTR
votes, raw = compute_indicators(df, {"ema_fast": 50, "ema_slow": 200}, fg=al_d)
assert votes.shape[1] == 11 and "feargreed" in votes.columns
ok = votes["feargreed"].notna()
assert (votes.loc[ok & (raw.fear_greed < 47), "feargreed"] == 1).all()
assert (votes.loc[ok & (raw.fear_greed > 54), "feargreed"] == -1).all()
assert (votes.loc[ok & raw.fear_greed.between(47, 54), "feargreed"] == 0).all()
assert (votes.loc[ok, "feargreed"] == 1).any() and (votes.loc[ok, "feargreed"] == -1).any()

# 4) konsensüs 11 üzerinden: toplam 11, eşik alt sınırı 6
sig = build_signals(votes, 8)
assert (sig.bull + sig.bear + sig.neutral).eq(11).all()
assert build_signals(votes, 3).state.equals(build_signals(votes, 6).state)
run_backtest(df, sig, 8, 4, 0.001, 365)   # hata vermemeli
assert any("Fear & Greed" in x for x in latest_table(votes, raw)["İndikatör"])
print(latest_table(votes, raw).tail(2).to_string(), "\n")

# 5) ileriye bakma yok: gelecekteki endeks değerlerini değiştirmek geçmiş oyları değiştirmemeli
fg2 = parsed.copy(); fg2.iloc[-300:] = 99
v2, _ = compute_indicators(df, {"ema_fast": 50, "ema_slow": 200}, fg=sentiment.align_fear_greed(fg2, df.index, "1d"))
cut = df.index[-300]
assert votes.loc[:cut - pd.Timedelta(days=1)].equals(v2.loc[:cut - pd.Timedelta(days=1)])

# 6) API çökerse: yeniden dene -> önbellek yedeği -> yoksa hata
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

# 7) bot entegrasyonu: rapor 11 indikatörü gösterir; API yoksa sahte sinyal yerine durur, durum değişmez
cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))
cfg.update(entry_threshold=7, exit_threshold=4, news_report=False)
fetch = lambda sym, tf, ex, since: df if tf == "1d" else df.resample("W-MON", label="left", closed="left").agg(
    dict(open="first", high="max", low="min", close="last", volume="sum")).dropna()
with tempfile.TemporaryDirectory() as tmp:
    st, sent = Path(tmp) / "state.json", []
    m = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: parsed)
    assert "Fear & Greed" in m[0]
    assert "(11 indikatörden)" in m[0]
    print(m[0][:900], "\n")
    def fg_down(): raise RuntimeError("Fear & Greed endeksi alınamadı: test")
    st2 = Path(tmp) / "state2.json"
    try: cs.run(cfg, sent.append, state_path=st2, fetch=fetch, fetch_fg=fg_down); assert False
    except RuntimeError: assert not st2.exists(), "hata sonrası durum yazılmamalı"
    # config'te kapatılırsa eski 10'lu mod
    cfg["fear_greed"] = False
    m10 = cs.run(cfg, sent.append, state_path=Path(tmp) / "s3.json", fetch=fetch)
    assert "(10 indikatörden)" in m10[0]
print("Tüm Fear & Greed testleri geçti ✔")
