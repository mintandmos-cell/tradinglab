"""Mobile push: ntfy (free app, no account) and/or Telegram bot."""
from __future__ import annotations

import logging

import requests

from .config import load_settings

log = logging.getLogger("tradinglab.notify")


def send(title: str, message: str, url: str | None = None, priority: int = 4, tags: list[str] | None = None) -> dict:
    s = load_settings()
    n = s["notify"]
    url = url or s.get("public_url") or None
    result = {}
    if n.get("ntfy_topic"):
        try:
            body = {"topic": n["ntfy_topic"], "title": title, "message": message, "priority": priority,
                    "tags": tags or ["chart_with_upwards_trend"]}
            if url:
                body["click"] = url
            r = requests.post(n.get("ntfy_server", "https://ntfy.sh").rstrip("/") + "/", json=body, timeout=15)
            result["ntfy"] = r.status_code
        except Exception as e:
            log.warning("ntfy failed: %s", e)
            result["ntfy"] = repr(e)
    if n.get("telegram_token") and n.get("telegram_chat_id"):
        try:
            text = f"<b>{title}</b>\n{message}" + (f"\n{url}" if url else "")
            r = requests.post(f"https://api.telegram.org/bot{n['telegram_token']}/sendMessage",
                              json={"chat_id": n["telegram_chat_id"], "text": text, "parse_mode": "HTML"}, timeout=15)
            result["telegram"] = r.status_code
        except Exception as e:
            log.warning("telegram failed: %s", e)
            result["telegram"] = repr(e)
    return result
