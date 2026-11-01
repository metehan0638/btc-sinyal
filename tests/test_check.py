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
from core.zones import compute_metrics, cross_events

cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))
cfg["news_report"] = False  # bu testte ağ kullanılmasın
df = synthetic(1800, seed=3)
fetch = lambda sym, tf, ex, since: df if tf == "1d" else df.resample("W-MON", label="left", closed="left").agg(
    dict(open="first", high="max", low="min", close="last", volume="sum")).dropna()
fg = pd.Series(np.full(300, 15.0), index=pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=300, freq="D"))
rd = lambda state, path: path.write_text(json.dumps(state), encoding="utf-8")

with tempfile.TemporaryDirectory() as tmp:
    st, sent = Path(tmp) / "state.json", []
    # 1) ilk çalışma: yeni düzen raporu + derece durumu kaydedilir
    m1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(m1) == 1 and "yeni düzene" in m1[0] and st.exists()
    for k in ("GÜNLÜK", "HAFTALIK", "Genel:", "ortalama puan", "gösterge:", "güçlü al", "RSI(14)", "Fear & Greed",
              "Golden/Dead cross (SMA50/200)", "Nasıl okunur", "emir değildir", "tavsiyesi değildir"):
        assert k in m1[0], f"rapor eksik: {k}"
    assert m1[0].count("Fear & Greed") == 1, "Fear & Greed yalnızca günlük blokta olmalı"
    assert m1[0].count("Golden/Dead cross (SMA50/200)") == 2, "cross her iki zaman diliminde bir gösterge olmalı"
    for bad in ("GİRİŞ", "ÇIKIŞ", "pozisyon", "backtest", "DİP BÖLGESİ", "ŞİŞMİŞ BÖLGE", "Trend yönü"):
        assert bad not in m1[0], f"eski dil kalmış: {bad}"
    s = json.loads(st.read_text(encoding="utf-8"))
    assert s["rating_1d"] in cs.RATING_TEXT and s["rating_1w"] in cs.RATING_TEXT and s["last_report"]
    assert "cross_1d" in s and "cross_1w" in s, "kesişim durumu kaydedilmeli (ilk çalışmada uyarı olmadan)"
    assert "GOLDEN CROSS" not in m1[0] and "DEAD CROSS" not in m1[0], "ilk çalışmada geçmiş kesişim için uyarı gelmemeli"
    # 2) aynı gün tekrar: rapor yok
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 3) yeni gün: tek rapor; derece değiştiyse "Önceki rapor" satırı görünür
    other = lambda z: "strong_sell" if z != "strong_sell" else "strong_buy"
    s["last_report"], s["rating_1d"] = "2000-01-01", other(s["rating_1d"]); rd(s, st)
    r1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(r1) == 1 and "GÖSTERGE DERECELERİ" in r1[0] and "Önceki rapor:" in r1[0] and "yeni düzene" not in r1[0]
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 3b) --report her zaman gönderir
    assert "DERECELERİ" in cs.run(cfg, sent.append, status_only=True, state_path=st, fetch=fetch, fetch_fg=lambda: fg)[0]
    # 3c) yeni kesişim olursa ayrı uyarı: durumu bir önceki kesişimden öncesine al -> yalnızca sonraki kesişim bildirilir, bir kez
    evs = cross_events(compute_metrics(df, cfg["timeframes"]["1d"]))
    assert len(evs) >= 2, "sentetik veride yeterli kesişim yok, test verisini değiştir"
    s = json.loads(st.read_text(encoding="utf-8")); s["cross_1d"] = evs[-2][0].isoformat(); rd(s, st)
    ca = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    last_ts, last_kind = evs[-1]
    assert len(ca) == 1 and ("GOLDEN CROSS" if last_kind == 1 else "DEAD CROSS") in ca[0] and f"{last_ts:%d.%m.%Y}" in ca[0], ca
    assert "Şu anki genel derece" in ca[0] and "tavsiyesi değildir" in ca[0] and "gecikmeli" in ca[0]
    assert json.loads(st.read_text(encoding="utf-8"))["cross_1d"] == last_ts.isoformat()
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == [], "aynı kesişim iki kez bildirilmemeli"
    s["cross_1d"] = evs[-2][0].isoformat(); rd(s, st)
    assert cs.run({**cfg, "cross_alerts": False}, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == [], "kapatılınca uyarı gelmez"
    # 3d) rating_thresholds config'ten okunur: çok düşük eşikte nötr olmaz
    low = cs.analyze(df, "1d", {**cfg, "rating_thresholds": [0.01, 0.02]}, None)[3]
    assert low["key"] in ("strong_buy", "strong_sell") or low["mean"] == 0
    # 4) gönderim hatasında durum kaydedilmemeli
    s = json.loads(st.read_text(encoding="utf-8")); s["last_report"] = "2000-01-01"; rd(s, st)
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
    # 7) önceki sürümlerin durum dosyaları (1d/1w, zone_*) sorun çıkarmaz: yeni düzen raporu gider, eski anahtarlar temizlenir
    old = Path(tmp) / "old.json"
    rd({"1d": "2026-10-27T00:00:00+00:00", "1w": "2026-10-19T00:00:00+00:00", "zone_1d": "dip", "zone_1w": "normal",
        "last_report": "2026-10-28", "news_hist": {}}, old)
    m7 = cs.run(cfg, sent.append, state_path=old, fetch=fetch, fetch_fg=lambda: fg)
    assert m7 and "yeni düzene" in m7[0]
    s7 = json.loads(old.read_text(encoding="utf-8"))
    assert not {"1d", "1w", "zone_1d", "zone_1w"} & set(s7) and "rating_1d" in s7
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
    assert any("DERECELERİ" in m or "yeni düzene" in m for m in n3) and not any("HYPE" in m for m in n3)
    print("ÖRNEK RAPOR:\n" + m1[0] + "\n")
print("Tüm bildirim testleri geçti ✔")
