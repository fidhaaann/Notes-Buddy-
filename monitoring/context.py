"""Request context helpers for structured logging."""

from __future__ import annotations

import contextvars
import uuid
from typing import Optional

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_user_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("user_id", default=None)
_operation: contextvars.ContextVar[str] = contextvars.ContextVar("operation", default="")


def set_request_context(
    user_id: Optional[int] = None,
    request_id: Optional[str] = None,
    operation: str | None = None,
) -> None:
    if request_id is None:
        request_id = uuid.uuid4().hex
    _request_id.set(request_id)
    if user_id is not None:
        _user_id.set(user_id)
    if operation is not None:
        _operation.set(operation)


def clear_request_context() -> None:
    _request_id.set("")
    _user_id.set(None)
    _operation.set("")


def get_context() -> dict:
    return {
        "request_id": _request_id.get(),
        "user_id": _user_id.get(),
        "operation": _operation.get(),
    }
