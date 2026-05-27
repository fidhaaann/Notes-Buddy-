"""
drive/drive_service.py
All Google Drive API interactions.

Security:
  - Query injection prevention via _sanitize_query_value()
  - Download size limits enforced
  - Filename sanitization on uploads and downloads
"""

import io
import logging
import mimetypes
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials

from drive.auth import get_credentials
from db.models import log_file, log_audit

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
_SHARED_DRIVE_PREFIX = "drive:"

_LIST_PAGE_SIZE = 200
_MAX_ITEMS_PER_FOLDER = 2000
_MAX_EXPANDED_FOLDERS = 30

_LIST_FIELDS = (
    "nextPageToken, files(id, name, mimeType, parents, shortcutDetails, size, driveId)"
)


@dataclass
class DirectoryListing:
    folders: list[dict] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    children_map: dict[str, tuple[list[dict], list[dict]]] = field(default_factory=dict)
    error_count: int = 0
    truncated: bool = False
    used_fallback: bool = False

# Max filename length for sanitization
_MAX_FILENAME_LENGTH = 200


def _sanitize_query_value(value: str) -> str:
    """Escape special characters for Google Drive API query strings.

    Prevents query injection by escaping backslashes and single quotes
    in user-supplied values before embedding them in query filters.
    """
    value = value.replace("\\", "\\\\").replace("'", "\\'")
    # V-NEW-05: Strip newlines/carriage returns that could cause unexpected parsing
    value = value.replace("\n", " ").replace("\r", " ")
    return value


def _sanitize_filename(filename: str) -> str:
    """Sanitize a filename for safe use.

    Removes:
      - Path separators (directory traversal)
      - Null bytes
      - Control characters
      - Leading/trailing whitespace and dots

    Truncates to _MAX_FILENAME_LENGTH characters.
    """
    if not filename:
        return "unnamed_file"

    # Remove null bytes
    filename = filename.replace("\x00", "")

    # Strip path — take only the basename
    filename = os.path.basename(filename)

    # Remove control characters (0x00-0x1F, 0x7F)
    filename = re.sub(r'[\x00-\x1f\x7f]', '', filename)

    # Strip leading/trailing dots and whitespace (prevents hidden files, path tricks)
    filename = filename.strip(". \t\n\r")

    # Truncate
    if len(filename) > _MAX_FILENAME_LENGTH:
        name, ext = os.path.splitext(filename)
        filename = name[:_MAX_FILENAME_LENGTH - len(ext)] + ext

    return filename or "unnamed_file"


def _service(telegram_id: int) -> Any:
    """Build an authorized Google Drive API service.

    Returns a dynamically-typed Resource; the type checker cannot resolve
    .files() on it, so the return type is Any.
    """
    creds = get_credentials(telegram_id)
    if creds is None:
        raise PermissionError("User not authenticated. Use /login first.")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _raise_permission_for_http_error(err: HttpError) -> None:
    """Translate auth-related HTTP errors into PermissionError."""
    status = getattr(err, "resp", None)
    if status and status.status in (401, 403):
        raise PermissionError("User not authenticated. Use /login first.") from err
    raise err


def _is_shared_drive_ref(folder_id: str) -> bool:
    return folder_id.startswith(_SHARED_DRIVE_PREFIX)


def _shared_drive_ref(drive_id: str) -> str:
    return f"{_SHARED_DRIVE_PREFIX}{drive_id}"


def _resolve_parent_ref(parent_id: str) -> tuple[str, dict[str, str]]:
    """Resolve a folder reference into a parent ID and list parameters."""
    if _is_shared_drive_ref(parent_id):
        drive_id = parent_id[len(_SHARED_DRIVE_PREFIX):]
        return drive_id, {"driveId": drive_id, "corpora": "drive"}
    return parent_id, {}


def _resolve_parent_for_write(parent_id: str) -> str:
    """Resolve a folder reference into a concrete parent ID for write ops."""
    if _is_shared_drive_ref(parent_id):
        return parent_id[len(_SHARED_DRIVE_PREFIX):]
    return parent_id


def _list_files_paginated(
    svc: Any,
    telegram_id: int,
    folder_id: str,
    q: str,
    fields: str = _LIST_FIELDS,
    page_size: int = _LIST_PAGE_SIZE,
    max_items: int = _MAX_ITEMS_PER_FOLDER,
    extra_params: Optional[dict[str, str]] = None,
) -> tuple[list[dict], bool]:
    items: list[dict] = []
    page_token: Optional[str] = None
    truncated = False
    extra_params = extra_params or {}
    while True:
        try:
            result = svc.files().list(
                q=q,
                fields=fields,
                pageSize=page_size,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                **extra_params,
            ).execute()
        except HttpError as e:
            status = getattr(e, "resp", None)
            logger.warning(
                "drive_api_list_error user=%s folder=%s status=%s",
                telegram_id,
                folder_id,
                getattr(status, "status", None),
            )
            _raise_permission_for_http_error(e)
        files = result.get("files", [])
        if not isinstance(files, list):
            files = []
        items.extend(files)
        if max_items and len(items) >= max_items:
            truncated = True
            items = items[:max_items]
            break
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    if truncated:
        logger.info(
            "drive_list_truncated user=%s folder=%s max_items=%s",
            telegram_id,
            folder_id,
            max_items,
        )
    return items, truncated


def _list_shared_drives(svc: Any, telegram_id: int) -> list[dict]:
    drives: list[dict] = []
    page_token: Optional[str] = None
    while True:
        try:
            result = svc.drives().list(
                pageSize=100,
                pageToken=page_token,
                fields="nextPageToken, drives(id, name)",
            ).execute()
        except HttpError:
            logger.warning(
                "shared_drive_list_failed user=%s; continuing without drives.",
                telegram_id,
            )
            return []
        items = result.get("drives", [])
        if isinstance(items, list):
            drives.extend(items)
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return drives


def _split_items(
    items: list[dict],
    telegram_id: int,
    folder_id: str,
    depth: int,
) -> tuple[list[dict], list[dict], int]:
    folders: list[dict] = []
    files: list[dict] = []
    errors = 0
    for raw in items:
        try:
            mime_type = raw.get("mimeType") or ""
            file_id = raw.get("id")
            if not file_id:
                errors += 1
                logger.warning(
                    "drive_item_missing_id user=%s folder=%s depth=%s mime=%s",
                    telegram_id,
                    folder_id,
                    depth,
                    mime_type,
                )
                continue
            name = raw.get("name") or "Unnamed item"
            shortcut_details = raw.get("shortcutDetails") or {}
            is_shortcut = mime_type == SHORTCUT_MIME or bool(shortcut_details)
            target_id = None
            target_mime = None
            if isinstance(shortcut_details, dict):
                target_id = shortcut_details.get("targetId")
                target_mime = shortcut_details.get("targetMimeType")
            is_folder = mime_type == FOLDER_MIME or (is_shortcut and target_mime == FOLDER_MIME)
            item = {
                "id": file_id,
                "name": name,
                "mimeType": mime_type,
                "isShortcut": is_shortcut,
                "shortcutTargetId": target_id,
                "shortcutTargetMimeType": target_mime,
            }
            if not raw.get("parents"):
                logger.info(
                    "drive_item_orphan user=%s folder=%s depth=%s item=%s mime=%s",
                    telegram_id,
                    folder_id,
                    depth,
                    file_id,
                    mime_type,
                )
            if is_folder:
                folders.append(item)
            else:
                files.append(item)
        except Exception as exc:
            errors += 1
            logger.warning(
                "drive_item_parse_error user=%s folder=%s depth=%s mime=%s error=%s",
                telegram_id,
                folder_id,
                depth,
                raw.get("mimeType") if isinstance(raw, dict) else "",
                exc,
            )
    return folders, files, errors


# ── Folders ───────────────────────────────────────────────────────────────────

def list_folders(telegram_id: int, parent_id: str = "root") -> list[dict]:
    svc = _service(telegram_id)
    parent_ref, extra = _resolve_parent_ref(parent_id)
    q = (
        f"'{parent_ref}' in parents "
        f"and mimeType='{FOLDER_MIME}' "
        f"and trashed=false"
    )
    items, _ = _list_files_paginated(
        svc,
        telegram_id,
        parent_id,
        q,
        fields="files(id, name, mimeType, parents)",
        page_size=100,
        extra_params=extra,
    )
    folders, _, _ = _split_items(items, telegram_id, parent_id, 0)
    return folders


def open_folder(telegram_id: int, folder_name: str, parent_id: Optional[str] = None) -> Optional[dict]:
    """Return the first folder matching folder_name, optionally under parent_id."""
    svc = _service(telegram_id)
    safe_name = _sanitize_query_value(folder_name)
    q = f"mimeType='{FOLDER_MIME}' and name='{safe_name}' and trashed=false"
    if parent_id:
        parent_ref, extra = _resolve_parent_ref(parent_id)
        q = f"'{parent_ref}' in parents and " + q
    else:
        extra = {}
    result = svc.files().list(
        q=q,
        fields="files(id, name)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        **extra,
    ).execute()
    files = result.get("files", [])
    return files[0] if files else None


# ── Files ─────────────────────────────────────────────────────────────────────

def list_files(telegram_id: int, parent_id: str = "root") -> list[dict]:
    svc = _service(telegram_id)
    parent_ref, extra = _resolve_parent_ref(parent_id)
    q = (
        f"'{parent_ref}' in parents "
        f"and trashed=false"
    )
    items, _ = _list_files_paginated(
        svc,
        telegram_id,
        parent_id,
        q,
        fields="files(id, name, mimeType, size, parents, shortcutDetails)",
        page_size=100,
        extra_params=extra,
    )
    _, files, _ = _split_items(items, telegram_id, parent_id, 0)
    return files


def list_directory(
    telegram_id: int,
    parent_id: str = "root",
    expand_children: bool = True,
    depth_limit: int = 1,
) -> DirectoryListing:
    svc = _service(telegram_id)
    listing = DirectoryListing()
    parent_ref, extra = _resolve_parent_ref(parent_id)
    q = f"'{parent_ref}' in parents and trashed=false"

    items, truncated = _list_files_paginated(
        svc,
        telegram_id,
        parent_id,
        q,
        fields=_LIST_FIELDS,
        extra_params=extra,
    )
    listing.truncated = truncated
    folders, files, errors = _split_items(items, telegram_id, parent_id, 0)
    listing.folders = folders
    listing.files = files
    listing.error_count += errors

    if parent_id == "root":
        shared = _list_shared_drives(svc, telegram_id)
        for drive in shared:
            drive_id = drive.get("id")
            if not drive_id:
                continue
            listing.folders.append(
                {
                    "id": _shared_drive_ref(drive_id),
                    "name": drive.get("name") or "Shared Drive",
                    "mimeType": FOLDER_MIME,
                    "isShortcut": False,
                    "isSharedDrive": True,
                    "shortcutTargetId": None,
                    "shortcutTargetMimeType": None,
                }
            )

    if expand_children and depth_limit > 0 and listing.folders:
        try:
            children_map, child_errors, child_truncated, used_fallback = _expand_children_map(
                svc,
                telegram_id,
                listing.folders,
                depth_limit=depth_limit,
            )
            listing.children_map = children_map
            listing.error_count += child_errors
            listing.truncated = listing.truncated or child_truncated
            listing.used_fallback = listing.used_fallback or used_fallback
        except Exception:
            listing.used_fallback = True
            listing.children_map = {}
            logger.exception(
                "drive_traversal_fallback user=%s folder=%s depth=%s",
                telegram_id,
                parent_id,
                depth_limit,
            )

    return listing


def _expand_children_map(
    svc: Any,
    telegram_id: int,
    folders: list[dict],
    depth_limit: int,
) -> tuple[dict[str, tuple[list[dict], list[dict]]], int, bool, bool]:
    children_map: dict[str, tuple[list[dict], list[dict]]] = {}
    errors = 0
    truncated = False
    used_fallback = False
    visited: set[str] = set()

    if len(folders) > _MAX_EXPANDED_FOLDERS:
        used_fallback = True
    for f in folders[:_MAX_EXPANDED_FOLDERS]:
        if f.get("isShortcut") or f.get("isSharedDrive"):
            continue
        folder_id = f.get("id")
        if not folder_id or folder_id in visited:
            continue
        visited.add(folder_id)
        try:
            q = f"'{folder_id}' in parents and trashed=false"
            items, was_truncated = _list_files_paginated(
                svc,
                telegram_id,
                folder_id,
                q,
                fields=_LIST_FIELDS,
            )
            sub_folders, sub_files, sub_errors = _split_items(items, telegram_id, folder_id, 1)
            errors += sub_errors
            truncated = truncated or was_truncated
            if sub_folders or sub_files:
                children_map[folder_id] = (sub_folders, sub_files)
        except Exception as exc:
            errors += 1
            logger.warning(
                "drive_traversal_child_error user=%s folder=%s depth=%s mime=%s error=%s",
                telegram_id,
                folder_id,
                depth_limit,
                FOLDER_MIME,
                exc,
            )
            continue
    return children_map, errors, truncated, used_fallback

def search_files(telegram_id: int, keyword: str) -> list[dict]:
    svc = _service(telegram_id)
    safe_keyword = _sanitize_query_value(keyword)
    q = f"name contains '{safe_keyword}' and trashed=false"
    items, _ = _list_files_paginated(
        svc,
        telegram_id,
        "search",
        q,
        fields="files(id, name, mimeType, shortcutDetails)",
        page_size=100,
        max_items=500,
    )
    cleaned: list[dict] = []
    for raw in items:
        if not raw.get("id"):
            continue
        raw["name"] = raw.get("name") or "Unnamed item"
        cleaned.append(raw)
    return cleaned


def get_recent_files(telegram_id: int, limit: int = 10) -> list[dict]:
    svc = _service(telegram_id)
    result = svc.files().list(
        orderBy="viewedByMeTime desc",
        pageSize=limit,
        fields="files(id, name, mimeType, shortcutDetails)",
        q="trashed=false",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return result.get("files", [])


def get_file_metadata(telegram_id: int, file_id: str) -> dict:
    svc = _service(telegram_id)
    return svc.files().get(
        fileId=file_id,
        fields="id, name, mimeType, size, createdTime, modifiedTime, parents, webViewLink, webContentLink, shortcutDetails",
        supportsAllDrives=True,
    ).execute()


def move_file(telegram_id: int, file_id: str, new_parent_id: str) -> dict:
    svc = _service(telegram_id)
    file = svc.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
    previous_parents = ",".join(file.get("parents", []))
    parent_ref = _resolve_parent_for_write(new_parent_id)
    result = svc.files().update(
        fileId=file_id,
        addParents=parent_ref,
        removeParents=previous_parents,
        fields="id, parents",
        supportsAllDrives=True,
    ).execute()
    log_audit(telegram_id, "move", file_id, f"to parent={new_parent_id}")
    return result


def create_folder(telegram_id: int, name: str, parent_id: str = "root") -> dict:
    svc = _service(telegram_id)
    safe_name = _sanitize_filename(name)
    parent_ref = _resolve_parent_for_write(parent_id)
    file_metadata = {
        "name": safe_name,
        "mimeType": FOLDER_MIME,
        "parents": [parent_ref]
    }
    return svc.files().create(
        body=file_metadata,
        fields="id, name",
        supportsAllDrives=True,
    ).execute()


def upload_file(
    telegram_id: int,
    file_bytes: bytes,
    filename: str,
    parent_id: str = "root",
) -> dict:
    svc = _service(telegram_id)

    # Sanitize filename before upload
    safe_filename = _sanitize_filename(filename)

    mime_type, _ = mimetypes.guess_type(safe_filename)
    mime_type = mime_type or "application/octet-stream"

    parent_ref = _resolve_parent_for_write(parent_id)
    file_metadata = {"name": safe_filename, "parents": [parent_ref]}
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
    uploaded = (
        svc.files()
        .create(body=file_metadata, media_body=media, fields="id, name, mimeType", supportsAllDrives=True)
        .execute()
    )
    log_file(uploaded["id"], uploaded["name"], uploaded.get("mimeType"))
    return uploaded


# Google Workspace MIME types must be exported rather than downloaded directly.
# Maps Google mime → (export mime, file extension)
_GOOGLE_EXPORT_MAP: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document":     ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",  ".docx"),
    "application/vnd.google-apps.spreadsheet":  ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",        ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing":      ("image/png",        ".png"),
    "application/vnd.google-apps.form":         ("application/pdf",  ".pdf"),
    "application/vnd.google-apps.script":       ("application/json", ".json"),
}

# Max download size — matches Telegram bot API limit (45 MB safe margin)
MAX_DOWNLOAD_BYTES = 45 * 1024 * 1024


def download_file(telegram_id: int, file_id: str) -> tuple[bytes, str]:
    """
    Return (file_bytes, filename).
    Automatically exports Google Workspace files (Docs, Sheets, Slides, etc.)
    to a compatible Office format instead of attempting a direct binary download.

    Raises ValueError if the download exceeds MAX_DOWNLOAD_BYTES.
    """
    svc  = _service(telegram_id)
    meta = svc.files().get(
        fileId=file_id,
        fields="name, mimeType, shortcutDetails",
        supportsAllDrives=True,
    ).execute()
    filename  = _sanitize_filename(meta["name"])
    mime_type = meta.get("mimeType", "")

    buf = io.BytesIO()

    if mime_type in _GOOGLE_EXPORT_MAP:
        # Google Workspace file — must be exported/converted
        export_mime, ext = _GOOGLE_EXPORT_MAP[mime_type]
        if not filename.endswith(ext):
            filename += ext                         # e.g. "My Doc" → "My Doc.docx"
        request = svc.files().export_media(
            fileId=file_id,
            mimeType=export_mime,
            supportsAllDrives=True,
        )
    else:
        # Regular binary file — direct download
        request = svc.files().get_media(fileId=file_id, supportsAllDrives=True)

    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
        if buf.tell() > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"File exceeds {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB download limit."
            )

    return buf.getvalue(), filename



def rename_file(telegram_id: int, file_id: str, new_name: str) -> dict:
    svc = _service(telegram_id)
    safe_name = _sanitize_filename(new_name)
    result = svc.files().update(
        fileId=file_id,
        body={"name": safe_name},
        fields="id, name",
        supportsAllDrives=True,
    ).execute()
    log_audit(telegram_id, "rename", file_id, f"new_name={safe_name}")
    return result


def delete_file(telegram_id: int, file_id: str) -> None:
    svc = _service(telegram_id)
    svc.files().delete(fileId=file_id, supportsAllDrives=True).execute()
    log_audit(telegram_id, "delete", file_id)


def find_file_by_name(telegram_id: int, name: str) -> Optional[dict]:
    """Find a single non-folder file by exact name."""
    svc = _service(telegram_id)
    safe_name = _sanitize_query_value(name)
    q = f"name='{safe_name}' and mimeType!='{FOLDER_MIME}' and trashed=false"
    result = svc.files().list(
        q=q,
        fields="files(id, name, mimeType)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = result.get("files", [])
    return files[0] if files else None
