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
df = synthetic(1800, seed=3)
fetch = lambda sym, tf, ex, since: df if tf == "1d" else df.resample("W-MON", label="left", closed="left").agg(
    dict(open="first", high="max", low="min", close="last", volume="sum")).dropna()

with tempfile.TemporaryDirectory() as tmp:
    st, sent = Path(tmp) / "state.json", []
    # 1) ilk çalışma: sadece durum mesajı
    m1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch)
    assert len(m1) == 1 and "Bot kuruldu" in m1[0] and st.exists()
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
    # 4) gönderim hatasında durum kaydedilmemeli
    s["1d"] = df.index[-60].isoformat(); s["1w"] = s["1d"]; st.write_text(json.dumps(s))
    def boom(_): raise RuntimeError("ağ yok")
    if m3:
        try: cs.run(cfg, boom, state_path=st, fetch=fetch); assert False
        except RuntimeError: pass
        assert json.loads(st.read_text())["1d"] == s["1d"], "hata sonrası durum değişmemeli"
print("\nTüm bildirim testleri geçti ✔")
