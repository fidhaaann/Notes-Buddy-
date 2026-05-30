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


def search_index_ranked(telegram_id: int, query: str) -> list[dict]:
    """Search with user behavior-aware ranking.

    Applies score boosts based on:
      - Favorite subjects (most searched topics)
      - Preferred file types (most downloaded categories)

    Falls back to standard FTS5 ordering if user_profile is unavailable.
    Results are re-ranked, never filtered.
    """
    results = search_index(telegram_id, query)
    if not results:
        return results

    try:
        from copilot import user_profile

        scored: list[tuple[float, dict]] = []
        for item in results:
            boost = user_profile.get_ranking_boost(
                telegram_id,
                item.get("name", ""),
                item.get("mime_type", ""),
            )
            scored.append((boost, item))

        # Re-sort: higher boost first, then original order as tiebreaker
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]
    except Exception:
        # If user_profile fails, return original results unmodified
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

