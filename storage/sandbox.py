"""Per-user temp sandbox management."""

from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path

from security import validators

TEMP_ROOT = Path(os.environ.get("TEMP_ROOT", os.path.join(os.getcwd(), "temp")))


def _ensure_root() -> None:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(TEMP_ROOT, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        except OSError:
            pass


def user_sandbox(telegram_id: int) -> Path:
    _ensure_root()
    safe_id = str(telegram_id)
    path = (TEMP_ROOT / safe_id).resolve()
    if TEMP_ROOT.resolve() not in path.parents and path != TEMP_ROOT.resolve():
        raise ValueError("Invalid sandbox path.")
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        except OSError:
            pass
    return path


def write_bytes(telegram_id: int, filename: str, data: bytes) -> Path:
    sandbox = user_sandbox(telegram_id)
    safe_name = validators.sanitize_filename(filename)
    target = (sandbox / safe_name).resolve()
    if sandbox.resolve() not in target.parents and target != sandbox.resolve():
        raise ValueError("Invalid file path.")
    target.write_bytes(data)
    return target


def cleanup_user_sandbox(telegram_id: int) -> None:
    sandbox = TEMP_ROOT / str(telegram_id)
    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)


def remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
    try:
        parent = path.parent
        if parent != TEMP_ROOT and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def cleanup_expired_sandboxes(ttl_seconds: int) -> None:
    _ensure_root()
    now = time.time()
    for child in TEMP_ROOT.iterdir():
        if not child.is_dir():
            continue
        try:
            if now - child.stat().st_mtime > ttl_seconds:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue
