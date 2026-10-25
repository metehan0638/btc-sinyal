"""Günlük/haftalık mum kapanışlarını kontrol eder; GİRİŞ/ÇIKIŞ sinyali olduysa Telegram'a mesaj atar.

Kullanım:
  python check_signals.py                # normal kontrol (Görev Zamanlayıcı bunu çalıştırır)
  python check_signals.py --dry-run      # mesajları göndermeden ekrana yazar, durumu kaydetmez
  python check_signals.py --report       # raporu hemen Telegram'a gönderir (--status da çalışır)
  python check_signals.py --news         # haber/hype raporunu hemen gönderir (durumu değiştirmez)
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

from core.backtest import position_series
from core.consensus import build_signals
from core.data import get_candles
from core.indicators import compute_indicators, latest_table
from core.news import build_news_report
from core.notifier import get_chats, load_env, send_telegram
from core.sentiment import align_fear_greed, fetch_fear_greed

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
TF_UP = {"1d": "GÜNLÜK", "1w": "HAFTALIK"}
TF_TR = {"1d": "Günlük", "1w": "Haftalık"}
MIN_BARS = {"1d": 1500, "1w": 250}  # bundan kısa geçmişle hesap güvenilmez (yedek borsaya düşülmüş olabilir)
MAX_ALERTS_PER_TF = 3  # uzun süre çalışmamışsa mesaj yağmurunu önler
DISCLAIMER = "⚠️ Yatırım tavsiyesi değildir."
IST = timezone(timedelta(hours=3))  # Türkiye saati
VOTE_GROUPS = [("AL", "🟢"), ("SAT", "🔴"), ("NÖTR", "⚪"), ("Yetersiz veri", "❔")]


def analyze(df: pd.DataFrame, tf: str, cfg: dict, fg=None):
    aligned = align_fear_greed(fg, df.index, tf) if fg is not None else None
    votes, raw = compute_indicators(df, cfg["timeframes"].get(tf, {}), fg=aligned)
    sig = build_signals(votes, cfg["entry_threshold"])
    pos = pd.Series(position_series(sig["bull"].values, sig["valid"].values,
                                    cfg["entry_threshold"], cfg["exit_threshold"]), index=df.index)
    return sig, raw, pos, votes


def find_events(pos: pd.Series, since: pd.Timestamp) -> list[tuple[pd.Timestamp, int]]:
    """since tarihinden SONRAKİ pozisyon değişimleri: (mum, +1 giriş / -1 çıkış)."""
    ch = pos.diff().fillna(0)
    ch = ch[(ch != 0) & (ch.index > since)]
    return [(ts, int(v)) for ts, v in ch.items()]


def _state_line(tf, results) -> str:
    sig, pos = results[tf][1], results[tf][3]
    last = sig.iloc[-1]
    where = "AL bölgesinde (pozisyonda)" if pos.iloc[-1] == 1 else "beklemede (nakitte)"
    return f"{TF_TR[tf]}: {where} — {int(last.bull)} AL / {int(last.bear)} SAT / {int(last.neutral)} nötr"


def event_message(tf, ts, kind, results, cfg) -> str:
    df, sig, raw = results[tf][:3]
    s, r = sig.loc[ts], raw.loc[ts]
    other = "1w" if tf == "1d" else "1d"
    if kind == 1:
        head = f"🟢 BTC {TF_UP[tf]} — GİRİŞ (AL) sinyali"
        n_ind = results[tf][4].shape[1]
        why = f"{int(s.bull)}/{n_ind} indikatör AL dedi (eşik: {cfg['entry_threshold']})"
        extra = f"\nATR tabanlı stop referansı: {r.close - 3 * r.atr:,.0f} (3×ATR)"
    else:
        head = f"🔴 BTC {TF_UP[tf]} — ÇIKIŞ sinyali"
        why = f"AL onayı {int(s.bull)}'e düştü (çıkış eşiği: ≤{cfg['exit_threshold']})"
        extra = ""
    return (f"{head}\n{ts:%d.%m.%Y} mumu kapandı\nFiyat: {r.close:,.0f} USDT\n{why}{extra}\n\n"
            f"{_state_line(other, results)}\n\n{DISCLAIMER}")


def _indicator_block(tf: str, results, cfg: dict) -> list[str]:
    df, sig, raw, pos, votes = results[tf]
    last = sig.iloc[-1]
    table = latest_table(votes, raw, cfg["timeframes"].get(tf, {}))
    lines = [f"━━ {TF_UP[tf]} ━━",
             f"Son mum: {df.index[-1]:%d.%m.%Y} · kapanış {df['close'].iloc[-1]:,.0f} USDT",
             f"Sonuç: {int(last.bull)} AL · {int(last.bear)} SAT · {int(last.neutral)} NÖTR  ({votes.shape[1]} indikatörden)"]
    if pos.iloc[-1] == 1:
        lines.append(f"Strateji: AL bölgesinde (pozisyonda). Çıkış için AL onayı ≤{cfg['exit_threshold']} olmalı (şu an {int(last.bull)}).")
    else:
        lines.append(f"Strateji: beklemede (nakitte). Giriş için AL onayı ≥{cfg['entry_threshold']} gerekir (şu an {int(last.bull)}).")
    for label, icon in VOTE_GROUPS:
        rows = table[table["Oy"] == label]
        if rows.empty:
            continue
        lines.append(f"\n{icon} {label} ({len(rows)})")
        lines += [f"• {r['İndikatör']}: {r['Değer']}" for _, r in rows.iterrows()]
    return lines


def report_message(results, cfg, first_run: bool = False) -> str:
    head = "✅ Bot kuruldu · BTC raporu" if first_run else "📊 BTC RAPORU"
    parts = [f"{head} — {datetime.now(IST):%d.%m.%Y}", ""]
    for tf in TF_UP:
        parts += _indicator_block(tf, results, cfg) + [""]
    parts.append(f"Kural: giriş AL onayı ≥{cfg['entry_threshold']}, çıkış ≤{cfg['exit_threshold']}. Giriş/çıkış olursa ayrıca uyarı gelir.")
    parts += ["", DISCLAIMER]
    return "\n".join(parts)


def load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _news_message(cfg: dict, hist: dict):
    """Haber/hype raporu. Haber tarafı hata verirse sinyal akışını bozmaz: (None, hist) döner."""
    if not cfg.get("news_report", True):
        return None, hist
    try:
        feeds = (cfg.get("news") or {}).get("feeds")
        return build_news_report(feeds, hist)
    except Exception as e:  # noqa: BLE001
        print(f"[haber] rapor atlandı: {e}")
        return None, hist


def run(cfg, send, dry_run=False, status_only=False, state_path=STATE_FILE, fetch=get_candles, fetch_fg=None) -> list[str]:
    # Fear & Greed alınamazsa burada hata verir: eksik veriyle sahte sinyal üretmektense durur, durum dosyası değişmez.
    if fetch_fg is None and cfg.get("fear_greed", True):
        fetch_fg = fetch_fear_greed
    fg = fetch_fg() if fetch_fg else None
    results = {}
    for tf in TF_UP:
        df = fetch(cfg["symbol"], tf, tuple(cfg["exchanges"]), cfg["since"])
        if len(df) < MIN_BARS[tf]:
            raise RuntimeError(f"{TF_TR[tf]}: yalnızca {len(df)} mum alındı (en az {MIN_BARS[tf]} gerekir). "
                               "Binance'e erişilemiyor, yedek borsanın geçmişi kısa olabilir; sahte sinyal üretmemek için durduruldu.")
        sig, raw, pos, votes = analyze(df, tf, cfg, fg)
        results[tf] = (df, sig, raw, pos, votes)

    if status_only:
        msgs = [report_message(results, cfg)]
        news_msg, _ = _news_message(cfg, load_state(state_path).get("news_hist", {}))
        if news_msg:
            msgs.append(news_msg)
        for m in msgs:
            send(m)
        return msgs

    state, messages, first_run = load_state(state_path), [], False
    new_state = dict(state)
    for tf, (df, sig, raw, pos, _votes) in results.items():
        seen = state.get(tf)
        if seen is None:
            first_run = True
        else:
            for ts, kind in find_events(pos, pd.Timestamp(seen))[-MAX_ALERTS_PER_TF:]:
                messages.append(event_message(tf, ts, kind, results, cfg))
        new_state[tf] = df.index[-1].isoformat()
    today = f"{datetime.now(IST):%Y-%m-%d}"
    if first_run or (cfg.get("daily_report", True) and state.get("last_report") != today):
        messages.append(report_message(results, cfg, first_run=first_run))
        new_state["last_report"] = today  # günde en fazla bir rapor
    if state.get("last_news") != today:
        news_msg, new_state["news_hist"] = _news_message(cfg, state.get("news_hist", {}))
        if news_msg:
            messages.append(news_msg)
            new_state["last_news"] = today  # günde en fazla bir haber raporu

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
    ap.add_argument("--status", "--report", dest="status", action="store_true")
    ap.add_argument("--news", action="store_true")
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
    if a.news:
        msg, _ = _news_message(cfg, load_state(STATE_FILE).get("news_hist", {}))
        if msg:
            send(msg)
        else:
            print("Haber raporu üretilemedi (kaynaklara ulaşılamadı ya da cfg'de news_report: false).")
        return
    run(cfg, send, dry_run=a.dry_run, status_only=a.status)


if __name__ == "__main__":
    main()
