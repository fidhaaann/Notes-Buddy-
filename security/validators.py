"""Input validation and sanitization helpers."""

from __future__ import annotations

import os
import re
from typing import Optional

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,100}$")
_INDEX_RE = re.compile(r"^[0-9]+(\.[0-9]+){0,2}$")
_SIMPLE_INDEX_RE = re.compile(r"^[0-9]+$")
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

SHARED_DRIVE_PREFIX = "drive:"


def sanitize_text(value: str) -> str:
    """Remove control characters and normalize whitespace."""
    if not value:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub(" ", value)
    return " ".join(cleaned.split())


def normalize_keyword(keyword: str, max_len: int = 100) -> str:
    cleaned = sanitize_text(keyword)
    if max_len and len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned.strip()


def sanitize_query_value(value: str) -> str:
    """Escape special characters for Google Drive API query strings."""
    if not value:
        return ""
    value = value.replace("\\", "\\\\").replace("'", "\\'")
    value = value.replace("\n", " ").replace("\r", " ")
    return value


def sanitize_filename(filename: str, max_len: int = 200) -> str:
    """Sanitize filenames for safe filesystem and Drive usage."""
    if not filename:
        return "unnamed_file"
    filename = filename.replace("\x00", "")
    filename = os.path.basename(filename)
    filename = _CONTROL_CHARS_RE.sub("", filename)
    filename = filename.strip(". \t\n\r")
    if max_len and len(filename) > max_len:
        name, ext = os.path.splitext(filename)
        filename = name[: max_len - len(ext)] + ext
    return filename or "unnamed_file"


def validate_index(index: str, max_len: int = 10) -> bool:
    """Validate hierarchical index strings like '1', '1.2', '1.2.3'."""
    if not index or len(index) > max_len:
        return False
    return bool(_INDEX_RE.match(index))


def validate_simple_index(index: str, max_len: int = 10) -> bool:
    if not index or len(index) > max_len:
        return False
    return bool(_SIMPLE_INDEX_RE.match(index))


def validate_drive_id(value: str, allow_root: bool = True) -> bool:
    """Validate Drive file/folder IDs and shared drive references."""
    if not value:
        return False
    if allow_root and value == "root":
        return True
    if value.startswith(SHARED_DRIVE_PREFIX):
        value = value[len(SHARED_DRIVE_PREFIX) :]
    return bool(_DRIVE_ID_RE.match(value))


def validate_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value or ""))


def sanitize_zip_filename(keyword: str, max_len: int = 50) -> str:
    safe = re.sub(r"[^\w\s-]", "", keyword or "").strip()
    safe = re.sub(r"\s+", "_", safe)
    if not safe:
        safe = "archive"
    return safe[:max_len]
