"""Small transport fakes shared by characterization tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock


def make_update_context(
    uid: int,
    text: str,
    *,
    user_data: dict | None = None,
    update_id: int = 1,
):
    """Return Telegram-shaped objects without constructing Telegram clients."""
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    update = SimpleNamespace(
        message=message,
        effective_message=message,
        effective_user=SimpleNamespace(id=uid),
        update_id=update_id,
    )
    context = SimpleNamespace(
        user_data={} if user_data is None else user_data,
        bot=SimpleNamespace(),
    )
    return update, context
