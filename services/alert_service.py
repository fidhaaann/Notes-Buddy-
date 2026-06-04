"""
services/alert_service.py
Telegram-first security alert delivery with email as optional fallback.
"""

from __future__ import annotations

import logging
import os

from telegram import Bot

from db import models

logger = logging.getLogger(__name__)

_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
_BOT: Bot | None = Bot(_BOT_TOKEN) if _BOT_TOKEN else None


def telegram_alerts_enabled(telegram_id: int) -> bool:
    settings = models.get_user_settings(telegram_id)
    return bool(settings.get("telegram_alerts_enabled", True))


def email_alerts_enabled(telegram_id: int) -> bool:
    settings = models.get_user_settings(telegram_id)
    return bool(settings.get("email_alerts_enabled", False))


async def send_telegram_alert(telegram_id: int, message: str) -> bool:
    if not telegram_alerts_enabled(telegram_id):
        return False
    if not _BOT:
        logger.warning("Telegram alerts disabled: TELEGRAM_BOT_TOKEN missing.")
        return False
    try:
        await _BOT.send_message(chat_id=telegram_id, text=message)
        return True
    except Exception as exc:
        logger.warning("Failed to send Telegram alert to %s: %s", telegram_id, str(exc)[:120])
        return False
