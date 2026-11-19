"""Haftalık RSI alarmı: eşik, veri zamanı ve mesaj içeriği."""
from datetime import datetime, timezone
import io
import json
import urllib.parse

import numpy as np
import pandas as pd

from core.weekly_rsi import calculate_weekly_rsi, format_report, qualifying_results
from core import data as market_data
from core.coinmarketcap import (CoinMarketCapError, fetch_top_100, get_top_100_with_fallback,
                                load_last_successful, save_last_successful)
from core import weekly_rsi as weekly_service_module


def _valid_ohlcv(close, index):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": np.ones(len(close))}, index=index)


def test_strict_threshold_and_sorted_qualifiers():
    rows = [{"symbol": f"C{i}/USDT", "rsi": value} for i, value in enumerate((27.99, 28.00, 28.01, 15.0, 45.0))]
    hits = qualifying_results(rows)
    assert [row["rsi"] for row in hits] == [15.0, 27.99]


def test_custom_threshold_is_strict_and_reported():
    rows = [{"symbol": "A", "rank": 1, "rsi": 27.49}, {"symbol": "B", "rank": 2, "rsi": 27.50}]
    assert [row["symbol"] for row in qualifying_results(rows, 27.5)] == ["A"]


def test_missing_rsi_is_never_included():
    assert qualifying_results([{"symbol": "BAD/USDT", "rsi": None}]) == []


def test_message_contains_only_hits_and_has_empty_state_message():
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    hits = qualifying_results([{"symbol": "A", "market_symbol": "A/USDT", "rank": 1, "rsi": 27.99},
                               {"symbol": "B", "market_symbol": "B/USDT", "rank": 2, "rsi": 28.0}])
    message = format_report(now, hits, {"total": 100, "successful": 97, "failures": 3})
    assert "A/USDT" in message and "B/USDT" not in message
    empty = format_report(now, [], {"total": 100, "successful": 97, "failures": 3})
    assert "hiçbir coinin" in empty and "A/USDT" not in empty
    assert "Başarılı tarama: 97" in message and "Veri alınamayan/eşleşmeyen: 3" in message


def test_rsi_fetch_uses_weekly_only_and_validates_100_bars():
    calls = []
    index = pd.date_range("2020-01-06", periods=101, freq="W-MON", tz="UTC")
    frame = _valid_ohlcv(np.linspace(100, 10, 101), index)

    def fetch(symbol, tf, exchanges, since):
        calls.append((symbol, tf, exchanges, since))
        return frame

    value, date = calculate_weekly_rsi("A/USDT", ["binance"], "2015-01-01", fetch=fetch)
    assert calls == [("A/USDT", "1w", ("binance",), "2015-01-01")]
    assert value == 0 and date == index[-1]


def test_insufficient_weekly_history_is_rejected():
    index = pd.date_range("2026-01-05", periods=30, freq="W-MON", tz="UTC")
    frame = _valid_ohlcv(np.arange(30.0), index)
    try:
        calculate_weekly_rsi("A/USDT", ["binance"], fetch=lambda *args: frame)
    except ValueError:
        pass
    else:
        raise AssertionError("short history should be rejected")


def test_open_weekly_candle_is_removed_by_shared_data_layer(monkeypatch):
    now = pd.Timestamp.now(tz="UTC")
    last_closed_open = (now - pd.Timedelta(days=8)).floor("D")
    open_candle_open = now.floor("D")
    frame = pd.DataFrame({
        "time": [int(last_closed_open.timestamp() * 1000), int(open_candle_open.timestamp() * 1000)],
        "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
        "close": [1.0, 2.0], "volume": [1.0, 1.0],
    })
    monkeypatch.setattr(market_data, "_update_from_exchange", lambda *args: frame)
    closed = market_data.get_candles("TEST/USDT", "1w", exchanges=("mock",), since="2020-01-01")
    assert len(closed) == 1 and closed["close"].iloc[0] == 1.0


def _cmc_payload():
    return {"status": {"error_code": "0"}, "data": [
        {"id": i + 1, "name": f"Coin {i + 1}", "symbol": f"C{i + 1}", "slug": f"coin-{i + 1}",
         "cmc_rank": i + 1, "quote": [{"symbol": "USD", "market_cap": 1000.0 - i}]}
        for i in range(100)
    ]}


def test_cmc_fetch_requests_exact_top_100_and_orders_by_market_cap():
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["key"] = request.get_header("X-cmc_pro_api_key")
        captured["timeout"] = timeout
        return io.BytesIO(json.dumps(_cmc_payload()).encode())

    coins = fetch_top_100("test-key", opener=opener)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)
    assert len(coins) == 100
    assert query == {"start": ["1"], "limit": ["100"], "sort": ["market_cap"],
                     "sort_dir": ["desc"], "convert": ["USD"]}
    assert captured["key"] == "test-key" and captured["timeout"] == 20
    assert [coin["market_cap"] for coin in coins] == sorted((coin["market_cap"] for coin in coins), reverse=True)
    assert coins[0]["id"] == 1


def test_last_successful_cmc_list_can_be_loaded_after_api_failure(tmp_path):
    coins = fetch_top_100("test-key", opener=lambda *args, **kwargs: io.BytesIO(json.dumps(_cmc_payload()).encode()))
    path = tmp_path / "cmc.json"
    saved_at = save_last_successful(coins, "2026-11-17T09:00:00+03:00", path=path)
    cached = load_last_successful(path=path)
    assert cached == (coins, saved_at)


def test_cmc_refresh_uses_new_api_ranking_and_drops_previous_assets(monkeypatch):
    original = fetch_top_100("test-key", opener=lambda *args, **kwargs: io.BytesIO(json.dumps(_cmc_payload()).encode()))
    updated = [dict(coin, id=coin["id"] + 1000, rank=coin["rank"], slug="new-" + coin["slug"])
               for coin in original]
    responses = iter((original, updated))
    monkeypatch.setattr("core.coinmarketcap.fetch_top_100", lambda _: next(responses))
    monkeypatch.setattr("core.coinmarketcap.save_last_successful", lambda *args, **kwargs: "saved")
    first = get_top_100_with_fallback("key")[0]
    second = get_top_100_with_fallback("key")[0]
    assert first[0]["id"] == 1 and second[0]["id"] == 1001


def test_cmc_api_failure_uses_cache_or_fails_safely(monkeypatch):
    monkeypatch.setattr("core.coinmarketcap.fetch_top_100", lambda _: (_ for _ in ()).throw(CoinMarketCapError("offline")))
    cached_coins = [{"id": i + 1, "name": f"Coin{i}", "symbol": f"C{i}", "slug": f"c{i}",
                    "rank": i + 1, "market_cap": float(100 - i)} for i in range(100)]
    monkeypatch.setattr("core.coinmarketcap.load_last_successful", lambda: (cached_coins, "2026-11-17T09:00:00+03:00"))
    result = get_top_100_with_fallback(None)
    assert result[0] == cached_coins and result[2] == "cache" and "offline" in result[3]
    monkeypatch.setattr("core.coinmarketcap.load_last_successful", lambda: None)
    try:
        get_top_100_with_fallback(None)
    except CoinMarketCapError:
        pass
    else:
        raise AssertionError("without API response and cache, the refresh must stop safely")


def test_duplicate_cmc_symbols_are_not_guessed_by_market_symbol(monkeypatch):
    coins = [
        {"id": 1, "rank": 1, "name": "Alpha", "symbol": "SHARED"},
        {"id": 2, "rank": 2, "name": "Beta", "symbol": "SHARED"},
        {"id": 3, "rank": 3, "name": "Unique", "symbol": "UNIQ"},
    ]
    monkeypatch.setattr(weekly_service_module, "_exchange_markets", lambda _: ({"binance": {
        "UNIQ/USDT": {"base": "UNIQ", "quote": "USDT", "symbol": "UNIQ/USDT", "spot": True, "active": True},
        "SHARED/USDT": {"base": "SHARED", "quote": "USDT", "symbol": "SHARED/USDT", "spot": True, "active": True},
    }}, []))
    resolved, errors = weekly_service_module.resolve_coin_markets(coins, ["binance"])
    assert set(resolved) == {3}
    assert len(errors) == 2 and all("güvenli eşleşme yok" in item["error"] for item in errors)
