"""check_signals mantığını sentetik veriyle test eder (internet/Telegram gerekmez)."""
import json, re, sys, tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import synthetic
import check_signals as cs

cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))
df = synthetic(1800, seed=3)
fetch_calls = []
def fetch(sym, tf, ex, since):
    fetch_calls.append((sym, tf, since))
    return df if tf == "1d" else df.resample("W-MON", label="left", closed="left").agg(
        dict(open="first", high="max", low="min", close="last", volume="sum")).dropna()
fg = pd.Series(np.full(300, 15.0), index=pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=300, freq="D"))
rd = lambda state, path: path.write_text(json.dumps(state), encoding="utf-8")

with tempfile.TemporaryDirectory() as tmp:
    st, sent = Path(tmp) / "state.json", []
    # 1) ilk çalışma: rapor gönderilir ve durum kaydedilir
    m1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(m1) == 2 and "KRİPTO TAKİP PANELİ" in m1[0] and " (1/2)" in m1[0] \
        and "KRİPTO TAKİP PANELİ" in m1[1] and " (2/2)" in m1[1] and st.exists()
    assert len(fetch_calls) == 12 and {symbol for symbol, _, _ in fetch_calls} == {
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"}
    assert {(symbol, since) for symbol, _, since in fetch_calls} == {
        ("BTC/USDT", "2017-08-17"), ("ETH/USDT", "2017-08-17"),
        ("SOL/USDT", "2020-08-11"), ("XRP/USDT", "2018-05-04")}
    assert all(len(message) < 4096 for message in m1)
    assert all(any(f"🪙 {ticker}" in message for message in m1) for ticker in ("BTC", "ETH", "SOL", "XRP"))
    assert all(ticker in m1[0] for ticker in ("BTC", "ETH")) and all(ticker in m1[1] for ticker in ("SOL", "XRP"))
    for k in ("📅 GÜNLÜK", "📅 HAFTALIK", "📅 AYLIK", "🎯 Dip Skoru:", "Günlük kapanış", "USDT", "-100", "+100", "●"):
        assert k in m1[0], f"rapor eksik: {k}"
    # eski sistemin hiçbir izi raporda kalmamalı; gösterge listesi de yok, yalnızca skor çubuğu
    for gone in ("Trend", "Aşırılık", "Skor:", "Genel:", "• ", "RSI", "Bollinger", "MFI", "Ichimoku", "CROSS", "Yatırım tavsiyesi",
                "POZİTİF", "OLUMSUZ", "VERİ YOK"):
        assert not any(gone in message for message in m1), f"eski/istenmeyen içerik kalmış: {gone}"
    assert all(m.count("🎯 Dip Skoru:") == 6 and m.count("Günlük kapanış") == 2 for m in m1), \
        "her coin için günlük/haftalık/aylık Dip Skoru ve günlük kapanış fiyatı olmalı"
    scores = [int(x) for m in m1 for x in re.findall(r"🎯 Dip Skoru: ([+-]?\d+)", m)]
    assert len(scores) == 12 and set(scores) <= {-100, -50, 0, 50, 100}, "yalnızca RSI varken skor 5 değerden biri olur"
    bars = [bar for m in m1 for bar in re.findall(r"^-100 (\S+) \+100$", m, re.M)]
    assert len(bars) == 12 and all(len(b) == cs.BAR_WIDTH and b.count("●") == 1 for b in bars)
    assert cs.LEGEND in m1[-1] and all(cs.LEGEND not in m for m in m1[:-1]), "açıklama satırı yalnızca son mesajda olmalı"
    assert sum(m.count("Fear & Greed") for m in m1) == 1 and "Fear & Greed: 15 — Aşırı korku" in m1[-1]
    assert "Fear & Greed" in m1[-1].rstrip().splitlines()[-1], "Fear & Greed raporun en sonunda olmalı"
    for m in m1:  # kapanış fiyatı coin başlığının hemen altında, skordan önce
        for section in m.split("🪙 ")[1:]:
            assert section.splitlines()[1].startswith("💰 Günlük kapanış") and section.index("Günlük kapanış") < section.index("Dip Skoru")
    s = json.loads(st.read_text(encoding="utf-8"))
    assert s["last_report"] and "last_news" not in s
    assert not any(k.startswith(("cross_", "rating_")) for k in s), "kaldırılan cross/derece anahtarları durumda olmamalı"
    # 2) aynı gün tekrar: rapor yok
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 3) yeni gün: tek rapor, sonra yine sessiz
    s["last_report"] = "2000-01-01"; rd(s, st)
    r1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(r1) == 2 and "KRİPTO TAKİP PANELİ" in r1[0]
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 3b) --report her zaman gönderir
    status_reports = cs.run(cfg, sent.append, status_only=True, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(status_reports) == 2 and all("KRİPTO TAKİP PANELİ" in message for message in status_reports)
    # 3c) daily_report kapalıysa (ilk çalışma dışında) rapor gelmez
    assert cs.run({**cfg, "daily_report": False}, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 4) önceki sürümlerin cross/derece anahtarları ilk raporda temizlenir, uyarı gönderilmez
    old = Path(tmp) / "cross.json"
    rd({"cross_1d": "2026-01-01T00:00:00+00:00", "cross_ETH_1w": "", "rating_BTC_1d": "x", "last_report": "2000-01-01"}, old)
    m4 = cs.run(cfg, sent.append, state_path=old, fetch=fetch, fetch_fg=lambda: fg)
    assert len(m4) == 2 and not any("CROSS" in m for m in m4)
    s4 = json.loads(old.read_text(encoding="utf-8"))
    assert not any(k.startswith(("cross_", "rating_")) for k in s4) and s4["last_report"] != "2000-01-01"
    # 5) gönderim hatasında durum kaydedilmemeli
    s = json.loads(st.read_text(encoding="utf-8")); s["last_report"] = "2000-01-01"; rd(s, st)
    def boom(_): raise RuntimeError("ağ yok")
    try: cs.run(cfg, boom, state_path=st, fetch=fetch, fetch_fg=lambda: fg); assert False
    except RuntimeError: pass
    assert json.loads(st.read_text(encoding="utf-8"))["last_report"] == "2000-01-01", "hata sonrası durum değişmemeli"
    # 6) Fear & Greed çökerse rapor yine gider (o satır olmadan skorlar aynı)
    def fg_down(): raise RuntimeError("Fear & Greed endeksi alınamadı: test")
    m6 = cs.run(cfg, sent.append, state_path=Path(tmp) / "s6.json", fetch=fetch, fetch_fg=fg_down)
    assert m6 and "Fear & Greed: — Veri yok" in m6[-1] and "🎯 Dip Skoru:" in m6[0]
    assert re.findall(r"🎯 Dip Skoru: ([+-]?\d+)", "\n".join(m6)) == re.findall(r"🎯 Dip Skoru: ([+-]?\d+)", "\n".join(m1)), \
        "Fear & Greed skora girmemeli: veri olsun olmasın skorlar aynı"
    # 7) kısa geçmiş (yedek borsa) -> yanlış rapor yerine hata
    try: cs.run(cfg, sent.append, state_path=Path(tmp) / "s7.json", fetch=lambda *a: df.iloc[:150], fetch_fg=lambda: fg); assert False
    except RuntimeError as e: assert "en az" in str(e)
    # 8) önceki sürümlerin durum dosyaları sorun çıkarmaz
    legacy = Path(tmp) / "legacy.json"
    rd({"1d": "2026-10-27T00:00:00+00:00", "1w": "2026-10-19T00:00:00+00:00", "zone_1d": "dip", "zone_1w": "normal",
        "last_report": "2026-10-28", "news_hist": {}}, legacy)
    m8 = cs.run(cfg, sent.append, state_path=legacy, fetch=fetch, fetch_fg=lambda: fg)
    assert m8 and len(m8) == 2
    s8 = json.loads(legacy.read_text(encoding="utf-8"))
    assert not {"1d", "1w", "zone_1d", "zone_1w"} & set(s8) and "news_hist" in s8
    print("ÖRNEK RAPOR:\n" + m1[0] + "\n")

# 9) uç durumlar: sürekli düşüş = Güçlü Dip (+100), sürekli yükseliş = Aşırı Şişmiş (-100)
asset = {"ticker": "TST", "name": "Test", "symbol": "TST/USDT"}
idx = pd.date_range("2024-01-01", periods=300, freq="D", tz="UTC")
def frame(close): return pd.DataFrame(dict(open=close, high=close, low=close, close=close, volume=1.0), index=idx)
falling, rising = frame(np.linspace(300, 100, 300)), frame(np.linspace(100, 300, 300))
res_down = {tf: cs.analyze(falling, tf, cfg) for tf in cs.TF_UP}
res_up = {tf: cs.analyze(rising, tf, cfg) for tf in cs.TF_UP}
down_text, up_text = "\n".join(cs._score_summary(res_down, asset)), "\n".join(cs._score_summary(res_up, asset))
assert down_text.count("🎯 Dip Skoru: +100 · 🟢🟢 Güçlü Dip") == 3 and up_text.count("🎯 Dip Skoru: -100 · 🔴🔴 Aşırı Şişmiş") == 3
assert cs.score_bar(100).split()[1][-1] == "●" and cs.score_bar(-100).split()[1][0] == "●"
# 10) skor çubuğu ve satır biçimi
bars = {v: cs.score_bar(v) for v in (-100, 0, 100)}
assert bars[-100].split()[1][0] == "●" and bars[100].split()[1][-1] == "●" and bars[0].split()[1][cs.BAR_WIDTH // 2] == "●"
assert cs.score_bar(250).split()[1][-1] == "●", "sınır dışı değer kenara sıkıştırılmalı"
assert cs._score_lines(dict(score=float("nan"), label=None, active=0, total=1)) == ["🎯 Dip Skoru: ❔ Yetersiz veri"]
assert cs._score_lines(dict(score=0.0, label="Nötr", active=1, total=1))[0] == "🎯 Dip Skoru: 0 · ⚪ Nötr"
assert cs._score_lines(dict(score=-50.0, label="Şişmiş", active=2, total=3))[0] == "🎯 Dip Skoru: -50 · 🔴 Şişmiş (2/3 gösterge)"
# 11) gösterge ayarı config'den gelir
assert cs.analyze(falling, "1d", {"indicators": {"rsi": {"period": 7}}})["names"] == {"rsi": "RSI(7)"}
try: cs.analyze(falling, "1d", {"indicators": {"macd": {}}}); assert False
except ValueError as e: assert "Bilinmeyen gösterge" in str(e)
print("Tüm bildirim testleri geçti ✔")
