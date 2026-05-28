"""Centralized security limits and operational caps."""

from __future__ import annotations

import os

# Upload limits (Telegram download cap is 20MB for bots)
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

# Telegram bot hard limit is 50MB; keep a safe margin
MAX_TELEGRAM_DOWNLOAD_BYTES = int(
    os.environ.get("MAX_TELEGRAM_DOWNLOAD_BYTES", str(45 * 1024 * 1024))
)

# ZIP limits
MAX_ZIP_FILES = int(os.environ.get("MAX_ZIP_FILES", "20"))
MAX_ZIP_BYTES = int(os.environ.get("MAX_ZIP_BYTES", str(100 * 1024 * 1024)))

# Search limits
MAX_SEARCH_LEN = int(os.environ.get("MAX_SEARCH_LEN", "100"))

# Task limits
TASK_WORKERS = int(os.environ.get("TASK_WORKERS", "2"))
TASK_TTL_SECONDS = int(os.environ.get("TASK_TTL_SECONDS", str(60 * 60 * 6)))  # 6 hours

# Rate limit defaults
RATE_LIMIT_COOLDOWN_SECONDS = float(os.environ.get("RATE_LIMIT_COOLDOWN", "3"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
RATE_LIMIT_MAX_ACTIONS = int(os.environ.get("RATE_LIMIT_MAX_ACTIONS", "30"))
