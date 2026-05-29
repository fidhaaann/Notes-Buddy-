"""Natural language normalization and typo correction."""

from __future__ import annotations

import re
from rapidfuzz import process, fuzz

_ABBREV_MAP = {
    "mod": "module",
    "mod2": "module 2",
    "mod3": "module 3",
    "ppt": "presentation",
    "pptx": "presentation",
    "doc": "document",
    "docx": "document",
    "vid": "video",
    "img": "image",
    "db": "database",
    "dbms": "database management systems",
    "ntoes": "notes",
    "dwnld": "download",
    "uplod": "upload",
    "upld": "upload",
    "dl": "download",
}

_ORDINALS = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
}

_ACTION_KEYWORDS = [
    "download",
    "upload",
    "search",
    "find",
    "open",
    "enter",
    "browse",
    "show",
    "list",
    "back",
    "previous",
    "home",
    "where",
    "info",
    "details",
    "delete",
    "remove",
    "rename",
    "move",
    "help",
]


def normalize_text(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower().strip()
    lowered = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", lowered)
    for key, value in _ABBREV_MAP.items():
        lowered = re.sub(rf"\b{re.escape(key)}\b", value, lowered)
    for key, value in _ORDINALS.items():
        lowered = re.sub(rf"\b{re.escape(key)}\b", value, lowered)
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def best_action_token(text: str) -> tuple[str | None, float]:
    if not text:
        return None, 0.0
    match = process.extractOne(
        text,
        _ACTION_KEYWORDS,
        scorer=fuzz.WRatio,
    )
    if not match:
        return None, 0.0
    return match[0], float(match[1])


def extract_index(text: str) -> str | None:
    match = re.search(r"\b(\d{1,2})\b", text)
    if not match:
        return None
    return match.group(1)
