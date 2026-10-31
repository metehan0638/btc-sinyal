"""check_signals mantığını sentetik veriyle test eder (internet/Telegram gerekmez)."""
import json, sys, tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import synthetic
import check_signals as cs

cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))
cfg["news_report"] = False  # bu testte ağ kullanılmasın
df = synthetic(1800, seed=3)
fetch = lambda sym, tf, ex, since: df if tf == "1d" else df.resample("W-MON", label="left", closed="left").agg(
    dict(open="first", high="max", low="min", close="last", volume="sum")).dropna()
fg = pd.Series(np.full(300, 15.0), index=pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=300, freq="D"))

with tempfile.TemporaryDirectory() as tmp:
    st, sent = Path(tmp) / "state.json", []
    # 1) ilk çalışma: yeni düzen raporu + bölge durumu kaydedilir
    m1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(m1) == 1 and "yeni düzene" in m1[0] and st.exists()
    for k in ("GÜNLÜK", "HAFTALIK", "Genel:", "göstergeden", "RSI(14)", "Fear & Greed", "Nasıl okunur", "tavsiyesi değildir"):
        assert k in m1[0], f"rapor eksik: {k}"
    assert m1[0].count("Fear & Greed") == 1, "Fear & Greed yalnızca günlük blokta olmalı"
    for bad in ("AL sinyali", "SAT sinyali", "GİRİŞ", "ÇIKIŞ", "pozisyon", "backtest"):
        assert bad not in m1[0], f"eski al-sat dili kalmış: {bad}"
    s = json.loads(st.read_text(encoding="utf-8"))
    assert s["zone_1d"] in cs.ZONE_TEXT and s["zone_1w"] in cs.ZONE_TEXT and s["last_report"]
    # 2) aynı gün tekrar: rapor yok
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 3) yeni gün: tek rapor; bölge değiştiyse "Önceki rapor" satırı görünür
    other = lambda z: "hot" if z != "hot" else "dip"
    s["last_report"], s["zone_1d"] = "2000-01-01", other(s["zone_1d"]); st.write_text(json.dumps(s), encoding="utf-8")
    r1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(r1) == 1 and "DİP / ŞİŞKİNLİK RAPORU" in r1[0] and "Önceki rapor:" in r1[0] and "yeni düzene" not in r1[0]
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 3b) --report her zaman gönderir
    assert "RAPORU" in cs.run(cfg, sent.append, status_only=True, state_path=st, fetch=fetch, fetch_fg=lambda: fg)[0]
    # 4) gönderim hatasında durum kaydedilmemeli
    s = json.loads(st.read_text(encoding="utf-8")); s["last_report"] = "2000-01-01"; st.write_text(json.dumps(s), encoding="utf-8")
    def boom(_): raise RuntimeError("ağ yok")
    try: cs.run(cfg, boom, state_path=st, fetch=fetch, fetch_fg=lambda: fg); assert False
    except RuntimeError: pass
    assert json.loads(st.read_text(encoding="utf-8"))["last_report"] == "2000-01-01", "hata sonrası durum değişmemeli"
    # 5) Fear & Greed çökerse rapor yine gider (o satır olmadan)
    def fg_down(): raise RuntimeError("Fear & Greed endeksi alınamadı: test")
    m5 = cs.run(cfg, sent.append, state_path=Path(tmp) / "s5.json", fetch=fetch, fetch_fg=fg_down)
    assert m5 and "Fear & Greed" not in m5[0] and "RSI(14)" in m5[0]
    # 6) kısa geçmiş (yedek borsa) -> yanlış rapor yerine hata
    try: cs.run(cfg, sent.append, state_path=Path(tmp) / "s6.json", fetch=lambda *a: df.iloc[:200], fetch_fg=lambda: fg); assert False
    except RuntimeError as e: assert "en az" in str(e)
    # 7) eski sürümün durum dosyası (1d/1w anahtarları) sorun çıkarmaz ve temizlenir
    old = Path(tmp) / "old.json"; old.write_text(json.dumps({"1d": "2026-10-27T00:00:00+00:00", "1w": "2026-10-19T00:00:00+00:00",
                                                           "last_report": "2026-10-28", "news_hist": {}}), encoding="utf-8")
    m7 = cs.run(cfg, sent.append, state_path=old, fetch=fetch, fetch_fg=lambda: fg)
    assert m7 and "yeni düzene" in m7[0]
    s7 = json.loads(old.read_text(encoding="utf-8")); assert "1d" not in s7 and "zone_1d" in s7
    # 8) haber raporu entegrasyonu (sahte haber modülü): günde bir kez, geçmiş state'e yazılır
    cfg["news_report"] = True
    cs.build_news_report = lambda feeds, hist: ("🔥 HYPE TEST", {"2026-10-24": {"_total": 50}})
    st2 = Path(tmp) / "state2.json"
    n1 = cs.run(cfg, sent.append, state_path=st2, fetch=fetch, fetch_fg=lambda: fg)
    assert any("HYPE TEST" in m for m in n1)
    saved = json.loads(st2.read_text(encoding="utf-8"))
    assert saved["last_news"] and saved["news_hist"] == {"2026-10-24": {"_total": 50}}
    assert cs.run(cfg, sent.append, state_path=st2, fetch=fetch, fetch_fg=lambda: fg) == [], "haber raporu günde bir kez"
    # 9) haber kaynakları çökerse ana rapor etkilenmemeli
    def broken(feeds, hist): raise RuntimeError("kaynaklar kapalı")
    cs.build_news_report = broken
    n3 = cs.run(cfg, sent.append, state_path=Path(tmp) / "state3.json", fetch=fetch, fetch_fg=lambda: fg)
    assert any("RAPORU" in m or "yeni düzene" in m for m in n3) and not any("HYPE" in m for m in n3)
    print("ÖRNEK RAPOR:\n" + m1[0] + "\n")
print("Tüm bildirim testleri geçti ✔")
