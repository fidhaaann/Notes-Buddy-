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

FOLDER_MIME = "application/vnd.google-apps.folder"


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
    metadata: dict = None,
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
