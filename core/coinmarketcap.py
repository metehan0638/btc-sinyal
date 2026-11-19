"""CoinMarketCap market-cap sıralı ilk 100 listesini getirir ve saklar."""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "weekly_rsi_coin_list.json"
ENDPOINT = "https://pro-api.coinmarketcap.com/v3/cryptocurrency/listings/latest"


class CoinMarketCapError(RuntimeError):
    pass


def _validate_rows(rows):
    if not isinstance(rows, list) or len(rows) != 100:
        raise CoinMarketCapError("CoinMarketCap API tam olarak 100 coin döndürmedi.")
    coins, seen_ids = [], set()
    for item in rows:
        try:
            coin_id = int(item["id"])
            symbol = str(item["symbol"]).strip().upper()
            name = str(item["name"]).strip()
            slug = str(item.get("slug", "")).strip()
            if "market_cap" in item:
                market_cap = float(item["market_cap"])
            else:
                quotes = item["quote"]
                if isinstance(quotes, dict):
                    quote = quotes.get("USD")
                else:
                    quote = next(q for q in quotes if q.get("symbol") == "USD")
                market_cap = float(quote["market_cap"])
            rank = int(item.get("cmc_rank") or item.get("rank"))
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            raise CoinMarketCapError("CoinMarketCap yanıtında zorunlu coin alanları eksik.") from exc
        if coin_id <= 0 or not symbol or not name or not math.isfinite(market_cap) or market_cap <= 0:
            raise CoinMarketCapError("CoinMarketCap yanıtında geçersiz coin kimliği veya piyasa değeri var.")
        if coin_id in seen_ids:
            raise CoinMarketCapError("CoinMarketCap yanıtında yinelenen coin ID var.")
        seen_ids.add(coin_id)
        coins.append({"id": coin_id, "symbol": symbol, "name": name, "slug": slug,
                      "rank": rank, "market_cap": market_cap})
    # API sort parametresi piyasa değeri sıralamasını ister; son kontrol/sıralama cevabı doğrular.
    return sorted(coins, key=lambda coin: (-coin["market_cap"], coin["rank"], coin["id"]))


def fetch_top_100(api_key, opener=urllib.request.urlopen, attempts=3):
    """CMC listings API'den 100 coin alır. API anahtarını hiçbir hataya eklemez."""
    if not api_key:
        raise CoinMarketCapError("COINMARKETCAP_API_KEY yapılandırılmamış.")
    query = urllib.parse.urlencode({"start": 1, "limit": 100, "sort": "market_cap",
                                    "sort_dir": "desc", "convert": "USD"})
    request = urllib.request.Request(f"{ENDPOINT}?{query}", headers={"X-CMC_PRO_API_KEY": api_key})
    for attempt in range(attempts):
        try:
            with opener(request, timeout=20) as response:
                payload = json.load(response)
            status = payload.get("status") or {}
            if int(status.get("error_code") or 0):
                # CMC status metnini göstermiyoruz; bazı sağlayıcılar hassas istek bilgisini yansıtabilir.
                raise CoinMarketCapError(f"CoinMarketCap API hata kodu {status['error_code']}.")
            return _validate_rows(payload.get("data"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or 500 <= exc.code < 600:
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
            raise CoinMarketCapError(f"CoinMarketCap HTTP hatası ({exc.code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
                continue
            reason = getattr(exc, "reason", None)
            raise CoinMarketCapError(f"CoinMarketCap bağlantısı başarısız: {reason or type(exc).__name__}.") from None
    raise CoinMarketCapError("CoinMarketCap API isteği başarısız.")


def save_last_successful(coins, fetched_at=None, path=CACHE_PATH):
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    payload = {"fetched_at": fetched_at, "coins": coins}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return fetched_at


def load_last_successful(path=CACHE_PATH):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        coins = _validate_rows(payload.get("coins"))
        fetched_at = payload["fetched_at"]
        datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        return coins, fetched_at
    except (OSError, ValueError, KeyError, TypeError, AttributeError, CoinMarketCapError):
        return None


def get_top_100_with_fallback(api_key):
    """Önce güncel CMC listesini alır; istek başarısızsa yalnızca doğrulanmış son listeyi döndürür."""
    try:
        coins = fetch_top_100(api_key)
        fetched_at = datetime.now(timezone.utc).isoformat()
        save_last_successful(coins, fetched_at)
        return coins, fetched_at, "api", None
    except Exception as exc:
        cached = load_last_successful()
        if cached is None:
            raise CoinMarketCapError(f"CMC listesi alınamadı ve son başarılı liste bulunamadı: {str(exc)[:180]}") from exc
        coins, fetched_at = cached
        return coins, fetched_at, "cache", str(exc)[:240]
