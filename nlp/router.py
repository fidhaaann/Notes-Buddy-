"""NLP routing for natural language messages."""

from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import process, fuzz

from bot import commands as bot_commands
from bot import formatter, nav, ui
from db import models
from drive import auth as drive_auth
from drive import drive_service as ds
from indexing import indexer, search as indexed_search
from nlp import context as nlp_context
from nlp import intents as intent_types
from nlp import normalize
from security import validators, limits
from services import anomaly_detection, stepup_auth
from services import parser as parser_utils
from tasks.manager import get_task_manager


_START_KEYWORDS = {"start", "get started", "begin", "launch"}
_LOGIN_KEYWORDS = {"login", "log in", "sign in", "connect", "authorize", "authenticate", "connect account", "connect my drive"}
_LOGOUT_KEYWORDS = {"logout", "log out", "sign out", "disconnect"}
_DOWNLOAD_KEYWORDS = {"download", "send", "give", "get"}
_UPLOAD_KEYWORDS = {"upload", "save", "store", "put"}
_SEARCH_KEYWORDS = {"search", "find", "look", "show"}
_OPEN_KEYWORDS = {"open", "enter", "go", "goto"}
_BACK_KEYWORDS = {"back", "previous", "up"}
_PWD_KEYWORDS = {"where", "path", "pwd"}
_INFO_KEYWORDS = {"info", "details", "metadata"}
_DELETE_KEYWORDS = {"delete", "remove"}
_RENAME_KEYWORDS = {"rename"}
_MOVE_KEYWORDS = {"move"}
_COPY_KEYWORDS = {"copy", "duplicate", "clone"}
_SHARE_KEYWORDS = {"share", "link"}
_ZIP_KEYWORDS = {"zip", "compress", "archive"}
_MKDIR_KEYWORDS = {"mkdir", "create folder", "new folder", "make folder", "create directory", "new directory"}
_FAVORITE_KEYWORDS = {"favorite", "favourite", "star", "pin"}
_UNFAVORITE_KEYWORDS = {"unfavorite", "unfavourite", "unstar", "remove favorite", "remove favourites"}
_FAVORITES_LIST_KEYWORDS = {"show favorites", "list favorites", "favorites"}
_RECENT_KEYWORDS = {"recent", "latest", "recent files", "latest files", "recent items", "latest items"}
_CLEAR_KEYWORDS = {"clear chat", "clear messages", "clear conversation"}
_HELP_KEYWORDS = {"help", "commands"}
_INDEX_KEYWORDS = {"index", "refresh"}
_MENU_KEYWORDS = {"menu", "main menu", "options", "show menu"}
_TOOL_KEYWORDS = {"tool", "tools", "abilities", "capabilities", "what can you do", "what can you help with"}
_EMAIL_KEYWORDS = {"email", "security email", "alert email", "set email"}
_VERIFY_KEYWORDS = {"verify", "otp", "code", "verification"}
_CANCEL_KEYWORDS = {"cancel", "stop", "abort", "never mind"}
_BULK_KEYWORDS = {"all", "everything", "every", "all files", "all items", "these", "those", "these files", "those files"}
_REFERENCE_WORDS = {"this", "that", "it", "one", "file", "folder", "document", "image", "pdf"}
_RECENT_REF_WORDS = {"recent", "latest", "newest"}

_HIGH_CONFIDENCE = 0.85
_MEDIUM_CONFIDENCE = 0.65
_LOW_CONFIDENCE = 0.5

_TYPE_ALIASES = {
    "pdf": "pdf",
    "pdfs": "pdf",
    "image": "image",
    "images": "image",
    "photo": "image",
    "photos": "image",
    "picture": "image",
    "pictures": "image",
    "video": "video",
    "videos": "video",
    "audio": "audio",
    "audios": "audio",
    "doc": "doc",
    "docs": "doc",
    "document": "doc",
    "documents": "doc",
    "sheet": "sheet",
    "sheets": "sheet",
    "spreadsheet": "sheet",
    "spreadsheets": "sheet",
    "slide": "slide",
    "slides": "slide",
    "presentation": "slide",
    "presentations": "slide",
}

_TYPE_MIME_PREFIXES = {
    "pdf": {"application/pdf"},
    "image": {"image/"},
    "video": {"video/"},
    "audio": {"audio/"},
    "doc": {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.google-apps.document",
    },
    "sheet": {
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.google-apps.spreadsheet",
        "text/csv",
    },
    "slide": {
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.google-apps.presentation",
    },
}

_TYPE_EXTENSIONS = {
    "pdf": {".pdf"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".webm"},
    "audio": {".mp3", ".wav", ".aac", ".m4a", ".ogg"},
    "doc": {".doc", ".docx"},
    "sheet": {".xls", ".xlsx", ".csv"},
    "slide": {".ppt", ".pptx"},
}

_ACTION_INTENT_MAP = {
    "start": intent_types.IntentType.START,
    "login": intent_types.IntentType.LOGIN,
    "logout": intent_types.IntentType.LOGOUT,
    "download": intent_types.IntentType.DOWNLOAD,
    "upload": intent_types.IntentType.UPLOAD,
    "search": intent_types.IntentType.SEARCH,
    "find": intent_types.IntentType.SEARCH,
    "open": intent_types.IntentType.OPEN_FOLDER,
    "enter": intent_types.IntentType.OPEN_FOLDER,
    "browse": intent_types.IntentType.BROWSE,
    "show": intent_types.IntentType.SEARCH,
    "list": intent_types.IntentType.BROWSE,
    "back": intent_types.IntentType.BACK,
    "previous": intent_types.IntentType.BACK,
    "home": intent_types.IntentType.BACK,
    "where": intent_types.IntentType.PWD,
    "info": intent_types.IntentType.INFO,
    "details": intent_types.IntentType.INFO,
    "delete": intent_types.IntentType.DELETE,
    "remove": intent_types.IntentType.DELETE,
    "rename": intent_types.IntentType.RENAME,
    "move": intent_types.IntentType.MOVE,
    "copy": intent_types.IntentType.COPY,
    "duplicate": intent_types.IntentType.COPY,
    "share": intent_types.IntentType.SHARE,
    "zip": intent_types.IntentType.ZIP,
    "compress": intent_types.IntentType.ZIP,
    "archive": intent_types.IntentType.ZIP,
    "favorite": intent_types.IntentType.FAVORITE,
    "favorites": intent_types.IntentType.FAVORITES,
    "recent": intent_types.IntentType.RECENT,
    "mkdir": intent_types.IntentType.MKDIR,
    "create": intent_types.IntentType.MKDIR,
    "folder": intent_types.IntentType.MKDIR,
    "menu": intent_types.IntentType.MENU,
    "tool": intent_types.IntentType.TOOL,
    "email": intent_types.IntentType.EMAIL,
    "verify": intent_types.IntentType.VERIFY,
    "cancel": intent_types.IntentType.CANCEL,
    "help": intent_types.IntentType.HELP,
}

_INTENT_EXAMPLES = {
    intent_types.IntentType.START: "start",
    intent_types.IntentType.LOGIN: "connect my drive",
    intent_types.IntentType.LOGOUT: "log out",
    intent_types.IntentType.MENU: "show menu",
    intent_types.IntentType.TOOL: "what can you do",
    intent_types.IntentType.BROWSE: "show what's inside",
    intent_types.IntentType.OPEN_FOLDER: "open the dbms folder",
    intent_types.IntentType.BACK: "go back",
    intent_types.IntentType.PWD: "where am i",
    intent_types.IntentType.SEARCH: "find dbms notes",
    intent_types.IntentType.DOWNLOAD: "download the second one",
    intent_types.IntentType.UPLOAD: "upload this to notes",
    intent_types.IntentType.INFO: "show details for the first file",
    intent_types.IntentType.RENAME: "rename this file to module 2 notes",
    intent_types.IntentType.DELETE: "delete the third file",
    intent_types.IntentType.MOVE: "move this to semester 4",
    intent_types.IntentType.COPY: "copy this into AI",
    intent_types.IntentType.ZIP: "zip all images",
    intent_types.IntentType.MKDIR: "create a folder called DBMS",
    intent_types.IntentType.FAVORITE: "favorite this file",
    intent_types.IntentType.FAVORITES: "show favorites",
    intent_types.IntentType.UNFAVORITE: "remove from favorites",
    intent_types.IntentType.RECENT: "show recent files",
    intent_types.IntentType.SHARE: "share this file",
    intent_types.IntentType.EMAIL: "set my email to you@example.com",
    intent_types.IntentType.VERIFY: "verify 123456",
    intent_types.IntentType.CANCEL: "cancel",
    intent_types.IntentType.HELP: "help",
}


def _extract_target(text: str) -> str | None:
    match = re.search(r"(?:to|in|into|on|for)\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


async def _ensure_folder_view(uid: int) -> nav.ViewContext | None:
    view = nav.get_active_view(uid)
    if view and any(item.is_folder for item in view.index_map.values()):
        return view
    listing = await ds.list_directory_async(uid, parent_id=nav.current_folder_id(uid))
    index_map = nav.build_flat_index_map(uid, listing.folders, listing.files)
    nav.set_active_view(uid, "folder", index_map, metadata={"folder_id": nav.current_folder_id(uid)})
    return nav.get_active_view(uid)


async def _ensure_file_view(uid: int) -> nav.ViewContext | None:
    view = nav.get_active_view(uid)
    if view and any(not item.is_folder for item in view.index_map.values()):
        return view
    listing = await ds.list_directory_async(uid, parent_id=nav.current_folder_id(uid))
    index_map = nav.build_flat_index_map(uid, listing.folders, listing.files)
    nav.set_active_view(uid, "folder", index_map, metadata={"folder_id": nav.current_folder_id(uid)})
    return nav.get_active_view(uid)


async def _folder_candidates(uid: int) -> dict[str, nav.IndexedItem]:
    view = await _ensure_folder_view(uid)
    if not view:
        return {}
    return {item.name: item for item in view.index_map.values() if item.is_folder}


async def _file_candidates(uid: int) -> dict[str, nav.IndexedItem]:
    view = await _ensure_file_view(uid)
    if not view:
        return {}
    return {item.name: item for item in view.index_map.values() if not item.is_folder}


def _is_bulk_request(text: str) -> bool:
    return any(k in text for k in _BULK_KEYWORDS)


def _mentions_recent(text: str) -> bool:
    return any(k in text for k in _RECENT_REF_WORDS)


def _has_reference_word(text: str) -> bool:
    return any(k in text for k in _REFERENCE_WORDS)


def _strip_action_words(text: str, extra: set[str] | None = None) -> str:
    stop = {
        "download", "upload", "search", "find", "show", "open", "enter", "go",
        "folder", "folders", "file", "files", "the", "a", "an", "to", "in", "into",
        "on", "for", "all", "every", "everything", "please", "my", "me", "this",
        "that", "one", "recent", "latest", "zip", "compress", "archive", "copy",
        "duplicate", "share", "link", "rename", "move", "delete", "remove", "info",
        "details", "menu", "tool", "start", "email", "verify", "cancel",
    }
    if extra:
        stop.update(extra)
    tokens = [t for t in text.split() if t not in stop]
    return " ".join(tokens).strip()


def _select_confident_match(
    query: str,
    candidates: dict[str, nav.IndexedItem],
    min_score: int = 90,
    min_gap: int = 12,
) -> tuple[nav.IndexedItem | None, list[str]]:
    if not query or not candidates:
        return None, []
    matches = process.extract(
        query,
        candidates.keys(),
        scorer=fuzz.WRatio,
        limit=5,
    )
    if not matches:
        return None, []
    best_name, best_score, _ = matches[0]
    second_score = matches[1][1] if len(matches) > 1 else 0
    if best_score >= min_score and (best_score - second_score) >= min_gap:
        return candidates.get(best_name), [m[0] for m in matches]
    return None, [m[0] for m in matches]


def _suggest_action_examples(text: str) -> list[str]:
    normalized = normalize.normalize_text(text)
    suggestions: list[str] = []
    for token, score in normalize.action_candidates(normalized, limit=5):
        if score < 70:
            continue
        intent = _ACTION_INTENT_MAP.get(token)
        if not intent:
            continue
        example = _INTENT_EXAMPLES.get(intent)
        if example and example not in suggestions:
            suggestions.append(example)
    return suggestions


def _extract_folder_name(text: str) -> str | None:
    match = re.search(r"(?:called|named)\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"(?:create|make)\s+(?:a\s+)?folder\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_rename_name(text: str) -> str | None:
    match = re.search(r"(?:rename|name|call|change(?:\s+the)?\s+name).*?\bto\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"\bas\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_copy_name(text: str) -> str | None:
    match = re.search(r"\bas\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_email(text: str) -> str | None:
    match = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text)
    if match:
        return match.group(1)
    return None


def _extract_otp(text: str) -> str | None:
    match = re.search(r"\b(\d{6})\b", text)
    if match:
        return match.group(1)
    return None


def _extract_type_hint(text: str) -> str | None:
    tokens = text.split()
    for token in tokens:
        hint = _TYPE_ALIASES.get(token)
        if hint:
            return hint
    return None


def _matches_type(name: str, mime_type: str, hint: str) -> bool:
    if not hint:
        return False
    mime_type = (mime_type or "").lower()
    name = (name or "").lower()
    prefixes = _TYPE_MIME_PREFIXES.get(hint, set())
    for prefix in prefixes:
        if prefix.endswith("/") and mime_type.startswith(prefix):
            return True
        if mime_type == prefix:
            return True
    for ext in _TYPE_EXTENSIONS.get(hint, set()):
        if name.endswith(ext):
            return True
    return False


def _item_matches_type(item: nav.IndexedItem, hint: str) -> bool:
    return _matches_type(item.name, item.mime_type, hint)


def _meta_matches_type(meta: dict, hint: str) -> bool:
    return _matches_type(meta.get("name", ""), meta.get("mime_type") or meta.get("mimeType", ""), hint)


def _remember_item(user_data: dict, item: nav.IndexedItem) -> None:
    nlp_context.set_state(
        user_data,
        last_item_id=item.id,
        last_item_name=item.name,
        last_item_is_folder=item.is_folder,
        last_item_index=item.full_index,
    )


def _get_last_item(user_data: dict) -> dict | None:
    state = nlp_context.get_state(user_data)
    if state.get("last_item_id"):
        return state
    return None


def interpret_intent(text: str) -> intent_types.Intent:
    raw = text.strip()
    normalized = normalize.normalize_text(raw)
    if not normalized:
        return intent_types.Intent(intent_types.IntentType.UNKNOWN, 0.0, raw_text=raw)
    bulk = _is_bulk_request(normalized)

    if normalized in _START_KEYWORDS:
        return intent_types.Intent(intent_types.IntentType.START, 0.95, raw_text=raw)

    if any(k in normalized for k in _HELP_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.HELP, 0.95, raw_text=raw)

    if any(k in normalized for k in _MENU_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.MENU, 0.95, raw_text=raw)

    if any(k in normalized for k in _TOOL_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.TOOL, 0.95, raw_text=raw)

    if any(k in normalized for k in _LOGIN_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.LOGIN, 0.95, raw_text=raw)

    if any(k in normalized for k in _LOGOUT_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.LOGOUT, 0.95, raw_text=raw)

    if any(k in normalized for k in _EMAIL_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.EMAIL,
            0.9,
            raw_text=raw,
            email=_extract_email(raw),
        )

    if any(k in normalized for k in _VERIFY_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.VERIFY,
            0.9,
            raw_text=raw,
            otp=_extract_otp(raw),
        )

    if any(k in normalized for k in _CANCEL_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.CANCEL, 0.9, raw_text=raw)

    if any(k in normalized for k in _CLEAR_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.CLEAR, 0.9, raw_text=raw)

    if any(k in normalized for k in _BACK_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.BACK, 0.9, raw_text=raw)

    if "current folder" in normalized and any(k in normalized for k in {"show", "list", "browse"}):
        return intent_types.Intent(intent_types.IntentType.BROWSE, 0.9, raw_text=raw)

    if "current folder" in normalized or "current path" in normalized:
        return intent_types.Intent(intent_types.IntentType.PWD, 0.9, raw_text=raw)

    if any(k in normalized for k in _PWD_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.PWD, 0.9, raw_text=raw)

    if any(k in normalized for k in {"inside", "contents"}):
        return intent_types.Intent(intent_types.IntentType.BROWSE, 0.85, raw_text=raw)

    if any(k in normalized for k in _INDEX_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.INDEX, 0.85, raw_text=raw)

    idx = normalize.extract_index(normalized)

    if any(k in normalized for k in _DOWNLOAD_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.DOWNLOAD,
            0.9,
            raw_text=raw,
            index=idx,
            query=normalized,
            bulk=bulk,
        )

    if any(k in normalized for k in _UPLOAD_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.UPLOAD,
            0.85,
            raw_text=raw,
            target_name=_extract_target(normalized),
        )

    if any(k in normalized for k in _DELETE_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.DELETE,
            0.9,
            raw_text=raw,
            index=idx,
            query=normalized,
            needs_confirmation=True,
            bulk=bulk,
        )

    if any(k in normalized for k in _RENAME_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.RENAME, 0.9, raw_text=raw, index=idx, query=normalized, needs_confirmation=True)

    if any(k in normalized for k in _MOVE_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.MOVE,
            0.9,
            raw_text=raw,
            index=idx,
            query=normalized,
            needs_confirmation=True,
            bulk=bulk,
        )

    if any(k in normalized for k in _COPY_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.COPY,
            0.9,
            raw_text=raw,
            index=idx,
            query=normalized,
            needs_confirmation=bulk,
            bulk=bulk,
        )

    if any(k in normalized for k in _SHARE_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.SHARE,
            0.9,
            raw_text=raw,
            index=idx,
            query=normalized,
            needs_confirmation=True,
        )

    if any(k in normalized for k in _ZIP_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.ZIP,
            0.85,
            raw_text=raw,
            query=normalized,
            bulk=bulk,
        )

    if any(k in normalized for k in _MKDIR_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.MKDIR,
            0.85,
            raw_text=raw,
            target_name=_extract_folder_name(raw) or _extract_folder_name(normalized),
        )

    if any(k in normalized for k in _FAVORITES_LIST_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.FAVORITES, 0.9, raw_text=raw)

    if any(k in normalized for k in _UNFAVORITE_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.UNFAVORITE, 0.85, raw_text=raw, index=idx, query=normalized)

    if any(k in normalized for k in _FAVORITE_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.FAVORITE, 0.85, raw_text=raw, index=idx, query=normalized)

    if any(k in normalized for k in _RECENT_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.RECENT, 0.85, raw_text=raw)

    if any(k in normalized for k in _INFO_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.INFO, 0.85, raw_text=raw, index=idx, query=normalized)

    if any(k in normalized for k in _OPEN_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.OPEN_FOLDER,
            0.85,
            raw_text=raw,
            target_name=_extract_target(normalized),
            query=normalized,
        )

    if re.fullmatch(r"(list|browse|show files|show folders|show current folder)", normalized):
        return intent_types.Intent(intent_types.IntentType.BROWSE, 0.85, raw_text=raw)

    if any(k in normalized for k in _SEARCH_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.SEARCH, 0.85, raw_text=raw, query=normalized)

    best_action, score = normalize.best_action_token(normalized)
    if best_action and score >= 80:
        intent = _ACTION_INTENT_MAP.get(best_action)
        if intent:
            return intent_types.Intent(intent, 0.6, raw_text=raw, query=normalized)

    return intent_types.Intent(intent_types.IntentType.UNKNOWN, 0.0, raw_text=raw)


def _resolve_last_item(uid: int, user_data: dict, want_folder: bool | None = None) -> nav.IndexedItem | None:
    state = _get_last_item(user_data)
    if not state:
        return None
    if want_folder is not None and state.get("last_item_is_folder") != want_folder:
        return None
    view = nav.get_active_view(uid)
    if view:
        for item in view.index_map.values():
            if item.id == state.get("last_item_id"):
                return item
    return nav.IndexedItem(
        id=state.get("last_item_id", ""),
        name=state.get("last_item_name", "item"),
        mime_type="",
        is_folder=bool(state.get("last_item_is_folder")),
        parent_index="",
        full_index=state.get("last_item_index", ""),
        path="",
    )


async def _resolve_file_item(uid: int, user_data: dict, intent: intent_types.Intent) -> nav.IndexedItem | None:
    normalized = normalize.normalize_text(intent.raw_text)
    type_hint = _extract_type_hint(normalized)
    if intent.index:
        item = nav.resolve_index(uid, intent.index)
        if item and not item.is_folder:
            return item
    if _mentions_recent(normalized):
        recent = await ds.get_recent_files_async(uid, limit=limits.MAX_RECENT_ITEMS)
        for item in recent:
            if not type_hint or _meta_matches_type(item, type_hint):
                return nav.IndexedItem(
                    id=item.get("id", ""),
                    name=item.get("name", "file"),
                    mime_type=item.get("mimeType", ""),
                    is_folder=False,
                    parent_index="",
                    full_index="",
                    path="Recent",
                )
    if _has_reference_word(normalize.normalize_text(intent.raw_text)) or type_hint:
        item = _resolve_last_item(uid, user_data, want_folder=False)
        if item and (not type_hint or _item_matches_type(item, type_hint)):
            return item
    view = await _ensure_file_view(uid)
    if view:
        files = [i for i in view.index_map.values() if not i.is_folder]
        if type_hint:
            files = [f for f in files if _item_matches_type(f, type_hint)]
        if len(files) == 1:
            return files[0]
    return None


def _resolve_folder_item(uid: int, user_data: dict, intent: intent_types.Intent) -> nav.IndexedItem | None:
    if intent.index:
        item = nav.resolve_index(uid, intent.index)
        if item and item.is_folder:
            return item
    if _has_reference_word(normalize.normalize_text(intent.raw_text)):
        item = _resolve_last_item(uid, user_data, want_folder=True)
        if item:
            return item
    view = nav.get_active_view(uid)
    if view:
        folders = [i for i in view.index_map.values() if i.is_folder]
        if len(folders) == 1:
            return folders[0]
    return None


async def execute_intent(update, context, intent: intent_types.Intent) -> bool:
    """Execute a resolved intent — called by both the copilot layer and the keyword fallback.

    This is the single dispatch point for all intent execution. The copilot
    layer (LLM) and the keyword router both produce an Intent dataclass,
    then hand it here for validated execution.

    Returns True if the intent was handled, False otherwise.
    """
    if not update.message:
        return False
    assert context.user_data is not None
    uid = update.effective_user.id

    # Auth gate — most intents require authentication
    if intent.intent not in {
        intent_types.IntentType.HELP,
        intent_types.IntentType.START,
        intent_types.IntentType.LOGIN,
        intent_types.IntentType.LOGOUT,
        intent_types.IntentType.CLEAR,
        intent_types.IntentType.MENU,
        intent_types.IntentType.TOOL,
        intent_types.IntentType.CANCEL,
        intent_types.IntentType.GREETING,
        intent_types.IntentType.OFF_TOPIC,
    }:
        if not models.get_user(uid):
            await update.message.reply_text(formatter.login_required())
            return True

    if intent.intent == intent_types.IntentType.HELP:
        await update.message.reply_text(
            formatter.tools_menu(),
            reply_markup=ui.back_to_menu_keyboard(),
        )
        return True

    if intent.intent == intent_types.IntentType.START:
        await bot_commands.cmd_start(update, context)
        return True

    if intent.intent == intent_types.IntentType.MENU:
        await bot_commands.cmd_menu(update, context)
        return True

    if intent.intent == intent_types.IntentType.TOOL:
        await bot_commands.cmd_tool(update, context)
        return True

    if intent.intent == intent_types.IntentType.LOGIN:
        await bot_commands.cmd_start(update, context)
        return True

    if intent.intent == intent_types.IntentType.LOGOUT:
        await _handle_logout(update)
        return True

    if intent.intent == intent_types.IntentType.CLEAR:
        await _handle_clear(update, context)
        return True

    if intent.intent == intent_types.IntentType.EMAIL:
        await _handle_email(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.VERIFY:
        await _handle_verify(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.CANCEL:
        await _handle_cancel(update, context)
        return True

    if intent.intent == intent_types.IntentType.BACK:
        if not nav.pop_folder(uid):
            await update.message.reply_text(formatter.current_path(nav.breadcrumb(uid)))
            return True
        await update.message.reply_text(formatter.current_path(nav.breadcrumb(uid)))
        return True

    if intent.intent == intent_types.IntentType.PWD:
        await update.message.reply_text(formatter.current_path(nav.breadcrumb(uid)))
        return True

    if intent.intent == intent_types.IntentType.INDEX:
        await _handle_index_folder(update, context)
        return True

    if intent.intent == intent_types.IntentType.BROWSE:
        await _handle_browse(update, context)
        return True

    if intent.intent == intent_types.IntentType.SEARCH:
        await _handle_search(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.OPEN_FOLDER:
        await _handle_open_folder(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.DOWNLOAD:
        await _handle_download(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.INFO:
        await _handle_info(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.UPLOAD:
        await _handle_upload_hint(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.ZIP:
        await _handle_zip(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.MKDIR:
        await _handle_mkdir(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.RECENT:
        await _handle_recent(update, context)
        return True

    if intent.intent == intent_types.IntentType.FAVORITES:
        await _handle_favorites(update, context)
        return True

    if intent.intent in {intent_types.IntentType.FAVORITE, intent_types.IntentType.UNFAVORITE}:
        await _handle_favorite_toggle(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.COPY:
        await _handle_copy(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.SHARE:
        await _handle_share(update, context, intent)
        return True

    if intent.intent in {
        intent_types.IntentType.DELETE,
        intent_types.IntentType.RENAME,
        intent_types.IntentType.MOVE,
    }:
        if intent.bulk:
            await _handle_bulk_action(update, context, intent)
        else:
            await _handle_sensitive(update, context, intent)
        return True

    return False


async def handle_nlp_message(update, context) -> bool:
    """Keyword-based NLP fallback — used when the copilot LLM is unavailable."""
    if not update.message or not update.message.text:
        return False
    assert context.user_data is not None
    uid = update.effective_user.id

    if nlp_context.is_expired(context.user_data):
        nlp_context.clear_state(context.user_data)

    text = update.message.text.strip()
    intent = interpret_intent(text)
    normalized = normalize.normalize_text(text)

    if intent.intent == intent_types.IntentType.UNKNOWN or intent.confidence < _LOW_CONFIDENCE:
        await update.message.reply_text(formatter.nlp_ambiguous_action())
        return True

    if intent.confidence < _MEDIUM_CONFIDENCE:
        suggestions = _suggest_action_examples(normalized)
        if suggestions:
            await update.message.reply_text(formatter.nlp_action_suggestions(suggestions))
        else:
            await update.message.reply_text(formatter.nlp_ambiguous_action())
        return True

    return await execute_intent(update, context, intent)



async def _handle_login(update) -> None:
    uid = update.effective_user.id
    if models.get_user(uid):
        await update.message.reply_text(
            formatter.welcome_authenticated(),
            reply_markup=ui.post_login_keyboard(),
        )
        return
    try:
        url = drive_auth.get_auth_url(uid)
    except FileNotFoundError:
        await update.message.reply_text(
            formatter.error("OAuth credentials not configured.", "Contact the bot administrator.")
        )
        return
    await update.message.reply_text(
        formatter.welcome_unauthenticated(),
        reply_markup=ui.login_keyboard(url),
    )


async def _handle_logout(update) -> None:
    uid = update.effective_user.id
    if not models.get_user(uid):
        await update.message.reply_text(formatter.login_required())
        return
    drive_auth.revoke_token(uid)
    models.delete_user(uid)
    nav.clear_user(uid)
    await update.message.reply_text(formatter.logout_successful())


async def _handle_clear(update, context) -> None:
    assert update.effective_chat is not None
    if not update.message:
        return
    chat_id = update.effective_chat.id
    msg_id = update.message.message_id
    deleted = 0
    for i in range(msg_id, max(msg_id - 50, 0), -1):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=i)
            deleted += 1
        except Exception:
            continue
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🧹 Cleared {deleted} messages.",
    )


async def _handle_search(update, context, intent: intent_types.Intent) -> None:
    uid = update.effective_user.id
    query = intent.query or ""
    results = indexed_search.search_index(uid, query)
    if not results:
        suggestions = indexed_search.suggest_files(uid, query)
        if suggestions:
            labels = [s["name"] for s in suggestions]
            await update.message.reply_text(formatter.nlp_suggestions("Closest Matches", labels))
            _set_suggestion_view(uid, suggestions, label_prefix="Match")
            return
        await update.message.reply_text(formatter.nlp_no_results(query))
        return

    index_map: dict[str, nav.IndexedItem] = {}
    for i, item in enumerate(results, 1):
        idx = str(i)
        index_map[idx] = nav.IndexedItem(
            id=item["file_id"],
            name=item["name"],
            mime_type=item.get("mime_type") or "",
            is_folder=False,
            parent_index="",
            full_index=idx,
            is_shortcut=False,
            shortcut_target_id=None,
            shortcut_target_mime_type=None,
            path=f"Search: {query}",
        )
    nav.set_active_view(uid, "search", index_map, metadata={"keyword": query})
    await update.message.reply_text(
        formatter.search_results_indexed(query, index_map),
        reply_markup=ui.back_to_menu_keyboard(),
    )


async def _handle_open_folder(update, context, intent: intent_types.Intent) -> None:
    uid = update.effective_user.id
    assert context.user_data is not None
    if intent.index:
        item = nav.resolve_index(uid, intent.index)
    else:
        item = _resolve_folder_item(uid, context.user_data, intent)
    if item and item.is_folder:
        target_id = item.shortcut_target_id if item.is_shortcut and item.shortcut_target_id else item.id
        if nav.is_in_stack(uid, target_id):
            await update.message.reply_text(
                formatter.error("Navigation loop detected.", "Folder is already in your path.")
            )
            return
        nav.push_folder(uid, target_id, item.name)
        _remember_item(context.user_data, item)
        await _handle_browse(update, context)
        return
    target = intent.target_name or _strip_action_words(normalize.normalize_text(intent.raw_text), extra=_OPEN_KEYWORDS)
    if not target:
        await update.message.reply_text(formatter.nlp_clarify("Which folder should I open?"))
        return
    candidates = await _folder_candidates(uid)
    if not candidates:
        await update.message.reply_text(
            formatter.error("No folders found here.", "Say \"show what's inside\" to refresh the list.")
        )
        return
    item, labels = _select_confident_match(target, candidates)
    if not item:
        if labels:
            await update.message.reply_text(formatter.nlp_suggestions("Closest Folders", labels))
            _set_folder_suggestion_view(uid, [candidates[m] for m in labels if m in candidates])
            return
        await update.message.reply_text(formatter.error("No matching folder found."))
        return
    if item.is_shortcut and item.shortcut_target_id:
        target_id = item.shortcut_target_id
    else:
        target_id = item.id
    if nav.is_in_stack(uid, target_id):
        await update.message.reply_text(formatter.error("Navigation loop detected.", "Folder is already in your path."))
        return
    nav.push_folder(uid, target_id, item.name)
    _remember_item(context.user_data, item)
    await _handle_browse(update, context)


async def _handle_download(update, context, intent: intent_types.Intent) -> None:
    uid = update.effective_user.id
    assert context.user_data is not None
    if intent.bulk:
        await _handle_bulk_zip(update, context, intent)
        return
    index = intent.index
    if not index:
        resolved = await _resolve_file_item(uid, context.user_data, intent)
        if resolved:
            await _download_item(update, context, resolved)
            _remember_item(context.user_data, resolved)
            return
        candidates = await _file_candidates(uid)
        if not candidates:
            await update.message.reply_text(
                formatter.error("Which file?", "Say 'download 1' or 'download the second one'.")
            )
            return
        query = _strip_action_words(normalize.normalize_text(intent.raw_text))
        item, labels = _select_confident_match(query, candidates)
        if item:
            await _download_item(update, context, item)
            return
        if labels:
            await update.message.reply_text(formatter.nlp_suggestions("Closest Files", labels))
            _set_file_suggestion_view(uid, [candidates[m] for m in labels if m in candidates])
            return
        await update.message.reply_text(
            formatter.error("Which file?", "Say 'download 1' or 'download the second one'.")
        )
        return
    item = nav.resolve_index(uid, index)
    if not item or item.is_folder:
        await update.message.reply_text(formatter.error("Invalid file selection."))
        return
    await _download_item(update, context, item)
    _remember_item(context.user_data, item)


async def _download_item(update, context, item: nav.IndexedItem) -> None:
    uid = update.effective_user.id
    if not await _require_stepup_nlp(update, context, "download files"):
        return
    if await anomaly_detection.check_anomaly(uid, "download"):
        await update.message.reply_text(formatter.error("Unusual activity detected."))
        return
    meta = await ds.get_file_metadata_async(uid, item.id)
    size_raw = int(meta["size"]) if meta.get("size") else 0
    if size_raw > 0 and size_raw > ds.MAX_DOWNLOAD_BYTES:
        await update.message.reply_text(
            formatter.download_too_large(
                meta.get("name", item.name),
                parser_utils.human_size(size_raw),
                meta.get("webViewLink", ""),
                meta.get("webContentLink", ""),
            )
        )
        return
    manager = get_task_manager(context)
    if not manager:
        await update.message.reply_text(formatter.error("Background queue unavailable."))
        return
    assert update.effective_chat is not None
    await manager.enqueue_download(
        telegram_id=uid,
        chat_id=update.effective_chat.id,
        file_id=item.id,
        filename=meta.get("name", item.name),
        size_str="Unknown" if not size_raw else parser_utils.human_size(size_raw),
    )


async def _handle_info(update, context, intent: intent_types.Intent) -> None:
    uid = update.effective_user.id
    assert context.user_data is not None
    index = intent.index
    if not index:
        item = await _resolve_file_item(uid, context.user_data, intent)
        if not item:
            await update.message.reply_text(formatter.error("Which file?", "Say 'details of 1'."))
            return
    else:
        item = nav.resolve_index(uid, index)
    if not item:
        await update.message.reply_text(formatter.error("Invalid selection."))
        return
    meta = await ds.get_file_metadata_async(uid, item.id)
    meta["_path"] = item.path
    is_fav = models.is_favorite(uid, item.id)
    await update.message.reply_text(
        formatter.file_info(meta),
        reply_markup=ui.file_actions_keyboard(item.id, is_fav),
    )
    _remember_item(context.user_data, item)


async def _handle_upload_hint(update, context, intent: intent_types.Intent) -> None:
    assert context.user_data is not None
    target = intent.target_name
    if target:
        context.user_data["pending_upload_target"] = target
        await update.message.reply_text(
            formatter.success("Upload Target Set", target),
        )
        return
    await update.message.reply_text(
        formatter.upload_mode_enabled()
    )


def _build_index_map(uid: int, files: list[dict], label: str) -> dict[str, nav.IndexedItem]:
    index_map: dict[str, nav.IndexedItem] = {}
    for i, item in enumerate(files, 1):
        idx = str(i)
        index_map[idx] = nav.IndexedItem(
            id=item.get("id", ""),
            name=item.get("name", "file"),
            mime_type=item.get("mimeType", ""),
            is_folder=False,
            parent_index="",
            full_index=idx,
            path=label,
        )
    return index_map


async def _handle_zip(update, context, intent: intent_types.Intent) -> None:
    uid = update.effective_user.id
    keyword = _strip_action_words(normalize.normalize_text(intent.raw_text))
    keyword = validators.normalize_keyword(keyword, limits.MAX_SEARCH_LEN)
    if not keyword:
        await update.message.reply_text(
            formatter.error("Missing keyword.", "Try: zip dbms notes")
        )
        return
    manager = get_task_manager(context)
    if not manager:
        await update.message.reply_text(formatter.error("Background queue unavailable."))
        return
    assert update.effective_chat is not None
    await manager.enqueue_zip(uid, update.effective_chat.id, keyword)


async def _handle_mkdir(update, context, intent: intent_types.Intent) -> None:
    assert context.user_data is not None
    uid = update.effective_user.id
    name = intent.target_name or _strip_action_words(normalize.normalize_text(intent.raw_text))
    name = validators.sanitize_text(name)
    if not name:
        context.user_data["pending_action"] = {"intent": "mkdir", "awaiting_name": True}
        await update.message.reply_text(formatter.nlp_clarify("What should the folder be named?"))
        return
    created = await ds.create_folder_async(uid, name, nav.current_folder_id(uid))
    await update.message.reply_text(formatter.success("Folder Created", created.get("name"), nav.breadcrumb(uid)))


async def _handle_recent(update, context) -> None:
    uid = update.effective_user.id
    recent = await ds.get_recent_files_async(uid, limit=limits.MAX_RECENT_ITEMS)
    if not recent:
        await update.message.reply_text(formatter.recent_results({}))
        return
    index_map = _build_index_map(uid, recent, "Recent")
    nav.set_active_view(uid, "recent", index_map)
    await update.message.reply_text(
        formatter.recent_results(index_map),
        reply_markup=ui.back_to_menu_keyboard(),
    )


async def _handle_favorites(update, context) -> None:
    uid = update.effective_user.id
    favorites = models.get_favorites(uid)[: limits.MAX_FAVORITES_ITEMS]
    if not favorites:
        await update.message.reply_text(formatter.favorites_results({}))
        return
    files: list[dict] = []
    for fid in favorites:
        try:
            files.append(await ds.get_file_metadata_async(uid, fid))
        except Exception:
            continue
    index_map = _build_index_map(uid, files, "Favorites")
    nav.set_active_view(uid, "favorites", index_map)
    await update.message.reply_text(
        formatter.favorites_results(index_map),
        reply_markup=ui.back_to_menu_keyboard(),
    )


async def _handle_favorite_toggle(update, context, intent: intent_types.Intent) -> None:
    assert context.user_data is not None
    uid = update.effective_user.id
    item = await _resolve_file_item(uid, context.user_data, intent)
    if not item:
        await update.message.reply_text(formatter.error("Which file?", "Say 'favorite 2'."))
        return
    if intent.intent == intent_types.IntentType.UNFAVORITE:
        models.remove_favorite(uid, item.id)
        await update.message.reply_text(formatter.success("Removed from Favorites", item.name))
    else:
        models.add_favorite(uid, item.id)
        await update.message.reply_text(formatter.success("Added to Favorites", item.name))
    _remember_item(context.user_data, item)


async def _handle_copy(update, context, intent: intent_types.Intent) -> None:
    assert context.user_data is not None
    uid = update.effective_user.id
    if intent.bulk:
        await _handle_bulk_action(update, context, intent)
        return
    item = await _resolve_file_item(uid, context.user_data, intent)
    if not item:
        await update.message.reply_text(formatter.error("Which file?", "Say 'copy 2'."))
        return
    target_name = _extract_target(intent.raw_text)
    dest_id = nav.current_folder_id(uid)
    dest_name = nav.current_folder_name(uid)
    if target_name:
        candidates = await _folder_candidates(uid)
        if not candidates:
            await update.message.reply_text(formatter.error("No folders found here.", "Say \"show what's inside\" to refresh the list."))
            return
        dest, labels = _select_confident_match(target_name, candidates)
        if not dest:
            await update.message.reply_text(formatter.nlp_suggestions("Closest Folders", labels or list(candidates.keys())[:5]))
            return
        if not dest:
            await update.message.reply_text(formatter.error("Destination not found."))
            return
        dest_id = dest.shortcut_target_id if dest.is_shortcut and dest.shortcut_target_id else dest.id
        dest_name = dest.name
    new_name = _extract_copy_name(intent.raw_text)
    copied = await ds.copy_file_async(uid, item.id, dest_id, new_name)
    await update.message.reply_text(
        formatter.success("Copied", copied.get("name", item.name), dest_name)
    )
    _remember_item(context.user_data, item)


async def _handle_share(update, context, intent: intent_types.Intent) -> None:
    assert context.user_data is not None
    uid = update.effective_user.id
    item = await _resolve_file_item(uid, context.user_data, intent)
    if not item:
        await update.message.reply_text(formatter.error("Which file?", "Say 'share 2'."))
        return
    if not await _require_share_permission(update, uid, item.id):
        return
    context.user_data["pending_action"] = {
        "intent": intent.intent.value,
        "file_id": item.id,
        "name": item.name,
        "share_role": "reader",
    }
    await update.message.reply_text(
        formatter.confirm_action("Share", item.name)
    )
    _remember_item(context.user_data, item)


async def _handle_bulk_action(update, context, intent: intent_types.Intent) -> None:
    assert context.user_data is not None
    uid = update.effective_user.id
    query = _strip_action_words(normalize.normalize_text(intent.raw_text))
    items: list[dict] = []
    if query:
        results = indexed_search.search_index(uid, query)
        total = len(results)
        items = results[: limits.MAX_BULK_ITEMS]
        if total > limits.MAX_BULK_ITEMS:
            await update.message.reply_text(
                formatter.error("Too many files for bulk action.", "Narrow your request.")
            )
            return
    else:
        view = await _ensure_file_view(uid)
        if view:
            candidates = [
                {"file_id": item.id, "name": item.name, "mime_type": item.mime_type}
                for item in view.index_map.values()
                if not item.is_folder
            ]
            if len(candidates) > limits.MAX_BULK_ITEMS:
                await update.message.reply_text(
                    formatter.error("Too many files for bulk action.", "Narrow your request.")
                )
                return
            items = candidates[: limits.MAX_BULK_ITEMS]
    if not items:
        await update.message.reply_text(formatter.error("No matching files found."))
        return
    action_map = {
        intent_types.IntentType.DELETE: "bulk_delete",
        intent_types.IntentType.MOVE: "bulk_move",
        intent_types.IntentType.COPY: "bulk_copy",
    }
    action = action_map.get(intent.intent)
    if not action:
        await update.message.reply_text(formatter.error("Unsupported bulk action."))
        return
    target = _extract_target(intent.raw_text)
    pending = {
        "intent": action,
        "items": items,
        "count": len(items),
        "target_name": target,
    }
    if action in {"bulk_move", "bulk_copy"} and not target:
        pending["awaiting_target"] = True
        context.user_data["pending_action"] = pending
        prompt = "Where should I move them?" if action == "bulk_move" else "Where should I copy them?"
        await update.message.reply_text(formatter.nlp_clarify(prompt))
        return
    context.user_data["pending_action"] = pending
    preview = [item.get("name", "file") for item in items[:3]]
    await update.message.reply_text(formatter.bulk_confirm(intent.intent.value.capitalize(), len(items), preview))


async def _handle_bulk_zip(update, context, intent: intent_types.Intent) -> None:
    assert context.user_data is not None
    uid = update.effective_user.id
    keyword = _strip_action_words(normalize.normalize_text(intent.raw_text))
    keyword = validators.normalize_keyword(keyword, limits.MAX_SEARCH_LEN)
    if not keyword:
        await update.message.reply_text(
            formatter.error("Missing keyword.", "Try: download all dbms notes")
        )
        return
    results = indexed_search.search_index(uid, keyword)
    if not results:
        await update.message.reply_text(formatter.error("No matching files found."))
        return
    if len(results) > limits.MAX_BULK_ITEMS:
        await update.message.reply_text(
            formatter.error("Too many files for bulk download.", "Narrow your request.")
        )
        return
    context.user_data["pending_action"] = {
        "intent": "bulk_zip",
        "keyword": keyword,
        "count": len(results),
    }
    preview = [item.get("name", "file") for item in results[:3]]
    await update.message.reply_text(formatter.bulk_confirm("Download", len(results), preview))


async def _handle_sensitive(update, context, intent: intent_types.Intent) -> None:
    assert context.user_data is not None
    uid = update.effective_user.id
    index = intent.index
    if not index:
        item = await _resolve_file_item(uid, context.user_data, intent)
        if not item:
            await update.message.reply_text(formatter.nlp_clarify("Which item?"))
            return
    else:
        item = nav.resolve_index(uid, index)
    if not item:
        await update.message.reply_text(formatter.error("Invalid selection."))
        return
    pending = {
        "intent": intent.intent.value,
        "file_id": item.id,
        "name": item.name,
        "index": index or item.full_index,
    }

    if intent.intent == intent_types.IntentType.RENAME:
        new_name = _extract_rename_name(intent.raw_text)
        if not new_name:
            pending["awaiting_name"] = True
            context.user_data["pending_action"] = pending
            await update.message.reply_text(
                formatter.nlp_clarify("What should I rename it to?")
            )
            return
        pending["new_name"] = new_name

    if intent.intent == intent_types.IntentType.MOVE:
        target = _extract_target(intent.raw_text)
        if not target:
            pending["awaiting_target"] = True
            context.user_data["pending_action"] = pending
            await update.message.reply_text(
                formatter.nlp_clarify("Where should I move it?")
            )
            return
        pending["target_name"] = target

    context.user_data["pending_action"] = pending
    await update.message.reply_text(
        formatter.confirm_action(intent.intent.value.capitalize(), item.name)
    )
    _remember_item(context.user_data, item)


async def _handle_index_folder(update, context) -> None:
    uid = update.effective_user.id
    fid = nav.current_folder_id(uid)
    listing = await ds.list_directory_async(uid, parent_id=fid)
    tasks = []
    for f in listing.files:
        file_id = f.get("id")
        if not file_id:
            continue
        name = f.get("name") or "file"
        mime = f.get("mimeType", "")
        parent_id = fid
        indexer.upsert_metadata(uid, file_id, name, mime, parent_id, None, None)
        tasks.append(file_id)
    manager = get_task_manager(context)
    if not manager:
        await update.message.reply_text(formatter.error("Background queue unavailable."))
        return
    for file_id in tasks:
        await manager.enqueue_index(uid, file_id)
    await update.message.reply_text(
        formatter.success("Indexing Started", nav.breadcrumb(uid))
    )


async def _handle_browse(update, context) -> None:
    uid = update.effective_user.id
    fid = nav.current_folder_id(uid)
    listing = await ds.list_directory_async(uid, parent_id=fid)
    folders = listing.folders
    files = listing.files
    for item in files:
        indexer.upsert_metadata(
            uid,
            item.get("id", ""),
            item.get("name", "file"),
            item.get("mimeType"),
            fid,
            int(item.get("size") or 0) if item.get("size") else None,
            None,
        )
    index_map = nav.build_flat_index_map(uid, folders, files)
    nav.set_active_view(uid, "folder", index_map, metadata={"folder_id": fid})
    text = formatter.directory_listing(nav.breadcrumb(uid), index_map, folders, files)
    await update.message.reply_text(text, reply_markup=ui.browse_keyboard(is_root=(fid == "root")))


def _set_suggestion_view(uid: int, suggestions: list[dict], label_prefix: str) -> None:
    index_map: dict[str, nav.IndexedItem] = {}
    for i, item in enumerate(suggestions, 1):
        idx = str(i)
        index_map[idx] = nav.IndexedItem(
            id=item["file_id"],
            name=item["name"],
            mime_type=item.get("mime_type") or "",
            is_folder=False,
            parent_index="",
            full_index=idx,
            is_shortcut=False,
            shortcut_target_id=None,
            shortcut_target_mime_type=None,
            path=f"{label_prefix} Suggestions",
        )
    nav.set_active_view(uid, "nlp_suggestions", index_map)


def _set_file_suggestion_view(uid: int, items: list[nav.IndexedItem]) -> None:
    index_map: dict[str, nav.IndexedItem] = {}
    for i, item in enumerate(items, 1):
        idx = str(i)
        index_map[idx] = nav.IndexedItem(
            id=item.id,
            name=item.name,
            mime_type=item.mime_type,
            is_folder=False,
            parent_index="",
            full_index=idx,
            is_shortcut=False,
            shortcut_target_id=None,
            shortcut_target_mime_type=None,
            path="File Suggestions",
        )
    nav.set_active_view(uid, "nlp_file_suggestions", index_map)


def _set_folder_suggestion_view(uid: int, items: list[nav.IndexedItem]) -> None:
    index_map: dict[str, nav.IndexedItem] = {}
    for i, item in enumerate(items, 1):
        idx = str(i)
        index_map[idx] = nav.IndexedItem(
            id=item.id,
            name=item.name,
            mime_type=item.mime_type,
            is_folder=True,
            parent_index="",
            full_index=idx,
            is_shortcut=item.is_shortcut,
            shortcut_target_id=item.shortcut_target_id,
            shortcut_target_mime_type=item.shortcut_target_mime_type,
            path="Folder Suggestions",
        )
    nav.set_active_view(uid, "nlp_folder_suggestions", index_map)


def _extract_after_to(text: str) -> str | None:
    match = re.search(r"\bto\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


async def _require_stepup_nlp(update, context, action_label: str) -> bool:
    assert context.user_data is not None
    uid = update.effective_user.id
    result = await stepup_auth.request_verification(uid, action_label)
    status = result.get("status")
    if status == "verified":
        context.user_data.pop("awaiting_email", None)
        context.user_data.pop("awaiting_otp", None)
        context.user_data.pop("pending_stepup_action", None)
        return True
    if status == "no_email":
        context.user_data["awaiting_email"] = True
        context.user_data["pending_stepup_action"] = action_label
        await update.message.reply_text(
            formatter.stepup_email_required(action_label),
            reply_markup=ui.stepup_email_entry_keyboard(),
        )
        return False
    if status == "email_failed":
        await update.message.reply_text(formatter.stepup_email_failed())
        return False
    if status == "sent":
        context.user_data["awaiting_otp"] = True
        context.user_data["pending_stepup_action"] = action_label
        await update.message.reply_text(
            formatter.stepup_code_sent(action_label, result.get("email", ""), result.get("ttl", 10)),
            reply_markup=ui.stepup_resend_keyboard(action_label),
        )
        return False
    if status == "cooldown":
        context.user_data["awaiting_otp"] = True
        context.user_data["pending_stepup_action"] = action_label
        await update.message.reply_text(
            formatter.stepup_code_pending(action_label, result.get("email", ""), result.get("retry_after", 60)),
            reply_markup=ui.stepup_resend_keyboard(action_label),
        )
        return False
    await update.message.reply_text(formatter.error("Verification required.", "Reply with the 6-digit code."))
    return False


async def _require_share_permission(update, uid: int, file_id: str) -> bool:
    try:
        meta = await ds.get_file_metadata_async(uid, file_id)
    except Exception:
        await update.message.reply_text(formatter.error("Unable to verify sharing permissions."))
        return False
    capabilities = meta.get("capabilities") or {}
    can_share = capabilities.get("canShare")
    if can_share is False:
        await update.message.reply_text(formatter.error("You don't have permission to share this item."))
        return False
    if can_share is None:
        await update.message.reply_text(formatter.error("Unable to verify sharing permissions."))
        return False
    return True


async def handle_pending_action(update, context) -> bool:
    if not update.message or not update.message.text:
        return False
    assert context.user_data is not None
    pending = context.user_data.get("pending_action")
    if not pending:
        return False
    text = update.message.text.strip().lower()
    if pending.get("intent") == "mkdir" and pending.get("awaiting_name"):
        folder_name = validators.sanitize_text(update.message.text.strip())
        if not folder_name:
            await update.message.reply_text(formatter.error("Folder name cannot be empty."))
            return True
        created = await ds.create_folder_async(update.effective_user.id, folder_name, nav.current_folder_id(update.effective_user.id))
        context.user_data.pop("pending_action", None)
        await update.message.reply_text(formatter.success("Folder Created", created.get("name"), nav.breadcrumb(update.effective_user.id)))
        return True
    if pending.get("awaiting_name"):
        pending["new_name"] = validators.sanitize_text(update.message.text.strip())
        pending.pop("awaiting_name", None)
        context.user_data["pending_action"] = pending
        await update.message.reply_text(
            formatter.confirm_action("Rename", pending.get("name", "file"))
        )
        return True
    if pending.get("awaiting_target"):
        pending["target_name"] = update.message.text.strip()
        pending.pop("awaiting_target", None)
        context.user_data["pending_action"] = pending
        if pending.get("intent", "").startswith("bulk_"):
            preview = [item.get("name", "file") for item in pending.get("items", [])[:3]]
            label = "Move" if pending.get("intent") == "bulk_move" else "Copy"
            await update.message.reply_text(
                formatter.bulk_confirm(label, pending.get("count", len(preview)), preview)
            )
        else:
            label = "Move" if pending.get("intent") == "move" else "Copy"
            await update.message.reply_text(formatter.confirm_action(label, pending.get("name", "file")))
        return True
    if text in {"cancel", "no", "stop"}:
        context.user_data.pop("pending_action", None)
        await update.message.reply_text(formatter.success("Cancelled"))
        return True
    if text not in {"confirm", "yes", "ok", "proceed"}:
        await update.message.reply_text(formatter.nlp_clarify("Reply with confirm or cancel."))
        return True

    await _execute_pending_action(update, context, pending)
    context.user_data.pop("pending_action", None)
    return True


async def _execute_pending_action(update, context, pending: dict) -> None:
    uid = update.effective_user.id
    action = pending.get("intent")
    if action in {"bulk_delete", "bulk_move", "bulk_copy", "bulk_zip"}:
        items = pending.get("items", [])
        if action == "bulk_zip":
            manager = get_task_manager(context)
            if not manager:
                await update.message.reply_text(formatter.error("Background queue unavailable."))
                return
            keyword = pending.get("keyword", "")
            if not keyword:
                await update.message.reply_text(formatter.error("Missing keyword."))
                return
            assert update.effective_chat is not None
            await manager.enqueue_zip(uid, update.effective_chat.id, keyword)
            return
        if not items:
            await update.message.reply_text(formatter.error("No items to process."))
            return
        if action == "bulk_delete":
            if not await _require_stepup_nlp(update, context, "delete files"):
                return
            for item in items:
                file_id = item.get("file_id")
                if validators.validate_drive_id(file_id, allow_root=False):
                    await ds.delete_file_async(uid, file_id)
            await update.message.reply_text(formatter.success("Deleted", f"{len(items)} items"))
            return
        if action in {"bulk_move", "bulk_copy"}:
            target_name = pending.get("target_name")
            if not target_name:
                await update.message.reply_text(formatter.error("Missing destination folder."))
                return
            candidates = await _folder_candidates(uid)
            if not candidates:
                await update.message.reply_text(
                    formatter.error("No folders found here.", "Say \"show what's inside\" to refresh the list.")
                )
                return
            dest, labels = _select_confident_match(target_name, candidates)
            if not dest:
                await update.message.reply_text(formatter.nlp_suggestions("Closest Folders", labels or list(candidates.keys())[:5]))
                return
            if not dest:
                await update.message.reply_text(formatter.error("Destination not found."))
                return
            dest_id = dest.shortcut_target_id if dest.is_shortcut and dest.shortcut_target_id else dest.id
            for item in items:
                file_id = item.get("file_id")
                if not validators.validate_drive_id(file_id, allow_root=False):
                    continue
                if action == "bulk_move":
                    await ds.move_file_async(uid, file_id, dest_id)
                else:
                    await ds.copy_file_async(uid, file_id, dest_id)
            label = "Moved" if action == "bulk_move" else "Copied"
            await update.message.reply_text(formatter.success(label, f"{len(items)} items", dest.name))
            return

    file_id = pending.get("file_id")
    if not file_id or not validators.validate_drive_id(file_id, allow_root=False):
        await update.message.reply_text(formatter.error("Invalid file reference."))
        return
    if action == intent_types.IntentType.DELETE.value:
        if not await _require_stepup_nlp(update, context, "delete files"):
            return
        await ds.delete_file_async(uid, file_id)
        await update.message.reply_text(formatter.success("Deleted"))
        return
    if action == intent_types.IntentType.RENAME.value:
        new_name = pending.get("new_name")
        if not new_name:
            await update.message.reply_text(formatter.error("Missing new name."))
            return
        updated = await ds.rename_file_async(uid, file_id, new_name)
        await update.message.reply_text(formatter.success("Renamed", updated.get("name")))
        return
    if action == intent_types.IntentType.MOVE.value:
        dest_id = pending.get("dest_id")
        dest_name = pending.get("dest_name")
        if not dest_id:
            target_name = pending.get("target_name")
            if not target_name:
                await update.message.reply_text(formatter.error("Missing destination folder."))
                return
            candidates = await _folder_candidates(uid)
            if not candidates:
                await update.message.reply_text(formatter.error("No folders found here.", "Say \"show what's inside\" to refresh the list."))
                return
            dest, labels = _select_confident_match(target_name, candidates)
            if not dest:
                await update.message.reply_text(formatter.nlp_suggestions("Closest Folders", labels or list(candidates.keys())[:5]))
                return
            if not dest:
                await update.message.reply_text(formatter.error("Destination not found."))
                return
            dest_id = dest.shortcut_target_id if dest.is_shortcut and dest.shortcut_target_id else dest.id
            dest_name = dest.name
        await ds.move_file_async(uid, file_id, dest_id)
        await update.message.reply_text(formatter.success("Moved", pending.get("name"), dest_name))
        return
    if action == intent_types.IntentType.COPY.value:
        target_name = pending.get("target_name")
        dest_id = nav.current_folder_id(uid)
        dest_name = nav.current_folder_name(uid)
        if target_name:
            candidates = await _folder_candidates(uid)
            dest, labels = _select_confident_match(target_name, candidates)
            if not dest:
                await update.message.reply_text(formatter.nlp_suggestions("Closest Folders", labels or list(candidates.keys())[:5]))
                return
            if not dest:
                await update.message.reply_text(formatter.error("Destination not found."))
                return
            dest_id = dest.shortcut_target_id if dest.is_shortcut and dest.shortcut_target_id else dest.id
            dest_name = dest.name
        new_name = pending.get("new_name")
        copied = await ds.copy_file_async(uid, file_id, dest_id, new_name)
        await update.message.reply_text(formatter.success("Copied", copied.get("name"), dest_name))
        return
    if action == intent_types.IntentType.SHARE.value:
        if not await _require_stepup_nlp(update, context, "share files"):
            return
        meta = await ds.create_share_link_async(uid, file_id, pending.get("share_role", "reader"))
        link = meta.get("webViewLink") or meta.get("webContentLink") or ""
        await update.message.reply_text(
            formatter.share_link(meta.get("name", pending.get("name", "file")), link or "Unavailable")
        )
        return
