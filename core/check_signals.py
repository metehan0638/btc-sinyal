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
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

from core import chart_analysis, mvrv, sentiment
from core.data import TF_MS, get_candles
from core.notifier import get_chats, load_env, send_telegram
from core.external import memoize
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


def analyze(df, tf: str, cfg: dict, sources: dict | None = None) -> dict:
    """Son kapanmış mum için göstergeleri, etiketleri ve genel skoru (Dip Skoru) hesaplar.
    `sources`: dış veri kaynakları ({"fear_greed": fonksiyon, "mvrv": fonksiyon}); verilmezse gerçek API'ler kullanılır."""
    return dict(df=df, **evaluate(df, build_indicators(cfg.get("indicators"), sources), tf))


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


def chart_comment_settings(cfg: dict) -> dict | None:
    """config.yaml `chart_comment` bölümünü doğrular. Kapalıysa (veya yoksa) None döner."""
    section = cfg.get("chart_comment") or {}
    if not section.get("enabled", False):
        return None
    unknown = set(section) - {"enabled", "timeframes", "candles", "params", "timeframe_params"}
    if unknown:
        raise ValueError(f"chart_comment içinde bilinmeyen ayar: {', '.join(sorted(unknown))}")
    timeframes = list(dict.fromkeys(section.get("timeframes") or ["4h", "1d"]))
    for tf in timeframes:
        if tf not in chart_analysis.TIMEFRAME_LABELS or (tf not in TF_MS and tf != "1M"):
            raise ValueError(f"chart_comment.timeframes içinde desteklenmeyen periyot: {tf}")
    params, per_tf = dict(section.get("params") or {}), {k: dict(v or {}) for k, v in (section.get("timeframe_params") or {}).items()}
    for override in (params, *per_tf.values()):
        chart_analysis.merge_params(override)  # bilinmeyen eşik anahtarı burada yakalanır
    return dict(timeframes=timeframes, candles={k: int(v) for k, v in (section.get("candles") or {}).items()}, params=params,
                timeframe_params=per_tf)


DEFAULT_COMMENT_CANDLES = 600  # ek periyot için varsayılan mum sayısı (EMA200 + formasyon penceresi için fazlasıyla yeter)


def build_chart_comments(cfg: dict, assets: list[dict], results_by_ticker: dict, fetch, now: datetime | None = None) -> dict:
    """Her varlık için "Grafik Yorumu" bölümünü üretir: {ticker: [tam metin, kısa metin]}. Kapalıysa {}.
    Asla istisna fırlatmaz: bir zaman dilimi (veri/hesaplama) hatası yalnızca o paragrafı "üretilemedi" yapar, hepsi başarısızsa bölüm
    "Grafik yorumu şu an üretilemedi." olur; rapor normal gider. Hata terminale/log'a yazılır."""
    try:
        settings = chart_comment_settings(cfg)
    except Exception as e:  # noqa: BLE001  yapılandırma hatası raporu bozmasın; logda görünür
        print(f"[grafik yorumu] ayar hatası, bölüm atlandı: {e}")
        return {}
    if not settings:
        return {}
    now = now or datetime.now(timezone.utc)
    comments = {}
    for asset in assets:
        analyses = []
        for tf in settings["timeframes"]:
            try:
                existing = results_by_ticker.get(asset["ticker"], {}).get(tf)
                if existing is not None:  # rapor için zaten çekilen geçmiş (1d/1w/1M): yeniden çekilmez
                    df = existing["df"]
                else:
                    candles = settings["candles"].get(tf, DEFAULT_COMMENT_CANDLES)
                    since = (now - timedelta(milliseconds=TF_MS[tf] * candles)).strftime("%Y-%m-%d")
                    df = fetch(asset["symbol"], tf, tuple(cfg["exchanges"]), since)
                analyses.append(chart_analysis.analyze(df, tf, {**settings["params"], **settings["timeframe_params"].get(tf, {})}))
            except Exception as e:  # noqa: BLE001
                print(f"[grafik yorumu] {asset['ticker']} {tf} atlandı: {type(e).__name__}: {e}")
                analyses.append(dict(timeframe=tf, status="invalid", reason="veri alınamadı"))
        for item in analyses:
            if item.get("status") != "ok":
                print(f"[grafik yorumu] {asset['ticker']} {item['timeframe']}: {item.get('reason')}")
        strong = {**chart_analysis.DEFAULT_PARAMS, **settings["params"]}["sr_strong_score"]
        comments[asset["ticker"]] = [chart_analysis.build_report(analyses, asset["name"], strong_score=strong),
                                     chart_analysis.build_report(analyses, asset["name"], compact=True, strong_score=strong)]
    return comments


def report_messages(results_by_ticker, assets: list[dict], first_run: bool = False,
                    fg_info: dict | None = None, comments: dict | None = None) -> list[str]:
    icon = "🆕" if first_run else "📊"
    groups = [assets[i:i + 2] for i in range(0, len(assets), 2)]

    def compose(level: int) -> list[str]:
        """level 0: tam Grafik Yorumu, 1: kısa yorum, 2: yorumsuz (mesaj sınırına sığmazsa sırayla denenir)."""
        messages = []
        for index, group in enumerate(groups, start=1):
            part = f" ({index}/{len(groups)})" if len(groups) > 1 else ""
            header = f"{icon} KRİPTO TAKİP PANELİ · {datetime.now(IST):%d.%m.%Y}{part}"
            sections = []
            for asset in group:
                section = "\n".join(_score_summary(results_by_ticker[asset["ticker"]], asset))
                variants = (comments or {}).get(asset["ticker"])
                variants = [variants] if isinstance(variants, str) else list(variants or [])
                if variants and level < 2:  # Grafik Yorumu, Dip Skoru çubuklarının hemen altında
                    section += "\n\n" + variants[min(level, len(variants) - 1)]
                sections.append(section)
            if index == len(groups):  # Fear & Greed raporun en sonunda, yalnızca son mesajda
                sections += [LEGEND, fear_greed_line(fg_info)]
            messages.append("\n\n".join([header, *sections]))
        return messages

    for level in range(3):
        messages = compose(level)
        if all(len(message) <= MAX_REPORT_CHARS for message in messages):
            return messages
        if level < 2 and comments:
            print("[grafik yorumu] mesaj çok uzun; " + ("kısa sürüm deneniyor." if level == 0 else "yorum atlandı."))
    raise ValueError("Skor raporu Telegram mesaj sınırını aşıyor.")


_CONFLICT_MARKER = re.compile(r"^(<<<<<<<|=======|>>>>>>>)")


def load_state(path: Path) -> dict:
    """Durum dosyasını okur. Dosyada Git birleştirme çakışması işaretleri varsa (<<<<<<<, =======, >>>>>>>) işaret satırlarını silip yeniden
    dener (aynı anahtar iki kez geçerse sonuncusu geçerli olur). Kurtarılamazsa bozuk dosyayı .bozuk olarak yedekler ve boş durumla devam eder:
    bozuk bir state.json günlük raporu kalıcı olarak durdurmamalı."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    for candidate, note in ((text, None), ("\n".join(l for l in text.splitlines() if not _CONFLICT_MARKER.match(l)),
                                          "çakışma işaretleri temizlendi")):
        try:
            state = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(state, dict):
            if note:
                print(f"[durum] {path.name}: {note}.")
            return state
    backup = path.with_name(path.name + ".bozuk")
    backup.write_text(text, encoding="utf-8")
    print(f"[durum] {path.name} okunamadı (bozuk JSON); {backup.name} olarak yedeklendi, boş durumla devam ediliyor.")
    return {}


def run(cfg, send, dry_run=False, status_only=False, state_path=STATE_FILE, fetch=get_candles, fetch_fg=None,
        fetch_mvrv=None) -> list[str]:
    # Dış veriler süreç başına bir kez alınır; Fear & Greed hem gösterge hem rapor sonu bilgi satırı için kullanılır.
    # Kaynak alınamazsa gösterge o çalışmada hesaba girmez (nötr sayılmaz), rapor yine gider.
    sources = {"fear_greed": memoize(fetch_fg or fetch_fear_greed), "mvrv": memoize(fetch_mvrv or mvrv.fetch_mvrv_data)}
    fg_info = None
    if cfg.get("fear_greed_info", True):
        try:
            fg_info = sentiment.summary(sources["fear_greed"]())
        except Exception as e:  # noqa: BLE001
            print(f"[fear&greed] atlandı: {e}")
    assets = configured_assets(cfg)
    if "mvrv" in (cfg.get("indicators") or {}) and any(asset["ticker"] != "BTC" for asset in assets):
        raise ValueError("MVRV Z-Score yalnızca Bitcoin için hesaplanır; config.yaml'daki varlıklar arasında BTC dışında coin var.")
    results_by_ticker = {}
    for asset in assets:
        results = {}
        for tf in TF_UP:
            df = fetch(asset["symbol"], tf, tuple(cfg["exchanges"]), asset["since"])
            if len(df) < MIN_BARS[tf]:
                raise RuntimeError(f"{asset['ticker']} {TF_TR[tf]}: yalnızca {len(df)} mum alındı "
                                   f"(en az {MIN_BARS[tf]} gerekir). Borsa geçmişi kısa olabilir; "
                                   "yanlış rapor üretmemek için durduruldu.")
            results[tf] = analyze(df, tf, cfg, sources)
        results_by_ticker[asset["ticker"]] = results

    state = load_state(state_path)
    if status_only:
        msgs = report_messages(results_by_ticker, assets, fg_info=fg_info,
                               comments=build_chart_comments(cfg, assets, results_by_ticker, fetch))
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
        messages += report_messages(results_by_ticker, assets, first_run=first_run, fg_info=fg_info,
                                    comments=build_chart_comments(cfg, assets, results_by_ticker, fetch))
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
