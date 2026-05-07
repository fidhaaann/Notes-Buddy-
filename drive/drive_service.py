"""
drive/drive_service.py
All Google Drive API interactions.
"""

import io
import mimetypes
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials

from drive.auth import get_credentials
from db.models import log_file

FOLDER_MIME = "application/vnd.google-apps.folder"


def _sanitize_query_value(value: str) -> str:
    """Escape special characters for Google Drive API query strings.
    
    Prevents query injection by escaping backslashes and single quotes
    in user-supplied values before embedding them in query filters.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _service(telegram_id: int):
    creds: Credentials = get_credentials(telegram_id)
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
        fields="id, name, mimeType, size, createdTime, parents, webViewLink, webContentLink"
    ).execute()


def move_file(telegram_id: int, file_id: str, new_parent_id: str) -> dict:
    svc = _service(telegram_id)
    file = svc.files().get(fileId=file_id, fields="parents").execute()
    previous_parents = ",".join(file.get("parents", []))
    return svc.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=previous_parents,
        fields="id, parents"
    ).execute()


def create_folder(telegram_id: int, name: str, parent_id: str = "root") -> dict:
    svc = _service(telegram_id)
    file_metadata = {
        "name": name,
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
    mime_type, _ = mimetypes.guess_type(filename)
    mime_type = mime_type or "application/octet-stream"

    file_metadata = {"name": filename, "parents": [parent_id]}
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


def download_file(telegram_id: int, file_id: str) -> tuple[bytes, str]:
    """
    Return (file_bytes, filename).
    Automatically exports Google Workspace files (Docs, Sheets, Slides, etc.)
    to a compatible Office format instead of attempting a direct binary download.
    """
    svc  = _service(telegram_id)
    meta = svc.files().get(fileId=file_id, fields="name, mimeType").execute()
    filename  = meta["name"]
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

    return buf.getvalue(), filename



def rename_file(telegram_id: int, file_id: str, new_name: str) -> dict:
    svc = _service(telegram_id)
    return svc.files().update(fileId=file_id, body={"name": new_name}, fields="id, name").execute()


def delete_file(telegram_id: int, file_id: str) -> None:
    svc = _service(telegram_id)
    svc.files().delete(fileId=file_id).execute()


def find_file_by_name(telegram_id: int, name: str) -> Optional[dict]:
    """Find a single non-folder file by exact name."""
    svc = _service(telegram_id)
    safe_name = _sanitize_query_value(name)
    q = f"name='{safe_name}' and mimeType!='{FOLDER_MIME}' and trashed=false"
    result = svc.files().list(q=q, fields="files(id, name, mimeType)", pageSize=1).execute()
    files = result.get("files", [])
    return files[0] if files else None
