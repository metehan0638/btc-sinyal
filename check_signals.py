"""Kripto varlıklar için Dip Skoru raporunu Telegram'a gönderir.
Dip Skoru, göstergelerin etiketlerinden (Güçlü Dip ... Aşırı Şişmiş) hesaplanan mekanik bir özettir; emir ya da tavsiye
değildir ve gerçek dip veya tepe garantisi vermez.

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
from core.indicators import ICONS, MIN_BARS, build_indicators
from core.scoring import evaluate

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
TF_UP = {"1d": "GÜNLÜK", "1w": "HAFTALIK", "1M": "AYLIK"}
TF_TR = {"1d": "Günlük", "1w": "Haftalık", "1M": "Aylık"}
IST = timezone(timedelta(hours=3))  # Türkiye saati
MAX_REPORT_CHARS = 3800  # Telegram'ın 4096 karakter sınırına güvenli pay bırak
LEGEND = "ℹ️ Dip Skoru: -100 aşırı şişmiş · 0 nötr · +100 güçlü dip (alım fırsatı yönünde)"
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


def analyze(df, tf: str, cfg: dict) -> dict:
    """Son kapanmış mum için göstergeleri, etiketleri ve genel skoru (Dip Skoru) hesaplar."""
    return dict(df=df, **evaluate(df, build_indicators(cfg.get("indicators"))))


def score_bar(score: float, width: int = BAR_WIDTH) -> str:
    """-100..+100 aralığında skoru gösteren metin çubuğu: ● skoru, │ sıfır noktasını gösterir."""
    cells = ["░"] * width
    mid = width // 2
    cells[mid] = "│"
    pos = round((max(-100.0, min(100.0, score)) + 100) / 200 * (width - 1))
    cells[pos] = "●"
    return f"-100 {''.join(cells)} +100"


def _score_lines(result: dict) -> list[str]:
    score = result["score"]
    if pd.isna(score):
        return ["🎯 Dip Skoru: ❔ Yetersiz veri"]
    value = int(round(score))
    shown = f"{value:+d}" if value else "0"
    partial = f" ({result['active']}/{result['total']} gösterge)" if result["active"] != result["total"] else ""
    return [f"🎯 Dip Skoru: {shown} · {ICONS[result['label']]} {result['label']}{partial}", score_bar(score)]


def _score_summary(results, asset: dict) -> list[str]:
    quote = asset["symbol"].split("/")[-1]
    daily = results["1d"]
    lines = [f"🪙 {asset['ticker']} — {asset['name']}",
             f"💰 Günlük kapanış ({daily['date']:%d.%m.%Y}): {daily['close']:,.8g} {quote}"]
    for tf, timeframe in TF_TR.items():
        lines += ["", f"📅 {timeframe.upper()}", *_score_lines(results[tf])]
    return lines


def fear_greed_line(fg_info: dict | None) -> str:
    if not fg_info:
        return "😶 Fear & Greed: — Veri yok"
    hist = " · ".join(f"{n} {fg_info[k]:.0f}" for n, k in (("dün", "yesterday"), ("7g", "week"), ("30g", "month"))
                      if fg_info.get(k) is not None)
    emoji = sentiment.EMOJI.get(fg_info["label"], "")
    return f"{emoji} Fear & Greed: {fg_info['value']:.0f} — {fg_info['label']}" + (f" ({hist})" if hist else "")


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
            sections += [LEGEND, fear_greed_line(fg_info)]
        messages.append("\n\n".join([header, *sections]))
    if any(len(message) > MAX_REPORT_CHARS for message in messages):
        raise ValueError("Skor raporu Telegram mesaj sınırını aşıyor.")
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
            results[tf] = analyze(df, tf, cfg)
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
    for key in [k for k in new_state if k.startswith(("cross_", "rating_"))]:  # kaldırılan cross uyarısı / eski dereceler
        new_state.pop(key)
    today = f"{datetime.now(IST):%Y-%m-%d}"
    first_run = not state.get("last_report")
    if first_run or (cfg.get("daily_report", True) and state.get("last_report") != today):
        messages += report_messages(results_by_ticker, assets, first_run=first_run, fg_info=fg_info)
        new_state["last_report"] = today  # günde en fazla bir rapor
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
