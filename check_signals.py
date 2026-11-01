"""BTC için gösterge derecelendirme raporunu (güçlü al ... güçlü sat) ve golden/dead cross uyarılarını Telegram'a gönderir.
Dereceler göstergelerin değerlerine bakan mekanik etiketlerdir; emir ya da tavsiye değildir.

Kullanım:
  python check_signals.py                # günlük rapor (Görev Zamanlayıcı / GitHub Actions bunu çalıştırır)
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

from core import sentiment
from core.data import get_candles
from core.news import build_news_report
from core.notifier import get_chats, load_env, send_telegram
from core.sentiment import fetch_fear_greed
from core.zones import (LEVEL_TITLE, LEVELS, MIN_BARS, RATING_TEXT, compute_metrics, cross_events, latest_rows,
                        summarize, trend_info)

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
TF_UP = {"1d": "GÜNLÜK", "1w": "HAFTALIK"}
TF_TR = {"1d": "Günlük", "1w": "Haftalık"}
DISCLAIMER = "⚠️ Yatırım tavsiyesi değildir."
HOW_TO_READ = ("Nasıl okunur: her gösterge değerine göre 5 dereceden birine girer (güçlü al · al · nötr · sat · güçlü sat); "
               "AL tarafı fiyatın ucuz/dip tarafında, SAT tarafı şişmiş tarafında olduğunu gösterir. "
               "Bunlar mekanik derecelerdir, emir değildir; aşırı satım düşüş trendinde uzun süre sürebilir.")
IST = timezone(timedelta(hours=3))  # Türkiye saati
MAX_CROSS_ALERTS = 2  # uzun süre çalışmamışsa en fazla bu kadar geçmiş kesişim uyarısı gönderilir
DEFAULT_THRESHOLDS = (0.3, 1.0)


def _thresholds(cfg: dict):
    return tuple(cfg.get("rating_thresholds") or DEFAULT_THRESHOLDS)


def analyze(df, tf: str, cfg: dict, fg_info: dict | None = None):
    """Döndürür: (mumlar, ham değerler, gösterge satırları, özet, trend). Fear & Greed yalnızca günlükte hesaba katılır."""
    params = cfg["timeframes"].get(tf, {})
    raw = compute_metrics(df, params)
    rows = latest_rows(raw, params, tf, fg_info if tf == "1d" else None)
    return df, raw, rows, summarize(rows, _thresholds(cfg)), trend_info(raw, params, tf)


def _score_line(summ: dict) -> str:
    c = summ["counts"]
    return (f"ortalama puan {summ['mean']:+.2f} ({summ['n']} gösterge: {c[2]} güçlü al · {c[1]} al · {c[0]} nötr · "
            f"{c[-1]} sat · {c[-2]} güçlü sat)")


def _indicator_block(tf: str, results, prev_rating: str | None) -> list[str]:
    df, raw, rows, summ, trend = results[tf]
    icon, label = RATING_TEXT[summ["key"]]
    lines = [f"━━ {TF_UP[tf]} ━━",
             f"Son mum: {df.index[-1]:%d.%m.%Y} · kapanış {df['close'].iloc[-1]:,.0f} USDT",
             f"Genel: {icon} {label}" + (f" — {_score_line(summ)}" if summ["n"] else "")]
    if prev_rating in RATING_TEXT and prev_rating != summ["key"]:
        p_icon, p_label = RATING_TEXT[prev_rating]
        lines.append(f"Önceki rapor: {p_icon} {p_label} → değişti")
    for grade in (2, 1, 0, -1, -2):
        group = [r for r in rows if r["grade"] == grade]
        if group:
            lines.append(f"\n{LEVELS[grade][0]} {LEVEL_TITLE[grade]} ({len(group)})")
            lines += [f"• {r['name']}: {r['text']}" + (f" — {r['note']}" if r["note"] else "") for r in group]
    missing = [r for r in rows if r["grade"] != r["grade"]]  # NaN
    if missing:
        lines.append(f"\n❔ Yetersiz veri ({len(missing)})")
        lines += [f"• {r['name']}" for r in missing]
    return lines


def cross_message(tf: str, ts, kind: int, results) -> str:
    df, raw, rows, summ, trend = results[tf]
    icon, label = RATING_TEXT[summ["key"]]
    fast, slow = trend["fast"], trend["slow"]
    golden = kind == 1
    close = raw["close"].loc[ts]
    return "\n".join([
        f"{'🟢' if golden else '🔴'} BTC {TF_UP[tf]} — {'GOLDEN CROSS (olumlu sinyal)' if golden else 'DEAD CROSS (olumsuz sinyal)'}",
        f"{ts:%d.%m.%Y} mumu kapandı: hızlı ortalama (SMA{fast}), yavaş ortalamayı (SMA{slow}) {'yukarı' if golden else 'aşağı'} kesti. "
        f"Kapanış {close:,.0f} USDT.",
        f"Şu anki genel derece: {icon} {label}" + (f" — {_score_line(summ)}" if summ["n"] else ""),
        "Not: Kesişim gecikmeli bir onay sinyalidir; fiyat çoğu zaman haftalardır o yönde hareket ediyordur. "
        "Bitcoin'de ortalamalar sık kesiştiği için yanlış alarm da olabilir.",
        "", DISCLAIMER])


def report_message(results, prev_ratings: dict | None = None, first_run: bool = False) -> str:
    head = "🆕 Bot yeni düzene geçti · BTC gösterge dereceleri" if first_run else "📊 BTC GÖSTERGE DERECELERİ"
    parts = [f"{head} — {datetime.now(IST):%d.%m.%Y}", ""]
    for tf in TF_UP:
        parts += _indicator_block(tf, results, (prev_ratings or {}).get(tf)) + [""]
    parts += [HOW_TO_READ, "", DISCLAIMER]
    return "\n".join(parts)


def load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _news_message(cfg: dict, hist: dict):
    """Haber/hype raporu. Haber tarafı hata verirse ana raporu bozmaz: (None, hist) döner."""
    if not cfg.get("news_report", True):
        return None, hist
    try:
        feeds = (cfg.get("news") or {}).get("feeds")
        return build_news_report(feeds, hist)
    except Exception as e:  # noqa: BLE001
        print(f"[haber] rapor atlandı: {e}")
        return None, hist


def run(cfg, send, dry_run=False, status_only=False, state_path=STATE_FILE, fetch=get_candles, fetch_fg=None) -> list[str]:
    # Fear & Greed alınamazsa rapor o satır olmadan gider.
    fg_info = None
    if cfg.get("fear_greed", True):
        try:
            fg_info = sentiment.summary((fetch_fg or fetch_fear_greed)())
        except Exception as e:  # noqa: BLE001
            print(f"[fear&greed] atlandı: {e}")
    results = {}
    for tf in TF_UP:
        df = fetch(cfg["symbol"], tf, tuple(cfg["exchanges"]), cfg["since"])
        if len(df) < MIN_BARS[tf]:
            raise RuntimeError(f"{TF_TR[tf]}: yalnızca {len(df)} mum alındı (en az {MIN_BARS[tf]} gerekir). "
                               "Binance'e erişilemiyor, yedek borsanın geçmişi kısa olabilir; yanlış rapor üretmemek için durduruldu.")
        results[tf] = analyze(df, tf, cfg, fg_info)

    state = load_state(state_path)
    prev_ratings = {tf: state.get(f"rating_{tf}") for tf in TF_UP}

    if status_only:
        msgs = [report_message(results, prev_ratings)]
        news_msg, _ = _news_message(cfg, state.get("news_hist", {}))
        if news_msg:
            msgs.append(news_msg)
        for m in msgs:
            send(m)
        return msgs

    messages, new_state = [], dict(state)
    for old in ("1d", "1w", "zone_1d", "zone_1w"):  # eski sürümlerin kalıntıları
        new_state.pop(old, None)
    if cfg.get("cross_alerts", True):  # yeni golden/dead cross olursa ayrı bir uyarı (ilk çalışmada yalnızca durum kaydedilir)
        for tf in TF_UP:
            raw, evs = results[tf][1], cross_events(results[tf][1])
            seen = state.get(f"cross_{tf}")
            if seen is not None:
                since = pd.Timestamp(seen) if seen else None
                messages += [cross_message(tf, ts, k, results) for ts, k in cross_events(raw, since)[-MAX_CROSS_ALERTS:]]
            new_state[f"cross_{tf}"] = evs[-1][0].isoformat() if evs else ""
    today = f"{datetime.now(IST):%Y-%m-%d}"
    first_run = any(r is None for r in prev_ratings.values())
    if first_run or (cfg.get("daily_report", True) and state.get("last_report") != today):
        messages.append(report_message(results, prev_ratings, first_run=first_run))
        new_state["last_report"] = today  # günde en fazla bir rapor
        for tf in TF_UP:
            new_state[f"rating_{tf}"] = results[tf][3]["key"]
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
        print("Bugün için yeni rapor yok.")
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
        send("🔔 Test mesajı: BTC rapor botu çalışıyor.")
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
