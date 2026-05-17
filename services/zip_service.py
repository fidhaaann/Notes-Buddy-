"""
services/zip_service.py
Creates in-memory ZIP archives from a list of (bytes, filename) tuples.

Security:
  - Filenames sanitized to prevent path traversal in archives
  - Duplicate filename handling
"""

import io
import os
import re
import zipfile


def _safe_zip_filename(filename: str) -> str:
    """Sanitize a filename for safe inclusion in a ZIP archive.

    Prevents:
      - Path traversal (../, absolute paths)
      - Null bytes
      - Control characters
    """
    if not filename:
        return "unnamed_file"

    # Remove null bytes
    filename = filename.replace("\x00", "")

    # Take only the basename (strip any path components)
    filename = os.path.basename(filename)

    # Remove control characters
    filename = re.sub(r'[\x00-\x1f\x7f]', '', filename)

    # Strip leading dots and whitespace
    filename = filename.lstrip(". ")

    return filename or "unnamed_file"


def create_zip(files: list[tuple[bytes, str]]) -> bytes:
    """
    files: list of (file_bytes, filename)
    Returns: ZIP archive as bytes.

    Filenames are sanitized and deduplicated.
    """
    buf = io.BytesIO()
    seen_names: dict[str, int] = {}

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_bytes, filename in files:
            safe_name = _safe_zip_filename(filename)

            # Deduplicate filenames
            if safe_name in seen_names:
                seen_names[safe_name] += 1
                name, ext = os.path.splitext(safe_name)
                safe_name = f"{name}_{seen_names[safe_name]}{ext}"
            else:
                seen_names[safe_name] = 0

            zf.writestr(safe_name, file_bytes)

    return buf.getvalue()
