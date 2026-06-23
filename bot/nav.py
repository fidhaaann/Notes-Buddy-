"""
bot/nav.py
Session-based navigation with context-aware active view indexing.

Each user has:
  - A folder breadcrumb stack (for cd / pwd / back)
  - An active view context (folder, search, recent, etc.)
  - Simple 1, 2, 3 indexing that's always relative to current view

No global index maps. No collisions. No prefixes.

Security: Stack depth is capped, and old inactive users are evicted.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

# ── Per-item metadata in index maps ───────────────────────────────────────────

@dataclass
class IndexedItem:
    """Represents one item (file or folder) in a listing."""
    id: str
    name: str
    mime_type: str
    is_folder: bool
    parent_index: str          # (deprecated, kept for compatibility)
    full_index: str            # "1", "2", "3" in current view
    is_shortcut: bool = False
    shortcut_target_id: Optional[str] = None
    shortcut_target_mime_type: Optional[str] = None
    path: str = ""             # full Drive path string


# ── Active view context ──────────────────────────────────────────────────────

@dataclass
class ViewContext:
    """Represents the current active view and its index mappings."""
    view_type: str                                 # "folder", "search", "recent", etc.
    index_map: dict[str, IndexedItem]              # Simple: "1" → item, "2" → item
    metadata: dict = field(default_factory=dict)   # Additional context (keyword, folder_id, etc.)
    created_at: float = field(default_factory=time.monotonic)  # For TTL expiry checks


# ── Per-user session ──────────────────────────────────────────────────────────

@dataclass
class _UserSession:
    stack: list[tuple[str, str]] = field(default_factory=lambda: [("root", "Home")])
    active_view: Optional[ViewContext] = None
    last_access: float = field(default_factory=time.monotonic)


_sessions: OrderedDict[int, _UserSession] = OrderedDict()

MAX_STACK_DEPTH = 50
MAX_USERS       = 5000
_SESSION_TTL    = 3600 * 24  # 24 hours
VIEW_TTL_SECONDS = 900       # 15 minutes — active view expiry

FOLDER_MIME = "application/vnd.google-apps.folder"

# Ordinal word → numeric index mapping for resolve_smart()
_ORDINAL_TO_INDEX = {
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

# Type keywords → mime type fragment mapping
_TYPE_FRAGMENTS = {
    "pdf": "pdf",
    "image": "image/",
    "photo": "image/",
    "picture": "image/",
    "video": "video/",
    "audio": "audio/",
    "doc": "document",
    "document": "document",
    "spreadsheet": "spreadsheet",
    "sheet": "spreadsheet",
    "presentation": "presentation",
    "slide": "presentation",
}


def _get(uid: int) -> _UserSession:
    now = time.monotonic()
    if uid in _sessions:
        session = _sessions[uid]
        session.last_access = now
        _sessions.move_to_end(uid)  # LRU: most recently used goes to end
        return session
    # Evict expired sessions first, then overflow
    while _sessions:
        oldest_uid, oldest = next(iter(_sessions.items()))
        if now - oldest.last_access > _SESSION_TTL or len(_sessions) >= MAX_USERS:
            del _sessions[oldest_uid]
        else:
            break
    session = _UserSession(last_access=now)
    _sessions[uid] = session
    return session


# ── Folder stack operations ───────────────────────────────────────────────────

def current_folder_id(uid: int) -> str:
    return _get(uid).stack[-1][0]


def current_folder_name(uid: int) -> str:
    return _get(uid).stack[-1][1]


def breadcrumb(uid: int) -> str:
    """Return a clean path string like 'Home > Notes > DBMS'."""
    return " > ".join(name for _, name in _get(uid).stack)


def push_folder(uid: int, folder_id: str, folder_name: str) -> None:
    s = _get(uid)
    if len(s.stack) >= MAX_STACK_DEPTH:
        s.stack = [s.stack[0]] + s.stack[-(MAX_STACK_DEPTH - 2):]
    s.stack.append((folder_id, folder_name))


def pop_folder(uid: int) -> bool:
    """Go back one level. Returns False if already at root."""
    s = _get(uid)
    if len(s.stack) <= 1:
        return False
    s.stack.pop()
    return True


def go_home(uid: int) -> None:
    _get(uid).stack = [("root", "Home")]


def clear_user(uid: int) -> None:
    _sessions.pop(uid, None)


def is_in_stack(uid: int, folder_id: str) -> bool:
    """Check if a folder already exists in the current breadcrumb stack."""
    return any(fid == folder_id for fid, _ in _get(uid).stack)


# ── View context management ──────────────────────────────────────────────────

def set_active_view(
    uid: int,
    view_type: str,
    index_map: dict[str, IndexedItem],
    metadata: Optional[dict] = None,
) -> None:
    """
    Set the active view context.
    
    Replaces any previous view. New index map becomes active.
    Users always resolve commands against this context.
    """
    s = _get(uid)
    s.active_view = ViewContext(
        view_type=view_type,
        index_map=index_map,
        metadata=metadata or {},
    )


def get_active_view(uid: int) -> Optional[ViewContext]:
    """Get current active view context."""
    s = _get(uid)
    return s.active_view


def set_active_view_metadata(uid: int, key: str, value: str) -> None:
    """Update metadata on active view."""
    s = _get(uid)
    if s.active_view:
        s.active_view.metadata[key] = value


def get_active_view_metadata(uid: int, key: str, default=None) -> Optional[str]:
    """Retrieve metadata from active view."""
    s = _get(uid)
    if s.active_view:
        return s.active_view.metadata.get(key, default)
    return default


def clear_view(uid: int) -> None:
    """Clear active view (e.g., on logout)."""
    s = _get(uid)
    s.active_view = None


# ── Index resolution against active view ──────────────────────────────────────

def resolve_index(uid: int, index: str) -> Optional[IndexedItem]:
    """
    Resolve an index against the CURRENT ACTIVE VIEW.
    
    This is the primary way to look up items.
    Returns None if no active view or index not found.
    """
    s = _get(uid)
    if not s.active_view:
        return None
    return s.active_view.index_map.get(index)


def resolve_index_silent(uid: int, index: str) -> Optional[IndexedItem]:
    """Alias for resolve_index() for backward compatibility."""
    return resolve_index(uid, index)


def resolve_smart(uid: int, ref: str) -> Optional[IndexedItem]:
    """Resolve a human-like reference against the active view.

    Handles:
      - Numbers: "1", "2" → direct index lookup
      - Ordinals: "first", "second", "last" → map to index
      - Name fragments: "dbms" → fuzzy match against item names
      - Type references: "the pdf" → filter by mime type

    Returns the matched IndexedItem, or None.
    """
    s = _get(uid)
    if not s.active_view or not s.active_view.index_map:
        return None

    index_map = s.active_view.index_map
    ref_clean = ref.strip().lower()

    # Strip common filler words
    ref_clean = re.sub(r"\b(the|a|an|one|file|folder|item)\b", "", ref_clean).strip()

    # 1. Direct numeric index
    if ref_clean.isdigit():
        return index_map.get(ref_clean)

    # 2. Ordinal lookup
    ordinal_idx = _ORDINAL_TO_INDEX.get(ref_clean)
    if ordinal_idx is not None:
        sorted_keys = sorted(index_map.keys(), key=lambda x: int(x) if x.isdigit() else 999)
        if ordinal_idx == -1:
            # "last"
            if sorted_keys:
                return index_map.get(sorted_keys[-1])
        elif 1 <= ordinal_idx <= len(sorted_keys):
            return index_map.get(sorted_keys[ordinal_idx - 1])
        return None

    # 3. Name fragment match
    for item in index_map.values():
        if ref_clean and ref_clean in item.name.lower():
            return item

    # 4. Type reference
    type_frag = _TYPE_FRAGMENTS.get(ref_clean)
    if type_frag:
        for item in index_map.values():
            mime_lower = (item.mime_type or "").lower()
            name_lower = item.name.lower()
            if type_frag in mime_lower or type_frag in name_lower:
                return item

    return None


def is_view_expired(uid: int) -> bool:
    """Check if the active view is older than VIEW_TTL_SECONDS."""
    s = _get(uid)
    if not s.active_view:
        return True
    return (time.monotonic() - s.active_view.created_at) > VIEW_TTL_SECONDS


def clear_expired_view(uid: int) -> bool:
    """Clear the active view if it has expired. Returns True if cleared."""
    if is_view_expired(uid):
        s = _get(uid)
        s.active_view = None
        return True
    return False


def get_view_item_count(uid: int) -> int:
    """Get the number of items in the current active view."""
    s = _get(uid)
    if not s.active_view:
        return 0
    return len(s.active_view.index_map)


def get_index_map(uid: int) -> dict[str, IndexedItem]:
    """Get current active view's index map."""
    s = _get(uid)
    if s.active_view:
        return s.active_view.index_map
    return {}


def build_flat_index_map(uid: int, folders: list[dict], files: list[dict]) -> dict[str, IndexedItem]:
    """
    Build a flat index map from folder and file listings.
    
    Returns index_map only. Caller must call set_active_view() to activate it.
    This separates index building from view activation.
    """
    path = breadcrumb(uid)
    index_map: dict[str, IndexedItem] = {}

    # Folders first: 1, 2, 3, ...
    folder_counter = 0
    for f in folders:
        folder_counter += 1
        idx = str(folder_counter)
        item = IndexedItem(
            id=f["id"],
            name=f["name"],
            mime_type=f.get("mimeType", FOLDER_MIME),
            is_folder=True,
            is_shortcut=bool(f.get("isShortcut")),
            shortcut_target_id=f.get("shortcutTargetId"),
            shortcut_target_mime_type=f.get("shortcutTargetMimeType"),
            parent_index="",
            full_index=idx,
            path=path,
        )
        index_map[idx] = item

    # Files continue: folder_counter+1, folder_counter+2, ...
    file_counter = 0
    for f in files:
        file_counter += 1
        idx = f"{folder_counter + file_counter}"
        item = IndexedItem(
            id=f["id"],
            name=f["name"],
            mime_type=f.get("mimeType", ""),
            is_folder=False,
            is_shortcut=bool(f.get("isShortcut")),
            shortcut_target_id=f.get("shortcutTargetId"),
            shortcut_target_mime_type=f.get("shortcutTargetMimeType"),
            parent_index="",
            full_index=idx,
            path=path,
        )
        index_map[idx] = item

    return index_map


def build_index_map(uid: int, folders: list[dict], files: list[dict]) -> dict[str, IndexedItem]:
    """Alias for backward compatibility. Use build_flat_index_map() instead."""
    return build_flat_index_map(uid, folders, files)


def cleanup_expired_sessions() -> int:
    """Remove expired user sessions and return count removed."""
    now = time.monotonic()
    removed = 0
    for uid in list(_sessions.keys()):
        session = _sessions.get(uid)
        if not session:
            continue
        if now - session.last_access > _SESSION_TTL:
            _sessions.pop(uid, None)
            removed += 1
    return removed
