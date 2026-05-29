"""Indexing pipeline for Drive files."""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from db import models
from drive import drive_service as ds
from indexing import extractors, normalize
from security import limits, validators

logger = logging.getLogger(__name__)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def index_from_bytes(
    telegram_id: int,
    file_id: str,
    filename: str,
    mime_type: str,
    parent_id: Optional[str],
    file_bytes: bytes,
) -> None:
    if not validators.validate_drive_id(file_id, allow_root=False):
        raise ValueError("Invalid file reference.")

    if limits.MAX_INDEX_BYTES and len(file_bytes) > limits.MAX_INDEX_BYTES:
        logger.info("index_skip size_limit file_id=%s", file_id)
        return

    content_hash = _hash_bytes(file_bytes)
    text = extractors.extract_text(file_bytes, mime_type, filename)
    if text:
        text = text[: limits.MAX_INDEX_CHARS]
    keywords = normalize.keywords(f"{filename} {text}")
    aliases = normalize.keywords(filename)

    models.upsert_file_index(
        telegram_id=telegram_id,
        file_id=file_id,
        name=filename,
        mime_type=mime_type,
        parent_id=parent_id,
        size_bytes=len(file_bytes),
        modified_time=None,
        content_hash=content_hash,
        keywords=keywords,
        aliases=aliases,
    )
    if text or keywords or aliases:
        models.upsert_file_fts(
            telegram_id=telegram_id,
            file_id=file_id,
            name=filename,
            content=text or "",
            keywords=keywords or "",
            aliases=aliases or "",
        )


def index_drive_file(telegram_id: int, file_id: str) -> None:
    meta = ds.get_file_metadata(telegram_id, file_id)
    filename = meta.get("name", "file")
    mime_type = meta.get("mimeType", "")
    parent_id = None
    parents = meta.get("parents") or []
    if parents:
        parent_id = parents[0]
    file_bytes, _ = ds.download_file(telegram_id, file_id)
    index_from_bytes(telegram_id, file_id, filename, mime_type, parent_id, file_bytes)


def upsert_metadata(
    telegram_id: int,
    file_id: str,
    name: str,
    mime_type: str | None,
    parent_id: str | None,
    size_bytes: int | None,
    modified_time: str | None,
) -> None:
    if not validators.validate_drive_id(file_id, allow_root=False):
        return
    safe_name = validators.sanitize_text(name)
    models.upsert_file_index(
        telegram_id=telegram_id,
        file_id=file_id,
        name=safe_name or name or "file",
        mime_type=mime_type,
        parent_id=parent_id,
        size_bytes=size_bytes,
        modified_time=modified_time,
        content_hash=None,
        keywords=None,
        aliases=None,
    )
