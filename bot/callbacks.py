"""
bot/callbacks.py
Handles ALL inline keyboard button presses (CallbackQueryHandler).

Callback data format:  "<namespace>:<action>[:<arg1>[:<arg2>]]"
  nav:browse / nav:back / nav:home / nav:menu / nav:pwd / nav:refresh
  nav:search / nav:upload / nav:login / nav:logout / nav:tools / nav:mkdir
  file:download:<id> / file:info:<id> / file:fav:<id> / file:delete:<id>
  confirm:delete:<id>
  upload:confirm / upload:cancel

Security:
  - Callback data validated and length-limited
  - file_id format validated before use
  - Error messages sanitized
  - Authentication checks on all file operations
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes
from googleapiclient.errors import HttpError

from drive import drive_service as ds
from bot import formatter, ui, nav
from bot.dialogue import (
    cancel_typed_pending_callback_work,
    publish_active_view_to_dialogue,
)
from db import models
from services.parser import human_size
from services import anomaly_detection
from services import stepup_auth
from security import limits, validators, uploads
from security.rate_limit import get_rate_limiter
from tasks.manager import get_task_manager
from storage import sandbox
from monitoring import context as monitoring_context
from monitoring import timing
from indexing import indexer

logger = logging.getLogger(__name__)
_RATE_LIMITER = get_rate_limiter()

# Telegram bot API hard limit (bytes). Use 45 MB to stay safely below 50 MB.
TELEGRAM_LIMIT = limits.MAX_TELEGRAM_DOWNLOAD_BYTES

# Max callback data length Telegram allows is 64 bytes; we validate within reason
_MAX_CALLBACK_DATA_LEN = 128


# ── validation helpers ────────────────────────────────────────────────────────

def _validate_callback_data(data: str) -> bool:
    """Basic validation on callback data to prevent injection."""
    if not data or len(data) > _MAX_CALLBACK_DATA_LEN:
        return False
    # Only allow printable ASCII, no control characters
    if not all(32 <= ord(c) <= 126 for c in data):
        return False
    return True


def _validate_file_id(file_id: str) -> bool:
    return validators.validate_drive_id(file_id, allow_root=False)


def _is_authenticated(uid: int) -> bool:
    """Check if user has valid credentials."""
    user = models.get_user(uid)
    return bool(user and user["token"])


# ── helpers ───────────────────────────────────────────────────────────────────

async def _reply(query, update, text: str, markup=None) -> None:
    """Send a NEW message (not edit) for clean UX. Answer the callback silently."""
    kwargs = {"text": text}
    if markup:
        kwargs["reply_markup"] = markup
    await update.effective_chat.send_message(**kwargs)


async def _edit(query, text: str, markup=None) -> None:
    """Edit the existing message (used sparingly for in-place updates)."""
    kwargs = {"text": text}
    if markup:
        kwargs["reply_markup"] = markup
    try:
        await query.edit_message_text(**kwargs)
    except Exception:
        pass  # Message may have been deleted or unchanged


async def _require_stepup(uid: int, action_label: str, query, update, context) -> bool:
    """Ensure step-up verification before sensitive actions."""
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
        await _reply(
            query, update,
            formatter.stepup_email_required(action_label),
            ui.stepup_email_entry_keyboard(),
        )
        return False
    if status == "email_failed":
        await _reply(query, update, formatter.stepup_email_failed())
        return False
    if status == "sent":
        context.user_data["awaiting_otp"] = True
        context.user_data["pending_stepup_action"] = action_label
        await _reply(
            query, update,
            formatter.stepup_code_sent(action_label, result.get("email", ""), result.get("ttl", 10)),
            ui.stepup_resend_keyboard(action_label),
        )
        return False
    if status == "cooldown":
        context.user_data["awaiting_otp"] = True
        context.user_data["pending_stepup_action"] = action_label
        await _reply(
            query, update,
            formatter.stepup_code_pending(action_label, result.get("email", ""), result.get("retry_after", 60)),
            ui.stepup_resend_keyboard(action_label),
        )
        return False
    await _reply(query, update, formatter.error("Verification required.", "Reply with the 6-digit code."))
    return False


async def _send_browse(uid: int, query, update, context=None) -> None:
    """Fetch current folder contents and send as a new message."""
    try:
        with timing.timed("cb_browse"):
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
            if context is not None:
                publish_active_view_to_dialogue(
                    update,
                    context,
                    authenticated=True,
                )
            
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

            await _reply(query, update, text, ui.browse_keyboard(is_root=is_root))
    except PermissionError:
        await _reply(query, update, formatter.login_required())
    except Exception as e:
        logger.exception("_send_browse error")
        if isinstance(e, HttpError):
            status = getattr(e, "resp", None)
            if status and status.status in (400, 401, 403, 404, 410):
                try:
                    from drive import auth as drive_auth
                    drive_auth.revoke_token(uid)
                    models.delete_user(uid)
                    nav.clear_user(uid)
                except Exception:
                    pass
                await _reply(query, update, formatter.login_required())
                return
        await _reply(query, update, formatter.error(
            "Could not load directory.", "Try again or say \"connect my drive\"."
        ))


async def _send_recent(uid: int, query, update, context=None) -> None:
    """Fetch recent files and send as a new message."""
    try:
        recent = await ds.get_recent_files_async(uid, limit=limits.MAX_RECENT_ITEMS)
        if not recent:
            await _reply(query, update, formatter.recent_results({}), ui.back_to_menu_keyboard())
            return
        index_map: dict[str, nav.IndexedItem] = {}
        for i, item in enumerate(recent, 1):
            idx = str(i)
            index_map[idx] = nav.IndexedItem(
                id=item.get("id", ""),
                name=item.get("name", "file"),
                mime_type=item.get("mimeType", ""),
                is_folder=False,
                parent_index="",
                full_index=idx,
                path="Recent",
            )
        nav.set_active_view(uid, "recent", index_map)
        if context is not None:
            publish_active_view_to_dialogue(
                update,
                context,
                authenticated=True,
            )
        await _reply(
            query,
            update,
            formatter.recent_results(index_map),
            ui.back_to_menu_keyboard(),
        )
    except Exception:
        logger.exception("_send_recent error")
        await _reply(query, update, formatter.error("Could not load recent files."))

# ── main dispatcher ───────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query is not None  # guaranteed by CallbackQueryHandler
    assert update.effective_user is not None
    uid = update.effective_user.id
    monitoring_context.set_request_context(user_id=uid, request_id=str(update.update_id), operation="callback")
    data = query.data or ""

    await query.answer()

    # Validate callback data format
    if not _validate_callback_data(data):
        logger.warning("Invalid callback data from user %s: length=%d", uid, len(data))
        return

    parts = data.split(":")
    ns = parts[0]

    if ns == "dialogue":
        action = parts[1] if len(parts) > 1 else ""
        if action == "cancel" and len(parts) == 2:
            if cancel_typed_pending_callback_work(update, context):
                await _reply(query, update, "Cancelled. No changes were made.")
            else:
                await _reply(
                    query,
                    update,
                    "There is no active action to cancel.",
                )
        return

    # ── Navigation ────────────────────────────────────────────────────────────

    if ns == "nav":
        action = parts[1] if len(parts) > 1 else ""

        if action == "home":
            if not _is_authenticated(uid):
                await _reply(query, update, formatter.login_required())
            elif nav.current_folder_id(uid) == "root":
                await _reply(query, update, formatter.already_home())
            else:
                nav.go_home(uid)
                await _send_browse(uid, query, update, context)

        elif action == "back":
            nav.pop_folder(uid)
            await _send_browse(uid, query, update, context)

        elif action in ("browse", "refresh"):
            await _send_browse(uid, query, update, context)

        elif action == "menu":
            await _reply(
                query, update,
                formatter.main_menu(),
                ui.main_menu_keyboard(),
            )

        elif action == "pwd":
            await _reply(
                query, update,
                formatter.current_path(nav.breadcrumb(uid)),
            )

        elif action == "search":
            await _reply(
                query, update,
                "🔍 Search\n\nSend your query in plain language.\nExample: show my dbms notes",
                ui.back_to_menu_keyboard(),
            )

        elif action == "upload":
            if not _is_authenticated(uid):
                await _reply(query, update, formatter.login_required())
                return
            assert context.user_data is not None
            context.user_data["upload_mode"] = True
            await _reply(
                query,
                update,
                "📤 Upload File\n\nSend a document, image, or video to upload.\n"
                "Tip: say \"upload this to notes\" to set a target folder.",
            )

        elif action == "login":
            from drive import auth as drive_auth
            try:
                url = drive_auth.get_auth_url(uid)
                await _reply(
                    query, update,
                    formatter.welcome_unauthenticated(),
                    ui.login_keyboard(url),
                )
            except FileNotFoundError:
                await _reply(
                    query, update,
                    formatter.error("OAuth credentials not configured.", "Contact the bot administrator."),
                )

        elif action == "logout":
            # V-NEW-02: Revoke token at Google before local deletion
            from drive import auth as drive_auth
            drive_auth.revoke_token(uid)
            models.delete_user(uid)  # Also cleans up favorites
            nav.clear_user(uid)
            await _reply(query, update, formatter.logout_successful())

        elif action == "tools":
            await _reply(
                query, update,
                formatter.tools_menu(),
                ui.back_to_menu_keyboard(),
            )

        elif action == "help":
            await _reply(
                query, update,
                formatter.tools_menu(),
                ui.back_to_menu_keyboard(),
            )

        elif action == "recent":
            if not _is_authenticated(uid):
                await _reply(query, update, formatter.login_required())
                return
            await _send_recent(uid, query, update, context)

        elif action == "mkdir":
            await _reply(
                query, update,
                "📁 Create Folder\n\nSay: create a folder called projects",
                ui.back_to_menu_keyboard(),
            )

        elif action == "clear":
            assert update.effective_chat is not None
            assert query.message is not None
            chat_id = update.effective_chat.id
            msg_id = query.message.message_id
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

        return

    # ── Step-up verification ──────────────────────────────────────────────────

    if ns == "stepup":
        action = parts[1] if len(parts) > 1 else ""
        action_label = parts[2] if len(parts) > 2 else ""
        allowed_actions = {"delete files", "download files", "upload files"}
        if action == "resend" and action_label in allowed_actions:
            if not _is_authenticated(uid):
                await _reply(query, update, formatter.login_required())
                return
            if await _require_stepup(uid, action_label, query, update, context):
                await _reply(query, update, formatter.stepup_already_verified(5))
            return

    # ── File ──────────────────────────────────────────────────────────────────

    if ns == "file":
        action = parts[1] if len(parts) > 1 else ""
        file_id = parts[2] if len(parts) > 2 else ""

        # Validate file_id format before any Drive API call
        if not _validate_file_id(file_id):
            logger.warning("Invalid file_id in callback from user %s", uid)
            await _reply(query, update, formatter.error("Invalid file reference."))
            return

        # All file operations require authentication
        if not _is_authenticated(uid):
            await _reply(query, update, formatter.login_required())
            return

        if action == "download":
            await _handle_download(uid, file_id, query, context, update)

        elif action in ("view", "info"):
            try:
                meta = await ds.get_file_metadata_async(uid, file_id)
                is_fav = models.is_favorite(uid, file_id)
                await _reply(
                    query, update,
                    formatter.file_info(meta),
                    ui.file_actions_keyboard(file_id, is_fav),
                )
            except Exception as e:
                logger.exception("file:info callback error")
                await _reply(query, update, formatter.error(
                    "Could not load file details."
                ))

        elif action == "fav":
            try:
                if models.is_favorite(uid, file_id):
                    models.remove_favorite(uid, file_id)
                    label = "Removed from Favorites"
                else:
                    models.add_favorite(uid, file_id)
                    label = "Added to Favorites"
                meta = await ds.get_file_metadata_async(uid, file_id)
                await _reply(
                    query, update,
                    formatter.success(label, meta.get("name")),
                    ui.back_to_menu_keyboard(),
                )
            except Exception as e:
                logger.exception("file:fav callback error")
                await _reply(query, update, formatter.error(
                    "Could not update favorites."
                ))

        elif action == "delete":
            try:
                meta = await ds.get_file_metadata_async(uid, file_id)
                meta["_path"] = nav.breadcrumb(uid)
                await _reply(
                    query, update,
                    formatter.confirm_delete_preview(meta),
                    ui.confirm_keyboard("delete", file_id),
                )
            except Exception as e:
                logger.exception("file:delete callback error")
                await _reply(query, update, formatter.error(
                    "Could not prepare deletion."
                ))

        return

    # ── Confirmation ──────────────────────────────────────────────────────────

    if ns == "confirm":
        action = parts[1] if len(parts) > 1 else ""
        file_id = parts[2] if len(parts) > 2 else ""

        # Validate file_id
        if not _validate_file_id(file_id):
            logger.warning("Invalid file_id in confirm callback from user %s", uid)
            await _reply(query, update, formatter.error("Invalid file reference."))
            return

        if not _is_authenticated(uid):
            await _reply(query, update, formatter.login_required())
            return

        if action == "delete":
            try:
                if _RATE_LIMITER.limited(uid, "delete"):
                    await _reply(query, update, formatter.error("Please wait before deleting again."))
                    return
                if not await _require_stepup(uid, "delete files", query, update, context):
                    return

                # Check for anomaly before deleting
                if await anomaly_detection.check_anomaly(uid, "delete"):
                    await _reply(query, update, formatter.error(
                        "⛔ Unusual Activity Detected",
                        "Your Google Drive access has been suspended for security.\n\n"
                        "Check your email and Telegram for alerts.\n"
                        "Use /login to reconnect when ready."
                    ))
                    return
                
                await ds.delete_file_async(uid, file_id)
                await _reply(
                    query, update,
                    formatter.success("Deleted"),
                    ui.back_to_menu_keyboard(),
                )
            except Exception as e:
                logger.exception("confirm:delete callback error")
                await _reply(query, update, formatter.error(
                    "Deletion failed.", "The file may have already been removed."
                ))
        return

    # ── Upload confirmation ───────────────────────────────────────────────────

    if ns == "upload":
        action = parts[1] if len(parts) > 1 else ""

        if not _is_authenticated(uid):
            await _reply(query, update, formatter.login_required())
            return

        if action == "confirm":
            assert context.user_data is not None
            pending = context.user_data.get("pending_upload")
            if not pending:
                await _reply(query, update, formatter.error("No pending upload."))
                return

            try:
                if _RATE_LIMITER.limited(uid, "upload"):
                    await _reply(query, update, formatter.error("Please wait before uploading again."))
                    return
                if not await _require_stepup(uid, "upload files", query, update, context):
                    return

                from drive.drive_service import upload_file_async
                fid = nav.current_folder_id(uid)
                tg_file = await context.bot.get_file(pending["file_id"])
                file_bytes = await tg_file.download_as_bytearray()
                ok, reason, safe_name, detected_mime = uploads.validate_upload(
                    bytes(file_bytes),
                    pending.get("file_name", ""),
                    pending.get("declared_mime", ""),
                    limits.MAX_UPLOAD_BYTES,
                )
                if not ok:
                    await _reply(query, update, formatter.error(reason))
                    return
                temp_path = sandbox.write_bytes(uid, safe_name, bytes(file_bytes))
                try:
                    uploaded = await upload_file_async(
                        uid,
                        bytes(file_bytes),
                        safe_name,
                        parent_id=fid,
                    )
                    manager = get_task_manager(context)
                    if manager:
                        await manager.enqueue_index(uid, uploaded["id"])
                    await _reply(
                        query, update,
                        formatter.upload_success(uploaded["name"], nav.breadcrumb(uid)),
                        ui.post_login_keyboard(),
                    )
                finally:
                    sandbox.remove_file(temp_path)
            except Exception as e:
                logger.exception("Upload confirm error")
                await _reply(query, update, formatter.error(
                    "Upload failed.", "Try again or check your connection."
                ))
            finally:
                context.user_data.pop("pending_upload", None)
                context.user_data.pop("upload_mode", None)

        elif action == "cancel":
            assert context.user_data is not None
            context.user_data.pop("pending_upload", None)
            context.user_data.pop("upload_mode", None)
            await _reply(
                query, update,
                formatter.success("Upload Cancelled"),
                ui.post_login_keyboard(),
            )

        return

    logger.warning("Unhandled callback namespace: %s from user %s", ns, uid)


# ── Download handler ──────────────────────────────────────────────────────────

async def _handle_download(uid: int, file_id: str, query, context, update) -> None:
    """Download a file from Drive and send it to Telegram."""
    try:
        with timing.timed("cb_download"):
            if _RATE_LIMITER.limited(uid, "download"):
                await _reply(query, update, formatter.error("Please wait before downloading again."))
                return
            if not await _require_stepup(uid, "download files", query, update, context):
                return

            # Check for anomaly before downloading
            if await anomaly_detection.check_anomaly(uid, "download"):
                await _reply(query, update, formatter.error(
                    "⛔ Unusual Activity Detected",
                    "Your Google Drive access has been suspended for security.\n\n"
                    "Check your email and Telegram for alerts.\n"
                    "Use /login to reconnect when ready."
                ))
                return

            meta = await ds.get_file_metadata_async(uid, file_id)
            fname = meta.get("name", "file")
            size_raw = int(meta["size"]) if meta.get("size") else 0
            size_str = human_size(size_raw) if size_raw else "Unknown"

            if size_raw > TELEGRAM_LIMIT:
                view_link = meta.get("webViewLink", "")
                content_link = meta.get("webContentLink", "")
                await _reply(
                    query, update,
                    formatter.download_too_large(fname, size_str, view_link, content_link),
                    ui.back_to_menu_keyboard(),
                )
                return

            manager = get_task_manager(context)
            if not manager:
                await _reply(query, update, formatter.error("Background queue unavailable."))
                return
            assert update.effective_chat is not None
            await manager.enqueue_download(
                telegram_id=uid,
                chat_id=update.effective_chat.id,
                file_id=file_id,
                filename=fname,
                size_str=size_str,
            )

    except PermissionError:
        await _reply(query, update, formatter.login_required(), ui.back_to_menu_keyboard())
    except Exception as e:
        logger.exception("Download failed for file_id in callback")
        await _reply(
            query, update,
            formatter.error(
                "Download failed.",
                "Try again or open the file directly in Google Drive.",
            ),
            ui.back_to_menu_keyboard(),
        )
