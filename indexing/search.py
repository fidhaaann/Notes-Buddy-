"""Indexed search using SQLite FTS5."""

from __future__ import annotations

from rapidfuzz import process, fuzz

from db import models
from indexing import normalize
from security import limits


def search_index(telegram_id: int, query: str) -> list[dict]:
    cleaned = normalize.normalize_text(query)
    tokens = normalize.tokens(cleaned)
    if not tokens:
        return []
    fts_query = " ".join(tokens)
    results = models.search_file_fts(telegram_id, fts_query, limits.MAX_FTS_RESULTS)
    return results


def suggest_files(telegram_id: int, query: str, limit: int = 5) -> list[dict]:
    files = models.list_indexed_files(telegram_id)
    if not files:
        return []
    names = {f["name"]: f for f in files if f.get("name")}
    matches = process.extract(
        query,
        names.keys(),
        scorer=fuzz.WRatio,
        limit=limit,
    )
    suggestions: list[dict] = []
    for name, score, _ in matches:
        item = names.get(name)
        if not item:
            continue
        item = dict(item)
        item["score"] = score
        suggestions.append(item)
    return suggestions
