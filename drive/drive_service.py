"""
drive/drive_service.py
All Google Drive API interactions.

Security:
  - Query injection prevention via _sanitize_query_value()
  - Download size limits enforced
  - Filename sanitization on uploads and downloads
"""

import asyncio
import io
import logging
import mimetypes
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials

from drive.auth import get_credentials
from db.models import log_file, log_audit
from security import limits, validators

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
_SHARED_DRIVE_PREFIX = "drive:"

_LIST_PAGE_SIZE = 200
_MAX_ITEMS_PER_FOLDER = 2000

_LIST_FIELDS = (
    "nextPageToken, files(id, name, mimeType, parents, shortcutDetails, size, driveId)"
)


@dataclass
class DirectoryListing:
    """Result of loading a single folder (lazy loading)."""
    folders: list[dict] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    error_count: int = 0
    truncated: bool = False

def _sanitize_query_value(value: str) -> str:
    """Escape special characters for Google Drive API query strings."""
    return validators.sanitize_query_value(value)


def _sanitize_filename(filename: str) -> str:
    """Sanitize a filename for safe use."""
    return validators.sanitize_filename(filename)


def _execute_with_retry(request, retries: int = 3, base_backoff: float = 0.5):
    """Execute a Google API request with safe retries for transient errors."""
    for attempt in range(retries):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e, "resp", None)
            code = getattr(status, "status", None)
            if code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(base_backoff * (2 ** attempt))
                continue
            raise


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
    if not validators.validate_drive_id(parent_id, allow_root=True):
        raise ValueError("Invalid folder reference.")
    if _is_shared_drive_ref(parent_id):
        drive_id = parent_id[len(_SHARED_DRIVE_PREFIX):]
        return drive_id, {"driveId": drive_id, "corpora": "drive"}
    return parent_id, {}


def _resolve_parent_for_write(parent_id: str) -> str:
    """Resolve a folder reference into a concrete parent ID for write ops."""
    if not validators.validate_drive_id(parent_id, allow_root=True):
        raise ValueError("Invalid folder reference.")
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
            request = svc.files().list(
                q=q,
                fields=fields,
                pageSize=page_size,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                **extra_params,
            )
            result = _execute_with_retry(request)
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
            request = svc.drives().list(
                pageSize=100,
                pageToken=page_token,
                fields="nextPageToken, drives(id, name)",
            )
            result = _execute_with_retry(request)
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
    request = svc.files().list(
        q=q,
        fields="files(id, name)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        **extra,
    )
    result = _execute_with_retry(request)
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
) -> DirectoryListing:
    """
    Lazy-load a single folder (no child expansion).
    
    This is the core of incremental folder loading:
    - Load ONLY current folder contents
    - Do NOT recursively expand children
    - Failures isolated to this folder only
    - User navigates on-demand with /cd
    """
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
        try:
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
        except Exception as exc:
            logger.warning(
                "shared_drives_list_failed user=%s error=%s",
                telegram_id,
                exc,
            )

    return listing


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
    request = svc.files().list(
        orderBy="viewedByMeTime desc",
        pageSize=limit,
        fields="files(id, name, mimeType, shortcutDetails)",
        q="trashed=false",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    )
    result = _execute_with_retry(request)
    return result.get("files", [])


def get_file_metadata(telegram_id: int, file_id: str) -> dict:
    svc = _service(telegram_id)
    if not validators.validate_drive_id(file_id, allow_root=False):
        raise ValueError("Invalid file reference.")
    request = svc.files().get(
        fileId=file_id,
        fields="id, name, mimeType, size, createdTime, modifiedTime, parents, webViewLink, webContentLink, shortcutDetails",
        supportsAllDrives=True,
    )
    return _execute_with_retry(request)


def move_file(telegram_id: int, file_id: str, new_parent_id: str) -> dict:
    svc = _service(telegram_id)
    if not validators.validate_drive_id(file_id, allow_root=False):
        raise ValueError("Invalid file reference.")
    request = svc.files().get(fileId=file_id, fields="parents", supportsAllDrives=True)
    file = _execute_with_retry(request)
    previous_parents = ",".join(file.get("parents", []))
    parent_ref = _resolve_parent_for_write(new_parent_id)
    request = svc.files().update(
        fileId=file_id,
        addParents=parent_ref,
        removeParents=previous_parents,
        fields="id, parents",
        supportsAllDrives=True,
    )
    result = _execute_with_retry(request)
    log_audit(telegram_id, "move", file_id, f"to parent={new_parent_id}")
    return result


def create_folder(telegram_id: int, name: str, parent_id: str = "root") -> dict:
    svc = _service(telegram_id)
    safe_name = _sanitize_filename(name)
    parent_ref = _resolve_parent_for_write(parent_id)
    if not safe_name:
        raise ValueError("Folder name cannot be empty.")
    file_metadata = {
        "name": safe_name,
        "mimeType": FOLDER_MIME,
        "parents": [parent_ref]
    }
    request = svc.files().create(
        body=file_metadata,
        fields="id, name",
        supportsAllDrives=True,
    )
    return _execute_with_retry(request)


def upload_file(
    telegram_id: int,
    file_bytes: bytes,
    filename: str,
    parent_id: str = "root",
) -> dict:
    svc = _service(telegram_id)
    if not file_bytes:
        raise ValueError("Empty upload payload.")

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
    )
    uploaded = _execute_with_retry(uploaded)
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
MAX_DOWNLOAD_BYTES = limits.MAX_TELEGRAM_DOWNLOAD_BYTES


def download_file(telegram_id: int, file_id: str) -> tuple[bytes, str]:
    """
    Return (file_bytes, filename).
    Automatically exports Google Workspace files (Docs, Sheets, Slides, etc.)
    to a compatible Office format instead of attempting a direct binary download.

    Raises ValueError if the download exceeds MAX_DOWNLOAD_BYTES.
    """
    svc  = _service(telegram_id)
    if not validators.validate_drive_id(file_id, allow_root=False):
        raise ValueError("Invalid file reference.")
    request = svc.files().get(
        fileId=file_id,
        fields="name, mimeType, shortcutDetails",
        supportsAllDrives=True,
    )
    meta = _execute_with_retry(request)
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
    if not validators.validate_drive_id(file_id, allow_root=False):
        raise ValueError("Invalid file reference.")
    safe_name = _sanitize_filename(new_name)
    request = svc.files().update(
        fileId=file_id,
        body={"name": safe_name},
        fields="id, name",
        supportsAllDrives=True,
    )
    result = _execute_with_retry(request)
    log_audit(telegram_id, "rename", file_id, f"new_name={safe_name}")
    return result


def delete_file(telegram_id: int, file_id: str) -> None:
    svc = _service(telegram_id)
    if not validators.validate_drive_id(file_id, allow_root=False):
        raise ValueError("Invalid file reference.")
    request = svc.files().delete(fileId=file_id, supportsAllDrives=True)
    _execute_with_retry(request)
    log_audit(telegram_id, "delete", file_id)


def find_file_by_name(telegram_id: int, name: str) -> Optional[dict]:
    """Find a single non-folder file by exact name."""
    svc = _service(telegram_id)
    safe_name = _sanitize_query_value(name)
    q = f"name='{safe_name}' and mimeType!='{FOLDER_MIME}' and trashed=false"
    request = svc.files().list(
        q=q,
        fields="files(id, name, mimeType)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    )
    result = _execute_with_retry(request)
    files = result.get("files", [])
    return files[0] if files else None


# ── Async wrappers (offload sync Google API calls) ─────────────────────────────

async def list_directory_async(telegram_id: int, parent_id: str = "root") -> DirectoryListing:
    return await asyncio.to_thread(list_directory, telegram_id, parent_id)


async def search_files_async(telegram_id: int, keyword: str) -> list[dict]:
    return await asyncio.to_thread(search_files, telegram_id, keyword)


async def get_file_metadata_async(telegram_id: int, file_id: str) -> dict:
    return await asyncio.to_thread(get_file_metadata, telegram_id, file_id)


async def upload_file_async(telegram_id: int, file_bytes: bytes, filename: str, parent_id: str = "root") -> dict:
    return await asyncio.to_thread(upload_file, telegram_id, file_bytes, filename, parent_id)


async def create_folder_async(telegram_id: int, name: str, parent_id: str = "root") -> dict:
    return await asyncio.to_thread(create_folder, telegram_id, name, parent_id)


async def rename_file_async(telegram_id: int, file_id: str, new_name: str) -> dict:
    return await asyncio.to_thread(rename_file, telegram_id, file_id, new_name)


async def move_file_async(telegram_id: int, file_id: str, new_parent_id: str) -> dict:
    return await asyncio.to_thread(move_file, telegram_id, file_id, new_parent_id)


async def delete_file_async(telegram_id: int, file_id: str) -> None:
    await asyncio.to_thread(delete_file, telegram_id, file_id)
