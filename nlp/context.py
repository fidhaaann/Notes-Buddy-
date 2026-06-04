"""Lightweight per-user NLP context stored in user_data.

Provides:
  - NLP state (last_item tracking, generic key/value)
  - Conversation history for copilot LLM
  - SearchContext: single source of truth for active search results
  - Query classifier: fresh search vs follow-up reference
  - Unified reference resolver: numeric, ordinal, name, type
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from security import limits


# ── Query classification ──────────────────────────────────────────────────────

class QueryType(str, Enum):
    FRESH_QUERY = "fresh"
    FOLLOW_UP = "follow_up"


# Words that signal a brand-new search against the full index
_FRESH_SIGNALS = {
    "find", "search", "look", "show", "locate", "get", "where",
    "any", "all", "list",
}

# Phrases that signal a fresh search (multi-word)
_FRESH_PHRASES = {
    "get notes about", "find notes", "search for", "look for",
    "show me", "find me", "get me",
}

# Words/patterns that signal a follow-up on existing results
_FOLLOWUP_SIGNALS = {
    "first", "second", "third", "fourth", "fifth", "last",
    "that", "this", "it", "the one", "same",
    "download it", "open it", "send it", "get it",
}

_ORDINAL_MAP = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
    "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7,
    "eighth": 8, "8th": 8,
    "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10,
    "last": -1,
}

# Standalone number pattern (just a number, maybe with action words)
_STANDALONE_NUMBER_RE = re.compile(
    r"^(?:download|open|send|get|info|details|delete|share|copy|move|rename|favorite|unfavorite)?\s*(\d{1,2})\s*$",
    re.IGNORECASE,
)

# Reference pattern in the middle of text: "download the first one", "get 2"
_INDEX_REF_RE = re.compile(r"\b(\d{1,2})\b")
_ORDINAL_RE = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last)\b",
    re.IGNORECASE,
)


def classify_query(text: str) -> QueryType:
    """Classify user input as FRESH_QUERY or FOLLOW_UP.

    Rules (in priority order):
      1. Standalone number → FOLLOW_UP  (e.g., "2", "download 3")
      2. Ordinal reference → FOLLOW_UP  (e.g., "the first one")
      3. Fresh signal words present → FRESH_QUERY
      4. Default → FRESH_QUERY (safer to re-search than resolve stale)
    """
    if not text:
        return QueryType.FRESH_QUERY

    lowered = text.lower().strip()

    # Check standalone number: "2", "download 3"
    if _STANDALONE_NUMBER_RE.match(lowered):
        return QueryType.FOLLOW_UP

    # Check for ordinal references
    if _ORDINAL_RE.search(lowered):
        # But only if there's no fresh search signal too
        # "find the first dbms notes" → FRESH (the ordinal is part of the query)
        has_fresh = any(sig in lowered for sig in _FRESH_SIGNALS)
        if not has_fresh:
            return QueryType.FOLLOW_UP

    # Check follow-up signals
    for sig in _FOLLOWUP_SIGNALS:
        if sig in lowered:
            has_fresh = any(fs in lowered for fs in _FRESH_SIGNALS)
            if not has_fresh:
                return QueryType.FOLLOW_UP

    # Check fresh phrases
    for phrase in _FRESH_PHRASES:
        if phrase in lowered:
            return QueryType.FRESH_QUERY

    # Check fresh signals
    if any(sig in lowered for sig in _FRESH_SIGNALS):
        return QueryType.FRESH_QUERY

    return QueryType.FRESH_QUERY


# ── Search context ────────────────────────────────────────────────────────────

# Default TTL for search context (15 minutes)
SEARCH_CONTEXT_TTL = int(getattr(limits, "SEARCH_CONTEXT_TTL_SECONDS", 900))


@dataclass
class SearchContext:
    """Stores the active search results and metadata."""
    results: list[dict] = field(default_factory=list)  # [{name, file_id, mime_type, ...}]
    query: str = ""
    timestamp: float = 0.0
    view_type: str = ""         # "search", "folder", "recent", "favorites"
    scope: str = "entire_drive"  # "entire_drive", "current_folder", folder name, type filter
    result_count: int = 0


def set_search_context(
    user_data: dict,
    results: list[dict],
    query: str = "",
    view_type: str = "search",
    scope: str = "entire_drive",
) -> None:
    """Store a new SearchContext as the active search state.

    This is the ONLY way to update the active search results.
    Always called after a fresh search or browse operation.
    """
    ctx = SearchContext(
        results=results[:25],
        query=query,
        timestamp=time.time(),
        view_type=view_type,
        scope=scope,
        result_count=len(results),
    )
    user_data["_search_context"] = {
        "results": ctx.results,
        "query": ctx.query,
        "timestamp": ctx.timestamp,
        "view_type": ctx.view_type,
        "scope": ctx.scope,
        "result_count": ctx.result_count,
    }


def get_search_context(user_data: dict) -> Optional[SearchContext]:
    """Get the active SearchContext, or None if none / expired."""
    raw = user_data.get("_search_context")
    if not raw or not isinstance(raw, dict):
        return None
    ctx = SearchContext(
        results=raw.get("results", []),
        query=raw.get("query", ""),
        timestamp=raw.get("timestamp", 0.0),
        view_type=raw.get("view_type", ""),
        scope=raw.get("scope", "entire_drive"),
        result_count=raw.get("result_count", 0),
    )
    return ctx


def is_search_context_valid(user_data: dict) -> bool:
    """Check if the active search context exists and hasn't expired."""
    ctx = get_search_context(user_data)
    if not ctx or not ctx.results:
        return False
    return (time.time() - ctx.timestamp) < SEARCH_CONTEXT_TTL


def clear_search_context(user_data: dict) -> None:
    """Clear the active search context. Called before every fresh search."""
    user_data.pop("_search_context", None)


def get_active_results(user_data: dict) -> tuple[list[dict], str]:
    """Get results from the active search context if still valid.

    Returns:
        (results, query) if valid, or ([], "") if expired/empty.
    """
    if not is_search_context_valid(user_data):
        return [], ""
    ctx = get_search_context(user_data)
    if not ctx:
        return [], ""
    return ctx.results, ctx.query


# ── Unified reference resolver ────────────────────────────────────────────────

def resolve_reference(user_data: dict, ref: str) -> Optional[dict]:
    """Resolve a human reference to a file from the active search context.

    Handles:
      - Numeric index: "1", "2", "3"
      - Ordinal words: "first", "second", "last"
      - Name fragment: "dbms" → fuzzy match against result names
      - Type reference: "the pdf" → filter by type

    Returns the matched result dict, or None.
    """
    results, _ = get_active_results(user_data)
    if not results:
        return None

    ref_lower = ref.lower().strip()

    # 1. Try numeric index
    try:
        idx = int(ref_lower)
        if 1 <= idx <= len(results):
            return results[idx - 1]
    except (ValueError, TypeError):
        pass

    # 2. Try ordinal
    ordinal_idx = _ORDINAL_MAP.get(ref_lower)
    if ordinal_idx is not None:
        if ordinal_idx == -1:
            return results[-1] if results else None
        if 1 <= ordinal_idx <= len(results):
            return results[ordinal_idx - 1]
        return None

    # 3. Try name fragment match
    for result in results:
        name = (result.get("name") or "").lower()
        if ref_lower in name:
            return result

    # 4. Try type reference
    type_map = {
        "pdf": "pdf",
        "image": "image/",
        "photo": "image/",
        "video": "video/",
        "audio": "audio/",
        "doc": "document",
        "document": "document",
        "spreadsheet": "spreadsheet",
        "sheet": "spreadsheet",
        "presentation": "presentation",
        "slide": "presentation",
    }
    type_prefix = type_map.get(ref_lower)
    if type_prefix:
        for result in results:
            mime = (result.get("mime_type") or result.get("mimeType") or "").lower()
            name = (result.get("name") or "").lower()
            if type_prefix in mime or type_prefix in name:
                return result

    return None


# ── NLP state (generic key/value for last_item tracking etc.) ─────────────────

def get_state(user_data: dict) -> dict:
    state = user_data.get("nlp_state")
    if not isinstance(state, dict):
        state = {"updated_at": 0}
        user_data["nlp_state"] = state
    return state


def set_state(user_data: dict, **kwargs) -> None:
    state = get_state(user_data)
    state.update(kwargs)
    state["updated_at"] = time.time()


def is_expired(user_data: dict) -> bool:
    state = get_state(user_data)
    updated = state.get("updated_at", 0)
    return time.time() - updated > limits.NLP_CONTEXT_TTL_SECONDS


def clear_state(user_data: dict) -> None:
    user_data.pop("nlp_state", None)


# ── Conversation history helpers (copilot) ────────────────────────────────────

def add_turn(user_data: dict, role: str, content: str, intent: str = "") -> None:
    """Add a conversation turn to session history."""
    history = user_data.setdefault("_conv_history", [])
    history.append({
        "role": role,
        "content": content[:500],
        "intent": intent,
        "ts": time.time(),
    })
    # Trim to max turns
    max_entries = getattr(limits, "COPILOT_MEMORY_TURNS", 10) * 2
    if len(history) > max_entries:
        user_data["_conv_history"] = history[-max_entries:]


def get_history(user_data: dict, limit: int = 10) -> list[dict[str, str]]:
    """Get recent conversation history formatted for the LLM."""
    history = user_data.get("_conv_history", [])
    ttl = getattr(limits, "COPILOT_MEMORY_TTL", 900)
    now = time.time()
    # Filter by TTL
    valid = [h for h in history if now - h.get("ts", 0) < ttl]
    recent = valid[-limit:]
    return [{"role": h["role"], "content": h["content"]} for h in recent]


# ── Legacy result helpers (kept for backward compatibility) ────────────────────

def set_last_results(user_data: dict, results: list[dict], label: str = "") -> None:
    """Store the last file listing results for reference resolution.

    DEPRECATED: Use set_search_context() instead. Kept for backward compat.
    """
    state = get_state(user_data)
    state["last_results"] = results[:25]
    state["last_results_label"] = label
    state["updated_at"] = time.time()


def get_last_results(user_data: dict) -> list[dict]:
    """Get the last file listing results.

    DEPRECATED: Use get_active_results() instead. Kept for backward compat.
    """
    state = get_state(user_data)
    return state.get("last_results", [])


def resolve_result_reference(user_data: dict, ref: str) -> Optional[dict]:
    """Resolve an index reference (e.g. '2') to a file from last results.

    DEPRECATED: Use resolve_reference() instead. Kept for backward compat.
    """
    results = get_last_results(user_data)
    if not results:
        return None
    try:
        idx = int(ref) - 1
        if 0 <= idx < len(results):
            return results[idx]
    except (ValueError, TypeError):
        pass
    return None
