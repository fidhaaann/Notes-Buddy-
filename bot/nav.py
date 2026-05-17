"""
bot/nav.py
Session-based navigation state and hierarchical index mapping.

Each user has:
  - A folder breadcrumb stack (for cd / pwd / back)
  - A cached index map built from the last /info listing, so commands
    like /download 1.2, /more 1.1.1, /cd 2 can resolve items by index.

Security: Stack depth is capped, and old inactive users are evicted.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

# ── Per-item metadata stored in the index map ─────────────────────────────────

@dataclass
class IndexedItem:
    """Represents one item (file or folder) in a hierarchical listing."""
    id: str
    name: str
    mime_type: str
    is_folder: bool
    parent_index: str          # e.g. "1" for a child of folder [1]
    full_index: str            # e.g. "1.2"
    path: str = ""             # full Drive path string


# ── Per-user session ──────────────────────────────────────────────────────────

@dataclass
class _UserSession:
    stack: list[tuple[str, str]] = field(default_factory=lambda: [("root", "Home")])
    index_map: dict[str, IndexedItem] = field(default_factory=dict)
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


# ── Hierarchical index map ────────────────────────────────────────────────────

def build_index_map(uid: int, folders: list[dict], files: list[dict]) -> dict[str, IndexedItem]:
    """
    Build a flat index map from the current folder listing.

    Folders are numbered 1, 2, 3, ...
    Files under each folder (if expanded) would be 1.1, 1.2, ...
    For a flat listing (current dir), folders get top-level indices,
    and files continue the numbering.

    Returns the map and also caches it on the user session.
    """
    s = _get(uid)
    s.index_map.clear()

    path = breadcrumb(uid)

    # Folders first, then files — each at top level
    folder_counter = 0
    for f in folders:
        folder_counter += 1
        idx = str(folder_counter)
        item = IndexedItem(
            id=f["id"],
            name=f["name"],
            mime_type=f.get("mimeType", FOLDER_MIME),
            is_folder=True,
            parent_index="",
            full_index=idx,
            path=path,
        )
        s.index_map[idx] = item

    file_counter = 0
    for f in files:
        file_counter += 1
        idx = f"{folder_counter + file_counter}"
        item = IndexedItem(
            id=f["id"],
            name=f["name"],
            mime_type=f.get("mimeType", ""),
            is_folder=False,
            parent_index="",
            full_index=idx,
            path=path,
        )
        s.index_map[idx] = item

    return s.index_map


def build_deep_index_map(
    uid: int,
    folders: list[dict],
    files: list[dict],
    children_map: dict[str, tuple[list[dict], list[dict]]],
) -> dict[str, IndexedItem]:
    """
    Build a hierarchical index map with sub-indices.

    folders/files: top-level items in the current directory.
    children_map: folder_id → (sub_folders, sub_files) for one level of expansion.

    Produces indices like:
      [1] FolderA
        [1.1] sub_file.pdf
        [1.2] sub_file2.pdf
      [2] FolderB
        [2.1] child.docx
      [3] standalone_file.txt
    """
    s = _get(uid)
    s.index_map.clear()
    path = breadcrumb(uid)

    folder_counter = 0
    for f in folders:
        folder_counter += 1
        parent_idx = str(folder_counter)
        item = IndexedItem(
            id=f["id"],
            name=f["name"],
            mime_type=f.get("mimeType", FOLDER_MIME),
            is_folder=True,
            parent_index="",
            full_index=parent_idx,
            path=path,
        )
        s.index_map[parent_idx] = item

        # Expand children if available
        if f["id"] in children_map:
            sub_folders, sub_files = children_map[f["id"]]
            child_counter = 0
            for sf in sub_folders:
                child_counter += 1
                child_idx = f"{parent_idx}.{child_counter}"
                s.index_map[child_idx] = IndexedItem(
                    id=sf["id"],
                    name=sf["name"],
                    mime_type=sf.get("mimeType", FOLDER_MIME),
                    is_folder=True,
                    parent_index=parent_idx,
                    full_index=child_idx,
                    path=f"{path} > {f['name']}",
                )
            for sf in sub_files:
                child_counter += 1
                child_idx = f"{parent_idx}.{child_counter}"
                s.index_map[child_idx] = IndexedItem(
                    id=sf["id"],
                    name=sf["name"],
                    mime_type=sf.get("mimeType", ""),
                    is_folder=False,
                    parent_index=parent_idx,
                    full_index=child_idx,
                    path=f"{path} > {f['name']}",
                )

    # Top-level files (after folders)
    file_counter = 0
    for f in files:
        file_counter += 1
        idx = str(folder_counter + file_counter)
        s.index_map[idx] = IndexedItem(
            id=f["id"],
            name=f["name"],
            mime_type=f.get("mimeType", ""),
            is_folder=False,
            parent_index="",
            full_index=idx,
            path=path,
        )

    return s.index_map


def resolve_index(uid: int, index: str) -> Optional[IndexedItem]:
    """Look up a cached item by its hierarchical index string."""
    s = _get(uid)
    return s.index_map.get(index)


def get_index_map(uid: int) -> dict[str, IndexedItem]:
    return _get(uid).index_map
