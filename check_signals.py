"""Kripto varlıklar için gösterge derecelendirme raporunu ve golden/dead cross uyarılarını Telegram'a gönderir.
Dereceler göstergelerin değerlerine bakan mekanik etiketlerdir; emir ya da tavsiye değildir.

Kullanım:
  python check_signals.py                # günlük rapor (Görev Zamanlayıcı / GitHub Actions bunu çalıştırır)
  python check_signals.py --dry-run      # mesajları göndermeden ekrana yazar, durumu kaydetmez
  python check_signals.py --report       # raporu hemen Telegram'a gönderir (--status da çalışır)
  python check_signals.py --test         # test mesajı gönderir
  python check_signals.py --get-chat-id  # bota mesaj attıktan sonra chat ID'ni bulur
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

from core import sentiment
from core.data import get_candles
from core.notifier import get_chats, load_env, send_telegram
from core.sentiment import fetch_fear_greed
from core.zones import (LEVELS, MIN_BARS, RATING_TEXT, compute_metrics, cross_events, latest_rows,
                        summarize, trend_info)

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
TF_UP = {"1d": "GÜNLÜK", "1w": "HAFTALIK", "1M": "AYLIK"}
TF_TR = {"1d": "Günlük", "1w": "Haftalık", "1M": "Aylık"}
IST = timezone(timedelta(hours=3))  # Türkiye saati
MAX_CROSS_ALERTS = 2  # uzun süre çalışmamışsa en fazla bu kadar geçmiş kesişim uyarısı gönderilir
MAX_REPORT_CHARS = 3800  # Telegram'ın 4096 karakter sınırına güvenli pay bırak
SIGNAL_LABELS = {2: "Güçlü al", 1: "Al", 0: "Nötr", -1: "Sat", -2: "Güçlü sat"}
BAR_WIDTH = 21  # skor çubuğundaki hücre sayısı (orta hücre = 0)


def configured_assets(cfg: dict) -> list[dict]:
    """Varlık listesini normalleştir; eski tek-sembol ayarını da destekle."""
    raw_assets = cfg.get("assets") or [{"symbol": cfg.get("symbol", "BTC/USDT"),
                                        "name": "Bitcoin", "ticker": "BTC",
                                        "since": cfg.get("since", "2017-08-17")}]
    assets, seen = [], set()
    for item in raw_assets:
        symbol = item["symbol"]
        ticker = item.get("ticker", symbol.split("/")[0]).upper()
        if ticker in seen:
            raise ValueError(f"Varlık listesinde yinelenen ticker: {ticker}")
        seen.add(ticker)
        assets.append({"ticker": ticker, "name": item.get("name", ticker), "symbol": symbol,
                       "since": item.get("since", cfg.get("since", "2017-08-17"))})
    if not assets:
        raise ValueError("En az bir varlık yapılandırılmalı.")
    return assets


def _cross_state_key(asset: dict, tf: str) -> str:
    # BTC anahtarlarını koru ki eski state.json dosyalarında aynı kesişim tekrar bildirilmesin.
    return f"cross_{tf}" if asset["ticker"] == "BTC" else f"cross_{asset['ticker']}_{tf}"


def analyze(df, tf: str, cfg: dict, fg_info: dict | None = None):
    """Döndürür: (mumlar, ham değerler, gösterge satırları, özet, trend). Fear & Greed yalnızca günlükte hesaba katılır."""
    params = cfg["timeframes"].get(tf, {})
    raw = compute_metrics(df, params)
    rows = latest_rows(raw, params, tf, fg_info if tf == "1d" else None)
    return df, raw, rows, trend_info(raw, params, tf)


def score_value(rows: list[dict]) -> float:
    """Geçerli göstergelerin dereceleri (-2..+2) ortalamasının 50 katı: -100 (hepsi güçlü sat) .. +100 (hepsi güçlü al).
    Hiç geçerli gösterge yoksa NaN."""
    mean = summarize(rows)["mean"]
    return float("nan") if pd.isna(mean) else float(mean) * 50


def score_bar(score: float, width: int = BAR_WIDTH) -> str:
    """-100..+100 aralığında skoru gösteren metin çubuğu: ● skoru, │ sıfır noktasını gösterir."""
    cells = ["░"] * width
    mid = width // 2
    cells[mid] = "│"
    pos = round((max(-100.0, min(100.0, score)) + 100) / 200 * (width - 1))
    cells[pos] = "●"
    return f"-100 {''.join(cells)} +100"


def _score_summary(results, asset: dict) -> list[str]:
    quote = asset["symbol"].split("/")[-1]
    close = results["1d"][1]["close"]
    lines = [f"🪙 {asset['ticker']} — {asset['name']}",
             f"💰 Günlük kapanış ({close.index[-1]:%d.%m.%Y}): {close.iloc[-1]:,.8g} {quote}"]
    for tf, timeframe in TF_TR.items():
        rows = results[tf][2]
        score = score_value(rows)
        lines.append("")
        if pd.isna(score):
            lines.append(f"📅 {timeframe.upper()} · ❔ Yetersiz veri")
            continue
        level = summarize(rows)["level"]
        value = int(round(score))
        shown = f"{value:+d}" if value else "0"
        lines.append(f"📅 {timeframe.upper()} · Skor: {shown} · {LEVELS[level][0]} {SIGNAL_LABELS[level]}")
        lines.append(score_bar(score))
    return lines


def fear_greed_line(fg_info: dict | None) -> str:
    if not fg_info:
        return "😶 Fear & Greed: — Veri yok"
    hist = " · ".join(f"{n} {fg_info[k]:.0f}" for n, k in (("dün", "yesterday"), ("7g", "week"), ("30g", "month"))
                      if fg_info.get(k) is not None)
    emoji = sentiment.EMOJI.get(fg_info["label"], "")
    return f"{emoji} Fear & Greed: {fg_info['value']:.0f} — {fg_info['label']}" + (f" ({hist})" if hist else "")


def cross_message(tf: str, ts, kind: int, results, asset: dict | None = None) -> str:
    df, raw, rows, trend = results[tf]
    asset = asset or {"ticker": "BTC", "name": "Bitcoin", "symbol": "BTC/USDT"}
    quote = asset["symbol"].split("/")[-1]
    fast, slow = trend["fast"], trend["slow"]
    golden = kind == 1
    close = raw["close"].loc[ts]
    return "\n".join([
        f"{'🟢' if golden else '🔴'} {asset['ticker']} {TF_UP[tf]} — {'GOLDEN CROSS (olumlu sinyal)' if golden else 'DEAD CROSS (olumsuz sinyal)'}",
        f"{asset['name']} ({asset['symbol']}) için {ts:%d.%m.%Y} mumu kapandı: hızlı ortalama (SMA{fast}), "
        f"yavaş ortalamayı (SMA{slow}) {'yukarı' if golden else 'aşağı'} kesti. Kapanış {close:,.8g} {quote}.",
        "Not: Kesişim gecikmeli bir onay sinyalidir; fiyat çoğu zaman haftalardır o yönde hareket ediyordur. "
        "Ortalamalar sık kesiştiği için yanlış alarm da olabilir."])


def report_messages(results_by_ticker, assets: list[dict], first_run: bool = False,
                    fg_info: dict | None = None) -> list[str]:
    icon = "🆕" if first_run else "📊"
    groups = [assets[i:i + 2] for i in range(0, len(assets), 2)]
    messages = []
    for index, group in enumerate(groups, start=1):
        part = f" ({index}/{len(groups)})" if len(groups) > 1 else ""
        header = f"{icon} KRİPTO TAKİP PANELİ · {datetime.now(IST):%d.%m.%Y}{part}"
        sections = ["\n".join(_score_summary(results_by_ticker[asset["ticker"]], asset))
                    for asset in group]
        if index == len(groups):  # Fear & Greed raporun en sonunda, yalnızca son mesajda
            sections.append(fear_greed_line(fg_info))
        messages.append("\n\n".join([header, *sections]))
    if any(len(message) > MAX_REPORT_CHARS for message in messages):
        raise ValueError("Gösterge raporu Telegram mesaj sınırını aşıyor.")
    return messages


def load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def run(cfg, send, dry_run=False, status_only=False, state_path=STATE_FILE, fetch=get_candles, fetch_fg=None) -> list[str]:
    # Fear & Greed alınamazsa rapor o satır olmadan gider.
    fg_info = None
    if cfg.get("fear_greed", True):
        try:
            fg_info = sentiment.summary((fetch_fg or fetch_fear_greed)())
        except Exception as e:  # noqa: BLE001
            print(f"[fear&greed] atlandı: {e}")
    assets = configured_assets(cfg)
    results_by_ticker = {}
    for asset in assets:
        results = {}
        for tf in TF_UP:
            df = fetch(asset["symbol"], tf, tuple(cfg["exchanges"]), asset["since"])
            if len(df) < MIN_BARS[tf]:
                raise RuntimeError(f"{asset['ticker']} {TF_TR[tf]}: yalnızca {len(df)} mum alındı "
                                   f"(en az {MIN_BARS[tf]} gerekir). Borsa geçmişi kısa olabilir; "
                                   "yanlış rapor üretmemek için durduruldu.")
            results[tf] = analyze(df, tf, cfg, fg_info)
        results_by_ticker[asset["ticker"]] = results

    state = load_state(state_path)
    if status_only:
        msgs = report_messages(results_by_ticker, assets, fg_info=fg_info)
        for m in msgs:
            send(m)
        return msgs

    messages, new_state = [], dict(state)
    new_state.pop("last_news", None)  # eski sürümden kalan haber Telegram durumu
    for old in ("1d", "1w", "1M", "zone_1d", "zone_1w"):  # eski sürümlerin kalıntıları
        new_state.pop(old, None)
    if cfg.get("cross_alerts", True):  # yeni golden/dead cross olursa ayrı bir uyarı (ilk çalışmada yalnızca durum kaydedilir)
        for asset in assets:
            results = results_by_ticker[asset["ticker"]]
            for tf in TF_UP:
                raw, evs = results[tf][1], cross_events(results[tf][1])
                key = _cross_state_key(asset, tf)
                seen = state.get(key)
                if seen is not None:
                    since = pd.Timestamp(seen) if seen else None
                    messages += [cross_message(tf, ts, k, results, asset)
                                 for ts, k in cross_events(raw, since)[-MAX_CROSS_ALERTS:]]
                new_state[key] = evs[-1][0].isoformat() if evs else ""
    today = f"{datetime.now(IST):%Y-%m-%d}"
    first_run = not state.get("last_report")
    if first_run or (cfg.get("daily_report", True) and state.get("last_report") != today):
        messages += report_messages(results_by_ticker, assets, first_run=first_run, fg_info=fg_info)
        new_state["last_report"] = today  # günde en fazla bir rapor
        for asset in assets:
            for tf in TF_UP:
                new_state.pop(f"rating_{asset['ticker']}_{tf}", None)
    for m in messages:
        send(m)
    if not dry_run:  # sadece gönderim başarılıysa buraya gelinir
        state_path.write_text(json.dumps(new_state, indent=2), encoding="utf-8")
    if not messages:
        print("Bugün için yeni rapor yok.")
    return messages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", "--report", dest="status", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--get-chat-id", action="store_true")
    a = ap.parse_args()

    load_env(ROOT / ".env")
    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")

    if a.get_chat_id:
        if not token:
            sys.exit("Önce .env dosyasına TELEGRAM_BOT_TOKEN yaz.")
        chats = get_chats(token)
        if not chats:
            sys.exit("Mesaj bulunamadı. Telegram'da botuna 'merhaba' yaz, sonra bu komutu tekrar çalıştır.")
        for cid, name in chats:
            print(f"Chat ID: {cid}   (ad: {name})")
        return

    if a.dry_run:
        send = lambda m: print("-" * 40 + "\n" + m + "\n")
    else:
        if not token or not chat_id:
            sys.exit(".env içinde TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID tanımlı olmalı (bkz. .env.example).")
        send = lambda m: send_telegram(token, chat_id, m)

    if a.test:
        send("🔔 Test mesajı: Kripto takip rapor botu çalışıyor.")
        print("Test mesajı gönderildi.")
        return
    run(cfg, send, dry_run=a.dry_run, status_only=a.status)


if __name__ == "__main__":
    main()
