"""Crypto Fear & Greed Index (alternative.me): ücretsiz, anahtar gerekmez. 2018-02-01'den beri günlük veri.
Sinyal mantığı (ters/contrarian): korku -> AL oyu, açgözlülük -> SAT oyu."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

FNG_URL = "https://api.alternative.me/fng/?limit=0&format=json"
CACHE_FILE = Path(__file__).resolve().parent.parent / "cache" / "fear_greed.csv"
UA = "Mozilla/5.0 (compatible; BTCSinyalBot/1.0)"
MAX_STALE_DAYS = 3  # önbellek yedeği en fazla bu kadar eski olabilir


def classify(v: float) -> str:
    if pd.isna(v):
        return "-"
    if v < 25:
        return "Aşırı korku"
    if v < 47:
        return "Korku"
    if v < 55:
        return "Nötr"
    if v < 75:
        return "Açgözlülük"
    return "Aşırı açgözlülük"


def _parse(payload: dict) -> pd.Series:
    rows = payload["data"]
    s = pd.Series({pd.Timestamp(int(r["timestamp"]), unit="s", tz="UTC").normalize(): float(r["value"]) for r in rows})
    s = s.sort_index()
    if len(s) < 200:
        raise ValueError(f"beklenenden az veri geldi ({len(s)} gün)")
    return s


def fetch_fear_greed(url: str = FNG_URL, attempts: int = 3, timeout: int = 20) -> pd.Series:
    """Günlük endeks serisi (index = UTC gün başı, değer 0-100). Hata olursa bekleyip tekrar dener;
    olmazsa en fazla 3 gün eski önbelleği kullanır; o da yoksa hata verir (yanlış sinyal üretmemek için)."""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                s = _parse(json.load(r))
            try:
                CACHE_FILE.parent.mkdir(exist_ok=True)
                s.rename_axis("date").to_csv(CACHE_FILE, header=["value"])
            except OSError:
                pass
            return s
        except Exception as e:  # noqa: BLE001
            last = e
            if i < attempts - 1:
                time.sleep(2 * (i + 1))
    if CACHE_FILE.exists():
        c = pd.read_csv(CACHE_FILE, index_col=0, parse_dates=True)["value"]
        c.index = pd.DatetimeIndex(c.index).tz_localize("UTC") if c.index.tz is None else c.index
        if c.index.max() >= pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=MAX_STALE_DAYS):
            print(f"[fear&greed] API alınamadı ({last}); önbellek kullanılıyor.")
            return c
    raise RuntimeError(f"Fear & Greed endeksi alınamadı: {last}")


def align_fear_greed(fg: pd.Series, index: pd.DatetimeIndex, tf: str) -> pd.Series:
    """Her mum için, o mum KAPANMADAN önce yayımlanmış son endeks değerini eşler (ileriye bakma yok):
    günlük mum -> mumun açıldığı günün değeri; haftalık mum -> haftanın son gününün (açılış+6 gün) değeri."""
    offset = {"1d": 0, "1w": 6}[tf]
    keys = index.normalize() + pd.Timedelta(days=offset)
    full_idx = pd.date_range(fg.index.min(), max(fg.index.max(), keys.max()), freq="D")
    full = fg.reindex(full_idx).ffill(limit=3)  # küçük veri boşluklarını (en çok 3 gün) doldur
    return pd.Series(full.reindex(keys).to_numpy(), index=index, name="fear_greed")
