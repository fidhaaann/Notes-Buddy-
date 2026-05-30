"""Text normalization utilities for indexing and search."""

from __future__ import annotations

import re
from collections import Counter

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

_STOPWORDS = {
    "the", "and", "or", "of", "to", "a", "in", "on", "for", "is", "are", "was",
    "were", "with", "this", "that", "these", "those", "by", "from", "at",
    "as", "be", "it", "its", "an", "into", "your", "my", "our", "their",
    "me", "you", "we", "they", "us", "them", "not", "no",
    "show", "find", "search", "download", "send", "give", "open", "folder",
    "file", "files", "notes",
}

_ABBREV_MAP = {
    "dbms": "database management systems",
    "db": "database",
    "mod": "module",
    "mod2": "module 2",
    "mod3": "module 3",
    "ppt": "presentation",
    "pptx": "presentation",
    "pdf": "pdf",
    "doc": "document",
    "docx": "document",
    "txt": "text",
    "vid": "video",
    "img": "image",
    "sem": "semester",
    "sem4": "semester 4",
    "sem5": "semester 5",
    "sem6": "semester 6",
    "ntoes": "notes",
    "nto": "note",
}


def normalize_text(value: str) -> str:
    if not value:
        return ""
    lower = value.lower()
    for key, replacement in _ABBREV_MAP.items():
        lower = re.sub(rf"\\b{re.escape(key)}\\b", replacement, lower)
    return lower


def tokens(value: str) -> list[str]:
    text = normalize_text(value)
    return [t for t in _TOKEN_RE.findall(text) if t and t not in _STOPWORDS]


def keywords(value: str, limit: int = 25) -> str:
    counts = Counter(tokens(value))
    if not counts:
        return ""
    ranked = [w for w, _ in counts.most_common(limit)]
    return " ".join(ranked)
