"""Telegram bildirimi (ek kütüphane gerektirmez) ve basit .env okuyucu."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_env(path) -> None:
    """KEY=VALUE satırlarını okuyup ortam değişkenlerine ekler."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _api(token: str, method: str, payload: dict | None = None, use_get: bool = False) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    if payload:
        q = urllib.parse.urlencode(payload)
        if use_get:
            url += "?" + q
        else:
            data = q.encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:  # token URL'de olduğu için hata metnini özelleştiriyoruz
        hint = "Token hatalı olabilir." if e.code in (401, 404) else "Chat ID hatalı ya da bota hiç mesaj atılmamış olabilir."
        raise RuntimeError(f"Telegram HTTP {e.code}. {hint}") from None
    except urllib.error.URLError as e:
        raise ConnectionError(f"Telegram'a bağlanılamadı: {e.reason}") from None
    except OSError as e:  # bağlantı sıfırlandı vb.
        raise ConnectionError(f"Telegram bağlantısı koptu: {e}") from None


def _call_with_retry(token, method, payload=None, attempts=3):
    """Bağlantı koparsa bekleyip tekrar dener; POST olmazsa GET ile de dener."""
    last = None
    for i in range(attempts):
        for use_get in (False, True):
            try:
                return _api(token, method, payload, use_get=use_get)
            except ConnectionError as e:
                last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"{last} ({attempts} deneme başarısız; VPN/antivirüs/güvenlik duvarını kontrol et)") from None


def send_telegram(token: str, chat_id: str, text: str) -> None:
    res = _call_with_retry(token, "sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"})
    if not res.get("ok"):
        raise RuntimeError(f"Telegram mesajı gönderilemedi: {res.get('description')}")


def get_chats(token: str) -> list[tuple[int, str]]:
    """Bota yazılmış son mesajlardan (chat_id, ad) listesi verir."""
    res = _call_with_retry(token, "getUpdates")
    seen = {}
    for u in res.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            seen[chat["id"]] = chat.get("first_name") or chat.get("title") or chat.get("username") or "?"
    return list(seen.items())
