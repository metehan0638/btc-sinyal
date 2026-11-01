"""CCXT ile mum verisi çeker, CSV olarak önbellekler. Sadece KAPANMIŞ mumları döndürür."""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

TF_MS = {"1d": 86_400_000, "1w": 7 * 86_400_000}
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
COLS = ["time", "open", "high", "low", "close", "volume"]


def _bar_end_ms(open_ms: int, tf: str) -> int:
    """Mumun bitiş anı; aylık mumlarda gerçek takvim ayını kullanır."""
    start = pd.Timestamp(open_ms, unit="ms", tz="UTC")
    if tf == "1M":
        return int((start + pd.DateOffset(months=1)).timestamp() * 1000)
    return open_ms + TF_MS[tf]


def _download(ex, symbol: str, tf: str, since_ms: int) -> pd.DataFrame:
    rows, prev_last = [], None
    while True:
        batch = ex.fetch_ohlcv(symbol, tf, since=since_ms, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1][0]
        if last == prev_last or _bar_end_ms(last, tf) >= int(time.time() * 1000):
            break
        prev_last, since_ms = last, last + 1
        time.sleep(ex.rateLimit / 1000)
    return pd.DataFrame(rows, columns=COLS)


def _download_binance_rest(base: str, symbol: str, tf: str, since_ms: int) -> pd.DataFrame:
    """ccxt'siz, Binance'in herkese açık veri adresinden (örn. data-api.binance.vision) mum çeker.
    GitHub Actions gibi ABD çıkışlı sunucularda normal api.binance.com engelli olabildiği için kullanılır."""
    sym, rows, prev_last = symbol.replace("/", ""), [], None
    endpoint = base.rstrip("/")
    if not endpoint.endswith("/api/v3"):
        endpoint += "/api/v3"
    while True:
        q = urllib.parse.urlencode({"symbol": sym, "interval": tf, "startTime": since_ms, "limit": 1000})
        with urllib.request.urlopen(f"{endpoint}/klines?{q}", timeout=30) as r:
            batch = json.load(r)
        if not batch:
            break
        rows += [[int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])] for b in batch]
        last = int(batch[-1][0])
        if last == prev_last or _bar_end_ms(last, tf) >= int(time.time() * 1000):
            break
        prev_last, since_ms = last, last + 1
    return pd.DataFrame(rows, columns=COLS)


def _update_from_exchange(name: str, symbol: str, tf: str, since: str, force: bool) -> pd.DataFrame:
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"{name}_{symbol.replace('/', '_')}_{tf}.csv"
    old = pd.DataFrame(columns=COLS)
    since_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)
    if cache.exists() and not force:
        old = pd.read_csv(cache)
        if len(old):
            since_ms = int(old["time"].iloc[-1])  # son mumu yeniden çek (güncellensin)

    base = os.environ.get("BINANCE_PUBLIC_URL")
    if name == "binance" and base:
        new = _download_binance_rest(base.rstrip("/"), symbol, tf, since_ms)
    else:
        import ccxt  # geç içe aktarım: yalnızca gerçekten veri çekilirken gerekir

        ex = getattr(ccxt, name)({"enableRateLimit": True})
        new = _download(ex, symbol, tf, since_ms)
    df = pd.concat([old, new]).drop_duplicates("time", keep="last").sort_values("time")
    df.to_csv(cache, index=False)
    return df


def get_candles(symbol="BTC/USDT", tf="1d", exchanges=("binance", "kraken", "bitstamp"),
                since="2017-08-17", force=False) -> pd.DataFrame:
    errors = []
    # Aylık mum Binance'in herkese açık API'sinde desteklenir; Kraken ve Bitstamp
    # aynı 1M aralığını desteklemediğinden bu zaman diliminde onlara geçme.
    monthly = tf == "1M"
    sources = ("binance",) if monthly else exchanges
    for name in sources:
        try:
            if monthly and name == "binance":
                base = os.environ.get("BINANCE_PUBLIC_URL", "https://data-api.binance.vision")
                df = _update_binance_monthly(base.rstrip("/"), symbol, since, force)
            else:
                df = _update_from_exchange(name, symbol, tf, since, force)
            print(f"[veri] {name} {symbol} {tf}: {len(df)} mum")
            break
        except Exception as e:  # ağ/erişim/sembol hatası -> sıradaki borsa
            errors.append(f"{name}: {e}")
    else:
        raise RuntimeError("Hiçbir borsadan veri alınamadı:\n" + "\n".join(errors))

    # Henüz kapanmamış mumu at (repainting'i önler)
    now_ms = int(time.time() * 1000)
    df = df[[ _bar_end_ms(int(ts), tf) <= now_ms for ts in df["time"] ]].copy()
    df.index = pd.to_datetime(df["time"], unit="ms", utc=True)
    df.index.name = "date"
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def _update_binance_monthly(base: str, symbol: str, since: str, force: bool) -> pd.DataFrame:
    """Aylık mumları Binance'in public REST API'sinden alıp diğer aralıklar gibi önbellekler."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"binance_{symbol.replace('/', '_')}_1M.csv"
    old = pd.DataFrame(columns=COLS)
    since_ts = pd.Timestamp(since, tz="UTC")
    since_ts = since_ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    since_ms = int(since_ts.timestamp() * 1000)
    if cache.exists() and not force:
        old = pd.read_csv(cache)
        if len(old):
            since_ms = int(old["time"].iloc[-1])  # son mumu yeniden çek (güncellensin)

    new = _download_binance_rest(base, symbol, "1M", since_ms)
    df = pd.concat([old, new]).drop_duplicates("time", keep="last").sort_values("time")
    df.to_csv(cache, index=False)
    return df
