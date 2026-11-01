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
                        trend_info)

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
TF_UP = {"1d": "GÜNLÜK", "1w": "HAFTALIK", "1M": "AYLIK"}
TF_TR = {"1d": "Günlük", "1w": "Haftalık", "1M": "Aylık"}
DISCLAIMER = "⚠️ Yatırım tavsiyesi değildir."
IST = timezone(timedelta(hours=3))  # Türkiye saati
MAX_CROSS_ALERTS = 2  # uzun süre çalışmamışsa en fazla bu kadar geçmiş kesişim uyarısı gönderilir
MAX_REPORT_CHARS = 3800  # Telegram'ın 4096 karakter sınırına güvenli pay bırak
REPORT_INDICATORS = (
    ("rsi", "RSI"),
    ("stochrsi", "Stoch RSI"),
    ("bollinger", "Bollinger"),
    ("mfi", "MFI"),
    ("ma_ratio", "Fiyat/SMA"),
    ("drawdown", "ATH düşüşü"),
    ("ichimoku", "Ichimoku"),
    ("fear_greed", "Fear & Greed"),
    ("cross", "SMA kesişimi"),
)


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


def _compact_indicator_table(results, asset: dict) -> list[str]:
    rows_by_tf = {tf: {row["key"]: row for row in results[tf][2]} for tf in TF_UP}
    lines = [f"🪙 {asset['ticker']}", "Gösterge | Günlük | Haftalık | Aylık"]
    for key, label in REPORT_INDICATORS:
        signals = []
        for tf in TF_UP:
            row = rows_by_tf[tf].get(key)
            grade = row.get("grade") if row else float("nan")
            signals.append("—" if pd.isna(grade) else LEVELS[int(grade)][0])
        lines.append(f"{label} | " + " | ".join(signals))
    return lines


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
        "Ortalamalar sık kesiştiği için yanlış alarm da olabilir.",
        "", DISCLAIMER])


def report_messages(results_by_ticker, assets: list[dict], first_run: bool = False) -> list[str]:
    icon = "🆕" if first_run else "📊"
    header = [f"{icon} KRİPTO TAKİP PANELİ · {datetime.now(IST):%d.%m.%Y}",
              "🟢🟢 Güçlü al · 🟢 Al · ⚪ Nötr · 🔴 Sat · 🔴🔴 Güçlü sat · — Veri yok"]
    footer = DISCLAIMER
    messages, current_sections = [], []

    def render(sections):
        return "\n\n".join(["\n".join(header), *("\n".join(section) for section in sections), footer])

    for asset in assets:
        section = _compact_indicator_table(results_by_ticker[asset["ticker"]], asset)
        candidate = render([*current_sections, section])
        if len(candidate) > MAX_REPORT_CHARS and current_sections:
            messages.append(render(current_sections))
            current_sections = [section]
        else:
            current_sections.append(section)

    if current_sections:
        messages.append(render(current_sections))
    if any(len(message) > MAX_REPORT_CHARS for message in messages):
        raise ValueError("Bir coin için gösterge tablosu Telegram mesaj sınırını aşıyor.")
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
        msgs = report_messages(results_by_ticker, assets)
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
        messages += report_messages(results_by_ticker, assets, first_run=first_run)
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
