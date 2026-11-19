"""Haftalık RSI alarmı testleri (internet ve Telegram gerekmez). Çalıştır: python tests/test_rsi_scan.py"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scan_rsi
from core import rsi_scan as rs
from core.indicators import wilder_rsi

IST = rs.IST
NOW = datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc)   # 17.11.2026 Salı 09:00 Türkiye
LAST_OPEN = "2026-11-09"                                   # son KAPANMIŞ haftalık mumun açılışı (Pzt)
UNCLOSED_OPEN = "2026-11-16"                               # içinde bulunulan, henüz kapanmamış haftalık mum
SETTINGS = rs.scan_settings({"rsi_scan": {"retry_delay": 0, "request_pause": 0}})
SYMBOLS = SETTINGS["symbols"]
TICKER = lambda symbol: symbol.split("/")[0]


# ------------------------------------------------------------------ test araçları
def weekly_frame(closes, last_open=LAST_OPEN):
    closes = np.asarray(closes, float)
    index = pd.date_range(end=pd.Timestamp(last_open, tz="UTC"), periods=len(closes), freq="7D")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1.0}, index=index)


def coded(value, n=120):
    """Tüm kapanışları `value` olan mumlar; coded_rsi ile RSI tam olarak `value` olur (sınır testleri için kesin kontrol)."""
    return weekly_frame([float(value)] * n)


def coded_rsi(close, period):
    return close.copy()


def series_with_rsi(target, n=150, seed=1):
    """Gerçek Wilder RSI'ı son mumda TAM `target` olan kapanış serisi (bağımsız Wilder hesabıyla son hareketi çözer)."""
    rng = np.random.default_rng(seed)
    base = 1000 + np.cumsum(rng.normal(0, 15, n - 1))
    change = np.diff(base)
    gain, loss = np.mean(np.clip(change[:14], 0, None)), np.mean(np.clip(-change[:14], 0, None))
    for c in change[14:]:
        gain, loss = (gain * 13 + max(c, 0)) / 14, (loss * 13 + max(-c, 0)) / 14
    rs_target, rs_now = target / (100 - target), gain / loss
    step = 13 * (rs_target * loss - gain) if rs_target >= rs_now else -13 * (gain / rs_target - loss)
    closes = np.append(base, base[-1] + step)
    assert closes.min() > 0
    assert abs(wilder_rsi(pd.Series(closes), 14).iloc[-1] - target) < 1e-9, "yardımcı fonksiyon hedef RSI'ı üretmeli"
    return closes


def make_fetch(frames, errors=None, calls=None):
    def fetch(symbol, tf, exchanges, since):
        if calls is not None:
            calls.append((symbol, tf, since))
        if errors and symbol in errors:
            raise errors[symbol]
        value = frames[symbol] if isinstance(frames, dict) else frames
        return None if value is None else value.copy()
    return fetch


def coded_fetch(values=None, default=50.0, **kw):
    values = values or {}
    return make_fetch({s: coded(values.get(TICKER(s), default)) for s in SYMBOLS}, **kw)


def scan(values=None, default=50.0, rsi_fn=coded_rsi, settings=SETTINGS, **kw):
    return rs.run_scan(settings, fetch=coded_fetch(values, default, **kw), now=NOW, rsi_fn=rsi_fn, sleep=lambda s: None)


def alerts_of(result):
    return [a["symbol"] for a in result["alerts"]]


def spec_example():
    return "\n".join(["🔔 HAFTALIK RSI TARAMA RAPORU", "", "📅 Tarama Tarihi: 17.11.2026", "", "🚨 Haftalık RSI 28'in Altındaki Coinler:", "", ""])


# ------------------------------------------------------------------ 1-5: eşik kuralı (RSI < 28)
def test_threshold_boundaries_with_exact_rsi_values():
    for value, included in ((27.99, True), (28.00, False), (28.01, False), (15, True), (45, False), (27.9999999, True), (0.0, True), (100.0, False)):
        result = scan({"BTC": value})
        assert (alerts_of(result) == ["BTC/USDT"]) == included, (value, alerts_of(result))
        assert ("BTC/USDT" in rs.build_message(result)) == included, value
    assert rs.THRESHOLD == 28.0


def test_threshold_boundaries_through_the_real_wilder_rsi():
    for value, included in ((27.99, True), (28.01, False), (15, True), (45, False)):
        frames = {s: weekly_frame(series_with_rsi(50 + i % 7, seed=i)) for i, s in enumerate(SYMBOLS)}
        frames["BTC/USDT"] = weekly_frame(series_with_rsi(value, seed=99))
        result = rs.run_scan(SETTINGS, fetch=make_fetch(frames), now=NOW, sleep=lambda s: None)
        assert (alerts_of(result) == ["BTC/USDT"]) == included, (value, alerts_of(result))
        assert result["scanned"] == 50 and result["failed"] == 0
        if included:
            assert abs(result["alerts"][0]["rsi"] - value) < 1e-6 and result["alerts"][0]["week"] == LAST_OPEN


def test_displayed_rsi_never_reaches_28_for_a_reported_coin():
    for raw, shown in ((27.996, "27.99"), (27.9999999999999, "27.99"), (27.994, "27.99"), (27.99, "27.99"), (24.63, "24.63"),
                       (24.629999999999995, "24.63"), (27.98999999999999, "27.99"), (15, "15.00"), (21.75, "21.75"), (0.499, "0.50")):
        assert rs.display_rsi(raw) == shown, (raw, rs.display_rsi(raw))
    for raw in np.linspace(0, 27.9999999, 20001):  # 28'in altındaki hiçbir değer "28.00" (veya üstü) görünmemeli
        assert float(rs.display_rsi(float(raw))) < 28.0, raw
    message = rs.build_message(scan({"BTC": 27.996, "ETH": 28.0}))
    assert "BTC/USDT — RSI: 27.99" in message and "ETH" not in message and "28.00" not in message


# ------------------------------------------------------------------ 6: veri alınamayan coin
def test_unavailable_coins_are_excluded_and_the_scan_continues():
    errors = {"ETH/USDT": RuntimeError("Hiçbir borsadan veri alınamadı:\nbinance: ccxt hata"), "SOL/USDT": ConnectionError("zaman aşımı")}
    result = scan({"ETH": 10, "SOL": 10, "BTC": 12}, errors=errors)
    assert alerts_of(result) == ["BTC/USDT"], "verisi alınamayan coin (RSI 10 olsa bile) bildirilmemeli"
    assert result["scanned"] == 48 and result["failed"] == 2 and result["total"] == 50
    message = rs.build_message(result)
    assert "ETH" not in message and "SOL" not in message, "verisi alınamayan coinlerin adı mesajda olmamalı"
    assert "Toplam Tespit: 1 / 48" in message and message.endswith("⚠️ Verisi alınamayan coin: 2 (taramaya dahil edilmedi)")
    assert any("ETH/USDT" in item for item in result["failed_symbols"]), "ayrıntı yalnızca günlük/durumda tutulur"
    for bad in (None, pd.DataFrame(), pd.DataFrame({"open": [1.0]}, index=pd.date_range("2026-11-02", periods=1, tz="UTC"))):
        frames = {s: coded(50) for s in SYMBOLS}
        frames["XRP/USDT"] = bad
        assert rs.run_scan(SETTINGS, fetch=make_fetch(frames), now=NOW, rsi_fn=coded_rsi)["failed"] == 1


# ------------------------------------------------------------------ 7-8: mesaj içeriği
def test_only_the_coins_below_28_appear_in_the_message():
    result = scan({"SOL": 27.5, "BTC": 10, "ADA": 20, "DOGE": 28.0, "XRP": 28.01, "ETH": 45})
    assert alerts_of(result) == ["BTC/USDT", "ADA/USDT", "SOL/USDT"]
    message = rs.build_message(result)
    listed = [line for line in message.splitlines() if line[:1].isdigit()]
    assert listed == ["1. BTC/USDT — RSI: 10.00", "2. ADA/USDT — RSI: 20.00", "3. SOL/USDT — RSI: 27.50"]
    assert "Toplam Tespit: 3 / 50" in message
    for ticker in (TICKER(s) for s in SYMBOLS):
        if ticker not in ("BTC", "ADA", "SOL"):
            assert f"{ticker}/USDT" not in message, ticker


def test_no_coin_below_28_sends_the_not_found_message_not_an_empty_list():
    result = scan({"BTC": 28.0, "ETH": 28.01, "SOL": 99})
    assert result["alerts"] == []
    expected = "\n".join(["🔔 HAFTALIK RSI TARAMA RAPORU", "", "📅 Tarama Tarihi: 17.11.2026", "",
                          "✅ Haftalık RSI değeri 28'in altında olan coin bulunamadı.", "", "📊 Taranan Coin: 50"])
    assert rs.build_message(result) == expected
    assert "🚨" not in expected and "/USDT" not in expected


def test_message_matches_the_specified_format_and_is_sorted_ascending():
    values = {"SOL": 21.75, "BTC": 24.63, "ETH": 26.18, "XRP": 27.42}
    message = rs.build_message(scan(values))
    expected = spec_example() + "\n".join(["1. SOL/USDT — RSI: 21.75", "2. BTC/USDT — RSI: 24.63", "3. ETH/USDT — RSI: 26.18",
                                           "4. XRP/USDT — RSI: 27.42", "", "📊 Toplam Tespit: 4 / 50"])
    assert message == expected
    tie = rs.build_message(scan({"ETH": 20.0, "BTC": 20.0}))
    assert tie.index("BTC/USDT") < tie.index("ETH/USDT"), "eşit RSI'da sembole göre sıralanır"
    # build_message son güvenlik olarak 28 ve üzerini asla yazmaz (alerts listesine yanlışlıkla girse bile)
    forced = rs.build_message(dict(date="17.11.2026", scanned=2, failed=0, alerts=[dict(symbol="BTC/USDT", rsi=28.0),
                                                                                    dict(symbol="ETH/USDT", rsi=27.0)]))
    assert "ETH/USDT — RSI: 27.00" in forced and "BTC" not in forced and "Toplam Tespit: 1 / 2" in forced


# ------------------------------------------------------------------ 9: kapanmamış mum
def test_unclosed_weekly_candle_is_never_used():
    for closed_rsi, unclosed_close, included in ((45, 1.0, False), (20, 5000.0, True)):
        closes = series_with_rsi(closed_rsi)
        frame = weekly_frame(closes)
        with_open = pd.concat([frame, weekly_frame([unclosed_close], UNCLOSED_OPEN)])  # bu haftanın (kapanmamış) mumu
        assert with_open.index[-1] == pd.Timestamp(UNCLOSED_OPEN, tz="UTC")
        assert wilder_rsi(with_open["close"], 14).iloc[-1] != wilder_rsi(frame["close"], 14).iloc[-1], "kapanmamış mum RSI'ı değiştirirdi"
        row = rs.scan_symbol("BTC/USDT", SETTINGS, make_fetch(with_open), NOW, wilder_rsi)
        assert abs(row["rsi"] - closed_rsi) < 1e-9 and row["week"] == LAST_OPEN and (row["rsi"] < 28) == included
    # sınır: mum tam kapanış anında kapanmış sayılır, bir saniye önce sayılmaz
    frame = weekly_frame(series_with_rsi(45))
    closes_at = datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)
    assert rs.closed_weekly(frame, closes_at, 100).index[-1] == pd.Timestamp(LAST_OPEN, tz="UTC")
    assert rs.closed_weekly(frame, closes_at - timedelta(seconds=1), 100).index[-1] == pd.Timestamp("2026-11-02", tz="UTC")


# ------------------------------------------------------------------ veri bütünlüğü
def test_history_length_gaps_and_bad_rows():
    closes = series_with_rsi(45, n=160)
    ok = rs.closed_weekly(weekly_frame(closes[-100:]), NOW, 100)
    assert len(ok) == 100
    try:
        rs.closed_weekly(weekly_frame(closes[-99:]), NOW, 100)
        assert False
    except rs.DataError as e:
        assert "yetersiz geçmiş (99 < 100" in str(e)
    # eksik hafta: kesintisiz son parça kısa kalırsa coin dışarıda; uzunsa yalnızca o parça kullanılır
    frame = weekly_frame(closes)
    for drop_from_end, expect_ok in ((95, False), (130, True)):
        gapped = frame.drop(frame.index[-drop_from_end])
        try:
            tail = rs.closed_weekly(gapped, NOW, 100)
            assert expect_ok and len(tail) == drop_from_end - 1 and tail.index.to_series().diff().dropna().eq(pd.Timedelta(days=7)).all()
        except rs.DataError:
            assert not expect_ok
    # sırasız ve tekrarlı satırlar düzeltilir
    messy = pd.concat([frame, frame.iloc[-3:]]).sample(frac=1, random_state=4)
    pd.testing.assert_frame_equal(rs.closed_weekly(messy, NOW, 100), rs.closed_weekly(frame, NOW, 100), check_freq=False)
    # bozuk fiyat (NaN, sıfır, negatif, metin) coinin verisini güvenilmez yapar: son parça kısalır, coin dışarıda
    for bad in (np.nan, 0.0, -5.0, "x"):
        broken = frame.copy().astype({"close": object})
        broken.iloc[-3, broken.columns.get_loc("close")] = bad
        try:
            rs.closed_weekly(broken, NOW, 100)
            assert False, bad
        except rs.DataError:
            pass
    # RSI hesaplanamayan (sabit fiyat) coin
    result = rs.run_scan(SETTINGS, fetch=make_fetch({s: coded(7) for s in SYMBOLS}), now=NOW, sleep=lambda s: None)
    assert result["scanned"] == 0 and result["failed"] == 50 and all("RSI hesaplanamadı" in f for f in result["failed_symbols"])


def test_transient_errors_are_retried_but_data_errors_are_not():
    settings = rs.scan_settings({"rsi_scan": {"coins": ["BTC", "ETH", "SOL"], "retries": 2, "retry_delay": 2, "request_pause": 0}})
    attempts, sleeps, progress = {}, [], []

    def fetch(symbol, tf, exchanges, since):
        attempts[symbol] = attempts.get(symbol, 0) + 1
        if symbol == "BTC/USDT" and attempts[symbol] < 3:
            raise RuntimeError("429 rate limit")
        if symbol == "ETH/USDT":
            raise RuntimeError("bağlantı koptu")
        return coded(20, n=50)  # SOL: yetersiz geçmiş -> veri hatası
    result = rs.run_scan(settings, fetch=fetch, now=NOW, rsi_fn=coded_rsi, sleep=sleeps.append, progress=lambda d, t: progress.append((d, t)))
    assert attempts == {"BTC/USDT": 3, "ETH/USDT": 3, "SOL/USDT": 1}, "geçici hata 2 kez yeniden denenir; veri hatası denenmez"
    assert sleeps == [2, 4, 2, 4], "bekleme artarak gider"
    assert result["scanned"] == 0 and result["failed"] == 3 and progress == [(1, 3), (2, 3), (3, 3)]
    ok = rs.run_scan(settings, fetch=make_fetch(coded(20)), now=NOW, rsi_fn=coded_rsi, sleep=sleeps.append)
    assert alerts_of(ok) == ["BTC/USDT", "ETH/USDT", "SOL/USDT"] and ok["total"] == 3
    pauses = []
    rs.run_scan({**settings, "request_pause": 0.25}, fetch=make_fetch(coded(20)), now=NOW, rsi_fn=coded_rsi, sleep=pauses.append)
    assert pauses == [0.25, 0.25], "coinler arasında hız sınırı payı (son coinden sonra bekleme yok)"


def test_fetch_requests_weekly_candles_with_enough_history():
    calls = []
    rs.run_scan(SETTINGS, fetch=coded_fetch(calls=calls), now=NOW, rsi_fn=coded_rsi, sleep=lambda s: None)
    assert {tf for _, tf, _ in calls} == {"1w"} and len(calls) == 50, "yalnızca haftalık (1w) mum istenir"
    assert {since for _, _, since in calls} == {(NOW - timedelta(weeks=202)).strftime("%Y-%m-%d")}


# ------------------------------------------------------------------ tarama + durum + Telegram
class Notifier:
    def __init__(self, fail=False):
        self.messages, self.fail = [], fail

    def __call__(self, text):
        if self.fail:
            raise RuntimeError("Telegram'a bağlanılamadı")
        self.messages.append(text)


def paths(tmp):
    return dict(state_path=Path(tmp) / "state.json", lock_path=Path(tmp) / "scan.lock")


def run_exec(tmp, kind="auto", send=True, notify=None, values=None, now=NOW, **kw):
    return rs.execute_scan(SETTINGS, kind=kind, send=send, fetch=coded_fetch(values or {"BTC": 20, "ETH": 25}), notify=notify, now=now,
                           rsi_fn=coded_rsi, sleep=lambda s: None, **paths(tmp), **kw)


def test_execute_scan_sends_once_and_stores_only_alerts():
    with tempfile.TemporaryDirectory() as tmp:
        notify = Notifier()
        result = run_exec(tmp, notify=notify, values={"BTC": 20, "ETH": 25, "SOL": 30})
        assert len(notify.messages) == 1 and notify.messages[0] == result["message"] and result["telegram"] == "sent"
        state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
        assert state["last_auto_date"] == "2026-11-17" and state["last_error"] is None
        scan_record = state["last_scan"]
        assert [a["symbol"] for a in scan_record["alerts"]] == ["BTC/USDT", "ETH/USDT"], "durumda yalnızca 28'in altındaki coinler"
        assert scan_record["scanned"] == 50 and scan_record["kind"] == "auto" and scan_record["telegram"] == "sent"
        assert "SOL" not in json.dumps(scan_record), "28 ve üzeri coin durum dosyasına da yazılmaz"
        assert not list(Path(tmp).glob("*.tmp")) and not (Path(tmp) / "scan.lock").exists(), "geçici dosya ve kilit kalmamalı"
        # manuel tarama: bugün 'yapıldı' işaretini DEĞİŞTİRMEZ; send=False ise Telegram'a gitmez
        manual = run_exec(tmp, kind="manual", send=False, notify=notify, now=NOW + timedelta(hours=1), values={"XRP": 5})
        assert manual["telegram"] == "skipped" and len(notify.messages) == 1
        state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
        assert state["last_scan"]["kind"] == "manual" and state["last_auto_date"] == "2026-11-17"
        assert [a["symbol"] for a in state["last_scan"]["alerts"]] == ["XRP/USDT"]
    with tempfile.TemporaryDirectory() as tmp:  # manuel + gönder: mesaj gider ama otomatik taramayı 'yapıldı' saymaz
        notify = Notifier()
        run_exec(tmp, kind="manual", send=True, notify=notify)
        assert len(notify.messages) == 1 and "last_auto_date" not in json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))


def test_telegram_failure_does_not_mark_the_day_as_done():
    with tempfile.TemporaryDirectory() as tmp:
        result = run_exec(tmp, notify=Notifier(fail=True))
        assert result["telegram"] == "failed" and "bağlanılamadı" in result["telegram_error"]
        state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
        assert "last_auto_date" not in state and state["last_scan"]["telegram"] == "failed" and state["last_error"]
        assert [a["symbol"] for a in state["last_scan"]["alerts"]] == ["BTC/USDT", "ETH/USDT"], "sonuç arayüzde yine görünür"


def test_dry_run_and_total_failure_do_not_send_anything():
    with tempfile.TemporaryDirectory() as tmp:
        notify = Notifier()
        result = run_exec(tmp, notify=notify, dry_run=True)
        assert result["telegram"] == "skipped" and not notify.messages and not (Path(tmp) / "state.json").exists()
        down = rs.ScanError
        try:
            rs.execute_scan(SETTINGS, fetch=coded_fetch(errors={s: RuntimeError("ağ yok") for s in SYMBOLS}), notify=notify, now=NOW,
                            sleep=lambda s: None, **paths(tmp))
            assert False
        except down as e:
            assert "Hiçbir coinin" in str(e)
        assert not notify.messages, "hiçbir veri alınamadıysa 'coin bulunamadı' mesajı gönderilmemeli"
        assert "Hiçbir coinin" in json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))["last_error"]


def test_scan_lock_prevents_concurrent_scans():
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "scan.lock"
        with rs.scan_lock(lock):
            assert lock.exists()
            try:
                with rs.scan_lock(lock):
                    assert False
            except rs.ScanBusy:
                pass
            notify = Notifier()
            try:
                run_exec(tmp, notify=notify)
                assert False
            except rs.ScanBusy:
                assert not notify.messages, "kilit varken tarama ve mesaj olmaz"
        assert not lock.exists()
        try:
            with rs.scan_lock(lock):
                raise ValueError("tarama sırasında hata")
        except ValueError:
            pass
        assert not lock.exists(), "hata olsa da kilit bırakılır"
        lock.write_text("123 0")
        old = time.time() - 4000
        os.utime(lock, (old, old))
        with rs.scan_lock(lock):  # çökmüş (eski) kilit geçersiz sayılır
            assert lock.exists()
        assert not lock.exists()


# ------------------------------------------------------------------ ayarlar
def test_settings_validation_and_defaults():
    s = rs.scan_settings({})
    assert len(s["symbols"]) == 50 and len(set(s["symbols"])) == 50 and s["symbols"][0] == "BTC/USDT"
    assert (s["time"], s["period"], s["min_weeks"], s["fetch_weeks"], s["enabled"]) == ("09:00", 14, 100, 200, True)
    assert s["exchanges"] == ["binance", "kraken", "bitstamp"]
    assert rs.scan_settings({"exchanges": ["binance"], "rsi_scan": {}})["exchanges"] == ["binance"]
    assert rs.scan_settings({"rsi_scan": {"exchanges": ["kraken"]}})["exchanges"] == ["kraken"]
    assert rs.scan_settings({"rsi_scan": {"coins": ["btc", "BTC", "BTC/USDT", "eth/usdt"]}})["symbols"] == ["BTC/USDT", "ETH/USDT"]
    for section, message in (({"coins": []}, "boş"), ({"coins": ["B TC"]}, "Geçersiz coin"), ({"time": "9:00"}, "SS:DD"), ({"time": "24:00"}, "SS:DD"),
                             ({"period": 1}, "en az 2"), ({"min_weeks": 14}, "büyük"), ({"fetch_weeks": 50}, "küçük olamaz"),
                             ({"saat": "09:00"}, "bilinmeyen")):
        try:
            rs.scan_settings({"rsi_scan": section})
            assert False, section
        except ValueError as e:
            assert message in str(e), (section, str(e))
    assert rs.to_symbol(" sol ") == "SOL/USDT" and rs.to_symbol("ETH/BTC") == "ETH/BTC"
    assert rs.parse_time("09:05").hour == 9 and rs.parse_time("23:59").minute == 59
    real = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml", encoding="utf-8"))
    real_settings = rs.scan_settings(real)
    assert len(real_settings["symbols"]) == 50 and real_settings["time"] == "09:00" and real_settings["min_weeks"] == 100


# ------------------------------------------------------------------ zamanlama mantığı
def at(hour, minute=0, day=17, tz=IST):
    return datetime(2026, 11, day, hour, minute, tzinfo=tz)


def test_auto_due_rules():
    due = lambda state=None, now=None, **cfg: rs.auto_due(state or {}, rs.scan_settings({"rsi_scan": cfg}), now)
    assert not due(now=at(8, 59)) and due(now=at(9, 0)) and due(now=at(15, 0)), "saat gelince (ve kaçırılmışsa sonra) çalışır"
    assert not due(now=datetime(2026, 11, 17, 5, 59, tzinfo=timezone.utc)) and due(now=datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc)), "saat Türkiye saatiyle"
    assert not due({"last_auto_date": "2026-11-17"}, at(15)) and due({"last_auto_date": "2026-11-16"}, at(9)), "günde en çok bir otomatik tarama"
    assert not due(now=at(15), enabled=False) and due({"auto_enabled": True}, at(15), enabled=False), "arayüz ayarı config'i geçer"
    assert not due({"auto_enabled": False}, at(15)), "arayüzden pasif yapılabilir"
    assert not due({"schedule_time": "14:30"}, at(9, 30)) and due({"schedule_time": "14:30"}, at(14, 30)), "saat durum dosyasından değişir"
    assert not due(now=at(9, 30), time="10:00") and due(now=at(10, 0), time="10:00"), "saat config'den değişir"
    assert due({"schedule_time": "bozuk"}, at(9)), "bozuk saat varsayılana döner"
    attempts = lambda count, minutes_ago, date="2026-11-17": {"attempts": {"date": date, "count": count, "last_try": (at(9, 40) - timedelta(minutes=minutes_ago)).isoformat()}}
    assert not due(attempts(1, 10), at(9, 40)) and due(attempts(1, 16), at(9, 40)), "başarısız denemeden 15 dk sonra yeniden"
    assert not due(attempts(3, 60), at(9, 40)), "günde en çok 3 deneme"
    assert due(attempts(3, 60, date="2026-11-16"), at(9, 40)), "dünün deneme sayısı bugünü etkilemez"


def test_next_scan_for_the_ui():
    settings = rs.scan_settings({})
    assert rs.next_scan({}, settings, at(8))["at"] == at(9) and rs.next_scan({}, settings, at(8))["kind"] == "scheduled"
    assert rs.next_scan({}, settings, at(10))["kind"] == "due"
    assert rs.next_scan({"last_auto_date": "2026-11-17"}, settings, at(10))["at"] == at(9, day=18)
    assert rs.next_scan({"auto_enabled": False}, settings, at(8)) == dict(kind="disabled", at=None)
    assert rs.next_scan({"schedule_time": "18:30"}, settings, at(8))["at"] == at(18, 30)
    failed = {"attempts": {"date": "2026-11-17", "count": 1, "last_try": at(9, 5).isoformat()}}
    assert rs.next_scan(failed, settings, at(9, 10)) == dict(kind="scheduled", at=at(9, 20)), "deneme aralığı bitince"
    exhausted = {"attempts": {"date": "2026-11-17", "count": 3, "last_try": at(9, 45).isoformat()}}
    assert rs.next_scan(exhausted, settings, at(12))["at"] == at(9, day=18)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def scheduler(tmp, clock, notify, fetch=None, **kw):
    return rs.ScanScheduler(SETTINGS, fetch=fetch or coded_fetch({"BTC": 20}), notify=notify, clock=clock, rsi_fn=coded_rsi,
                            sleep=lambda s: None, poll_seconds=0.05, **paths(tmp), **kw)


def test_scheduler_runs_once_per_day_and_survives_restarts():
    with tempfile.TemporaryDirectory() as tmp:
        notify, clock = Notifier(), Clock(at(8, 0))
        sched = scheduler(tmp, clock, notify)
        assert sched.tick() == "idle" and not notify.messages, "saatten önce tarama yok"
        clock.now = at(9, 0)
        assert sched.tick() == "done" and len(notify.messages) == 1
        for minute in (0, 1, 30):
            clock.now = at(9, minute) + timedelta(seconds=30)
            assert sched.tick() == "idle", "aynı gün ikinci tarama yok"
        clock.now = at(20, 0)
        assert sched.tick() == "idle" and len(notify.messages) == 1
        restarted = scheduler(tmp, clock, notify)  # uygulama yeniden başladı
        assert restarted.tick() == "idle" and len(notify.messages) == 1, "yeniden başlatma mükerrer mesaj göndermemeli"
        clock.now = at(9, 0, day=18)
        assert restarted.tick() == "done" and len(notify.messages) == 2, "ertesi gün yeniden çalışır"
    with tempfile.TemporaryDirectory() as tmp:  # kaçırılan tarama: uygulama 14:00'te açılırsa hemen çalışır
        notify = Notifier()
        assert scheduler(tmp, Clock(at(14, 0)), notify).tick() == "done" and len(notify.messages) == 1


def test_scheduler_retries_failed_telegram_with_limits():
    with tempfile.TemporaryDirectory() as tmp:
        notify, clock = Notifier(fail=True), Clock(at(9, 0))
        sched = scheduler(tmp, clock, notify)
        assert sched.tick() == "telegram_failed"
        calls = []
        for minutes in (5, 14):
            clock.now = at(9, 0) + timedelta(minutes=minutes)
            calls.append(sched.tick())
        assert calls == ["idle", "idle"], "15 dakikadan önce yeniden denenmez"
        clock.now = at(9, 16)
        assert sched.tick() == "telegram_failed"
        clock.now = at(9, 32)
        assert sched.tick() == "telegram_failed"
        clock.now = at(9, 50)
        assert sched.tick() == "idle", "günde en çok 3 deneme"
        state = sched.state()
        assert state["attempts"]["count"] == 3 and "last_auto_date" not in state
        clock.now = at(9, 0, day=18)
        notify.fail = False
        assert sched.tick() == "done" and len(notify.messages) == 1, "ertesi gün sayaç sıfırlanır; tek mesaj"


def test_scheduler_reports_errors_without_crashing_and_honours_ui_settings():
    with tempfile.TemporaryDirectory() as tmp:
        notify, clock = Notifier(), Clock(at(9, 0))
        broken = scheduler(tmp, clock, notify, fetch=coded_fetch(errors={s: RuntimeError("ağ yok") for s in SYMBOLS}))
        assert broken.tick() == "error" and "Hiçbir coinin" in broken.state()["last_error"] and not notify.messages
        sched = scheduler(tmp, Clock(at(9, 30)), notify)
        sched.set_enabled(False)
        assert sched.tick() == "idle" and sched.next_scan()["kind"] == "disabled"
        sched.set_enabled(True)
        sched.set_time("18:00")
        assert sched.tick() == "idle" and sched.next_scan()["at"] == at(18, 0)
        try:
            sched.set_time("25:99")
            assert False
        except ValueError:
            pass
        assert sched.state()["schedule_time"] == "18:00"
        sched._running = "manual"
        assert sched.tick(at(19)) == "busy"


def test_manual_scan_runs_in_the_background_without_blocking():
    with tempfile.TemporaryDirectory() as tmp:
        gate, entered = threading.Event(), threading.Event()
        base = coded_fetch({"BTC": 20})

        def slow_fetch(symbol, tf, exchanges, since):
            entered.set()
            assert gate.wait(10), "test zaman aşımı"
            return base(symbol, tf, exchanges, since)
        notify = Notifier()
        sched = scheduler(tmp, Clock(at(10)), notify, fetch=slow_fetch)
        started = time.time()
        assert sched.start_manual(send_telegram=False) is True
        assert time.time() - started < 1.0, "manuel tarama arayüzü kilitlememeli (hemen dönmeli)"
        assert entered.wait(5) and sched.running == "manual"
        assert sched.start_manual() is False, "tarama sürerken ikinci tarama başlamaz"
        assert sched.tick(at(10)) == "busy"
        gate.set()
        deadline = time.time() + 10
        while sched.running and time.time() < deadline:
            time.sleep(0.02)
        assert sched.running is None and sched.progress == (50, 50)
        last = sched.state()["last_scan"]
        assert last["kind"] == "manual" and last["telegram"] == "skipped" and not notify.messages
        assert [a["symbol"] for a in last["alerts"]] == ["BTC/USDT"]
        assert sched.start_manual(send_telegram=True) is True
        deadline = time.time() + 10
        while (sched.running or not notify.messages) and time.time() < deadline:
            time.sleep(0.02)
        assert len(notify.messages) == 1 and sched.state()["last_scan"]["telegram"] == "sent"


def test_scheduler_thread_starts_once_and_stops():
    with tempfile.TemporaryDirectory() as tmp:
        notify = Notifier()
        sched = scheduler(tmp, Clock(at(9, 0)), notify)
        sched.start()
        first = sched._thread
        sched.start()
        assert sched._thread is first, "start() tekrar çağrılırsa ikinci iş parçacığı açılmaz"
        deadline = time.time() + 10
        while not notify.messages and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.3)  # birkaç döngü daha dönsün
        assert len(notify.messages) == 1, "zamanlayıcı döngüsü mükerrer mesaj göndermemeli"
        sched.stop()
        first.join(5)
        assert not first.is_alive()


# ------------------------------------------------------------------ durum dosyası
def test_state_file_is_robust_and_thread_safe():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        assert rs.load_state(path) == {}
        path.write_text("{bozuk", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            assert rs.load_state(path) == {}
        path.write_text("[1, 2]", encoding="utf-8")
        assert rs.load_state(path) == {}
        path.unlink()
        threads = [threading.Thread(target=rs.update_state, args=(path,), kwargs={f"k{i}": i}) for i in range(30)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert rs.load_state(path) == {f"k{i}": i for i in range(30)} and not list(Path(tmp).glob("*.tmp"))


# ------------------------------------------------------------------ Telegram yardımcıları
def test_default_notifier_and_test_message():
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
        try:
            rs.default_notifier(Path(tmp) / "yok.env")
            assert False
        except RuntimeError as e:
            assert "TELEGRAM_BOT_TOKEN" in str(e)
        env = Path(tmp) / ".env"
        env.write_text("TELEGRAM_BOT_TOKEN=abc:123\nTELEGRAM_CHAT_ID=42\n", encoding="utf-8")
        with mock.patch.object(rs, "send_telegram") as send:
            rs.default_notifier(env)("merhaba")
            send.assert_called_once_with("abc:123", "42", "merhaba")
    notify = Notifier()
    text = rs.send_test_message(notify, NOW)
    assert notify.messages == [text] and "Telegram bağlantı testi başarılı" in text and "17.11.2026 09:00" in text
    try:
        rs.send_test_message(Notifier(fail=True), NOW)
        assert False
    except RuntimeError:
        pass


# ------------------------------------------------------------------ komut satırı
def test_cli_flow():
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config.yaml"
        config.write_text(yaml.safe_dump({"exchanges": ["binance"], "rsi_scan": {"coins": ["BTC", "ETH", "SOL"], "retry_delay": 0, "request_pause": 0}}),
                          encoding="utf-8")
        notify = Notifier()
        frames = {"BTC/USDT": weekly_frame(series_with_rsi(20)), "ETH/USDT": weekly_frame(series_with_rsi(45, seed=2)),
                  "SOL/USDT": weekly_frame(series_with_rsi(27.99, seed=3))}
        common = dict(fetch=make_fetch(frames), notify=notify, now=NOW, config_path=config, **paths(tmp))

        def cli(*argv, **over):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = scan_rsi.main(list(argv), **{**common, **over})
            return code, out.getvalue(), err.getvalue()
        code, out, _ = cli("--dry-run")
        assert code == 0 and "BTC/USDT — RSI: 20.00" in out and "SOL/USDT — RSI: 27.99" in out and "ETH" not in out
        assert not notify.messages and not (Path(tmp) / "state.json").exists(), "dry-run gönderme ve kaydetme yapmaz"
        code, out, _ = cli()
        assert code == 0 and len(notify.messages) == 1 and "Toplam Tespit: 2 / 3" in notify.messages[0]
        code, out, _ = cli()
        assert code == 0 and "zaten yapıldı" in out and len(notify.messages) == 1, "aynı gün ikinci otomatik tarama yok"
        code, out, _ = cli("--force")
        assert code == 0 and len(notify.messages) == 2
        rs.update_state(Path(tmp) / "state.json", auto_enabled=False, last_auto_date="2000-01-01")
        code, out, _ = cli()
        assert code == 0 and "pasif" in out and len(notify.messages) == 2
        code, out, _ = cli("--test-telegram")
        assert code == 0 and "Telegram bağlantı testi" in notify.messages[-1]
        code, _, err = cli("--test-telegram", notify=Notifier(fail=True))
        assert code == 1 and "Telegram testi başarısız" in err
        rs.update_state(Path(tmp) / "state.json", auto_enabled=True, last_auto_date="2000-01-01")
        code, _, err = cli(notify=Notifier(fail=True))
        assert code == 1 and "Telegram gönderilemedi" in err
        code, _, err = cli("--force", fetch=make_fetch({s: None for s in frames}))
        assert code == 1 and "Tarama başarısız" in err
        with rs.scan_lock(Path(tmp) / "scan.lock"):
            code, out, _ = cli("--force")
        assert code == 0 and "Başka bir tarama" in out


def test_all():
    test_threshold_boundaries_with_exact_rsi_values()
    test_threshold_boundaries_through_the_real_wilder_rsi()
    test_displayed_rsi_never_reaches_28_for_a_reported_coin()
    test_unavailable_coins_are_excluded_and_the_scan_continues()
    test_only_the_coins_below_28_appear_in_the_message()
    test_no_coin_below_28_sends_the_not_found_message_not_an_empty_list()
    test_message_matches_the_specified_format_and_is_sorted_ascending()
    test_unclosed_weekly_candle_is_never_used()
    test_history_length_gaps_and_bad_rows()
    test_transient_errors_are_retried_but_data_errors_are_not()
    test_fetch_requests_weekly_candles_with_enough_history()
    test_execute_scan_sends_once_and_stores_only_alerts()
    test_telegram_failure_does_not_mark_the_day_as_done()
    test_dry_run_and_total_failure_do_not_send_anything()
    test_scan_lock_prevents_concurrent_scans()
    test_settings_validation_and_defaults()
    test_auto_due_rules()
    test_next_scan_for_the_ui()
    test_scheduler_runs_once_per_day_and_survives_restarts()
    test_scheduler_retries_failed_telegram_with_limits()
    test_scheduler_reports_errors_without_crashing_and_honours_ui_settings()
    test_manual_scan_runs_in_the_background_without_blocking()
    test_scheduler_thread_starts_once_and_stops()
    test_state_file_is_robust_and_thread_safe()
    test_default_notifier_and_test_message()
    test_cli_flow()


if __name__ == "__main__":
    test_all()
    print("Haftalık RSI alarmı testleri geçti ✔")
