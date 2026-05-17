"""
drive/drive_service.py
All Google Drive API interactions.

Security:
  - Query injection prevention via _sanitize_query_value()
  - Download size limits enforced
  - Filename sanitization on uploads and downloads
"""

import io
import mimetypes
import os
import re
from typing import Any, Optional

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials

from drive.auth import get_credentials
from db.models import log_file, log_audit

FOLDER_MIME = "application/vnd.google-apps.folder"

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


# ── Folders ───────────────────────────────────────────────────────────────────

def list_folders(telegram_id: int, parent_id: str = "root") -> list[dict]:
    svc = _service(telegram_id)
    q = (
        f"'{parent_id}' in parents "
        f"and mimeType='{FOLDER_MIME}' "
        f"and trashed=false"
    )
    result = svc.files().list(
        q=q,
        fields="files(id, name, mimeType)",   # mimeType required for browse_keyboard routing
        pageSize=50,
    ).execute()
    return result.get("files", [])


def open_folder(telegram_id: int, folder_name: str, parent_id: Optional[str] = None) -> Optional[dict]:
    """Return the first folder matching folder_name, optionally under parent_id."""
    svc = _service(telegram_id)
    safe_name = _sanitize_query_value(folder_name)
    q = f"mimeType='{FOLDER_MIME}' and name='{safe_name}' and trashed=false"
    if parent_id:
        q = f"'{parent_id}' in parents and " + q
    result = svc.files().list(q=q, fields="files(id, name)", pageSize=1).execute()
    files = result.get("files", [])
    return files[0] if files else None


# ── Files ─────────────────────────────────────────────────────────────────────

def list_files(telegram_id: int, parent_id: str = "root") -> list[dict]:
    svc = _service(telegram_id)
    q = (
        f"'{parent_id}' in parents "
        f"and mimeType!='{FOLDER_MIME}' "
        f"and trashed=false"
    )
    result = svc.files().list(
        q=q,
        fields="files(id, name, mimeType, size)",
        pageSize=50,
    ).execute()
    return result.get("files", [])

def search_files(telegram_id: int, keyword: str) -> list[dict]:
    svc = _service(telegram_id)
    safe_keyword = _sanitize_query_value(keyword)
    q = f"name contains '{safe_keyword}' and trashed=false"
    result = svc.files().list(
        q=q,
        fields="files(id, name, mimeType)",
        pageSize=50,
    ).execute()
    return result.get("files", [])


def get_recent_files(telegram_id: int, limit: int = 10) -> list[dict]:
    svc = _service(telegram_id)
    result = svc.files().list(
        orderBy="viewedByMeTime desc",
        pageSize=limit,
        fields="files(id, name, mimeType)",
        q="trashed=false"
    ).execute()
    return result.get("files", [])


def get_file_metadata(telegram_id: int, file_id: str) -> dict:
    svc = _service(telegram_id)
    return svc.files().get(
        fileId=file_id,
        fields="id, name, mimeType, size, createdTime, modifiedTime, parents, webViewLink, webContentLink"
    ).execute()


def move_file(telegram_id: int, file_id: str, new_parent_id: str) -> dict:
    svc = _service(telegram_id)
    file = svc.files().get(fileId=file_id, fields="parents").execute()
    previous_parents = ",".join(file.get("parents", []))
    result = svc.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=previous_parents,
        fields="id, parents"
    ).execute()
    log_audit(telegram_id, "move", file_id, f"to parent={new_parent_id}")
    return result


def create_folder(telegram_id: int, name: str, parent_id: str = "root") -> dict:
    svc = _service(telegram_id)
    safe_name = _sanitize_filename(name)
    file_metadata = {
        "name": safe_name,
        "mimeType": FOLDER_MIME,
        "parents": [parent_id]
    }
    return svc.files().create(body=file_metadata, fields="id, name").execute()


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

    file_metadata = {"name": safe_filename, "parents": [parent_id]}
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
    uploaded = (
        svc.files()
        .create(body=file_metadata, media_body=media, fields="id, name, mimeType")
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
    meta = svc.files().get(fileId=file_id, fields="name, mimeType").execute()
    filename  = _sanitize_filename(meta["name"])
    mime_type = meta.get("mimeType", "")

    buf = io.BytesIO()

    if mime_type in _GOOGLE_EXPORT_MAP:
        # Google Workspace file — must be exported/converted
        export_mime, ext = _GOOGLE_EXPORT_MAP[mime_type]
        if not filename.endswith(ext):
            filename += ext                         # e.g. "My Doc" → "My Doc.docx"
        request = svc.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        # Regular binary file — direct download
        request = svc.files().get_media(fileId=file_id)

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
    result = svc.files().update(fileId=file_id, body={"name": safe_name}, fields="id, name").execute()
    log_audit(telegram_id, "rename", file_id, f"new_name={safe_name}")
    return result


def delete_file(telegram_id: int, file_id: str) -> None:
    svc = _service(telegram_id)
    svc.files().delete(fileId=file_id).execute()
    log_audit(telegram_id, "delete", file_id)


def find_file_by_name(telegram_id: int, name: str) -> Optional[dict]:
    """Find a single non-folder file by exact name."""
    svc = _service(telegram_id)
    safe_name = _sanitize_query_value(name)
    q = f"name='{safe_name}' and mimeType!='{FOLDER_MIME}' and trashed=false"
    result = svc.files().list(q=q, fields="files(id, name, mimeType)", pageSize=1).execute()
    files = result.get("files", [])
    return files[0] if files else None
