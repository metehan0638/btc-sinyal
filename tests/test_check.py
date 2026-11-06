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
from core.zones import compute_metrics, cross_events

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
    # 1) ilk çalışma: yeni düzen raporu + derece durumu kaydedilir
    m1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(m1) == 2 and "KRİPTO TAKİP PANELİ" in m1[0] and " (1/2)" in m1[0] \
        and "KRİPTO TAKİP PANELİ" in m1[1] and " (2/2)" in m1[1] and st.exists()
    assert not any("HYPE RAPORU" in message for message in m1), "Telegram'a haber raporu gönderilmemeli"
    assert len(fetch_calls) == 12 and {symbol for symbol, _, _ in fetch_calls} == {
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"}
    assert {(symbol, since) for symbol, _, since in fetch_calls} == {
        ("BTC/USDT", "2017-08-17"), ("ETH/USDT", "2017-08-17"),
        ("SOL/USDT", "2020-08-11"), ("XRP/USDT", "2018-05-04")}
    assert all(len(message) < 4096 for message in m1), "coin başına Telegram raporu ileti sınırını aşmamalı"
    assert all(any(f"🪙 {ticker}" in message for message in m1)
               for ticker in ("BTC", "ETH", "SOL", "XRP"))
    assert all(ticker in m1[0] for ticker in ("BTC", "ETH"))
    assert all(ticker in m1[1] for ticker in ("SOL", "XRP"))
    for k in ("📅 GÜNLÜK", "📅 HAFTALIK", "📅 AYLIK", "Skor:", "Günlük kapanış", "USDT", "-100", "+100", "●"):
        assert k in m1[0], f"rapor eksik: {k}"
    assert all("Yatırım tavsiyesi değildir" not in message for message in m1)
    for heading in ("POZİTİF", "NÖTR", "OLUMSUZ", "VERİ YOK"):
        assert heading not in m1[0], f"raporda gereksiz grup başlığı var: {heading}"
    # gösterge listesi artık raporda yok; yalnızca skor çubuğu var
    for gone in ("• ", "RSI", "Stoch RSI", "Bollinger", "MFI", "Fiyat/SMA", "ATH düşüşü", "Ichimoku", "SMA kesişimi"):
        assert not any(gone in message for message in m1), f"gösterge listesi kalmış: {gone}"
    assert all(m.count("Skor:") == 6 for m in m1), "her coin için günlük/haftalık/aylık skor olmalı"
    assert all(m.count("Günlük kapanış") == 2 for m in m1), "her coin için günlük kapanış fiyatı olmalı"
    assert sum(m.count("Fear & Greed") for m in m1) == 1 and "Fear & Greed: 15 — Aşırı korku" in m1[-1], \
        "Fear & Greed yalnızca raporun sonunda (son mesaj) olmalı"
    assert m1[-1].rstrip().splitlines()[-1].startswith(("😱", "😨", "😐", "😏", "🤑")) and "Fear & Greed" in m1[-1].rstrip().splitlines()[-1]
    # kapanış fiyatı, coin başlığının hemen altında ve ilk skor çubuğundan önce
    for m in m1:
        for section in m.split("🪙 ")[1:]:
            lines = section.splitlines()
            assert lines[1].startswith("💰 Günlük kapanış") and section.index("Günlük kapanış") < section.index("Skor:")
    scores = [int(x) for m in m1 for x in re.findall(r"Skor: ([+-]?\d+)", m)]
    assert len(scores) == 12 and all(-100 <= v <= 100 for v in scores), scores
    for bar in re.findall(r"-100 (\S+) \+100", m1[0]):
        assert len(bar) == cs.BAR_WIDTH and bar.count("●") == 1
    s = json.loads(st.read_text(encoding="utf-8"))
    assert s["last_report"] and not any(k.startswith("rating_") for k in s)
    assert "last_news" not in s
    assert all(f"cross_{tf}" in s for tf in ("1d", "1w", "1M")), "BTC kesişim durumu korunmalı"
    assert all(f"cross_{ticker}_{tf}" in s for ticker in ("ETH", "SOL", "XRP") for tf in ("1d", "1w", "1M")), \
        "her altcoin kesişim durumu ayrı kaydedilmeli"
    assert "GOLDEN CROSS" not in m1[0] and "DEAD CROSS" not in m1[0], "ilk çalışmada geçmiş kesişim için uyarı gelmemeli"
    # 2) aynı gün tekrar: rapor yok
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 3) yeni gün: tek rapor; artık ağırlıklı genel derece/önceki derece yok
    s["last_report"] = "2000-01-01"; rd(s, st)
    r1 = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(r1) == 2 and "KRİPTO TAKİP PANELİ" in r1[0] and "Genel:" not in r1[0]
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == []
    # 3b) --report her zaman gönderir
    status_reports = cs.run(cfg, sent.append, status_only=True, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(status_reports) == 2 and all("KRİPTO TAKİP PANELİ" in message for message in status_reports)
    # 3c) yeni kesişim olursa ayrı uyarı: durumu bir önceki kesişimden öncesine al -> yalnızca sonraki kesişim bildirilir, bir kez
    evs = cross_events(compute_metrics(df, cfg["timeframes"]["1d"]))
    assert len(evs) >= 2, "sentetik veride yeterli kesişim yok, test verisini değiştir"
    s = json.loads(st.read_text(encoding="utf-8")); s["cross_ETH_1d"] = evs[-2][0].isoformat(); rd(s, st)
    eth_alert = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    assert len(eth_alert) == 1 and "ETH GÜNLÜK" in eth_alert[0] and "Ethereum (ETH/USDT)" in eth_alert[0]
    assert f"{evs[-1][0]:%d.%m.%Y}" in eth_alert[0]
    s = json.loads(st.read_text(encoding="utf-8")); s["cross_1d"] = evs[-2][0].isoformat(); rd(s, st)
    ca = cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg)
    last_ts, last_kind = evs[-1]
    assert len(ca) == 1 and ("GOLDEN CROSS" if last_kind == 1 else "DEAD CROSS") in ca[0] and f"{last_ts:%d.%m.%Y}" in ca[0], ca
    assert "BTC GÜNLÜK" in ca[0] and "genel derece" not in ca[0] and "Yatırım tavsiyesi değildir" not in ca[0] and "gecikmeli" in ca[0]
    assert json.loads(st.read_text(encoding="utf-8"))["cross_1d"] == last_ts.isoformat()
    assert cs.run(cfg, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == [], "aynı kesişim iki kez bildirilmemeli"
    s["cross_1d"] = evs[-2][0].isoformat(); rd(s, st)
    assert cs.run({**cfg, "cross_alerts": False}, sent.append, state_path=st, fetch=fetch, fetch_fg=lambda: fg) == [], "kapatılınca uyarı gelmez"
    # 4) gönderim hatasında durum kaydedilmemeli
    s = json.loads(st.read_text(encoding="utf-8")); s["last_report"] = "2000-01-01"; rd(s, st)
    def boom(_): raise RuntimeError("ağ yok")
    try: cs.run(cfg, boom, state_path=st, fetch=fetch, fetch_fg=lambda: fg); assert False
    except RuntimeError: pass
    assert json.loads(st.read_text(encoding="utf-8"))["last_report"] == "2000-01-01", "hata sonrası durum değişmemeli"
    # 5) Fear & Greed çökerse rapor yine gider (o satır olmadan)
    def fg_down(): raise RuntimeError("Fear & Greed endeksi alınamadı: test")
    m5 = cs.run(cfg, sent.append, state_path=Path(tmp) / "s5.json", fetch=fetch, fetch_fg=fg_down)
    assert m5 and "Fear & Greed: — Veri yok" in m5[-1] and "Skor:" in m5[0]
    # 6) kısa geçmiş (yedek borsa) -> yanlış rapor yerine hata
    try: cs.run(cfg, sent.append, state_path=Path(tmp) / "s6.json", fetch=lambda *a: df.iloc[:200], fetch_fg=lambda: fg); assert False
    except RuntimeError as e: assert "en az" in str(e)
    # 7) önceki sürümlerin durum dosyaları (1d/1w, zone_*) sorun çıkarmaz: yeni düzen raporu gider, eski anahtarlar temizlenir
    old = Path(tmp) / "old.json"
    rd({"1d": "2026-10-27T00:00:00+00:00", "1w": "2026-10-19T00:00:00+00:00", "zone_1d": "dip", "zone_1w": "normal",
        "last_report": "2026-10-28", "news_hist": {}}, old)
    m7 = cs.run(cfg, sent.append, state_path=old, fetch=fetch, fetch_fg=lambda: fg)
    assert m7 and len(m7) == 2 and "KRİPTO TAKİP PANELİ" in m7[0]
    s7 = json.loads(old.read_text(encoding="utf-8"))
    assert not {"1d", "1w", "zone_1d", "zone_1w"} & set(s7) and not any(k.startswith("rating_") for k in s7)
    print("ÖRNEK RAPOR:\n" + m1[0] + "\n")
# 8) skor ve çubuk birim testleri
mk = lambda *grades: [dict(grade=g) for g in grades]
assert cs.score_value(mk(2, 2, 2)) == 100 and cs.score_value(mk(-2, -2)) == -100 and cs.score_value(mk(0, 0, 0)) == 0
assert cs.score_value(mk(2, -2)) == 0 and cs.score_value(mk(1, 0, float("nan"))) == 25, "NaN göstergeler hesaba katılmamalı"
assert np.isnan(cs.score_value(mk(float("nan"))))
bars = {v: cs.score_bar(v) for v in (-100, 0, 100)}
assert bars[-100].split()[1][0] == "●" and bars[100].split()[1][-1] == "●" and bars[0].split()[1][cs.BAR_WIDTH // 2] == "●"
assert cs.score_bar(250).split()[1][-1] == "●", "sınır dışı değer kenara sıkıştırılmalı"
print("Tüm bildirim testleri geçti ✔")
