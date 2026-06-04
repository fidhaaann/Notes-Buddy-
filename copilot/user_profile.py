"""
copilot/user_profile.py
User behavior tracking and preference learning.

Tracks user actions (searches, downloads, folder accesses) in SQLite
and computes lightweight behavioral signals:
  - favorite subjects (most searched/accessed topics)
  - preferred file types (most downloaded MIME categories)
  - frequently accessed folders

These signals are used ONLY for ranking, never for filtering.
The bot never auto-executes based on predictions.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)

# ── MIME → category mapping ───────────────────────────────────────────────────

_MIME_CATEGORIES: dict[str, str] = {
    "application/pdf": "pdf",
    "image/": "image",
    "video/": "video",
    "audio/": "audio",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "doc",
    "application/vnd.google-apps.document": "doc",
    "application/vnd.ms-excel": "sheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "sheet",
    "application/vnd.google-apps.spreadsheet": "sheet",
    "text/csv": "sheet",
    "application/vnd.ms-powerpoint": "slide",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "slide",
    "application/vnd.google-apps.presentation": "slide",
}


def _categorize_mime(mime_type: str) -> str:
    """Map a MIME type to a human-friendly category."""
    if not mime_type:
        return "unknown"
    mime_lower = mime_type.lower()
    # Exact match first
    if mime_lower in _MIME_CATEGORIES:
        return _MIME_CATEGORIES[mime_lower]
    # Prefix match (for image/, video/, audio/)
    for prefix, category in _MIME_CATEGORIES.items():
        if prefix.endswith("/") and mime_lower.startswith(prefix):
            return category
    return "other"


# ── Public API ────────────────────────────────────────────────────────────────

def log_action(
    telegram_id: int,
    action: str,
    target: str = "",
    target_name: str = "",
    file_type: str = "",
) -> None:
    """Record a user action for behavioral learning.
    
    Actions: 'search', 'download', 'upload', 'open_folder', 'view', 'browse'
    """
    from db import models
    try:
        models.log_behavior(telegram_id, action, target, target_name, file_type)
    except Exception:
        logger.debug("behavior_log_failed user=%s action=%s", telegram_id, action)


def log_search(telegram_id: int, query: str) -> None:
    """Record a search action."""
    log_action(telegram_id, "search", target=query, target_name=query)


def log_download(telegram_id: int, file_id: str, name: str, mime_type: str = "") -> None:
    """Record a download action."""
    log_action(telegram_id, "download", target=file_id, target_name=name,
               file_type=_categorize_mime(mime_type))


def log_upload(telegram_id: int, file_id: str, name: str, mime_type: str = "") -> None:
    """Record an upload action."""
    log_action(telegram_id, "upload", target=file_id, target_name=name,
               file_type=_categorize_mime(mime_type))


def log_folder_access(telegram_id: int, folder_id: str, name: str) -> None:
    """Record a folder access."""
    log_action(telegram_id, "open_folder", target=folder_id, target_name=name)


def get_favorite_subjects(telegram_id: int, limit: int = 5) -> list[str]:
    """Get the user's most searched topics (by frequency).
    
    Returns list of search query strings, most frequent first.
    """
    from db import models
    try:
        rows = models.get_user_behavior(telegram_id, "search", limit=50)
        if not rows:
            return []
        # Extract keywords from search targets and count frequency
        word_counts: Counter[str] = Counter()
        for row in rows:
            target = row.get("target_name", "").lower().strip()
            if target:
                # Split multi-word queries into individual subject words
                words = target.split()
                for word in words:
                    # Filter out stop words
                    if len(word) > 2 and word not in _STOP_WORDS:
                        word_counts[word] += 1
        return [word for word, _ in word_counts.most_common(limit)]
    except Exception:
        return []


def get_preferred_file_types(telegram_id: int, limit: int = 3) -> list[str]:
    """Get the user's most downloaded file type categories.
    
    Returns list like ['pdf', 'doc', 'image'], most frequent first.
    """
    from db import models
    try:
        rows = models.get_user_behavior(telegram_id, "download", limit=100)
        if not rows:
            return []
        type_counts: Counter[str] = Counter()
        for row in rows:
            ft = row.get("file_type", "").strip()
            if ft and ft not in ("unknown", "other"):
                type_counts[ft] += 1
        return [ft for ft, _ in type_counts.most_common(limit)]
    except Exception:
        return []


def get_frequent_folders(telegram_id: int, limit: int = 5) -> list[dict]:
    """Get the user's most accessed folders.
    
    Returns list of {"target": folder_id, "target_name": name, "count": int}.
    """
    from db import models
    try:
        rows = models.get_user_behavior(telegram_id, "open_folder", limit=100)
        if not rows:
            return []
        folder_counts: Counter[str] = Counter()
        folder_names: dict[str, str] = {}
        for row in rows:
            fid = row.get("target", "")
            name = row.get("target_name", "")
            if fid:
                folder_counts[fid] += 1
                folder_names[fid] = name
        return [
            {"target": fid, "target_name": folder_names.get(fid, ""), "count": count}
            for fid, count in folder_counts.most_common(limit)
        ]
    except Exception:
        return []


def get_ranking_boost(telegram_id: int, file_name: str, mime_type: str = "") -> float:
    """Compute a ranking boost score for a file based on user preferences.
    
    Returns a float in [0.0, 1.0] that can be used to re-rank search results.
    Higher = more relevant to this user's behavior.
    """
    boost = 0.0

    # Boost for preferred file types
    if mime_type:
        category = _categorize_mime(mime_type)
        preferred = get_preferred_file_types(telegram_id, limit=3)
        if category in preferred:
            rank = preferred.index(category)
            boost += 0.3 - (rank * 0.05)  # 0.30, 0.25, 0.20

    # Boost for favorite subject keywords
    favorites = get_favorite_subjects(telegram_id, limit=5)
    name_lower = file_name.lower()
    for i, subject in enumerate(favorites):
        if subject in name_lower:
            boost += 0.3 - (i * 0.04)  # 0.30, 0.26, 0.22, 0.18, 0.14
            break

    return min(1.0, boost)


def get_action_count(telegram_id: int) -> int:
    """Return total recorded actions for the user."""
    from db import models
    try:
        with models.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM user_behavior WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0


def get_experience_level(telegram_id: int) -> str:
    """Classify user experience based on action count."""
    count = get_action_count(telegram_id)
    if count < 20:
        return "beginner"
    if count >= 100:
        return "expert"
    return "intermediate"


def get_effective_mode(telegram_id: int) -> str:
    """Return 'guided', 'expert', or 'adaptive' based on user settings."""
    from db import models
    settings = models.get_user_settings(telegram_id)
    override = settings.get("mode_override")
    if override in {"guided", "expert"}:
        return override
    return "adaptive"


# ── Stop words for subject extraction ─────────────────────────────────────────

_STOP_WORDS: set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "was", "are", "were", "be", "been",
    "my", "me", "this", "that", "these", "those", "it", "its",
    "all", "any", "some", "show", "find", "search", "download", "upload",
    "get", "give", "send", "open", "go", "see", "look", "please",
    "file", "files", "folder", "folders", "notes", "note",
}
