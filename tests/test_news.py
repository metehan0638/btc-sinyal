"""Haber/hype modülünü sahte RSS ile test eder (internet gerekmez)."""
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import news

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>https://x/{i}</link><pubDate>{format_datetime(NOW - timedelta(hours=h))}</pubDate>"
                   f"<description>&lt;p&gt;{d}&lt;/p&gt;</description></item>" for i, (t, h, d) in enumerate(items))
    return f"<rss><channel>{body}</channel></rss>".encode()


def atom(items):
    body = "".join(f"<entry><title>{t}</title><link href='https://y/{i}'/><updated>{(NOW - timedelta(hours=h)).isoformat()}</updated>"
                   f"<summary>{d}</summary></entry>" for i, (t, h, d) in enumerate(items))
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'.encode()


A = [("SEC approves new spot Solana ETF as BlackRock files", 2, ""),
     ("Hackers drain $40M from DeFi lending protocol in exploit", 3, ""),
     ("SOL rallies 8% as Solana ETF inflows grow", 5, ""),
     ("Bitcoin holds above $80,000 ahead of Fed meeting", 6, "Interest rates and inflation in focus"),
     ("Market optimism returns", 7, "")]
B = [("Solana ETF approval: what it means for SOL", 1, ""),
     ("Another DeFi exploit hits lending platform, hackers steal funds", 4, ""),
     ("AI agents token surge as OpenAI news lifts sector", 8, ""),
     ("Old news from last week", 24 * 6, "")]

# 1) ayrıştırma (RSS + Atom)
a1, a2 = news.parse_feed(rss(A), "CoinDesk"), news.parse_feed(atom(B), "Decrypt")
assert len(a1) == 5 and len(a2) == 4 and a1[3].summary.startswith("Interest rates")
assert a2[0].link == "https://y/0" and a1[0].ts.tzinfo is not None

# 2) sınıflandırma
arts = [news.classify(a) for a in a1 + a2]
by = lambda t: next(a for a in arts if a.title.startswith(t))
assert "ETF / Kurumsal" in by("SEC approves new sp").cats and "Regülasyon / Hukuk" in by("SEC approves new sp").cats
assert "SOL" in by("SEC approves new sp").coins
assert "Güvenlik / Hack" in by("Hackers drain $40M").cats and "DeFi" in by("Hackers drain $40M").cats
assert by("Bitcoin holds above").coins == ["BTC"] and "Makro / Ekonomi" in by("Bitcoin holds above").cats
assert by("Market optimism retu").cats == [] and by("Market optimism retu").coins == [], "'optimism' kelimesi OP coini sayılmamalı"
assert "Yapay Zekâ" in by("AI agents token surg").cats

# 3) hype (geçmişsiz): trend yok, Solana ve ETF öne çıkmalı
res = news.compute_hype(arts, {}, NOW)
assert not res["use_trend"] and res["n24"] == 8, res["n24"]   # 6 günlük haber dışarıda
assert res["coins"][0]["key"] == "SOL" and res["coins"][0]["n"] == 3 and len(res["coins"][0]["sources"]) == 2
assert res["cats"][0]["key"] in ("ETF / Kurumsal", "Güvenlik / Hack", "DeFi")

# 4) geçmişle trend: SOL normalde günde 1 haber -> artış, BTC normalde çok -> düşük
hist = {f"2026-10-{d}": {"_total": 40, "coin:SOL": 1, "coin:BTC": 20} for d in (21, 22, 23, 24)}
res2 = news.compute_hype(arts, hist, NOW)
sol = next(r for r in res2["coins"] if r["key"] == "SOL")
assert res2["use_trend"] and sol["trend"] > 1.5, sol["trend"]

# 5) geçmiş güncelleme: aynı veriyle ikinci çağrı sayımları şişirmez; 10 günden eskisi silinir
h = news.update_history({}, arts, NOW)
h2 = news.update_history(__import__("copy").deepcopy(h), arts, NOW)
assert h == h2
old = {f"2026-09-{d:02d}": {"_total": 1} for d in range(1, 20)}
assert len(news.update_history(old, arts, NOW)) <= news.KEEP_DAYS

# 6) tam rapor + sahte fetch
fetch = lambda feeds: (arts, ["CoinDesk", "Decrypt"], ["The Block"])
msg, newhist = news.build_news_report({"a": "u", "b": "u", "c": "u"}, {}, fetch=fetch, now=NOW)
assert "HYPE RAPORU" in msg and "Solana (SOL)" in msg and "Ulaşılamayan kaynaklar: The Block" in msg and len(msg) < 4000
print(msg)

# 6b) güvensiz XML (ENTITY) reddedilir
bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><rss><channel><item><title>&b;</title></item></channel></rss>'
try:
    news.parse_feed(bomb, "x"); assert False
except ValueError:
    pass

# 7) tek kaynak yetmez
try:
    news.build_news_report(None, {}, fetch=lambda f: (arts, ["CoinDesk"], ["x"]), now=NOW); assert False
except news.NewsUnavailable as e:
    print("\nuyarı OK:", e)
print("\nTüm haber testleri geçti ✔")
