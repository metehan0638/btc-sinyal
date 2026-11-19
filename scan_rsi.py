"""Haftalık RSI alarmı komut satırı. Çalıştır: python scan_rsi.py

    python scan_rsi.py                  # bugün otomatik tarama yapılmadıysa tara ve Telegram'a gönder (cron / Görev Zamanlayıcı / Actions için)
    python scan_rsi.py --force          # hemen tara ve gönder (manuel)
    python scan_rsi.py --dry-run        # tara, mesajı ekrana yaz; gönderme ve durum kaydetme yok
    python scan_rsi.py --test-telegram  # Telegram bağlantı testi mesajı gönder

Not: Bu komut saat kontrolü yapmaz (zamanı dış zamanlayıcı belirler); yalnızca aynı gün ikinci otomatik taramayı engeller.
Streamlit uygulamasındaki zamanlayıcı da açıksa ikisini birden kullanma: aynı gün iki mesaj gelebilir.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from core import rsi_scan as scan
from core.notifier import load_env

ROOT = Path(__file__).resolve().parent


def main(argv=None, fetch=None, notify=None, now=None, state_path=None, lock_path=None, config_path=None) -> int:
    parser = argparse.ArgumentParser(description="Haftalık RSI(14) < 28 tarama ve Telegram bildirimi")
    parser.add_argument("--force", action="store_true", help="bugün tarama yapılmış olsa da hemen tara ve gönder")
    parser.add_argument("--dry-run", action="store_true", help="mesajı ekrana yaz, gönderme ve durum kaydetme")
    parser.add_argument("--test-telegram", action="store_true", help="Telegram bağlantı testi mesajı gönder")
    args = parser.parse_args(argv)
    load_env(ROOT / ".env")
    cfg = yaml.safe_load(open(config_path or ROOT / "config.yaml", encoding="utf-8")) or {}
    settings = scan.scan_settings(cfg)
    state_path, lock_path = state_path or scan.STATE_FILE, lock_path or scan.LOCK_FILE
    if args.test_telegram:
        try:
            scan.send_test_message(notify, now)
        except Exception as e:  # noqa: BLE001
            print(f"Telegram testi başarısız: {e}", file=sys.stderr)
            return 1
        print("Telegram test mesajı gönderildi.")
        return 0
    now = now or datetime.now(timezone.utc)
    if not (args.force or args.dry_run):
        state = scan.load_state(state_path)
        enabled, _ = scan.effective_settings(state, settings)
        if not enabled:
            print("Otomatik tarama pasif (config.yaml rsi_scan.enabled veya arayüz); atlandı. Zorlamak için --force.")
            return 0
        if state.get("last_auto_date") == now.astimezone(scan.IST).strftime("%Y-%m-%d"):
            print("Bugün otomatik tarama zaten yapıldı; atlandı. Zorlamak için --force.")
            return 0
    try:
        result = scan.execute_scan(settings, kind="manual" if args.force else "auto", send=not args.dry_run, fetch=fetch, notify=notify,
                                   state_path=state_path, lock_path=lock_path, now=now, dry_run=args.dry_run)
    except scan.ScanBusy as e:
        print(f"Atlandı: {e}")
        return 0
    except scan.ScanError as e:
        print(f"Tarama başarısız: {e}", file=sys.stderr)
        return 1
    print(result["message"])
    for item in result["failed_symbols"]:
        print(f"[veri alınamadı] {item}")
    if result["telegram"] == "failed":
        print(f"Telegram gönderilemedi: {result['telegram_error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
