"""Kripto haber akışı: RSS topla -> kategori / coin eşleştir -> hype skoru -> rapor.
Ek kütüphane gerektirmez (yalnızca standart kütüphane)."""
from __future__ import annotations

import copy
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

IST = timezone(timedelta(hours=3))
UA = "Mozilla/5.0 (compatible; BTCSinyalBot/1.0)"

DEFAULT_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "CryptoPotato": "https://cryptopotato.com/feed/",
}

# ---- sabitler -------------------------------------------------------------
TOP_N = 5             # raporda gösterilecek kategori / coin sayısı
MIN_MENTIONS = 2      # bir konunun listeye girmesi için son 24 saatte en az haber sayısı
MIN_TOTAL = 15        # geçmişte bir günün "yeterli veri" sayılması için toplam haber sayısı
MIN_BASE_DAYS = 3     # trend hesabı için gereken geçmiş gün sayısı
KEEP_DAYS = 10

# Kategori -> anahtar kelime kalıpları (regex, büyük/küçük harf duyarsız; (?-i:..) = duyarlı)
CATEGORIES = {
    "ETF / Kurumsal": [r"\betfs?\b", r"blackrock", r"grayscale", r"fidelity", r"institution", r"asset manager",
                       r"treasury (?:reserve|strategy|company|purchase)", r"microstrategy", r"(?-i:\bStrategy\b)", r"\bspot (?:bitcoin|ether)"],
    "Regülasyon / Hukuk": [r"\bsec\b", r"\bcftc\b", r"regulat", r"lawsuit", r"\bsues?\b", r"\bcourt\b", r"\bcongress\b",
                           r"\bsenate\b", r"\blegislat", r"\bbill\b", r"\bbans?\b", r"\bmica\b", r"settlement", r"\bcompliance\b", r"\bindict", r"\bcharged\b"],
    "Güvenlik / Hack": [r"\bhack", r"exploit", r"\bstolen\b", r"\bdrain", r"\bbreach", r"vulnerab", r"rug ?pull", r"\bscams?\b", r"phishing", r"\bheist\b"],
    "DeFi": [r"\bdefi\b", r"\bdex\b", r"\blending\b", r"\byield\b", r"liquidity", r"\bstaking\b", r"restaking", r"uniswap", r"\baave\b", r"\bmakerdao\b"],
    "Yapay Zekâ": [r"(?-i:\bAI\b)", r"artificial intelligence", r"\bai agents?\b", r"machine learning", r"\bopenai\b", r"\bllm\b", r"bittensor"],
    "Memecoin": [r"\bmeme ?coins?\b", r"\bmemes?\b", r"\bdoge(?:coin)?\b", r"\bshiba\b", r"\bpepe\b", r"\bbonk\b", r"pump\.fun"],
    "Layer-2 / Altyapı": [r"layer[- ]?2", r"\bl2s?\b", r"rollup", r"arbitrum", r"\bzk\b", r"hard ?fork", r"\bmainnet\b", r"\btestnet\b", r"\bupgrade\b", r"\bprotocol\b"],
    "NFT / Oyun": [r"\bnfts?\b", r"\bgaming\b", r"metaverse", r"opensea", r"web3 game"],
    "Stablecoin / Ödeme": [r"stablecoin", r"\busdt\b", r"\busdc\b", r"\btether\b", r"(?-i:\bCircle\b)", r"\bpayments?\b", r"\bpaypal\b", r"\bvisa\b", r"mastercard", r"\bstripe\b"],
    "Borsa / Platform": [r"\bbinance\b", r"\bcoinbase\b", r"\bkraken\b", r"\bokx\b", r"\bbybit\b", r"\blisting\b", r"\bdelist", r"\bexchange\b", r"\bipo\b", r"\brobinhood\b"],
    "RWA / Tokenizasyon": [r"tokeniz", r"\brwa\b", r"real[- ]world asset"],
    "Makro / Ekonomi": [r"(?-i:\bFed\b)", r"federal reserve", r"interest rates?", r"inflation", r"\bcpi\b", r"powell", r"tariff", r"recession", r"jobs report", r"\bgdp\b", r"rate cut"],
    "Fiyat Hareketi": [r"\brall(?:y|ies)\b", r"\bsurg", r"\bplunge", r"\bcrash", r"all-time high", r"\bath\b", r"liquidation", r"\bbull(?:ish)?\b", r"\bbear(?:ish)?\b", r"\bdumps?\b", r"\bsoars?\b", r"\btumbles?\b"],
    "Madencilik": [r"\bminers?\b", r"\bmining\b", r"hashrate", r"\bhalving\b"],
}

# Sembol -> (görünen ad, kalıplar). Kısa tickerlar yalnızca BÜYÜK harfle eşleşir (yanlış pozitifi azaltır).
COINS = {
    "BTC": ("Bitcoin", [r"\bbitcoin\b", r"(?-i:\bBTC\b)"]),
    "ETH": ("Ethereum", [r"\bether(?:eum)?\b", r"(?-i:\bETH\b)"]),
    "SOL": ("Solana", [r"\bsolana\b", r"(?-i:\bSOL\b)"]),
    "XRP": ("XRP", [r"\bripple\b", r"(?-i:\bXRP\b)"]),
    "BNB": ("BNB", [r"\bbnb\b", r"binance coin"]),
    "DOGE": ("Dogecoin", [r"\bdoge(?:coin)?\b"]),
    "ADA": ("Cardano", [r"\bcardano\b", r"(?-i:\bADA\b)"]),
    "AVAX": ("Avalanche", [r"\bavalanche\b", r"(?-i:\bAVAX\b)"]),
    "LINK": ("Chainlink", [r"\bchainlink\b", r"(?-i:\bLINK\b)"]),
    "DOT": ("Polkadot", [r"\bpolkadot\b", r"(?-i:\bDOT\b)"]),
    "TON": ("Toncoin", [r"\btoncoin\b", r"the open network", r"(?-i:\bTON\b)"]),
    "TRX": ("Tron", [r"(?-i:\b(?:TRON|Tron|TRX)\b)"]),
    "LTC": ("Litecoin", [r"\blitecoin\b", r"(?-i:\bLTC\b)"]),
    "SHIB": ("Shiba Inu", [r"shiba inu", r"(?-i:\bSHIB\b)"]),
    "SUI": ("Sui", [r"(?-i:\b(?:SUI|Sui)\b)"]),
    "HYPE": ("Hyperliquid", [r"hyperliquid"]),
    "PEPE": ("Pepe", [r"\bpepe\b"]),
    "UNI": ("Uniswap", [r"uniswap"]),
    "AAVE": ("Aave", [r"\baave\b"]),
    "ARB": ("Arbitrum", [r"arbitrum"]),
    "POL": ("Polygon", [r"\bpolygon\b", r"(?-i:\b(?:POL|MATIC)\b)"]),
    "NEAR": ("NEAR", [r"(?-i:\bNEAR\b)"]),
    "TAO": ("Bittensor", [r"bittensor"]),
    "XMR": ("Monero", [r"\bmonero\b"]),
    "ZEC": ("Zcash", [r"\bzcash\b"]),
    "ENA": ("Ethena", [r"\bethena\b"]),
    "USDT": ("Tether", [r"\btether\b", r"(?-i:\bUSDT\b)"]),
    "USDC": ("USDC", [r"(?-i:\bUSDC\b)"]),
}

_CAT_RE = {k: [re.compile(p, re.I) for p in v] for k, v in CATEGORIES.items()}
_COIN_RE = {k: (n, [re.compile(p, re.I) for p in v]) for k, (n, v) in COINS.items()}


class NewsUnavailable(RuntimeError):
    pass


@dataclass
class Article:
    title: str
    source: str
    link: str
    ts: datetime
    summary: str = ""
    cats: list = field(default_factory=list)
    coins: list = field(default_factory=list)


# ---- RSS / Atom ayrıştırma --------------------------------------------------
def _strip(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _parse_date(s: str | None):
    if not s or not s.strip():
        return None
    try:
        d = parsedate_to_datetime(s.strip())
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _local(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def parse_feed(data: bytes, source: str) -> list[Article]:
    root = ET.fromstring(data)
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        kids = {}
        for c in el:
            kids.setdefault(_local(c.tag), c)
        title = _strip(kids["title"].text) if "title" in kids else ""
        link = ""
        if "link" in kids:
            link = (kids["link"].get("href") or kids["link"].text or "").strip()
        date = next((_parse_date(kids[k].text) for k in ("pubDate", "published", "updated", "date") if k in kids), None)
        summ = next((_strip(kids[k].text) for k in ("description", "summary", "content") if k in kids), "")
        if title and date:
            out.append(Article(title=title, source=source, link=link, ts=date, summary=summ[:600]))
    return out


def fetch_news(feeds: dict | None = None, timeout: int = 20, max_items: int = 60):
    """Döndürür: (makaleler, çalışan_kaynaklar, çalışmayan_kaynaklar). Tek tek kaynak hatası tüm akışı durdurmaz."""
    feeds = feeds or DEFAULT_FEEDS
    articles, ok, failed, seen = [], [], [], set()
    for name, url in feeds.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                items = parse_feed(r.read(), name)[:max_items]
            if not items:
                raise ValueError("haber bulunamadı")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"[haber] {name} atlandı: {e}")
            continue
        ok.append(name)
        for a in items:
            key = re.sub(r"\W+", "", a.title.lower())
            if key not in seen:
                seen.add(key)
                articles.append(a)
    return articles, ok, failed


# ---- sınıflandırma ----------------------------------------------------------
def _hits(pats, text: str) -> int:
    return sum(len(p.findall(text)) for p in pats)


def classify(a: Article) -> Article:
    body = a.summary[:400]
    cats = {}
    for label, pats in _CAT_RE.items():
        t, s = _hits(pats, a.title), _hits(pats, body)
        if t > 0 or s >= 2:
            cats[label] = 2 * t + min(s, 2)
    a.cats = [k for k, _ in sorted(cats.items(), key=lambda x: -x[1])[:3]]
    coins = {}
    for sym, (_, pats) in _COIN_RE.items():
        t, s = _hits(pats, a.title), _hits(pats, body)
        if t > 0 or s >= 2:
            coins[sym] = 2 * t + min(s, 2)
    a.coins = [k for k, _ in sorted(coins.items(), key=lambda x: -x[1])[:4]]
    return a


# ---- hype skoru -------------------------------------------------------------
def _day(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%Y-%m-%d")


def _topics(a: Article):
    return [("cat", c) for c in a.cats] + [("coin", s) for s in a.coins]


def compute_hype(articles, hist: dict, now: datetime, hours: int = 24) -> dict:
    """Skor = haber sayısı × (1 + 0.3×(kaynak sayısı-1)) × trend.
    Trend = son 24 saat / önceki günlerin ortalaması (en az MIN_BASE_DAYS gün geçmiş varsa), 0.5–3 arasına sıkıştırılır."""
    recent = [a for a in articles if a.ts >= now - timedelta(hours=hours)]
    agg = {}
    for a in recent:
        for t in _topics(a):
            d = agg.setdefault(t, {"arts": [], "src": set()})
            d["arts"].append(a)
            d["src"].add(a.source)
    today = _day(now)
    prev = sorted(d for d, v in hist.items() if d < today and v.get("_total", 0) >= MIN_TOTAL)[-7:]
    use_trend = len(prev) >= MIN_BASE_DAYS
    rows = []
    for (kind, key), d in agg.items():
        n = len(d["arts"])
        if n < MIN_MENTIONS:
            continue
        trend = None
        if use_trend:
            base = sum(hist[x].get(f"{kind}:{key}", 0) for x in prev) / len(prev)
            trend = min(max((n + 1) / (base + 1), 0.5), 3.0)
        score = n * (1 + 0.3 * (len(d["src"]) - 1)) * (trend or 1.0)
        rows.append(dict(kind=kind, key=key, label=key if kind == "cat" else f"{COINS[key][0]} ({key})",
                         n=n, sources=sorted(d["src"]), trend=trend, score=score,
                         example=max(d["arts"], key=lambda a: a.ts)))
    pick = lambda k: sorted((r for r in rows if r["kind"] == k), key=lambda r: -r["score"])[:TOP_N]
    return dict(cats=pick("cat"), coins=pick("coin"), n24=len(recent), base_days=len(prev), use_trend=use_trend)


def update_history(hist: dict, articles, now: datetime) -> dict:
    """Günlük konu sayaçlarını saklar (en büyük değer korunur; aynı gün birden çok çalışma sayımı şişirmez)."""
    days = sorted({_day(a.ts) for a in articles})
    skip = days[0] if len(days) > 1 else None  # akıştaki en eski gün büyük olasılıkla yarım
    per_day = {}
    for a in articles:
        d = _day(a.ts)
        if d == skip:
            continue
        c = per_day.setdefault(d, {})
        c["_total"] = c.get("_total", 0) + 1
        for kind, key in _topics(a):
            c[f"{kind}:{key}"] = c.get(f"{kind}:{key}", 0) + 1
    for d, counts in per_day.items():
        h = hist.setdefault(d, {})
        for k, v in counts.items():
            h[k] = max(h.get(k, 0), v)
    for d in sorted(hist)[:-KEEP_DAYS]:
        del hist[d]
    return hist


# ---- rapor ------------------------------------------------------------------
def _short(t: str, n: int = 95) -> str:
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _trend_txt(r) -> str:
    if r["trend"] is None:
        return ""
    arrow = "🔺" if r["trend"] >= 1.5 else ("🔻" if r["trend"] <= 0.7 else "▫️")
    return f" · olağanın ×{r['trend']:.1f} {arrow}"


def _lines(rows) -> list[str]:
    out = []
    for i, r in enumerate(rows, 1):
        out.append(f"{i}. {r['label']} — {r['n']} haber · {len(r['sources'])} kaynak{_trend_txt(r)}")
        out.append(f"   ↳ “{_short(r['example'].title)}” ({r['example'].source})")
    return out or ["(bugün belirgin bir konu yok)"]


def format_report(res: dict, now: datetime) -> str:
    parts = [f"🔥 KRİPTO HYPE RAPORU — {now.astimezone(IST):%d.%m.%Y}", "",
             f"Son 24 saatte {res['ok']}/{res['total']} kaynaktan {res['n24']} haber tarandı.", "",
             "📂 ÖNE ÇIKAN KATEGORİLER"] + _lines(res["cats"]) + ["", "🪙 ÖNE ÇIKAN COİN / PROJELER"] + _lines(res["coins"]) + [""]
    if not res["use_trend"]:
        parts.append(f"ℹ️ Trend (olağana göre artış) için geçmiş veri birikiyor ({res['base_days']}/{MIN_BASE_DAYS} gün). Şimdilik sıralama haber ve kaynak sayısına göre.")
    parts.append("Kategori/coin eşleştirmesi anahtar kelimeyle yapılır, yanılabilir. Başlıklar kaynaklardan alınmıştır.")
    if res.get("failed"):
        parts.append("Ulaşılamayan kaynaklar: " + ", ".join(res["failed"]))
    parts += ["", "⚠️ Yatırım tavsiyesi değildir."]
    return "\n".join(parts)


def analyze(feeds=None, hist=None, fetch=None, now=None):
    """Haberleri çek, sınıflandır, hype hesapla. Döndürür: (makaleler, sonuç sözlüğü)."""
    now = now or datetime.now(timezone.utc)
    feeds = feeds or DEFAULT_FEEDS
    articles, ok, failed = (fetch or fetch_news)(feeds)
    if len(ok) < 2 or not articles:
        raise NewsUnavailable(f"Yeterli haber kaynağına ulaşılamadı ({len(ok)}/{len(feeds)}).")
    for a in articles:
        classify(a)
    res = compute_hype(articles, hist or {}, now)
    res.update(ok=len(ok), total=len(feeds), failed=failed)
    return articles, res


def build_news_report(feeds=None, hist=None, fetch=None, now=None):
    """Döndürür: (Telegram mesajı, güncellenmiş geçmiş)."""
    now = now or datetime.now(timezone.utc)
    articles, res = analyze(feeds, hist, fetch, now)
    return format_report(res, now), update_history(copy.deepcopy(hist or {}), articles, now)
