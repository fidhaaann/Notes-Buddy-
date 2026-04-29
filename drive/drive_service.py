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
        fields="files(id, name)",
        pageSize=50,
    ).execute()
    return result.get("files", [])


def open_folder(telegram_id: int, folder_name: str, parent_id: str = "root") -> Optional[dict]:
    """Return the first folder matching folder_name under parent_id."""
    svc = _service(telegram_id)
    q = (
        f"'{parent_id}' in parents "
        f"and mimeType='{FOLDER_MIME}' "
        f"and name='{folder_name}' "
        f"and trashed=false"
    )
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
    q = f"name contains '{keyword}' and trashed=false"
    result = svc.files().list(
        q=q,
        fields="files(id, name, mimeType)",
        pageSize=50,
    ).execute()
    return result.get("files", [])


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


def download_file(telegram_id: int, file_id: str) -> tuple[bytes, str]:
    """Return (file_bytes, filename)."""
    svc = _service(telegram_id)
    meta = svc.files().get(fileId=file_id, fields="name").execute()
    filename = meta["name"]

    request = svc.files().get_media(fileId=file_id)
    buf = io.BytesIO()
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
    q = f"name='{name}' and mimeType!='{FOLDER_MIME}' and trashed=false"
    result = svc.files().list(q=q, fields="files(id, name, mimeType)", pageSize=1).execute()
    files = result.get("files", [])
    return files[0] if files else None
