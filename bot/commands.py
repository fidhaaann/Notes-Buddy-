"""
bot/commands.py
All /command handlers.

Implements the terminal-style navigation system with hierarchical indexing.
Commands: /start, /info, /cd, /pwd, /download, /more, /upload, /search,
          /zip, /rename, /delete, /move, /mkdir, /logout, /email, /verify, /clear, /menu, /help, /tool

Security:
  - Input validation on all index parameters
  - Error message sanitization (never expose raw exceptions)
  - Per-user rate limiting on expensive operations
  - Filename sanitization on ZIP output
  - Email validation for security alerts
  - Anomaly detection for suspicious patterns
"""

import asyncio
import logging
import os
import re

from telegram import Update
from telegram.ext import ContextTypes
from googleapiclient.errors import HttpError

from drive import auth as drive_auth
from drive import drive_service as ds
from bot import formatter, ui
from bot import nav
from services import parser as p
from services import anomaly_detection
from services import stepup_auth
from db import models
from monitoring import context as monitoring_context
from monitoring import timing
from indexing import indexer
from indexing import search as indexed_search
from security import limits, validators
from security.rate_limit import get_rate_limiter
from tasks.manager import get_task_manager

logger = logging.getLogger(__name__)

# Safety limits
TELEGRAM_LIMIT = limits.MAX_TELEGRAM_DOWNLOAD_BYTES

# Rate limiter
_RATE_LIMITER = get_rate_limiter()

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
    uid = update.effective_user.id
    monitoring_context.set_request_context(user_id=uid, request_id=str(update.update_id))
    return uid


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
    return validators.validate_index(index)


def _check_rate_limit(uid: int, operation: str) -> bool:
    """Returns True if the user is rate-limited (should be blocked)."""
    return _RATE_LIMITER.limited(uid, operation)


async def _require_stepup(update: Update, context: ContextTypes.DEFAULT_TYPE, action_label: str) -> bool:
    """Ensure the user is verified for sensitive actions."""
    uid = _uid(update)
    assert context.user_data is not None
    result = await stepup_auth.request_verification(uid, action_label)
    status = result.get("status")
    if status == "verified":
        context.user_data.pop("awaiting_email", None)
        context.user_data.pop("awaiting_otp", None)
        context.user_data.pop("pending_stepup_action", None)
        return True
    if status == "no_email":
        context.user_data["awaiting_email"] = True
        context.user_data["pending_stepup_action"] = action_label
        await _msg(update).reply_text(
            formatter.stepup_email_required(action_label),
            reply_markup=ui.stepup_email_entry_keyboard(),
        )
        return False
    if status == "email_failed":
        await _msg(update).reply_text(formatter.stepup_email_failed())
        return False
    if status == "sent":
        context.user_data["awaiting_otp"] = True
        context.user_data["pending_stepup_action"] = action_label
        await _msg(update).reply_text(
            formatter.stepup_code_sent(action_label, result.get("email", ""), result.get("ttl", 10)),
            reply_markup=ui.stepup_resend_keyboard(action_label),
        )
        return False
    if status == "cooldown":
        context.user_data["awaiting_otp"] = True
        context.user_data["pending_stepup_action"] = action_label
        await _msg(update).reply_text(
            formatter.stepup_code_pending(action_label, result.get("email", ""), result.get("retry_after", 60)),
            reply_markup=ui.stepup_resend_keyboard(action_label),
        )
        return False
    await _msg(update).reply_text(formatter.error("Verification required.", "Reply with the 6-digit code."))
    return False


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
        with timing.timed("cmd_info"):
            fid = nav.current_folder_id(uid)
            path = nav.breadcrumb(uid)

            try:
                listing = await ds.list_directory_async(uid, parent_id=fid)
            except HttpError as e:
                status = getattr(e, "resp", None)
                if status and status.status in (400, 404, 410):
                    nav.go_home(uid)
                    fid = nav.current_folder_id(uid)
                    path = nav.breadcrumb(uid)
                    listing = await ds.list_directory_async(uid, parent_id=fid)
                elif status and status.status in (401, 403):
                    raise PermissionError("User not authenticated.") from e
                else:
                    raise

            folders = listing.folders
            files = listing.files

            for item in files:
                indexer.upsert_metadata(
                    uid,
                    item.get("id", ""),
                    item.get("name", "file"),
                    item.get("mimeType"),
                    fid,
                    int(item.get("size") or 0) if item.get("size") else None,
                    None,
                )

            index_map = nav.build_flat_index_map(uid, folders, files)
            
            # Set as active view: folder browsing context
            nav.set_active_view(uid, "folder", index_map, metadata={"folder_id": fid})

            text = formatter.directory_listing(path, index_map, folders, files)
            if listing.error_count or listing.truncated:
                text = (
                    formatter.partial_browse_warning(
                        listing.error_count, listing.truncated, False
                    )
                    + "\n\n"
                    + text
                )
            is_root = (fid == "root")

            await _msg(update).reply_text(
                text,
                reply_markup=ui.browse_keyboard(is_root=is_root),
            )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_info error")
        if isinstance(e, HttpError):
            status = getattr(e, "resp", None)
            if status and status.status in (400, 401, 403, 404, 410):
                try:
                    drive_auth.revoke_token(uid)
                    models.delete_user(uid)
                    nav.clear_user(uid)
                except Exception:
                    pass
                await _msg(update).reply_text(formatter.login_required())
                return
        await _msg(update).reply_text(
            formatter.error(
                "Could not load directory listing.",
                "Try again or say \"connect my drive\" to re-authenticate.",
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
    
    # Validate as simple integer
    if not validators.validate_simple_index(index):
        await _msg(update).reply_text(
            formatter.error(
                "Invalid index.",
                "Use numbers like 1 or 2.",
            )
        )
        return

    item = nav.resolve_index(uid, index)

    if not item:
        await _msg(update).reply_text(
            formatter.error(
                f"Invalid selection [{index}].",
                "Say \"show what's inside\" to refresh the list.",
            )
        )
        return

    if not item.is_folder:
        await _msg(update).reply_text(
            formatter.error(
                f"[{index}] is a file, not a directory.",
                "Say \"download 2\" or \"details 2\" for files.",
            )
        )
        return
    if item.is_shortcut:
        if not item.shortcut_target_id:
            await _msg(update).reply_text(
                formatter.error(
                    "Shortcut target is unavailable.",
                    "Try opening the item directly in Google Drive.",
                )
            )
            return
        if nav.is_in_stack(uid, item.shortcut_target_id):
            await _msg(update).reply_text(
                formatter.error(
                    "Navigation loop detected.",
                    "This folder is already in your path.",
                )
            )
            return
        nav.push_folder(uid, item.shortcut_target_id, item.name)
    else:
        if nav.is_in_stack(uid, item.id):
            await _msg(update).reply_text(
                formatter.error(
                    "Navigation loop detected.",
                    "This folder is already in your path.",
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
                "Try: download 1.",
            )
        )
        return

    index = args[0].strip()
    
    # Validate as simple integer
    if not validators.validate_simple_index(index):
        await _msg(update).reply_text(
            formatter.error(
                "Invalid index.",
                "Use numbers like 1 or 2.",
            )
        )
        return

    item = nav.resolve_index(uid, index)

    if not item:
        # Get current view info for better error message
        view = nav.get_active_view(uid)
        if view:
            max_idx = len(view.index_map)
            await _msg(update).reply_text(
                formatter.error(
                    f"Invalid selection [{index}].",
                    f"Please choose a valid item (1-{max_idx}). Say \"show what's inside\" or \"search for <keyword>\".",
                )
            )
        else:
            await _msg(update).reply_text(
                formatter.error(
                    "No active view.",
                    "Say \"show what's inside\" or \"search for <keyword>\".",
                )
            )
        return

    if item.is_folder:
        await _msg(update).reply_text(
            formatter.error(
                f"[{index}] is a folder.",
                "Say \"open 2\" to enter it, or \"zip all files\" to download its contents.",
            )
        )
        return

    try:
        with timing.timed("cmd_download"):
            if not await _require_stepup(update, context, "download files"):
                return

            if await anomaly_detection.check_anomaly(uid, "download"):
                await _msg(update).reply_text(
                    formatter.error(
                        "⛔ Unusual Activity Detected",
                        "Your Google Drive access has been suspended for security.\n\n"
                        "Check your email and Telegram for alerts.\n"
                        "Say \"connect my drive\" to reconnect when ready.",
                    )
                )
                return

            # Get metadata for size check
            meta = await ds.get_file_metadata_async(uid, item.id)
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

            manager = get_task_manager(context)
            if not manager:
                await _msg(update).reply_text(
                    formatter.error("Background queue unavailable.", "Try again later.")
                )
                return
            assert update.effective_chat is not None
            await manager.enqueue_download(
                telegram_id=uid,
                chat_id=update.effective_chat.id,
                file_id=item.id,
                filename=fname,
                size_str=size_str,
            )

    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
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
                "Usage: /more <index>  (e.g. /more 1)",
            )
        )
        return

    index = args[0].strip()
    
    # Validate as simple integer
    if not validators.validate_simple_index(index):
        await _msg(update).reply_text(
            formatter.error(
                "Invalid index.",
                "Use numbers like 1 or 2.",
            )
        )
        return

    item = nav.resolve_index(uid, index)

    if not item:
        await _msg(update).reply_text(
            formatter.error(
                f"Index [{index}] not found.",
                "Say \"show what's inside\" to refresh the list.",
            )
        )
        return

    try:
        meta = await ds.get_file_metadata_async(uid, item.id)
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
                "Try: search for <keyword>.",
            )
        )
        return

    keyword = validators.normalize_keyword(" ".join(args), limits.MAX_SEARCH_LEN)
    if not keyword:
        await _msg(update).reply_text(
            formatter.error("Invalid keyword.", "Try a different search term.")
        )
        return

    try:
        with timing.timed("cmd_search"):
            files = indexed_search.search_index(uid, keyword)
            if not files:
                suggestions = indexed_search.suggest_files(uid, keyword)
                if suggestions:
                    labels = [s["name"] for s in suggestions]
                    index_map: dict[str, nav.IndexedItem] = {}
                    for i, s in enumerate(suggestions, 1):
                        idx = str(i)
                        index_map[idx] = nav.IndexedItem(
                            id=s["file_id"],
                            name=s["name"],
                            mime_type=s.get("mime_type", ""),
                            is_folder=False,
                            parent_index="",
                            full_index=idx,
                            is_shortcut=False,
                            shortcut_target_id=None,
                            shortcut_target_mime_type=None,
                            path="Suggestions",
                        )
                    nav.set_active_view(uid, "search_suggestions", index_map, metadata={"keyword": keyword})
                    await _msg(update).reply_text(
                        formatter.nlp_suggestions("Closest Matches", labels),
                        reply_markup=ui.back_to_menu_keyboard(),
                    )
                    return
                await _msg(update).reply_text(
                    formatter.nlp_no_results(keyword),
                    reply_markup=ui.back_to_menu_keyboard(),
                )
                return

            # Build proper IndexedItem objects for search results
            index_map: dict[str, nav.IndexedItem] = {}
            for i, f in enumerate(files, 1):
                idx = str(i)
                index_map[idx] = nav.IndexedItem(
                    id=f["file_id"],
                    name=f["name"],
                    mime_type=f.get("mime_type", ""),
                    is_folder=False,
                    parent_index="",
                    full_index=idx,
                    is_shortcut=False,
                    shortcut_target_id=None,
                    shortcut_target_mime_type=None,
                    path=f"Search: {keyword}",
                )

            # Set as active view: search results context
            nav.set_active_view(uid, "search", index_map, metadata={"keyword": keyword})

            await _msg(update).reply_text(
                formatter.search_results_indexed(keyword, index_map),
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
    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return
    if _check_rate_limit(uid, "rename"):
        await _msg(update).reply_text(
            formatter.error("Please wait before renaming again.")
        )
        return
    args = p.parse_args(_text(update), "/rename")
    if len(args) < 2:
        await _msg(update).reply_text(
            formatter.error("Missing arguments.", "Try: rename 2 to <new name>.")
        )
        return
    index, new_name = args[0].strip(), " ".join(args[1:])
    if not _validate_index(index):
        await _msg(update).reply_text(
            formatter.error(
                "Invalid index format.",
                "Use indices like 1, 1.2, or 1.2.3 from the current list.",
            )
        )
        return
    try:
        item = nav.resolve_index(uid, index)
        if not item:
            await _msg(update).reply_text(
                formatter.error(
                    f"Index [{index}] not found.",
                    "Say \"show what's inside\" to refresh the list.",
                )
            )
            return
        assert context.user_data is not None
        context.user_data["pending_action"] = {
            "intent": "rename",
            "file_id": item.id,
            "name": item.name,
            "index": index,
            "new_name": new_name,
        }
        await _msg(update).reply_text(
            formatter.confirm_action("Rename", item.name),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_rename error")
        await _msg(update).reply_text(
            formatter.error("Rename failed.", "Check the name and try again.")
        )


async def cmd_move(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return
    if _check_rate_limit(uid, "move"):
        await _msg(update).reply_text(
            formatter.error("Please wait before moving again.")
        )
        return
    args = p.parse_args(_text(update), "/move")
    if len(args) < 2:
        await _msg(update).reply_text(
            formatter.error("Missing arguments.", "Try: move 2 to 5.")
        )
        return
    file_index, folder_index = args[0].strip(), args[1].strip()
    if not _validate_index(file_index) or not _validate_index(folder_index):
        await _msg(update).reply_text(
            formatter.error(
                "Invalid index format.",
                "Use indices like 1, 1.2, or 1.2.3 from the current list.",
            )
        )
        return
    try:
        file_item = nav.resolve_index(uid, file_index)
        if not file_item:
            await _msg(update).reply_text(
                formatter.error(
                    f"Index [{file_index}] not found.",
                    "Say \"show what's inside\" to refresh the list.",
                )
            )
            return
        dest_item = nav.resolve_index(uid, folder_index)
        if not dest_item:
            await _msg(update).reply_text(
                formatter.error(
                    f"Index [{folder_index}] not found.",
                    "Say \"show what's inside\" to refresh the list.",
                )
            )
            return
        if not dest_item.is_folder:
            await _msg(update).reply_text(
                formatter.error(
                    f"[{folder_index}] is not a folder.",
                    "Choose a folder index from the current list.",
                )
            )
            return
        dest_id = dest_item.id
        if dest_item.is_shortcut:
            if not dest_item.shortcut_target_id:
                await _msg(update).reply_text(
                    formatter.error(
                        "Shortcut target is unavailable.",
                        "Try selecting a different destination folder.",
                    )
                )
                return
            dest_id = dest_item.shortcut_target_id
        assert context.user_data is not None
        context.user_data["pending_action"] = {
            "intent": "move",
            "file_id": file_item.id,
            "name": file_item.name,
            "index": file_index,
            "dest_id": dest_id,
            "dest_name": dest_item.name,
        }
        await _msg(update).reply_text(
            formatter.confirm_action("Move", file_item.name),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_move error")
        await _msg(update).reply_text(
            formatter.error("Move failed.", "Check the indices and try again.")
        )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return
    if _check_rate_limit(uid, "delete"):
        await _msg(update).reply_text(
            formatter.error("Please wait before deleting again.")
        )
        return
    args = p.parse_args(_text(update), "/delete")
    if not args:
        await _msg(update).reply_text(
            formatter.error("Missing index.", "Try: delete 3.")
        )
        return
    target = args[0].strip()
    try:
        if _validate_index(target):
            item = nav.resolve_index(uid, target)
            if not item:
                await _msg(update).reply_text(
                    formatter.error(
                        f"Index [{target}] not found.",
                        "Say \"show what's inside\" to refresh the list.",
                    )
                )
                return
            if item.is_folder and not item.is_shortcut:
                await _msg(update).reply_text(
                    formatter.error(
                        f"[{target}] is a folder.",
                        "Say \"open 2\" to enter it, or delete files inside it.",
                    )
                )
                return
            meta = await ds.get_file_metadata_async(uid, item.id)
            meta["_path"] = item.path
            await _msg(update).reply_text(
                formatter.confirm_delete_preview(meta, target),
                reply_markup=ui.confirm_keyboard("delete", item.id),
            )
            return
        await _msg(update).reply_text(
            formatter.error(
                "Invalid index format.",
                "Say \"show what's inside\" and delete by number like \"delete 3\".",
            )
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
    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return
    if _check_rate_limit(uid, "mkdir"):
        await _msg(update).reply_text(
            formatter.error("Please wait before creating folders again.")
        )
        return
    args = p.parse_args(_text(update), "/mkdir")
    if not args:
        await _msg(update).reply_text(
            formatter.error("Missing name.", "Try: create folder <name>.")
        )
        return
    name = " ".join(args)
    try:
        created = await ds.create_folder_async(uid, name, parent_id=nav.current_folder_id(uid))
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
# /index — Index current folder for NLP search
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_index(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return
    if _check_rate_limit(uid, "index"):
        await _msg(update).reply_text(
            formatter.error("Please wait before indexing again.")
        )
        return

    manager = get_task_manager(context)
    if not manager:
        await _msg(update).reply_text(
            formatter.error("Background queue unavailable.", "Try again later.")
        )
        return

    try:
        fid = nav.current_folder_id(uid)
        listing = await ds.list_directory_async(uid, parent_id=fid)
        count = 0
        for item in listing.files:
            file_id = item.get("id")
            if not file_id:
                continue
            indexer.upsert_metadata(
                uid,
                file_id,
                item.get("name", "file"),
                item.get("mimeType"),
                fid,
                int(item.get("size") or 0) if item.get("size") else None,
                None,
            )
            await manager.enqueue_index(uid, file_id)
            count += 1

        if count == 0:
            await _msg(update).reply_text(
                formatter.error("No files to index in this folder.")
            )
            return
        await _msg(update).reply_text(
            formatter.success("Indexing Started", f"{count} files"),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await _msg(update).reply_text(formatter.login_required())
    except Exception:
        logger.exception("cmd_index error")
        await _msg(update).reply_text(
            formatter.error("Indexing failed.", "Try again later.")
        )


# ─────────────────────────────────────────────────────────────────────────────
# /zip <keyword>
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)

    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return

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
    keyword = validators.normalize_keyword(" ".join(args), limits.MAX_SEARCH_LEN)
    if not keyword:
        await _msg(update).reply_text(
            formatter.error("Invalid keyword.", "Try a different search term.")
        )
        return

    try:
        with timing.timed("cmd_zip"):
            manager = get_task_manager(context)
            if not manager:
                await _msg(update).reply_text(
                    formatter.error("Background queue unavailable.", "Try again later.")
                )
                return
            assert update.effective_chat is not None
            await manager.enqueue_zip(uid, update.effective_chat.id, keyword)
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
            assert context.user_data is not None
            context.user_data["awaiting_email"] = True
            await _msg(update).reply_text(
                "📧 Security Alerts\n\n"
                "Set your email to receive alerts if unusual activity is detected.\n\n"
                "Reply with your email now (e.g. you@example.com)\n"
                "or use /email your-email@example.com",
                reply_markup=ui.stepup_email_entry_keyboard(),
            )
        return
    
    email = " ".join(args).strip()
    
    # Validate email format
    if not validators.validate_email(email):
        await _msg(update).reply_text(
            formatter.error(
                "Invalid email format.",
                f"Please use a valid email like: user@example.com"
            )
        )
        return
    
    # Store email
    if models.set_user_email(uid, email):
        assert context.user_data is not None
        context.user_data.pop("awaiting_email", None)
        await _msg(update).reply_text(
            f"✅ Email updated\n\n"
            f"Address: {email}\n\n"
            "You will receive security alerts at this email if suspicious activity is detected."
        )
        logger.info("Email set for user %s: %s", uid, email)

        pending_action = context.user_data.get("pending_stepup_action")
        if pending_action:
            result = await stepup_auth.request_verification(uid, pending_action)
            status = result.get("status")
            if status == "verified":
                context.user_data.pop("pending_stepup_action", None)
                await _msg(update).reply_text(
                    formatter.stepup_verified(result.get("window", 5)),
                    reply_markup=ui.post_login_keyboard(),
                )
            elif status == "sent":
                context.user_data["awaiting_otp"] = True
                await _msg(update).reply_text(
                    formatter.stepup_code_sent(
                        pending_action,
                        result.get("email", ""),
                        result.get("ttl", 10),
                    ),
                    reply_markup=ui.stepup_resend_keyboard(pending_action),
                )
            elif status == "cooldown":
                context.user_data["awaiting_otp"] = True
                await _msg(update).reply_text(
                    formatter.stepup_code_pending(
                        pending_action,
                        result.get("email", ""),
                        result.get("retry_after", 60),
                    ),
                    reply_markup=ui.stepup_resend_keyboard(pending_action),
                )
            elif status == "email_failed":
                await _msg(update).reply_text(formatter.stepup_email_failed())
    else:
        await _msg(update).reply_text(
            formatter.error(
                "Failed to update email.",
                "Please try again later."
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# /verify  — Confirm a sensitive action with OTP
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(_text(update), "/verify")

    if not _is_authenticated(uid):
        await _msg(update).reply_text(formatter.login_required())
        return

    if not args:
        await _msg(update).reply_text(
            formatter.error("Missing code.", "Reply with the 6-digit code.")
        )
        return

    code = args[0].strip()
    if not (code.isdigit() and len(code) == 6):
        await _msg(update).reply_text(
            formatter.error("Invalid code format.", "Use the 6-digit code from your email.")
        )
        return

    result = stepup_auth.verify_code(uid, code)
    status = result.get("status")

    if status == "verified":
        assert context.user_data is not None
        context.user_data.pop("awaiting_otp", None)
        context.user_data.pop("pending_stepup_action", None)
        await _msg(update).reply_text(
            formatter.stepup_verified(result.get("window", 5)),
            reply_markup=ui.post_login_keyboard(),
        )
        return
    if status == "already_verified":
        assert context.user_data is not None
        context.user_data.pop("awaiting_otp", None)
        await _msg(update).reply_text(
            formatter.stepup_already_verified(result.get("remaining", 1))
        )
        return
    if status == "invalid":
        await _msg(update).reply_text(
            formatter.stepup_invalid_code(result.get("remaining", 0))
        )
        return
    if status == "expired":
        assert context.user_data is not None
        context.user_data.pop("awaiting_otp", None)
        context.user_data.pop("pending_stepup_action", None)
        await _msg(update).reply_text(formatter.stepup_code_expired())
        return
    if status == "locked":
        assert context.user_data is not None
        context.user_data.pop("awaiting_otp", None)
        context.user_data.pop("pending_stepup_action", None)
        await _msg(update).reply_text(formatter.stepup_locked())
        return

    await _msg(update).reply_text(
        formatter.error("No active verification.", "Retry the action to get a new code.")
    )
