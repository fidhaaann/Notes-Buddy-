"""Small transport fakes shared by characterization tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock


class FakeClock:
    """Controllable monotonic-style clock for deterministic state tests."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


class SequenceIds:
    """Deterministic opaque ID source."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.prefix}-{self.value}"


def make_update_context(
    uid: int,
    text: str,
    *,
    user_data: dict | None = None,
    update_id: int = 1,
    chat_id: int | None = None,
    thread_id: int | None = None,
    bot_data: dict | None = None,
):
    """Return Telegram-shaped objects without constructing Telegram clients."""
    message = SimpleNamespace(
        text=text,
        reply_text=AsyncMock(),
        message_thread_id=thread_id,
    )
    shared_bot_data = {} if bot_data is None else bot_data
    effective_chat = SimpleNamespace(id=uid if chat_id is None else chat_id)
    update = SimpleNamespace(
        message=message,
        effective_message=message,
        effective_user=SimpleNamespace(id=uid),
        effective_chat=effective_chat,
        update_id=update_id,
    )
    context = SimpleNamespace(
        user_data={} if user_data is None else user_data,
        bot=SimpleNamespace(),
        bot_data=shared_bot_data,
        application=SimpleNamespace(bot_data=shared_bot_data),
    )
    return update, context
