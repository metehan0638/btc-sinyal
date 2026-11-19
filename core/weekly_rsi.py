"""Günlük haftalık RSI alarmı ve kalıcı zamanlayıcı durumu."""
from __future__ import annotations

import json
import os
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path

import pandas as pd
import numpy as np

from core.data import get_candles
from core.coinmarketcap import get_top_100_with_fallback
from core.indicators import wilder_rsi
from core.notifier import send_telegram

# Türkiye saati UTC+03:00 sabitidir; sabit offset Windows'ta tzdata paketine bağımlılığı önler.
IST = timezone(timedelta(hours=3), name="Europe/Istanbul")
DEFAULT_THRESHOLD = 28.0
STATE_PATH = Path(__file__).resolve().parent.parent / "weekly_rsi_state.json"
SETTINGS_PATH = STATE_PATH.with_name("weekly_rsi_settings.json")
_MARKETS_CACHE = {"expires": 0.0, "exchanges": (), "markets": {}}


def calculate_weekly_rsi(symbol: str, exchanges, since="2015-01-01", fetch=get_candles):
    """Yalnızca kapanmış 1w mumlardan RSI(14) döndürür; veri sorunu istisna üretir."""
    candles = fetch(symbol, "1w", tuple(exchanges), since)
    required = ("open", "high", "low", "close", "volume")
    if not isinstance(candles, pd.DataFrame) or not set(required).issubset(candles.columns) or len(candles) < 100:
        raise ValueError("100 kapanmış haftalık mum bulunamadı")
    candles = candles.sort_index()
    if candles.index.has_duplicates:
        raise ValueError("Haftalık mumlarda yinelenen tarih var")
    candles = candles.loc[:, list(required)].apply(pd.to_numeric, errors="coerce")
    values = candles.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Haftalık OHLCV verisinde eksik veya sonlu olmayan değer var")
    if ((candles[["open", "high", "low", "close"]] <= 0).any().any()
            or (candles["volume"] < 0).any()
            or (candles["high"] < candles[["open", "close", "low"]].max(axis=1)).any()
            or (candles["low"] > candles[["open", "close", "high"]].min(axis=1)).any()):
        raise ValueError("Haftalık OHLCV değerleri tutarsız")
    close = candles["close"]
    value = float(wilder_rsi(close, 14).iloc[-1])
    if not pd.notna(value):
        raise ValueError("Haftalık RSI hesaplanamadı")
    return value, candles.index[-1]


def qualifying_results(results, threshold=DEFAULT_THRESHOLD):
    threshold = float(threshold)
    return sorted((row for row in results if row.get("rsi") is not None and row["rsi"] < threshold),
                  key=lambda row: (row["rsi"], row.get("rank", 10**9), row.get("id", 10**12)))


def format_report(date, hits, scanned, list_source="api", list_updated_at=None, threshold=DEFAULT_THRESHOLD):
    successful = scanned.get("successful", 0) if isinstance(scanned, dict) else scanned
    total = scanned.get("total", 100) if isinstance(scanned, dict) else 100
    failures = scanned.get("failures", 0) if isinstance(scanned, dict) else max(total - successful, 0)
    date_text = date.astimezone(IST).strftime("%d.%m.%Y %H:%M")
    lines = ["🔔 COINMARKETCAP İLK 100 – HAFTALIK RSI RAPORU", "", f"📅 Tarama: {date_text}", ""]
    if list_source == "cache" and list_updated_at:
        cached_at = datetime.fromisoformat(list_updated_at.replace("Z", "+00:00")).astimezone(IST)
        lines.append(f"📋 Son başarılı CMC listesi kullanıldı: {cached_at:%d.%m.%Y %H:%M}")
        lines.append("")
    if hits:
        lines.append(f"🚨 Haftalık RSI {threshold:g}'in Altındaki Coinler:")
        lines += [f"{i}. #{row['rank']} {row['market_symbol']} — RSI: {row['rsi']:.2f}"
                  for i, row in enumerate(hits, 1)]
    else:
        lines.append(f"✅ İlk 100 içerisindeki hiçbir coinin haftalık RSI değeri {threshold:g}'in altında değil.")
    lines += ["", f"📊 CoinMarketCap İlk 100'den taranan: {total}",
              f"✅ Başarılı tarama: {successful}", f"⚠️ Veri alınamayan/eşleşmeyen: {failures}",
              f"🚨 RSI 28 altı: {len(hits)}"]
    return "\n".join(lines)


def _exchange_markets(exchange_names):
    """Borsa market metadata'sını bir saat bellekte tutar; ağ çağrısı bir taramada tekrarlanmaz."""
    key = tuple(exchange_names)
    if _MARKETS_CACHE["exchanges"] == key and _MARKETS_CACHE["expires"] > time_monotonic():
        return _MARKETS_CACHE["markets"], _MARKETS_CACHE.get("errors", [])
    try:
        import ccxt
    except ImportError:
        return {}, ["ccxt kurulmamış."]

    markets, errors = {}, []
    for name in key:
        try:
            exchange = getattr(ccxt, name)({"enableRateLimit": True})
            markets[name] = exchange.load_markets()
        except Exception as exc:
            errors.append(f"{name}: {str(exc)[:180]}")
    if markets:
        _MARKETS_CACHE.update(exchanges=key, markets=markets, errors=errors,
                              expires=time_monotonic() + 3600)
    return markets, errors


def time_monotonic():
    import time
    return time.monotonic()


def resolve_coin_markets(coins, exchange_names, quote="USDT"):
    """CMC coinlerini yalnızca tekil sembol ve canlı USDT spot market'iyle eşler."""
    markets_by_exchange, exchange_errors = _exchange_markets(exchange_names)
    symbol_counts = Counter(coin["symbol"] for coin in coins)
    resolved, errors = {}, []
    for coin in coins:
        ticker = coin["symbol"]
        if symbol_counts[ticker] > 1:
            errors.append({"id": coin["id"], "rank": coin["rank"], "name": coin["name"],
                           "symbol": ticker, "error": "CMC ilk 100 içinde sembol birden fazla coinde var; güvenli eşleşme yok."})
            continue
        candidates = []
        for exchange_name in exchange_names:
            exchange_candidates = []
            for market in markets_by_exchange.get(exchange_name, {}).values():
                is_spot = market.get("spot") is True or market.get("type") == "spot"
                if (market.get("base") == ticker and market.get("quote") == quote
                        and market.get("active") is True and is_spot
                        and not market.get("swap") and not market.get("future")):
                    exchange_candidates.append((exchange_name, market))
            candidates.extend(exchange_candidates)
            if exchange_candidates:
                break
        if not candidates:
            detail = "USDT spot market bulunamadı."
            if not markets_by_exchange and exchange_errors:
                detail = "Borsa market listesi alınamadı. " + "; ".join(exchange_errors)
            errors.append({"id": coin["id"], "rank": coin["rank"], "name": coin["name"],
                           "symbol": ticker, "error": detail})
            continue
        exact = [(name, market) for name, market in candidates if market.get("symbol") == f"{ticker}/{quote}"]
        if len(exact) != 1:
            errors.append({"id": coin["id"], "rank": coin["rank"], "name": coin["name"],
                           "symbol": ticker, "error": "Tekil USDT spot eşleşmesi yok."})
            continue
        exchange_name, market = exact[0]
        resolved[coin["id"]] = {**coin, "exchange": exchange_name, "market_symbol": market["symbol"]}
    return resolved, errors


class WeeklyRSIService:
    def __init__(self, config):
        self.config = dict(config)
        try:
            self.config.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self.config["threshold"] = self._valid_threshold(self.config.get("threshold", DEFAULT_THRESHOLD))
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-rsi")
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.scheduler_thread = None
        self._load()

    def _load(self):
        try:
            self.state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("last_results", [])
        self.state.setdefault("last_errors", self.state.pop("errors", []))
        self.state.setdefault("selected_coins", [])
        if any(not {"id", "rank", "market_symbol"}.issubset(row) for row in self.state["last_results"]):
            self.state["last_results"] = []
        # A process restart ends any in-memory scan that was active.
        self.state["running"] = False

    @staticmethod
    def _valid_threshold(value):
        value = float(value)
        if not 0 <= value <= 100:
            raise ValueError("RSI alarm eşiği 0 ile 100 arasında olmalı.")
        return value

    def _save(self):
        temp = STATE_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(STATE_PATH)

    def start(self):
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            return
        if not self.config.get("enabled", True):
            return
        self.stop_event.clear()
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, name="weekly-rsi-scheduler", daemon=True)
        self.scheduler_thread.start()

    def _scheduler_loop(self):
        while not self.stop_event.is_set():
            if not self.config.get("enabled", True):
                self.wake_event.wait(60)
                self.wake_event.clear()
                continue
            target = next_scheduled_time(self.config)
            delay = max(0.0, (target - datetime.now(IST)).total_seconds())
            interrupted = self.wake_event.wait(min(delay, 60.0))
            self.wake_event.clear()
            if self.stop_event.is_set():
                return
            if not interrupted and datetime.now(IST) >= target:
                self.executor.submit(self.run_scan)

    def next_run(self):
        if not self.config.get("enabled", True):
            return None
        return next_scheduled_time(self.config)

    def status(self):
        with self.lock:
            return {**self.state, "active": bool(self.config.get("enabled", True) and self.scheduler_thread and self.scheduler_thread.is_alive()),
                    "next_run": self.next_run(), "busy": bool(self.state.get("running"))}

    def submit_scan(self):
        return self.executor.submit(self.run_scan, True)

    def update_schedule(self, enabled, scan_time, threshold=DEFAULT_THRESHOLD):
        threshold = self._valid_threshold(threshold)
        self.config.update(enabled=bool(enabled), time=scan_time.strftime("%H:%M"), threshold=threshold)
        SETTINGS_PATH.write_text(json.dumps({"enabled": self.config["enabled"], "time": self.config["time"],
                                             "threshold": threshold},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
        if self.state.get("last_scan_at") and not self.state.get("last_scan_error"):
            if self.state.get("last_all_results") is not None:
                self.state["last_results"] = qualifying_results(self.state["last_all_results"], threshold)
                self.state["threshold_results_stale"] = False
            else:
                self.state["last_results"] = []
                self.state["threshold_results_stale"] = True
            self._save()
        self.wake_event.set()
        if enabled:
            self.start()

    def run_scan(self, manual=False):
        today = datetime.now(IST).date().isoformat()
        with self.lock:
            if self.state.get("running"):
                return
            if not manual and self.state.get("last_scan_date") == today:
                return
            self.state["running"] = True
            self.state["last_scan_date"] = today
            self._save()
        errors, results = [], []
        api_key = os.getenv("COINMARKETCAP_API_KEY")
        try:
            coins, fetched_at, list_source, list_error = get_top_100_with_fallback(api_key)
        except Exception as exc:
            now = datetime.now(IST)
            message = f"CMC ilk 100 listesi alınamadı ve kullanılabilir önbellek yok: {str(exc)[:240]}"
            with self.lock:
                self.state.update(running=False, last_scan_at=now.isoformat(), last_scan_error=message,
                                  last_results=[], last_errors=[], scanned=0, requested=100,
                                  last_all_results=[], threshold_results_stale=False,
                                  selected_coins=[], coin_list_count=0, coin_list_source=None,
                                  coin_list_updated_at=None)
                self._save()
            return self.status()

        with self.lock:
            self.state.update(coin_list_count=len(coins), coin_list_source=list_source,
                              coin_list_updated_at=fetched_at, coin_list_error=list_error,
                              selected_coins=[{k: coin[k] for k in ("id", "rank", "name", "symbol", "slug", "market_cap")}
                                              | {"market_symbol": None, "exchange": None} for coin in coins])
            self._save()

        exchanges = self.config.get("exchanges", ["binance"])
        try:
            resolved, errors = resolve_coin_markets(coins, exchanges, self.config.get("quote", "USDT"))
        except Exception as exc:
            resolved = {}
            errors = [{"id": coin["id"], "rank": coin["rank"], "name": coin["name"],
                       "symbol": coin["symbol"], "error": f"Borsa sembol eşleştirmesi başarısız: {str(exc)[:180]}"}
                      for coin in coins]
        for item in coins:
            match = resolved.get(item["id"])
            if match is None:
                continue
            try:
                value, candle_date = calculate_weekly_rsi(match["market_symbol"], [match["exchange"]],
                                                          self.config.get("since", "2015-01-01"))
                results.append({**match, "rsi": value, "candle_date": str(candle_date)})
            except Exception as exc:
                errors.append({"id": item["id"], "rank": item["rank"], "name": item["name"],
                               "symbol": item["symbol"], "error": str(exc)[:240]})
        threshold = self.config["threshold"]
        hits = qualifying_results(results, threshold)
        now = datetime.now(IST)
        report = format_report(now, hits,
                               {"total": len(coins), "successful": len(results), "failures": len(errors)},
                               list_source=list_source, list_updated_at=fetched_at, threshold=threshold)
        token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        sent = False
        telegram_error = None if token and chat_id else "TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID yapılandırılmamış."
        if token and chat_id:
            # Önce kalıcı işaret koyarak süreç yeniden başlarsa aynı gün çift mesaj atılmasını önle.
            with self.lock:
                should_send = self.state.get("notified_date") != today
                if should_send:
                    self.state["notified_date"] = today
                    self._save()
            if should_send:
                try:
                    send_telegram(token, chat_id, report)
                    sent = True
                except Exception as exc:
                    telegram_error = str(exc)
        with self.lock:
            self.state.update(running=False, last_scan_at=now.isoformat(), scanned=len(results),
                              requested=len(coins), coin_list_count=len(coins), coin_list_source=list_source,
                              coin_list_updated_at=fetched_at, coin_list_error=list_error,
                              selected_coins=[{k: coin[k] for k in ("id", "rank", "name", "symbol", "slug", "market_cap")}
                                              | ({"market_symbol": resolved[coin["id"]]["market_symbol"],
                                                  "exchange": resolved[coin["id"]]["exchange"]}
                                                 if coin["id"] in resolved else {"market_symbol": None, "exchange": None})
                                              for coin in coins],
                              last_results=hits, last_errors=errors, last_scan_error=None,
                              last_all_results=results, threshold_results_stale=False,
                              telegram_sent=sent, telegram_error=telegram_error, last_report=report)
            self._save()
        return self.status()


def next_scheduled_time(config, now=None):
    """Sonraki Türkiye saati taramasını döndürür; aynı günün geçmiş saati ertesi güne taşınır."""
    now = now or datetime.now(IST)
    hh, mm = map(int, config.get("time", "09:00").split(":"))
    candidate = datetime.combine(now.date(), clock_time(hh, mm), IST)
    return candidate if candidate > now else candidate + timedelta(days=1)


