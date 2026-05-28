"""Global Telegram bot error handler."""

from __future__ import annotations

import logging

from bot import formatter

logger = logging.getLogger(__name__)


async def handle_error(update, context) -> None:
    logger.exception("unhandled_bot_error")
    if update and getattr(update, "effective_chat", None):
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=formatter.error("Something went wrong.", "Please try again."),
            )
        except Exception:
            pass
