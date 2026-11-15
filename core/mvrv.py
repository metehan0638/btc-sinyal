"""Bitcoin MVRV Z-Score verisi (Coin Metrics Community API: ücretsiz, anahtarsız, ticari olmayan kullanım için CC BY-NC 4.0).

    MVRV Z-Score = (Piyasa değeri − Gerçekleşmiş değer) / Piyasa değerinin standart sapması

- Piyasa değeri: CapMrktCurUSD. Gerçekleşmiş değer (Realized Cap) = piyasa değeri / MVRV (CapMVRVCur).
- Standart sapma, verinin başından o güne kadarki TÜM piyasa değeri geçmişinden hesaplanır (genişleyen pencere, popülasyon sapması);
  böylece bir günün skoru yalnızca o güne kadarki veriye dayanır (geleceğe bakmaz).
- Değer yalnızca Bitcoin içindir.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
PARAMS = {"assets": "btc", "metrics": "CapMrktCurUSD,CapMVRVCur", "frequency": "1d",
          "start_time": "2010-07-01", "page_size": "10000"}
CACHE_FILE = Path(__file__).resolve().parent.parent / "cache" / "mvrv.csv"
UA = "Mozilla/5.0 (compatible; BTCSinyalBot/1.0)"
MAX_STALE_DAYS = 5   # önbellek yedeği en fazla bu kadar eski olabilir
MIN_ROWS = 1000      # BTC günlük geçmişi binlerce satırdır; bundan azı eksik/bozuk yanıt demektir
MAX_PAGES = 10


def _parse(rows: list[dict]) -> pd.DataFrame:
    """Coin Metrics satırlarını DataFrame'e çevirir: indeks = UTC gün, sütunlar mcap ve mvrv (sayısal olmayanlar NaN)."""
    stamps, mcap, mvrv = [], [], []
    for row in rows:
        try:
            stamps.append(pd.Timestamp(row["time"]).tz_convert("UTC").floor("D"))
        except (KeyError, ValueError, TypeError):
            continue
        mcap.append(pd.to_numeric(row.get("CapMrktCurUSD"), errors="coerce"))
        mvrv.append(pd.to_numeric(row.get("CapMVRVCur"), errors="coerce"))
    frame = pd.DataFrame({"mcap": mcap, "mvrv": mvrv}, index=pd.DatetimeIndex(stamps)).astype(float)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame.index.name = None
    return frame


def _download(url: str, timeout: int) -> pd.DataFrame:
    rows: list[dict] = []
    target = f"{url}?{urllib.parse.urlencode(PARAMS)}"
    for _ in range(MAX_PAGES):
        request = urllib.request.Request(target, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        rows += payload["data"]
        target = payload.get("next_page_url")
        if not target:
            break
    frame = _parse(rows)
    if len(frame) < MIN_ROWS or frame["mcap"].notna().sum() < MIN_ROWS or frame["mvrv"].notna().sum() < MIN_ROWS:
        raise ValueError(f"beklenenden az veri geldi ({len(frame)} gün)")
    return frame


def fetch_mvrv_data(url: str = URL, attempts: int = 3, timeout: int = 30) -> pd.DataFrame:
    """Günlük piyasa değeri ve MVRV (sütunlar: mcap, mvrv; indeks = UTC gün). Hata olursa bekleyip tekrar dener;
    olmazsa en fazla 5 gün eski önbelleği kullanır; o da yoksa hata verir."""
    last = None
    for i in range(attempts):
        try:
            frame = _download(url, timeout)
            try:
                CACHE_FILE.parent.mkdir(exist_ok=True)
                frame.rename_axis("date").to_csv(CACHE_FILE)
            except OSError:
                pass
            return frame
        except Exception as e:  # noqa: BLE001
            last = e
            if i < attempts - 1:
                time.sleep(2 * (i + 1))
    if CACHE_FILE.exists():
        cached = pd.read_csv(CACHE_FILE, index_col=0, parse_dates=True)
        cached.index = pd.DatetimeIndex(cached.index).tz_localize("UTC") if cached.index.tz is None else cached.index
        cached.index.name = None
        if cached.index.max() >= pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=MAX_STALE_DAYS):
            print(f"[mvrv] API alınamadı ({last}); önbellek kullanılıyor.")
            return cached
    raise RuntimeError(f"MVRV verisi alınamadı: {last}")


def zscore(frame: pd.DataFrame) -> pd.Series:
    """MVRV Z-Score serisi. Piyasa değeri veya MVRV geçersiz (<= 0, NaN) olan günler NaN olur."""
    frame = frame.copy()
    valid = (frame["mcap"] > 0) & (frame["mvrv"] > 0)
    mcap = frame["mcap"].where(valid)
    realized = mcap / frame["mvrv"].where(valid)
    deviation = frame["mcap"].where(frame["mcap"] > 0).expanding(min_periods=2).std(ddof=0)
    out = (mcap - realized) / deviation.where(deviation > 0)
    out.name = "mvrv_z"
    return out.astype(float)
