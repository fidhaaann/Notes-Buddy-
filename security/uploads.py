"""Upload validation and MIME verification helpers."""

from __future__ import annotations

import mimetypes
from typing import Optional, Tuple

import filetype

from security import validators

_DENY_EXTENSIONS = {
    ".exe", ".dll", ".bat", ".cmd", ".com", ".msi", ".ps1", ".psm1",
    ".sh", ".bash", ".zsh", ".ksh", ".jar", ".apk", ".app", ".bin",
    ".js", ".mjs", ".cjs", ".vbs", ".vb", ".wsf", ".scr",
}

_DENY_MIME_PREFIXES = {
    "application/x-dosexec",
    "application/x-msdownload",
    "application/x-sh",
    "application/x-bash",
    "application/x-shellscript",
    "application/x-executable",
    "application/javascript",
    "text/javascript",
}


def _mime_compatible(declared: str, detected: str) -> bool:
    if not declared or not detected:
        return True
    if declared in {"application/octet-stream", "binary/octet-stream"}:
        return True
    if declared == detected:
        return True
    declared_main = declared.split("/", 1)[0]
    detected_main = detected.split("/", 1)[0]
    if declared_main == detected_main and declared_main in {"image", "video", "audio"}:
        return True
    return False


def detect_mime(file_bytes: bytes, filename: str | None = None) -> str:
    guess = filetype.guess(file_bytes)
    if guess:
        return guess.mime
    if filename:
        mime, _ = mimetypes.guess_type(filename)
        if mime:
            return mime
    return "application/octet-stream"


def validate_upload(
    file_bytes: bytes,
    filename: str,
    declared_mime: Optional[str],
    max_bytes: int,
) -> Tuple[bool, str, str, str]:
    """Validate upload bytes and return (ok, reason, safe_filename, detected_mime)."""
    if not file_bytes:
        return False, "Empty file upload rejected.", "unnamed_file", "application/octet-stream"
    if max_bytes and len(file_bytes) > max_bytes:
        return False, "File exceeds upload size limit.", "unnamed_file", "application/octet-stream"

    safe_name = validators.sanitize_filename(filename)
    lower_name = safe_name.lower()
    for ext in _DENY_EXTENSIONS:
        if lower_name.endswith(ext):
            return False, "Executable file types are not allowed.", safe_name, "application/octet-stream"

    detected = detect_mime(file_bytes, safe_name)
    if detected in _DENY_MIME_PREFIXES:
        return False, "Unsupported file type detected.", safe_name, detected

    declared = (declared_mime or "").strip().lower()
    if declared and not _mime_compatible(declared, detected):
        return False, "File type mismatch detected.", safe_name, detected

    return True, "", safe_name, detected
