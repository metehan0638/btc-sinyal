"""check_signals mantığını sentetik veriyle test eder (internet/Telegram gerekmez)."""
import json, re, sys, tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import make_fg, make_mvrv_frame, synthetic
import check_signals as cs

cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))
real_indicators = cfg["indicators"]
assert [a["ticker"] for a in cs.configured_assets(cfg)] == ["BTC"], "sistemde yalnızca Bitcoin olmalı"
cfg["indicators"] = {"rsi": {"period": 14}}  # aşağıdaki akış testleri yalnızca RSI ile (skorlar 5 değerden biri); beş gösterge bölüm 12'de
df = synthetic(1800, seed=3)
fetch_calls = []
def fetch(sym, tf, ex, since):
    fetch_calls.append((sym, tf, since))
    if tf == "1d":
        return df
    return df.resample("W-MON" if tf == "1w" else "MS", label="left", closed="left").agg(
        dict(open="first", high="max", low="min", close="last", volume="sum")).dropna()
fg = pd.Series(np.full(300, 15.0), index=pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=300, freq="D"))
rd = lambda state, path: path.write_text(json.dumps(state), encoding="utf-8")

with tempfile.TemporaryDirectory() as tmp:
    st, sent = Path(tmp) / "state.json", []
    # 1) ilk çalışma: rapor gönderilir ve durum kaydedilir
    m1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(m1) == 1 and "KRİPTO TAKİP PANELİ" in m1[0] and " (1/" not in m1[0], "tek coin: tek mesaj, sayfa numarası yok"
    assert st.exists() and len(fetch_calls) == 3 and {symbol for symbol, _, _ in fetch_calls} == {"BTC/USDT"}
    assert {(symbol, since) for symbol, _, since in fetch_calls} == {("BTC/USDT", "2017-08-17")}
    assert all(len(message) < 4096 for message in m1) and "🪙 BTC — Bitcoin" in m1[0]
    assert not any(f"🪙 {ticker}" in message for message in m1 for ticker in ("ETH", "SOL", "XRP")), "BTC dışında coin olmamalı"
    for k in ("📅 GÜNLÜK", "📅 HAFTALIK", "📅 AYLIK", "🎯 Dip Skoru:", "Günlük kapanış", "USDT", "-100", "+100", "●"):
        assert k in m1[0], f"rapor eksik: {k}"
    # eski sistemin hiçbir izi raporda kalmamalı; gösterge listesi de yok, yalnızca skor çubuğu
    for gone in ("Trend", "Aşırılık", "Skor:", "Genel:", "• ", "RSI", "Bollinger", "MFI", "Ichimoku", "CROSS", "Yatırım tavsiyesi",
                "POZİTİF", "OLUMSUZ", "VERİ YOK"):
        assert not any(gone in message for message in m1), f"eski/istenmeyen içerik kalmış: {gone}"
    assert all(m.count("🎯 Dip Skoru:") == 3 and m.count("Günlük kapanış") == 1 for m in m1), \
        "günlük/haftalık/aylık Dip Skoru ve günlük kapanış fiyatı olmalı"
    scores = [int(x) for m in m1 for x in re.findall(r"🎯 Dip Skoru: ([+-]?\d+)", m)]
    assert len(scores) == 3 and set(scores) <= {-100, -50, 0, 50, 100}, "yalnızca RSI varken skor 5 değerden biri olur"
    bars = [bar for m in m1 for bar in re.findall(r"^-100 (\S+) \+100$", m, re.M)]
    assert len(bars) == 3 and all(len(b) == cs.BAR_WIDTH and b.count("●") == 1 for b in bars)
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
    assert len(r1) == 1 and "KRİPTO TAKİP PANELİ" in r1[0]
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 3b) --report her zaman gönderir
    status_reports = cs.run(cfg, sent.append, status_only=True, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(status_reports) == 1 and all("KRİPTO TAKİP PANELİ" in message for message in status_reports)
    # 3c) daily_report kapalıysa (ilk çalışma dışında) rapor gelmez
    assert cs.run({**cfg, "daily_report": False}, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 4) önceki sürümlerin cross/derece anahtarları ilk raporda temizlenir, uyarı gönderilmez
    old = Path(tmp) / "cross.json"
    rd({"cross_1d": "2026-01-01T00:00:00+00:00", "cross_ETH_1w": "", "rating_BTC_1d": "x", "last_report": "2000-01-01"}, old)
    m4 = cs.run(cfg, sent.append, state_path=old, fetch=fetch, fetch_fg=lambda: fg)
    assert len(m4) == 1 and not any("CROSS" in m for m in m4)
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
    assert m8 and len(m8) == 1
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
# 12) beş gösterge uçtan uca (RSI, Bollinger %B, MFI, Fear & Greed, MVRV): eşit oyla ortalanır, config.yaml'daki gerçek ayar kullanılır
from core.indicators import POINTS, build_indicators
KEYS = ["rsi", "bollinger", "mfi", "fear_greed", "mvrv"]
assert [i.key for i in build_indicators(real_indicators, {"fear_greed": lambda: None, "mvrv": lambda: None})] == KEYS
sources = {"fear_greed": lambda: make_fg("2017-12-01", 2200, seed=3), "mvrv": lambda: make_mvrv_frame("2017-01-01", 3200)}
five = {**cfg, "indicators": real_indicators}
res5 = {tf: cs.analyze(fetch("BTC/USDT", tf, None, None), tf, five, sources) for tf in cs.TF_UP}
for tf, r in res5.items():
    assert r["active"] == r["total"] == 5 and list(r["names"]) == KEYS, (tf, r["labels"])
    assert r["score"] == sum(POINTS[label] for label in r["labels"].values()) / 5
    assert r["names"]["mfi"] == "MFI(14)" and r["names"]["fear_greed"] == "Fear & Greed" and r["names"]["mvrv"] == "MVRV Z-Score"
assert "gösterge)" not in "\n".join(cs._score_summary(res5, asset)), "tüm göstergeler aktifken sayı gösterilmez"
with tempfile.TemporaryDirectory() as tmp2:
    full = cs.run(five, sent.append, state_path=Path(tmp2) / "s.json", fetch=fetch, fetch_fg=sources["fear_greed"],
                  fetch_mvrv=sources["mvrv"])
    assert len(full) == 1 and full[0].count("🎯 Dip Skoru:") == 3 and "gösterge)" not in full[0]
    five_scores = [int(x) for x in re.findall(r"🎯 Dip Skoru: ([+-]?\d+)", full[0])]
    assert len(five_scores) == 3 and all(-100 <= v <= 100 and v % 10 == 0 for v in five_scores), five_scores
    assert not any(name in full[0] for name in ("Bollinger", "MFI", "MVRV", "RSI")), "rapor gösterge listesi içermez, yalnızca skor"
    assert full[0].count("Fear & Greed") == 1, "Fear & Greed yalnızca rapor sonundaki bilgi satırında"
    # bir dış kaynak çökerse: gösterge o çalışmada hesaba girmez (nötr sayılmaz), satırlarda aktif sayı görünür, rapor yine gider
    def down(): raise RuntimeError("kaynak çöktü")
    no_mvrv = cs.run(five, sent.append, state_path=Path(tmp2) / "s2.json", fetch=fetch, fetch_fg=sources["fear_greed"], fetch_mvrv=down)
    assert len(no_mvrv) == 1 and no_mvrv[0].count("(4/5 gösterge)") == 3
    no_fg = cs.run(five, sent.append, state_path=Path(tmp2) / "s3.json", fetch=fetch, fetch_fg=down, fetch_mvrv=sources["mvrv"])
    assert len(no_fg) == 1 and no_fg[0].count("(4/5 gösterge)") == 3 and "Fear & Greed: — Veri yok" in no_fg[0]
    neither = cs.run(five, sent.append, state_path=Path(tmp2) / "s4.json", fetch=fetch, fetch_fg=down, fetch_mvrv=down)
    assert len(neither) == 1 and neither[0].count("(3/5 gösterge)") == 3
    # MVRV yalnızca Bitcoin içindir: BTC dışında varlık varsa net hata
    other = {"ticker": "TST1", "name": "Test", "symbol": "TST1/USDT", "since": "2018-01-01"}
    try:
        cs.run({**five, "assets": [*cfg["assets"], other]}, sent.append, state_path=Path(tmp2) / "s5.json", fetch=fetch,
               fetch_fg=sources["fear_greed"], fetch_mvrv=sources["mvrv"])
        assert False
    except ValueError as e:
        assert "yalnızca Bitcoin" in str(e)
    # çok varlıklı yapı hâlâ genel çalışır (MVRV olmadan): 3 varlık -> 2 mesaj, mesaj başına en çok 2 coin
    third = {**other, "ticker": "TST2", "symbol": "TST2/USDT"}
    multi = cs.run({**cfg, "assets": [*cfg["assets"], other, third]}, sent.append, state_path=Path(tmp2) / "s6.json", fetch=fetch,
                   fetch_fg=lambda: fg)
    assert len(multi) == 2 and " (1/2)" in multi[0] and " (2/2)" in multi[1] and "🪙 TST2" in multi[1]
# doğrusal düşüş: RSI 0 (+100), %B ≈ 0.088 (+50), MFI 0 (+100) -> üç fiyat/hacim göstergesi; dış kaynak olmadan yalnızca bu üçü sayılır
three = {**cfg, "indicators": {k: real_indicators[k] for k in ("rsi", "bollinger", "mfi")}}
res_down3 = {tf: cs.analyze(frame(np.linspace(300, 100, 300)).assign(volume=np.linspace(1, 3, 300)), tf, three) for tf in cs.TF_UP}
assert res_down3["1d"]["labels"] == {"rsi": "Güçlü Dip", "bollinger": "Dip", "mfi": "Güçlü Dip"}
assert res_down3["1d"]["score"] == (100 + 50 + 100) / 3
# eksik dış veri nötr sayılmaz: Bollinger henüz yokken (ilk 19 mum) skor yalnızca RSI ve MFI'dan gelir
early = cs.analyze(falling.iloc[:15], "1d", three)
assert early["labels"]["bollinger"] is None and early["active"] == 2 and early["total"] == 3 and early["score"] == 100
assert cs._score_lines(early)[0] == "🎯 Dip Skoru: +100 · 🟢🟢 Güçlü Dip (2/3 gösterge)"
print("Tüm bildirim testleri geçti ✔")
