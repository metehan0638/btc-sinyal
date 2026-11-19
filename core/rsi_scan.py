"""Haftalık RSI alarmı: 50 coini haftalık RSI(14) < 28 için tarar ve Telegram'a bildirir.

Bu özellik Bitcoin'e özel Dip Skoru sisteminden BAĞIMSIZDIR: kendi coin listesini (config.yaml `rsi_scan.coins`) ve kendi durum dosyasını
(rsi_scan_state.json) kullanır. Karar kuralı tek ve değişmezdir: kapanmış haftalık mumlardan Wilder RSI(14) hesaplanır ve yalnızca
`RSI < 28` (kesinlikle küçük) olan coinler bildirilir. RSI değeri 28 veya üzerindeki hiçbir coin mesajda, tabloda ya da alarm listesinde yer almaz.

Veri güvenilirliği: RSI başlangıç tohumundan etkilenir (Wilder yumuşatma). Ölçüm: 100 haftalık geçmişle son mumun RSI'ı tam geçmişe göre
ortalama 0,02 (en kötü %99'da 0,12) puan sapar; 52 haftada eşik civarında %95'te 1,7 puan sapar. Bu yüzden en az 100 kapanmış haftalık mum şarttır
(daha azı "yetersiz veri" sayılır) ve 200 hafta çekilir.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from core.data import get_candles
from core.indicators import wilder_rsi
from core.notifier import load_env, send_telegram

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "rsi_scan_state.json"
LOCK_FILE = ROOT / "rsi_scan.lock"
IST = timezone(timedelta(hours=3))   # Türkiye saati (sabit UTC+3, yaz saati uygulaması yok)

THRESHOLD = 28.0         # DEĞİŞMEZ kural: yalnızca RSI < 28
TIMEFRAME = "1w"
MAX_ATTEMPTS_PER_DAY = 3     # otomatik taramada Telegram/veri hatasında günlük en çok deneme
RETRY_AFTER_MINUTES = 15     # başarısız otomatik taramadan sonra yeniden deneme aralığı
LOCK_STALE_SECONDS = 1800    # bundan eski kilit dosyası çökmüş sayılır

# Binance'te uzun süredir USDT paritesi olan, piyasa değeri yüksek 50 coin (stabil koinler hariç). Bu liste bir varsayımdır:
# kendi listeni config.yaml `rsi_scan.coins` altına yaz. Pariteye ulaşılamayan coin atlanır ve "veri alınamayan" olarak sayılır.
DEFAULT_COINS = [
    "BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "DOGE", "ADA", "LINK", "AVAX",
    "XLM", "BCH", "LTC", "SUI", "HBAR", "DOT", "TON", "SHIB", "UNI", "AAVE",
    "NEAR", "ETC", "APT", "ICP", "ARB", "OP", "FIL", "ATOM", "ALGO", "VET",
    "INJ", "POL", "RENDER", "SEI", "FET", "STX", "PEPE", "CRV", "GRT", "IMX",
    "SAND", "MANA", "THETA", "EGLD", "FLOW", "XTZ", "RUNE", "LDO", "GALA", "CHZ",
]
DEFAULTS = dict(enabled=True, time="09:00", period=14, min_weeks=100, fetch_weeks=200, retries=1, retry_delay=3.0,
                request_pause=0.25, coins=DEFAULT_COINS)


class ScanError(RuntimeError):
    """Tarama tamamlanamadı."""


class ScanBusy(ScanError):
    """Başka bir tarama çalışıyor."""


class DataError(ValueError):
    """Bir coinin verisi eksik/hatalı (yeniden denenmez; coin taramadan çıkarılır)."""


# ---------------------------------------------------------------- ayarlar

def to_symbol(item) -> str:
    """'btc' -> 'BTC/USDT'; 'ETH/USDT' aynen kalır."""
    text = str(item).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,15}(/[A-Z0-9]{2,10})?", text):
        raise ValueError(f"Geçersiz coin adı: {item!r}")
    return text if "/" in text else f"{text}/USDT"


def parse_time(text: str) -> dtime:
    if not isinstance(text, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text.strip()):
        raise ValueError(f"Saat 'SS:DD' biçiminde olmalı (örn. 09:00), verilen: {text!r}")
    hour, minute = text.strip().split(":")
    return dtime(int(hour), int(minute))


def scan_settings(cfg: dict | None = None) -> dict:
    """config.yaml'daki `rsi_scan` bölümünü varsayılanlarla birleştirir ve doğrular."""
    section = dict((cfg or {}).get("rsi_scan") or {})
    unknown = set(section) - set(DEFAULTS) - {"exchanges"}
    if unknown:
        raise ValueError(f"rsi_scan içinde bilinmeyen ayar: {', '.join(sorted(unknown))}")
    s = {**DEFAULTS, **section}
    s["period"], s["min_weeks"], s["fetch_weeks"], s["retries"] = (int(s[k]) for k in ("period", "min_weeks", "fetch_weeks", "retries"))
    if s["period"] < 2:
        raise ValueError("rsi_scan.period en az 2 olmalı.")
    if s["min_weeks"] <= s["period"]:
        raise ValueError("rsi_scan.min_weeks, RSI periyodundan büyük olmalı.")
    if s["fetch_weeks"] < s["min_weeks"]:
        raise ValueError("rsi_scan.fetch_weeks, min_weeks'ten küçük olamaz.")
    parse_time(s["time"])
    symbols = list(dict.fromkeys(to_symbol(c) for c in (s["coins"] or [])))
    if not symbols:
        raise ValueError("rsi_scan.coins boş olamaz.")
    s["symbols"] = symbols
    s["exchanges"] = list(section.get("exchanges") or (cfg or {}).get("exchanges") or ["binance", "kraken", "bitstamp"])
    s["enabled"] = bool(s["enabled"])
    return s


# ---------------------------------------------------------------- tarama

def closed_weekly(df: pd.DataFrame | None, now: datetime, min_weeks: int) -> pd.DataFrame:
    """Haftalık mumları temizler: sıralar, tekrarları atar, KAPANMAMIŞ mumu (açılış + 7 gün > şimdi) çıkarır, bozuk fiyatlı satırları atar ve
    SON kesintisiz (ardışık 7 günlük) parçayı döndürür. Parça `min_weeks`'ten kısaysa DataError."""
    if df is None or len(df) == 0 or "close" not in df:
        raise DataError("haftalık veri yok")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[df.index + pd.Timedelta(days=7) <= pd.Timestamp(now)]
    close = pd.to_numeric(df["close"], errors="coerce")
    df = df[np.isfinite(close) & (close > 0)].assign(close=close)
    if len(df) == 0:
        raise DataError("geçerli kapanmış haftalık mum yok")
    broken = (df.index.to_series().diff() != pd.Timedelta(days=7)).to_numpy()
    start = int(np.flatnonzero(broken)[-1])  # ilk satır her zaman "kopuk" sayılır; son kopukluk son kesintisiz parçanın başıdır
    tail = df.iloc[start:]
    if len(tail) < min_weeks:
        raise DataError(f"yetersiz geçmiş ({len(tail)} < {min_weeks} kapanmış haftalık mum)")
    return tail


def scan_symbol(symbol: str, settings: dict, fetch, now: datetime, rsi_fn) -> dict:
    since = (now - timedelta(weeks=settings["fetch_weeks"] + 2)).strftime("%Y-%m-%d")
    tail = closed_weekly(fetch(symbol, TIMEFRAME, settings["exchanges"], since), now, settings["min_weeks"])
    rsi = rsi_fn(tail["close"], settings["period"]).iloc[-1]
    if rsi is None or not math.isfinite(float(rsi)):
        raise DataError("RSI hesaplanamadı")
    return dict(symbol=symbol, rsi=float(rsi), close=float(tail["close"].iloc[-1]), week=tail.index[-1].strftime("%Y-%m-%d"))


def run_scan(settings: dict, fetch=None, now: datetime | None = None, rsi_fn=wilder_rsi, progress=None, sleep=time.sleep) -> dict:
    """Tüm coinleri tarar. Bir coin hata verirse taramaya devam edilir (hata sayılır). Geçici hatalar (ağ, hız sınırı) bir kez yeniden denenir."""
    fetch = fetch or get_candles
    now = now or datetime.now(timezone.utc)
    symbols, ok, failed = settings["symbols"], [], []
    for i, symbol in enumerate(symbols, 1):
        for attempt in range(settings["retries"] + 1):
            try:
                ok.append(scan_symbol(symbol, settings, fetch, now, rsi_fn))
                break
            except DataError as e:
                failed.append((symbol, str(e)))
                break
            except Exception as e:  # noqa: BLE001  ağ / hız sınırı / borsa hatası
                if attempt < settings["retries"]:
                    sleep(settings["retry_delay"] * (attempt + 1))
                    continue
                failed.append((symbol, (str(e).splitlines() or [type(e).__name__])[0][:160]))
        if progress:
            progress(i, len(symbols))
        if i < len(symbols) and settings["request_pause"]:
            sleep(settings["request_pause"])
    alerts = sorted((r for r in ok if r["rsi"] < THRESHOLD), key=lambda r: (r["rsi"], r["symbol"]))
    return dict(at=now, date=now.astimezone(IST).strftime("%d.%m.%Y"), total=len(symbols), scanned=len(ok), failed=len(failed),
                failed_symbols=[f"{s} ({why})" for s, why in failed], alerts=alerts)


# ---------------------------------------------------------------- mesaj

def display_rsi(value: float) -> str:
    """RSI'ı 2 ondalıkla gösterir (normal yuvarlama). Tek istisna: 28'in altındaki bir değer yuvarlanınca '28.00' olacaksa '27.99' gösterilir;
    böylece bildirilen bir coin asla "28.00" görünmez (örn. 27.996 -> '27.99'). Yuvarlama float'ın en kısa ondalık gösterimi üzerinden yapılır."""
    shown = Decimal(repr(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    limit = Decimal(repr(THRESHOLD))
    if value < THRESHOLD and shown >= limit:
        shown = limit - Decimal("0.01")
    return str(shown)


def build_message(result: dict) -> str:
    """Telegram mesajı. Yalnızca RSI < 28 olan coinleri içerir (RSI'a göre küçükten büyüğe)."""
    head = f"🔔 HAFTALIK RSI TARAMA RAPORU\n\n📅 Tarama Tarihi: {result['date']}\n\n"
    alerts = [r for r in result["alerts"] if r["rsi"] < THRESHOLD]  # son güvenlik: 28 ve üzeri asla yazılmaz
    alerts.sort(key=lambda r: (r["rsi"], r["symbol"]))
    if alerts:
        lines = "\n".join(f"{n}. {r['symbol']} — RSI: {display_rsi(r['rsi'])}" for n, r in enumerate(alerts, 1))
        body = f"🚨 Haftalık RSI 28'in Altındaki Coinler:\n\n{lines}\n\n📊 Toplam Tespit: {len(alerts)} / {result['scanned']}"
    else:
        body = f"✅ Haftalık RSI değeri 28'in altında olan coin bulunamadı.\n\n📊 Taranan Coin: {result['scanned']}"
    if result["failed"]:
        body += f"\n\n⚠️ Verisi alınamayan coin: {result['failed']} (taramaya dahil edilmedi)"
    return head + body


# ---------------------------------------------------------------- durum dosyası ve kilit

_STATE_LOCK = threading.RLock()


def load_state(path=STATE_FILE) -> dict:
    """Durum dosyası yoksa veya bozuksa boş durum döner (uygulama çökmez)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"[rsi-tarama] durum dosyası okunamadı, boş kabul edildi: {e}")
        return {}


def update_state(path=STATE_FILE, **changes) -> dict:
    """Durumu okuyup değişiklikleri uygular ve atomik yazar (iş parçacığı güvenli)."""
    with _STATE_LOCK:
        state = {**load_state(path), **changes}
        temp = Path(str(path) + ".tmp")
        temp.write_text(json.dumps(state, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temp, path)
        return state


@contextmanager
def scan_lock(path=LOCK_FILE, stale_seconds: float = LOCK_STALE_SECONDS):
    """Aynı anda yalnızca bir tarama (aynı makinedeki Streamlit, komut satırı vb.). Eski (çökmüş) kilit geçersiz sayılır."""
    path = Path(path)
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age <= stale_seconds or attempt == 1:
                raise ScanBusy("Başka bir tarama çalışıyor.") from None
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    try:
        os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
        os.close(fd)
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- Telegram

def default_notifier(env_path=ROOT / ".env"):
    """.env'deki TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID ile mesaj gönderen fonksiyon döndürür."""
    load_env(env_path)
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID tanımlı değil (.env dosyasını kontrol et).")
    return lambda text: send_telegram(token, chat, text)


def send_test_message(notify=None, now: datetime | None = None) -> str:
    """Telegram bağlantısını sınar. Gönderilen metni döndürür; hata olursa istisna fırlatır."""
    stamp = (now or datetime.now(timezone.utc)).astimezone(IST).strftime("%d.%m.%Y %H:%M")
    text = f"✅ Haftalık RSI alarmı: Telegram bağlantı testi başarılı.\n🕒 {stamp} (Türkiye saati)"
    (notify or default_notifier())(text)
    return text


# ---------------------------------------------------------------- tarama + durum + bildirim

def execute_scan(settings: dict, kind: str = "manual", send: bool = True, fetch=None, notify=None, state_path=STATE_FILE,
                 lock_path=LOCK_FILE, now: datetime | None = None, rsi_fn=wilder_rsi, progress=None, sleep=time.sleep,
                 dry_run: bool = False) -> dict:
    """Bir tarama çalıştırır: kilit alır, tarar, mesajı BİR kez gönderir ve sonucu durum dosyasına yazar.

    kind="auto": Telegram başarıyla gittiyse bugün 'yapıldı' sayılır (günde bir otomatik tarama). Gönderim başarısızsa yapıldı sayılmaz.
    Hiçbir coinin verisi alınamazsa 'coin bulunamadı' DENMEZ: ScanError fırlatılır. dry_run: gönderme ve durum kaydetme yok."""
    with scan_lock(lock_path):
        result = run_scan(settings, fetch=fetch, now=now, rsi_fn=rsi_fn, progress=progress, sleep=sleep)
        if result["scanned"] == 0:
            reason = f"Hiçbir coinin haftalık verisi alınamadı ({result['failed']} coin)."
            if not dry_run:
                update_state(state_path, last_error=reason)
            raise ScanError(reason)
        message = build_message(result)
        telegram, error = "skipped", None
        if send and not dry_run:
            try:
                (notify or default_notifier())(message)
                telegram = "sent"
            except Exception as e:  # noqa: BLE001
                telegram, error = "failed", str(e)
        if not dry_run:
            record = dict(at=result["at"].isoformat(), kind=kind, total=result["total"], scanned=result["scanned"],
                          failed=result["failed"], failed_symbols=result["failed_symbols"], telegram=telegram, telegram_error=error,
                          alerts=[dict(symbol=r["symbol"], rsi=r["rsi"], close=r["close"], week=r["week"]) for r in result["alerts"]])
            changes = dict(last_scan=record, last_error=error)
            if kind == "auto" and telegram == "sent":
                changes["last_auto_date"] = result["at"].astimezone(IST).strftime("%Y-%m-%d")
            update_state(state_path, **changes)
        result.update(message=message, telegram=telegram, telegram_error=error)
        return result


# ---------------------------------------------------------------- zamanlama

def effective_settings(state: dict, settings: dict) -> tuple[bool, dtime]:
    """Arayüzden yapılan değişiklikler (durum dosyası) config.yaml varsayılanlarının önüne geçer."""
    enabled = state.get("auto_enabled")
    enabled = settings["enabled"] if enabled is None else bool(enabled)
    try:
        at = parse_time(state.get("schedule_time") or settings["time"])
    except ValueError:
        at = parse_time(settings["time"])
    return enabled, at


def auto_due(state: dict, settings: dict, now: datetime) -> bool:
    """Otomatik tarama şimdi çalışmalı mı? Etkin, saati gelmiş, bugün yapılmamış, deneme sınırı/aralığı uygun."""
    now = now.astimezone(IST)
    enabled, at = effective_settings(state, settings)
    today = now.strftime("%Y-%m-%d")
    if not enabled or state.get("last_auto_date") == today or now.time() < at:
        return False
    attempts = state.get("attempts") or {}
    if attempts.get("date") == today:
        if int(attempts.get("count", 0)) >= MAX_ATTEMPTS_PER_DAY:
            return False
        try:
            if now - datetime.fromisoformat(attempts["last_try"]) < timedelta(minutes=RETRY_AFTER_MINUTES):
                return False
        except (KeyError, ValueError):
            pass
    return True


def next_scan(state: dict, settings: dict, now: datetime) -> dict:
    """Arayüz için bir sonraki otomatik tarama: {'kind': 'disabled'|'due'|'scheduled', 'at': datetime|None}."""
    now = now.astimezone(IST)
    enabled, at = effective_settings(state, settings)
    if not enabled:
        return dict(kind="disabled", at=None)
    if auto_due(state, settings, now):
        return dict(kind="due", at=now)
    today = now.strftime("%Y-%m-%d")
    attempts = state.get("attempts") or {}
    done_today = state.get("last_auto_date") == today or (attempts.get("date") == today and int(attempts.get("count", 0)) >= MAX_ATTEMPTS_PER_DAY)
    day = now.date() + timedelta(days=1) if done_today else now.date()
    scheduled = datetime.combine(day, at, tzinfo=IST)
    if scheduled <= now:  # deneme aralığı bekleniyor: aralık bitince
        try:
            scheduled = datetime.fromisoformat(attempts["last_try"]) + timedelta(minutes=RETRY_AFTER_MINUTES)
        except (KeyError, ValueError):
            scheduled = now
    return dict(kind="scheduled", at=scheduled)


class ScanScheduler:
    """Arka plan iş parçacığında günde bir otomatik tarama yapar ve arayüzden manuel tarama başlatılmasını sağlar.
    Arayüzü kilitlemez: tüm tarama işleri ayrı iş parçacıklarında çalışır. Uygulama yeniden başlayınca `start()` zamanlamayı yeniden kurar
    (bugünkü saat geçmiş ve bugün otomatik tarama yapılmamışsa hemen çalışır)."""

    def __init__(self, settings: dict, state_path=STATE_FILE, lock_path=LOCK_FILE, fetch=None, notify=None, clock=None,
                 poll_seconds: float = 20.0, sleep=time.sleep, rsi_fn=wilder_rsi):
        self.settings, self.state_path, self.lock_path = settings, state_path, lock_path
        self.fetch, self.notify, self.rsi_fn, self.sleep = fetch, notify, rsi_fn, sleep
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._running: str | None = None
        self._progress = (0, len(settings["symbols"]))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- durum
    @property
    def running(self) -> str | None:
        return self._running

    @property
    def progress(self) -> tuple[int, int]:
        return self._progress

    def state(self) -> dict:
        return load_state(self.state_path)

    def next_scan(self) -> dict:
        return next_scan(self.state(), self.settings, self.clock())

    def set_enabled(self, enabled: bool) -> None:
        update_state(self.state_path, auto_enabled=bool(enabled))

    def set_time(self, text: str) -> None:
        parse_time(text)
        update_state(self.state_path, schedule_time=text.strip())

    # --- çalıştırma
    def _run(self, kind: str, send: bool) -> dict | None:
        with self._lock:
            if self._running:
                return None
            self._running, self._progress = kind, (0, len(self.settings["symbols"]))
        try:
            return execute_scan(self.settings, kind=kind, send=send, fetch=self.fetch, notify=self.notify, state_path=self.state_path,
                                lock_path=self.lock_path, now=self.clock(), rsi_fn=self.rsi_fn, sleep=self.sleep,
                                progress=lambda done, total: setattr(self, "_progress", (done, total)))
        finally:
            self._running = None

    def tick(self, now: datetime | None = None) -> str:
        """Zamanlayıcı adımı: otomatik tarama zamanıysa çalıştırır. Döndürür: idle | done | telegram_failed | busy | error."""
        now = (now or self.clock()).astimezone(IST)
        state = self.state()
        if self._running or not auto_due(state, self.settings, now):
            return "busy" if self._running else "idle"
        today = now.strftime("%Y-%m-%d")
        attempts = state.get("attempts") or {}
        count = int(attempts.get("count", 0)) + 1 if attempts.get("date") == today else 1
        update_state(self.state_path, attempts=dict(date=today, count=count, last_try=now.isoformat()))
        try:
            result = self._run("auto", True)
        except ScanBusy:
            return "busy"
        except Exception as e:  # noqa: BLE001
            update_state(self.state_path, last_error=str(e))
            return "error"
        if result is None:
            return "busy"
        return "done" if result["telegram"] == "sent" else "telegram_failed"

    def start_manual(self, send_telegram: bool = False) -> bool:
        """Arka planda manuel tarama başlatır. Zaten tarama sürüyorsa False döner."""
        if self._running:
            return False

        def job():
            try:
                self._run("manual", send_telegram)
            except Exception as e:  # noqa: BLE001
                update_state(self.state_path, last_error=str(e))
        threading.Thread(target=job, name="rsi-manual-scan", daemon=True).start()
        return True

    def start(self) -> None:
        """Zamanlayıcı iş parçacığını başlatır (tekrar çağrılırsa bir şey yapmaz)."""
        if self._thread and self._thread.is_alive():
            return

        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001  zamanlayıcı asla ölmemeli
                    print(f"[rsi-tarama] zamanlayıcı hatası: {e}")
                if self._stop.wait(self.poll_seconds):
                    return
        self._stop.clear()
        self._thread = threading.Thread(target=loop, name="rsi-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
