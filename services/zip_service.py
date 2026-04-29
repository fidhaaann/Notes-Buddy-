"""
services/zip_service.py
Creates in-memory ZIP archives from a list of (bytes, filename) tuples.
"""

import io
import zipfile


def create_zip(files: list[tuple[bytes, str]]) -> bytes:
    """
    files: list of (file_bytes, filename)
    Returns: ZIP archive as bytes.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_bytes, filename in files:
            zf.writestr(filename, file_bytes)
    return buf.getvalue()
