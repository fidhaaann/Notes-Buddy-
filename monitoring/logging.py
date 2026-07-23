"""Structured logging configuration with sensitive data redaction."""

from __future__ import annotations

import json
import logging
import os
import re
import traceback
from datetime import datetime, timezone

from monitoring import context

_SENSITIVE_PATTERNS = [
    re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}\b"),  # Telegram bot tokens
    re.compile(r"ya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"gAAAAA[A-Za-z0-9_/+-]{20,}"),
    re.compile(r"(?<=['\"\\s=:])[A-Za-z0-9_/+-]{40,}"),
    re.compile(r"[A-Za-z]:\\\\[^\\s]+"),
    re.compile(r"/[^\\s]*/"),
]


def _sanitize_text(value: str) -> str:
    if not value:
        return value
    sanitized = value
    for pattern in _SENSITIVE_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        ctx = context.get_context()
        record.request_id = ctx.get("request_id") or "-"
        record.user_id = ctx.get("user_id")
        record.operation = ctx.get("operation") or "-"
        return True


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _sanitize_text(record.msg)
        if record.args:
            sanitized_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    sanitized_args.append(_sanitize_text(arg))
                else:
                    sanitized_args.append(arg)
            record.args = tuple(sanitized_args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = _sanitize_text(record.getMessage())
        payload = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", None),
            "operation": getattr(record, "operation", "-"),
        }
        if record.exc_info:
            exc_text = "".join(traceback.format_exception(*record.exc_info))
            payload["exception"] = _sanitize_text(exc_text)[:4000]
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SensitiveDataFilter())
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
