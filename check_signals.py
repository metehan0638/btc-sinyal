"""Günlük/haftalık mum kapanışlarını kontrol eder; GİRİŞ/ÇIKIŞ sinyali olduysa Telegram'a mesaj atar.

Kullanım:
  python check_signals.py                # normal kontrol (Görev Zamanlayıcı bunu çalıştırır)
  python check_signals.py --dry-run      # mesajları göndermeden ekrana yazar, durumu kaydetmez
  python check_signals.py --status       # mevcut durumu Telegram'a gönderir
  python check_signals.py --test         # test mesajı gönderir
  python check_signals.py --get-chat-id  # bota mesaj attıktan sonra chat ID'ni bulur
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import yaml

from core.backtest import position_series
from core.consensus import build_signals
from core.data import get_candles
from core.indicators import compute_indicators
from core.notifier import get_chats, load_env, send_telegram

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
TF_UP = {"1d": "GÜNLÜK", "1w": "HAFTALIK"}
TF_TR = {"1d": "Günlük", "1w": "Haftalık"}
MIN_BARS = {"1d": 1500, "1w": 250}  # bundan kısa geçmişle hesap güvenilmez (yedek borsaya düşülmüş olabilir)
MAX_ALERTS_PER_TF = 3  # uzun süre çalışmamışsa mesaj yağmurunu önler
DISCLAIMER = "⚠️ Yatırım tavsiyesi değildir."


def analyze(df: pd.DataFrame, tf: str, cfg: dict):
    votes, raw = compute_indicators(df, cfg["timeframes"].get(tf, {}))
    sig = build_signals(votes, cfg["entry_threshold"])
    pos = pd.Series(position_series(sig["bull"].values, sig["valid"].values,
                                    cfg["entry_threshold"], cfg["exit_threshold"]), index=df.index)
    return sig, raw, pos


def find_events(pos: pd.Series, since: pd.Timestamp) -> list[tuple[pd.Timestamp, int]]:
    """since tarihinden SONRAKİ pozisyon değişimleri: (mum, +1 giriş / -1 çıkış)."""
    ch = pos.diff().fillna(0)
    ch = ch[(ch != 0) & (ch.index > since)]
    return [(ts, int(v)) for ts, v in ch.items()]


def _state_line(tf, results) -> str:
    _, sig, _, pos = results[tf]
    last = sig.iloc[-1]
    where = "AL bölgesinde (pozisyonda)" if pos.iloc[-1] == 1 else "beklemede (nakitte)"
    return f"{TF_TR[tf]}: {where} — {int(last.bull)} AL / {int(last.bear)} SAT / {int(last.neutral)} nötr"


def event_message(tf, ts, kind, results, cfg) -> str:
    df, sig, raw, _ = results[tf]
    s, r = sig.loc[ts], raw.loc[ts]
    other = "1w" if tf == "1d" else "1d"
    if kind == 1:
        head = f"🟢 BTC {TF_UP[tf]} — GİRİŞ (AL) sinyali"
        why = f"{int(s.bull)}/10 indikatör AL dedi (eşik: {cfg['entry_threshold']})"
        extra = f"\nATR tabanlı stop referansı: {r.close - 3 * r.atr:,.0f} (3×ATR)"
    else:
        head = f"🔴 BTC {TF_UP[tf]} — ÇIKIŞ sinyali"
        why = f"AL onayı {int(s.bull)}'e düştü (çıkış eşiği: ≤{cfg['exit_threshold']})"
        extra = ""
    return (f"{head}\n{ts:%d.%m.%Y} mumu kapandı\nFiyat: {r.close:,.0f} USDT\n{why}{extra}\n\n"
            f"{_state_line(other, results)}\n\n{DISCLAIMER}")


def status_message(results, first_run: bool) -> str:
    title = "✅ Bot kuruldu, mevcut durum:" if first_run else "📊 BTC güncel durum:"
    lines = [title, ""]
    for tf in TF_UP:
        df = results[tf][0]
        lines.append(f"{_state_line(tf, results)}  (son mum: {df.index[-1]:%d.%m.%Y}, kapanış {df['close'].iloc[-1]:,.0f})")
    lines += ["", "Bundan sonra yalnızca GİRİŞ/ÇIKIŞ sinyali oluştuğunda mesaj alacaksın.", "", DISCLAIMER]
    return "\n".join(lines)


def load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def run(cfg, send, dry_run=False, status_only=False, state_path=STATE_FILE, fetch=get_candles) -> list[str]:
    results = {}
    for tf in TF_UP:
        df = fetch(cfg["symbol"], tf, tuple(cfg["exchanges"]), cfg["since"])
        if len(df) < MIN_BARS[tf]:
            raise RuntimeError(f"{TF_TR[tf]}: yalnızca {len(df)} mum alındı (en az {MIN_BARS[tf]} gerekir). "
                               "Binance'e erişilemiyor, yedek borsanın geçmişi kısa olabilir; sahte sinyal üretmemek için durduruldu.")
        sig, raw, pos = analyze(df, tf, cfg)
        results[tf] = (df, sig, raw, pos)

    if status_only:
        msg = status_message(results, first_run=False)
        send(msg)
        return [msg]

    state, new_state, messages, first_run = load_state(state_path), {}, [], False
    for tf, (df, sig, raw, pos) in results.items():
        seen = state.get(tf)
        if seen is None:
            first_run = True
        else:
            for ts, kind in find_events(pos, pd.Timestamp(seen))[-MAX_ALERTS_PER_TF:]:
                messages.append(event_message(tf, ts, kind, results, cfg))
        new_state[tf] = df.index[-1].isoformat()
    if first_run:
        messages.append(status_message(results, first_run=True))

    for m in messages:
        send(m)
    if not dry_run:  # sadece gönderim başarılıysa buraya gelinir
        state_path.write_text(json.dumps(new_state, indent=2), encoding="utf-8")
    if not messages:
        print("Yeni sinyal yok.")
    return messages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
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
        send("🔔 Test mesajı: BTC sinyal botu çalışıyor.")
        print("Test mesajı gönderildi.")
        return
    run(cfg, send, dry_run=a.dry_run, status_only=a.status)


if __name__ == "__main__":
    main()
