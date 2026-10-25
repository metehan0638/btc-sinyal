"""check_signals mantığını sentetik veriyle test eder (internet/Telegram gerekmez)."""
import sys, tempfile
from pathlib import Path
import pandas as pd, yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import synthetic
import check_signals as cs

cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))
cfg["entry_threshold"], cfg["exit_threshold"] = 7, 4
cfg["news_report"] = False  # bu testte ağ kullanılmasın
cfg["fear_greed"] = False   # (Fear & Greed testleri test_sentiment.py içinde)
df = synthetic(1800, seed=3)
fetch = lambda sym, tf, ex, since: df if tf == "1d" else df.resample("W-MON", label="left", closed="left").agg(
    dict(open="first", high="max", low="min", close="last", volume="sum")).dropna()

with tempfile.TemporaryDirectory() as tmp:
    st, sent = Path(tmp) / "state.json", []
    # 1) ilk çalışma: sadece durum mesajı
    m1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch)
    assert len(m1) == 1 and "Bot kuruldu" in m1[0] and st.exists()
    assert all(k in m1[0] for k in ("🟢 AL", "🔴 SAT", "⚪ NÖTR", "GÜNLÜK", "HAFTALIK", "EMA", "10 indikatörden")), "rapor eksik"
    # 2) hemen tekrar: yeni sinyal yok
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch) == []
    # 3) 60 gün geriye al: arada oluşan olaylar bildirilmeli, tekrar çalıştırınca bir daha gelmemeli
    import json
    s = json.loads(st.read_text()); s["1d"] = df.index[-60].isoformat(); s["1w"] = s["1d"]; st.write_text(json.dumps(s))
    m3 = cs.run(cfg, sent.append, state_path=st, fetch=fetch)
    m4 = cs.run(cfg, sent.append, state_path=st, fetch=fetch)
    assert m4 == [], "aynı sinyal iki kez gönderilmemeli"
    print(f"60 gün geriden devam: {len(m3)} mesaj\n")
    for m in m3[:2] or m1:
        print(m, "\n" + "-" * 30)
    # 3b) yeni gün: günde bir rapor, ikinci çalışmada tekrar yok
    s = json.loads(st.read_text()); s["last_report"] = "2000-01-01"; st.write_text(json.dumps(s))
    r1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch)
    assert sum("BTC RAPORU" in m for m in r1) == 1
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch) == []
    # 3c) --report her zaman gönderir
    assert "BTC RAPORU" in cs.run(cfg, sent.append, status_only=True, state_path=st, fetch=fetch)[0]
    print("ÖRNEK RAPOR:\n" + r1[-1] + "\n")
    # 4) gönderim hatasında durum kaydedilmemeli
    s["1d"] = df.index[-60].isoformat(); s["1w"] = s["1d"]; st.write_text(json.dumps(s))
    def boom(_): raise RuntimeError("ağ yok")
    if m3:
        try: cs.run(cfg, boom, state_path=st, fetch=fetch); assert False
        except RuntimeError: pass
        assert json.loads(st.read_text())["1d"] == s["1d"], "hata sonrası durum değişmemeli"
    # 5) haber raporu entegrasyonu (sahte haber modülü): günde bir kez, geçmiş state'e yazılır
    cfg["news_report"] = True
    cs.build_news_report = lambda feeds, hist: ("🔥 HYPE TEST", {"2026-10-24": {"_total": 50}})
    st2 = Path(tmp) / "state2.json"
    n1 = cs.run(cfg, sent.append, state_path=st2, fetch=fetch)
    assert any("HYPE TEST" in m for m in n1)
    saved = json.loads(st2.read_text())
    assert saved["last_news"] and saved["news_hist"] == {"2026-10-24": {"_total": 50}}
    assert cs.run(cfg, sent.append, state_path=st2, fetch=fetch) == [], "haber raporu günde bir kez"
    # 6) haber kaynakları çökerse sinyal akışı etkilenmemeli
    def broken(feeds, hist): raise RuntimeError("kaynaklar kapalı")
    cs.build_news_report = broken
    st3 = Path(tmp) / "state3.json"
    n3 = cs.run(cfg, sent.append, state_path=st3, fetch=fetch)
    assert any("BTC RAPORU" in m or "Bot kuruldu" in m for m in n3) and not any("HYPE" in m for m in n3)
    assert "last_news" not in json.loads(st3.read_text()), "başarısız haber raporu tekrar denenmeli"
print("\nTüm bildirim testleri geçti ✔")
