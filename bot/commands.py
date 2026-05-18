"""
bot/commands.py
All /command handlers.

Implements the terminal-style navigation system with hierarchical indexing.
Commands: /start, /info, /cd, /pwd, /download, /more, /upload, /search,
          /zip, /rename, /delete, /move, /mkdir, /logout, /email, /clear, /menu, /help, /tool

Security:
  - Input validation on all index parameters
  - Error message sanitization (never expose raw exceptions)
  - Per-user rate limiting on expensive operations
  - Filename sanitization on ZIP output
  - Email validation for security alerts
  - Anomaly detection for suspicious patterns
"""

import asyncio
import io
import logging
import os
import re
import time

from telegram import Update
from telegram.ext import ContextTypes

from drive import auth as drive_auth
from drive import drive_service as ds
from bot import formatter, ui
from bot import nav
from services import parser as p
from services.zip_service import create_zip
from services import anomaly_detection
from db import models

logger = logging.getLogger(__name__)

# Email validation
_EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# Safety limits
MAX_ZIP_FILES = 20
MAX_ZIP_BYTES = 100 * 1024 * 1024  # 100 MB
TELEGRAM_LIMIT = 45 * 1024 * 1024  # 45 MB safe margin

# Rate limiting: (command_key) -> {uid: last_timestamp}
_RATE_LIMITS: dict[str, dict[int, float]] = {}
_RATE_COOLDOWN = 3.0  # seconds between expensive operations per user

# F-04: Optional user allowlist — if set, only these Telegram IDs can use the bot.
# Format: comma-separated IDs, e.g. "123456789,987654321"
_ALLOWED_USERS_RAW = os.environ.get("ALLOWED_USERS", "").strip()
_ALLOWED_USERS: set[int] | None = None
if _ALLOWED_USERS_RAW:
    try:
        _ALLOWED_USERS = {int(uid.strip()) for uid in _ALLOWED_USERS_RAW.split(",") if uid.strip()}
        logger.info("User allowlist active: %d user(s) permitted.", len(_ALLOWED_USERS))
    except ValueError:
        logger.warning("ALLOWED_USERS contains invalid IDs — allowlist disabled.")


def _is_allowed(uid: int) -> bool:
    """Check if a user is permitted to use the bot.

    Returns True if no allowlist is configured (open access) or
    if the user's Telegram ID is in the allowlist.
    """
    if _ALLOWED_USERS is None:
        return True
    return uid in _ALLOWED_USERS


def _uid(update: Update) -> int:
    assert update.effective_user is not None  # guaranteed by handler filters
    return update.effective_user.id


def _msg(update: Update):  # noqa: ANN202
    """Return update.message with an assert guard for the type checker."""
    assert update.message is not None  # guaranteed by CommandHandler
    return update.message


def _text(update: Update) -> str:
    """Return update.message.text, defaulting to '' if None."""
    msg = _msg(update)
    return msg.text or ""


def _is_authenticated(uid: int) -> bool:
    user = models.get_user(uid)
    return bool(user and user["token"])


def _safe_error(e: Exception) -> str:
    """Sanitize exception messages before showing to users.

    Never expose raw tracebacks, file paths, token data, or internal details.
    """
    msg = str(e)
    # Strip anything that looks like a file path
    msg = re.sub(r'[A-Za-z]:\\[^\s]+', '[path]', msg)
    msg = re.sub(r'/[^\s]*/', '[path]/', msg)
    # Strip Google OAuth access tokens (ya29.xxx)
    msg = re.sub(r'ya29\.[A-Za-z0-9_.-]+', '[redacted]', msg)
    # Strip Fernet tokens
    msg = re.sub(r'gAAAAA[A-Za-z0-9_/+-]{20,}', '[redacted]', msg)
    # Strip generic secrets (40+ chars)
    msg = re.sub(r'(?<=["\s=:])[A-Za-z0-9_/+-]{40,}', '[redacted]', msg)
    # Strip Google Drive file IDs (25-50 char identifiers)
    msg = re.sub(r'\b[A-Za-z0-9_-]{25,50}\b', '[id]', msg)
    # Truncate very long messages
    if len(msg) > 200:
        msg = msg[:197] + "..."
    return msg


def _validate_index(index: str) -> bool:
    """Validate a hierarchical index string (e.g. '1', '1.2', '1.2.3').

    Rules:
      - Only digits and dots
      - Max depth of 3 levels
      - Max length of 10 characters
      - No empty segments
    """
    if not index or len(index) > 10:
        return False
    if not re.match(r'^[0-9]+(\.[0-9]+){0,2}$', index):
        return False
    return True


def _check_rate_limit(uid: int, operation: str) -> bool:
    """Returns True if the user is rate-limited (should be blocked)."""
    now = time.monotonic()
    if operation not in _RATE_LIMITS:
        _RATE_LIMITS[operation] = {}
    last = _RATE_LIMITS[operation].get(uid, 0)
    if now - last < _RATE_COOLDOWN:
        return True  # rate limited
    _RATE_LIMITS[operation][uid] = now
    return False


def _sanitize_zip_filename(keyword: str) -> str:
    """Sanitize a keyword for use as a ZIP filename."""
    # Remove anything that's not alphanumeric, dash, underscore, or space
    safe = re.sub(r'[^\w\s-]', '', keyword).strip()
    safe = re.sub(r'\s+', '_', safe)
    if not safe:
        safe = "archive"
    return safe[:50]  # Limit length


# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)

    # F-04: Check user allowlist before any action
    if not _is_allowed(uid):
        await _msg(update).reply_text(
            formatter.error(
                "Access denied.",
                "This bot is restricted. Contact the administrator.",
            )
        )
        return

    if _is_authenticated(uid):
        await _msg(update).reply_text(
            formatter.welcome_authenticated(),
            reply_markup=ui.post_login_keyboard(),
        )
    else:
        # Generate OAuth URL and send as inline button (never raw URL)
        try:
            auth_url = drive_auth.get_auth_url(uid)
            await _msg(update).reply_text(formatter.oauth_scope_warning())
            await _msg(update).reply_text(
                formatter.welcome_unauthenticated(),
                reply_markup=ui.login_keyboard(auth_url),
            )
        except FileNotFoundError:
            await _msg(update).reply_text(
                formatter.error(
                    "OAuth credentials not configured.",
                    "Contact the bot administrator.",
                )
            )


# ─────────────────────────────────────────────────────────────────────────────
# /info  — List current directory with hierarchical indices
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)

    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return

    if _check_rate_limit(uid, "info"):
        await _msg(update).reply_text(
            formatter.error("Please wait a moment before listing again.")
        )
        return

    try:
        fid = nav.current_folder_id(uid)
        path = nav.breadcrumb(uid)

        folders = ds.list_folders(uid, parent_id=fid)
        files = ds.list_files(uid, parent_id=fid)

        # Build one level of children for each folder (expanded view)
        children_map: dict[str, tuple[list[dict], list[dict]]] = {}
        for f in folders:
            try:
                sub_folders = ds.list_folders(uid, parent_id=f["id"])
                sub_files = ds.list_files(uid, parent_id=f["id"])
                if sub_folders or sub_files:
                    children_map[f["id"]] = (sub_folders, sub_files)
            except Exception:
                continue

        # Build hierarchical index map
        index_map = nav.build_deep_index_map(uid, folders, files, children_map)

        text = formatter.directory_listing(path, index_map, folders, files)
        is_root = (fid == "root")

        await _msg(update).reply_text(
            text,
            reply_markup=ui.browse_keyboard(is_root=is_root),
        )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_info error")
        await _msg(update).reply_text(
            formatter.error(
                "Could not load directory listing.",
                "Try again or use /start to re-authenticate.",
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# /cd <index>  — Enter folder  |  /cd  — Go back
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_cd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)

    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return

    args = p.parse_args(_text(update), "/cd")

    # /cd with no args → go back
    if not args:
        went_back = nav.pop_folder(uid)
        if not went_back:
            await _msg(update).reply_text(
                formatter.current_path(nav.breadcrumb(uid))
            )
            return
        # After going back, show directory listing
        await cmd_info(update, context)
        return

    # Validate index format
    index = args[0].strip()
    if not _validate_index(index):
        await _msg(update).reply_text(
            formatter.error(
                "Invalid index format.",
                "Use indices like 1, 1.2, or 1.2.3 from the /info listing.",
            )
        )
        return

    item = nav.resolve_index(uid, index)

    if not item:
        await _msg(update).reply_text(
            formatter.error(
                f"Index [{index}] not found.",
                "Use /info to see the current directory listing.",
            )
        )
        return

    if not item.is_folder:
        await _msg(update).reply_text(
            formatter.error(
                f"[{index}] is a file, not a directory.",
                "Use /download or /more for file operations.",
            )
        )
        return

    nav.push_folder(uid, item.id, item.name)
    # Show contents of the new directory
    await cmd_info(update, context)


# ─────────────────────────────────────────────────────────────────────────────
# /pwd  — Print working directory
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_pwd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    await _msg(update).reply_text(
        formatter.current_path(nav.breadcrumb(uid))
    )


# ─────────────────────────────────────────────────────────────────────────────
# /download <index>  — Download file by index
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)

    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return

    args = p.parse_args(_text(update), "/download")
    if not args:
        await _msg(update).reply_text(
            formatter.error(
                "Missing index.",
                "Usage: /download <index>  (e.g. /download 1.2)",
            )
        )
        return

    index = args[0].strip()
    if not _validate_index(index):
        await _msg(update).reply_text(
            formatter.error(
                "Invalid index format.",
                "Use indices like 1, 1.2, or 1.2.3 from the /info listing.",
            )
        )
        return

    item = nav.resolve_index(uid, index)

    if not item:
        await _msg(update).reply_text(
            formatter.error(
                f"Index [{index}] not found.",
                "Use /info to see the current directory listing.",
            )
        )
        return

    if item.is_folder:
        await _msg(update).reply_text(
            formatter.error(
                f"[{index}] is a folder.",
                "Use /cd to enter it, or /zip to download its contents.",
            )
        )
        return

    try:
        # Get metadata for size check
        meta = ds.get_file_metadata(uid, item.id)
        fname = meta.get("name", item.name)
        size_raw = int(meta["size"]) if meta.get("size") else 0
        size_str = p.human_size(size_raw) if size_raw else "Unknown"

        # Too large for Telegram
        if size_raw > TELEGRAM_LIMIT:
            view_link = meta.get("webViewLink", "")
            content_link = meta.get("webContentLink", "")
            await _msg(update).reply_text(
                formatter.download_too_large(fname, size_str, view_link, content_link),
                reply_markup=ui.back_to_menu_keyboard(),
            )
            return

        # Send progress message
        progress_msg = await _msg(update).reply_text(
            formatter.download_progress(fname, size_str)
        )

        # Download in background thread
        file_bytes, downloaded_name = await asyncio.to_thread(ds.download_file, uid, item.id)

        # Send file
        assert update.effective_chat is not None
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=io.BytesIO(file_bytes),
            filename=downloaded_name,
            read_timeout=180,
            write_timeout=180,
            connect_timeout=30,
        )

        # Update progress message
        try:
            await progress_msg.edit_text(
                formatter.success("Download Complete", downloaded_name)
            )
        except Exception:
            pass

    except Exception as e:
        logger.exception("Download failed for index %s", index)
        await _msg(update).reply_text(
            formatter.error(
                "Download failed.",
                "Try again or open the file directly in Google Drive.",
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# /more <index>  — Show file/folder metadata
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_more(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)

    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return

    args = p.parse_args(_text(update), "/more")
    if not args:
        await _msg(update).reply_text(
            formatter.error(
                "Missing index.",
                "Usage: /more <index>  (e.g. /more 1.2)",
            )
        )
        return

    index = args[0].strip()
    if not _validate_index(index):
        await _msg(update).reply_text(
            formatter.error(
                "Invalid index format.",
                "Use indices like 1, 1.2, or 1.2.3 from the /info listing.",
            )
        )
        return

    item = nav.resolve_index(uid, index)

    if not item:
        await _msg(update).reply_text(
            formatter.error(
                f"Index [{index}] not found.",
                "Use /info to see the current directory listing.",
            )
        )
        return

    try:
        meta = ds.get_file_metadata(uid, item.id)
        meta["_path"] = item.path
        is_fav = models.is_favorite(uid, item.id)
        await _msg(update).reply_text(
            formatter.file_info(meta),
            reply_markup=ui.file_actions_keyboard(item.id, is_fav),
        )
    except Exception as e:
        logger.exception("cmd_more error")
        await _msg(update).reply_text(
            formatter.error("Could not retrieve file information.")
        )


# ─────────────────────────────────────────────────────────────────────────────
# /upload  — Enter upload mode
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)

    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return

    # Set upload mode in user_data
    assert context.user_data is not None
    context.user_data["upload_mode"] = True
    await _msg(update).reply_text(formatter.upload_mode_enabled())


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel upload mode or any pending operation."""
    assert context.user_data is not None
    context.user_data.pop("upload_mode", None)
    context.user_data.pop("pending_upload", None)
    await _msg(update).reply_text(
        formatter.success("Cancelled"),
        reply_markup=ui.post_login_keyboard(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# /search <keyword>
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)

    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return

    if _check_rate_limit(uid, "search"):
        await _msg(update).reply_text(
            formatter.error("Please wait a moment before searching again.")
        )
        return

    args = p.parse_args(_text(update), "/search")
    if not args:
        await _msg(update).reply_text(
            formatter.error(
                "Missing keyword.",
                "Usage: /search <keyword>",
            )
        )
        return

    keyword = " ".join(args)
    # Limit keyword length to prevent abuse
    if len(keyword) > 100:
        keyword = keyword[:100]

    try:
        files = ds.search_files(uid, keyword)
        await _msg(update).reply_text(
            formatter.search_results(keyword, files),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_search error")
        await _msg(update).reply_text(
            formatter.error("Search failed.", "Try a different keyword.")
        )


# ─────────────────────────────────────────────────────────────────────────────
# /rename  /move  /delete  /mkdir
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(_text(update), "/rename")
    if len(args) < 2:
        await _msg(update).reply_text(
            formatter.error("Missing arguments.", "Usage: /rename <old_name> <new_name>")
        )
        return
    old_name, new_name = args[0], " ".join(args[1:])
    try:
        fm = ds.find_file_by_name(uid, old_name)
        if not fm:
            await _msg(update).reply_text(
                formatter.error(f"File not found.", "Use /search to locate it.")
            )
            return
        updated = ds.rename_file(uid, fm["id"], new_name)
        await _msg(update).reply_text(
            formatter.success("Renamed", updated['name']),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_rename error")
        await _msg(update).reply_text(
            formatter.error("Rename failed.", "Check the filename and try again.")
        )


async def cmd_move(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(_text(update), "/move")
    if len(args) < 2:
        await _msg(update).reply_text(
            formatter.error("Missing arguments.", "Usage: /move <file_name> <folder_name>")
        )
        return
    file_name, folder_name = args[0], " ".join(args[1:])
    try:
        fm = ds.find_file_by_name(uid, file_name)
        if not fm:
            await _msg(update).reply_text(
                formatter.error("File not found.", "Use /search to locate it.")
            )
            return
        tf = ds.open_folder(uid, folder_name)
        if not tf:
            await _msg(update).reply_text(
                formatter.error("Destination folder not found.")
            )
            return
        ds.move_file(uid, fm["id"], tf["id"])
        await _msg(update).reply_text(
            formatter.success("Moved", file_name, folder_name),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_move error")
        await _msg(update).reply_text(
            formatter.error("Move failed.", "Check the names and try again.")
        )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(_text(update), "/delete")
    if not args:
        await _msg(update).reply_text(
            formatter.error("Missing filename.", "Usage: /delete <filename>")
        )
        return
    filename = " ".join(args)
    try:
        fm = ds.find_file_by_name(uid, filename)
        if not fm:
            await _msg(update).reply_text(
                formatter.error("File not found.", "Use /search to locate it.")
            )
            return
        await _msg(update).reply_text(
            formatter.confirm_action("Delete", filename),
            reply_markup=ui.confirm_keyboard("delete", fm["id"]),
        )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_delete error")
        await _msg(update).reply_text(
            formatter.error("Could not prepare deletion.", "Try again.")
        )


async def cmd_mkdir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(_text(update), "/mkdir")
    if not args:
        await _msg(update).reply_text(
            formatter.error("Missing name.", "Usage: /mkdir <folder_name>")
        )
        return
    name = " ".join(args)
    try:
        created = ds.create_folder(uid, name, parent_id=nav.current_folder_id(uid))
        await _msg(update).reply_text(
            formatter.success("Folder Created", created["name"], nav.breadcrumb(uid)),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_mkdir error")
        await _msg(update).reply_text(
            formatter.error("Could not create folder.", "Try a different name.")
        )


# ─────────────────────────────────────────────────────────────────────────────
# /zip <keyword>
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)

    if _check_rate_limit(uid, "zip"):
        await _msg(update).reply_text(
            formatter.error("Please wait before creating another archive.")
        )
        return

    args = p.parse_args(_text(update), "/zip")
    if not args:
        await _msg(update).reply_text(
            formatter.error("Missing keyword.", "Usage: /zip <keyword>")
        )
        return
    keyword = " ".join(args)
    # Limit keyword length
    if len(keyword) > 100:
        keyword = keyword[:100]

    try:
        files = ds.search_files(uid, keyword)
        if not files:
            await _msg(update).reply_text(
                formatter.error("No files matched your keyword.", "Try a different keyword.")
            )
            return

        if len(files) > MAX_ZIP_FILES:
            await _msg(update).reply_text(
                formatter.error(
                    f"Too many files ({len(files)}).",
                    f"ZIP is limited to {MAX_ZIP_FILES} files. Use a more specific keyword.",
                )
            )
            return

        total = sum(int(f.get("size", 0)) for f in files)
        if total > MAX_ZIP_BYTES:
            await _msg(update).reply_text(
                formatter.error(
                    f"Combined size too large ({p.human_size(total)}).",
                    f"ZIP is limited to {p.human_size(MAX_ZIP_BYTES)}. Use a more specific keyword.",
                )
            )
            return

        size_str = p.human_size(total) if total else "Unknown"
        await _msg(update).reply_text(formatter.zip_preparing(len(files), size_str))

        collected: list[tuple[bytes, str]] = []
        for f in files:
            try:
                file_bytes, fname = ds.download_file(uid, f["id"])
                collected.append((file_bytes, fname))
            except Exception:
                logger.warning("Skipping %s in ZIP — download failed", f.get("name", "unknown"))

        if not collected:
            await _msg(update).reply_text(
                formatter.error("Could not download any files for the archive.")
            )
            return

        zip_bytes = create_zip(collected)
        # Sanitize keyword for use as filename
        zip_name = f"{_sanitize_zip_filename(keyword)}_files.zip"
        await _msg(update).reply_document(
            document=io.BytesIO(zip_bytes),
            filename=zip_name,
            caption=formatter.zip_ready(zip_name, len(collected)),
        )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_zip error")
        await _msg(update).reply_text(
            formatter.error("Archive creation failed.", "Try again with a different keyword.")
        )


# ─────────────────────────────────────────────────────────────────────────────
# /logout
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    # V-NEW-02: Revoke token at Google before local deletion
    drive_auth.revoke_token(uid)
    models.delete_user(uid)  # Also cleans up favorites (V-15)
    nav.clear_user(uid)
    await _msg(update).reply_text(formatter.logout_successful())


# ─────────────────────────────────────────────────────────────────────────────
# /clear
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear recent messages in the chat."""
    assert update.effective_chat is not None
    chat_id = update.effective_chat.id
    msg_id = _msg(update).message_id

    deleted = 0
    for i in range(msg_id, max(msg_id - 50, 0), -1):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=i)
            deleted += 1
        except Exception:
            continue
        await asyncio.sleep(0.05)

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🧹 Cleared {deleted} messages.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /menu  /help
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _msg(update).reply_text(
        formatter.main_menu(),
        reply_markup=ui.main_menu_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _msg(update).reply_text(
        formatter.tools_menu(),
        reply_markup=ui.back_to_menu_keyboard(),
    )


async def cmd_tool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_help(update, context)


# ─────────────────────────────────────────────────────────────────────────────
# /email  — Set email for security alerts
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set user's email address for security alerts."""
    uid = _uid(update)
    args = p.parse_command_text(_text(update))
    
    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return
    
    if not args:
        # Show current email
        current_email = models.get_user_email(uid)
        if current_email:
            await _msg(update).reply_text(
                f"📧 Your current email: {current_email}\n\n"
                "Usage: /email <your-email@example.com>"
            )
        else:
            await _msg(update).reply_text(
                "📧 Security Alerts\n\n"
                "Set your email to receive alerts if unusual activity is detected.\n\n"
                "Usage: /email your-email@example.com"
            )
        return
    
    email = " ".join(args).strip()
    
    # Validate email format
    if not _EMAIL_PATTERN.match(email):
        await _msg(update).reply_text(
            formatter.error(
                "Invalid email format.",
                f"Please use a valid email like: user@example.com"
            )
        )
        return
    
    # Store email
    if models.set_user_email(uid, email):
        await _msg(update).reply_text(
            f"✅ Email updated\n\n"
            f"Address: {email}\n\n"
            "You will receive security alerts at this email if suspicious activity is detected."
        )
        logger.info("Email set for user %s: %s", uid, email)
    else:
        await _msg(update).reply_text(
            formatter.error(
                "Failed to update email.",
                "Please try again later."
            )
        )
