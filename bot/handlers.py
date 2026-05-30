"""
bot/handlers.py
Registers all handlers with the Application.
Called once from main.py.

Handler registration order:
  1. Commands (highest priority)
  2. Inline keyboard callbacks
  3. File upload handler (documents, images, videos)

Security:
  - Upload size validation
  - Filename sanitization
  - Authentication checks
"""

import logging

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from bot.commands import (
    cmd_start,
    cmd_menu,
    cmd_help,
    cmd_tool,
    cmd_info,
    cmd_cd,
    cmd_pwd,
    cmd_download,
    cmd_more,
    cmd_upload,
    cmd_cancel,
    cmd_search,
    cmd_rename,
    cmd_move,
    cmd_delete,
    cmd_mkdir,
    cmd_zip,
    cmd_logout,
    cmd_email,
    cmd_verify,
    cmd_clear,
    cmd_index,
)
from bot.callbacks import handle_callback
from bot.errors import handle_error
from bot import formatter, nav, ui
from db import models
from services import stepup_auth
from security import limits, validators
from security import uploads
from security.rate_limit import get_rate_limiter
from rapidfuzz import process, fuzz
from nlp import router as nlp_router
from tasks.manager import get_task_manager
from storage import sandbox
from monitoring import context as monitoring_context

logger = logging.getLogger(__name__)
_RATE_LIMITER = get_rate_limiter()

# Max upload size (20 MB — Telegram bot API limit for file downloads)
MAX_UPLOAD_BYTES = limits.MAX_UPLOAD_BYTES


async def handle_file_upload(update, context) -> None:
    """
    Handles document, image, and video messages.

    Uploads directly to the current Drive folder.

    Security:
      - Validates file size before downloading
      - Sanitizes filename
      - Checks authentication
    """
    from drive.drive_service import upload_file_async, _sanitize_filename

    uid = update.effective_user.id
    monitoring_context.set_request_context(user_id=uid, request_id=str(update.update_id), operation="upload")
    if not update.message:
        return

    doc = update.message.document
    video = update.message.video
    photo = update.message.photo[-1] if update.message.photo else None

    if not doc and not video and not photo:
        return

    if _RATE_LIMITER.limited(uid, "upload"):
        await update.message.reply_text(
            formatter.error("Please wait before uploading again.")
        )
        return
    # Check authentication
    from bot.commands import _is_authenticated
    if not _is_authenticated(uid):
        await update.message.reply_text(formatter.login_required())
        return

    # Resolve NLP upload target if provided
    target_folder_id = None
    target_name = context.user_data.get("pending_upload_target")
    if target_name:
        view = await nlp_router._ensure_folder_view(uid)
        if not view:
            await update.message.reply_text(
                formatter.error("No folders found here.", "Say \"show what's inside\" to refresh the list.")
            )
            return
        folder_items = {item.name: item for item in view.index_map.values() if item.is_folder}
        matches = process.extract(target_name, folder_items.keys(), scorer=fuzz.WRatio, limit=5)
        if not matches:
            await update.message.reply_text(
                formatter.error("No matching folder found.")
            )
            return
        best_name, best_score, _ = matches[0]
        second_score = matches[1][1] if len(matches) > 1 else 0
        if best_score < 90 or (best_score - second_score) < 12:
            await update.message.reply_text(
                formatter.nlp_suggestions("Closest Folders", [m[0] for m in matches])
            )
            return
        item = folder_items.get(best_name)
        if item:
            target_folder_id = item.shortcut_target_id if item.is_shortcut and item.shortcut_target_id else item.id
            context.user_data.pop("pending_upload_target", None)

    # Validate file size before downloading (defense in depth)
    file_size = None
    file_id = ""
    file_name = ""

    declared_mime = ""
    if doc:
        file_id = doc.file_id
        file_size = doc.file_size
        file_name = doc.file_name or "unnamed_file"
        declared_mime = doc.mime_type or ""
    elif video:
        file_id = video.file_id
        file_size = video.file_size
        file_name = video.file_name or f"video_{video.file_unique_id}.mp4"
        declared_mime = video.mime_type or ""
    elif photo:
        file_id = photo.file_id
        file_size = photo.file_size
        file_name = f"photo_{photo.file_unique_id}.jpg"

    if file_size and file_size > MAX_UPLOAD_BYTES:
        await update.message.reply_text(
            formatter.error(
                f"File too large ({file_size // (1024*1024)} MB).",
                f"Maximum upload size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
            )
        )
        return

    try:
        # Step-up verification for uploads
        result = await stepup_auth.request_verification(uid, "upload files")
        status = result.get("status")
        if status != "verified":
            assert context.user_data is not None
            context.user_data["pending_upload"] = {
                "file_id": file_id,
                "file_name": _sanitize_filename(file_name),
                "declared_mime": declared_mime,
            }
            context.user_data["pending_stepup_action"] = "upload files"
            await update.message.reply_text(
                formatter.upload_confirm(_sanitize_filename(file_name), nav.breadcrumb(uid)),
                reply_markup=ui.upload_confirm_keyboard(),
            )
            if status == "no_email":
                context.user_data["awaiting_email"] = True
                await update.message.reply_text(
                    formatter.stepup_email_required("upload files"),
                    reply_markup=ui.stepup_email_entry_keyboard(),
                )
            elif status == "email_failed":
                await update.message.reply_text(formatter.stepup_email_failed())
            elif status == "sent":
                context.user_data["awaiting_otp"] = True
                await update.message.reply_text(
                    formatter.stepup_code_sent(
                        "upload files",
                        result.get("email", ""),
                        result.get("ttl", 10),
                    ),
                    reply_markup=ui.stepup_resend_keyboard("upload files"),
                )
            elif status == "cooldown":
                context.user_data["awaiting_otp"] = True
                await update.message.reply_text(
                    formatter.stepup_code_pending(
                        "upload files",
                        result.get("email", ""),
                        result.get("retry_after", 60),
                    ),
                    reply_markup=ui.stepup_resend_keyboard("upload files"),
                )
            else:
                await update.message.reply_text(
                    formatter.error("Verification required.", "Reply with the 6-digit code.")
                )
            return
        tg_file = await context.bot.get_file(file_id)
        file_bytes = await tg_file.download_as_bytearray()
        ok, reason, safe_filename, detected_mime = uploads.validate_upload(
            bytes(file_bytes), file_name, declared_mime, MAX_UPLOAD_BYTES
        )
        if not ok:
            await update.message.reply_text(formatter.error(reason))
            return
        temp_path = sandbox.write_bytes(uid, safe_filename, bytes(file_bytes))

        # Direct upload (no confirmation)
        await update.message.reply_text(formatter.processing("Uploading"))
        fid = target_folder_id or nav.current_folder_id(uid)
        try:
            uploaded = await upload_file_async(uid, bytes(file_bytes), safe_filename, parent_id=fid)
            manager = get_task_manager(context)
            if manager:
                await manager.enqueue_index(uid, uploaded["id"])
            await update.message.reply_text(
                formatter.upload_success(uploaded["name"], nav.breadcrumb(uid)),
                reply_markup=ui.post_login_keyboard(),
            )
        finally:
            sandbox.remove_file(temp_path)

    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("File upload error")
        await update.message.reply_text(
            formatter.error("Upload failed.", "Try again or check your connection.")
        )


async def handle_text_input(update, context) -> None:
    """Handle guided email/OTP input without requiring commands."""
    if not update.message or not update.message.text:
        return

    assert context.user_data is not None
    uid = update.effective_user.id
    monitoring_context.set_request_context(user_id=uid, request_id=str(update.update_id), operation="text")
    text = update.message.text.strip()
    from bot.commands import _is_authenticated
    is_authenticated = _is_authenticated(uid)

    if context.user_data.get("awaiting_email"):
        if not validators.validate_email(text):
            await update.message.reply_text(
                formatter.error(
                    "Invalid email format.",
                    "Reply with a valid address like user@example.com",
                )
            )
            return

        if models.set_user_email(uid, text):
            context.user_data.pop("awaiting_email", None)
            await update.message.reply_text(
                f"✅ Email updated\n\n"
                f"Address: {text}\n\n"
                "You will receive security alerts at this email if suspicious activity is detected."
            )

            pending_action = context.user_data.get("pending_stepup_action")
            if pending_action:
                result = await stepup_auth.request_verification(uid, pending_action)
                status = result.get("status")
                if status == "verified":
                    context.user_data.pop("pending_stepup_action", None)
                    await update.message.reply_text(
                        formatter.stepup_verified(result.get("window", 5)),
                        reply_markup=ui.post_login_keyboard(),
                    )
                elif status == "sent":
                    context.user_data["awaiting_otp"] = True
                    await update.message.reply_text(
                        formatter.stepup_code_sent(
                            pending_action,
                            result.get("email", ""),
                            result.get("ttl", 10),
                        ),
                        reply_markup=ui.stepup_resend_keyboard(pending_action),
                    )
                elif status == "cooldown":
                    context.user_data["awaiting_otp"] = True
                    await update.message.reply_text(
                        formatter.stepup_code_pending(
                            pending_action,
                            result.get("email", ""),
                            result.get("retry_after", 60),
                        ),
                        reply_markup=ui.stepup_resend_keyboard(pending_action),
                    )
                elif status == "email_failed":
                    await update.message.reply_text(formatter.stepup_email_failed())
            return

        await update.message.reply_text(
            formatter.error("Failed to update email.", "Please try again later.")
        )
        return

    if context.user_data.get("awaiting_otp"):
        if not (text.isdigit() and len(text) == 6):
            await update.message.reply_text(
                formatter.error("Invalid code format.", "Reply with the 6-digit code.")
            )
            return

        result = stepup_auth.verify_code(uid, text)
        status = result.get("status")

        if status == "verified":
            context.user_data.pop("awaiting_otp", None)
            context.user_data.pop("pending_stepup_action", None)
            await update.message.reply_text(
                formatter.stepup_verified(result.get("window", 5)),
                reply_markup=ui.post_login_keyboard(),
            )
            return
        if status == "already_verified":
            context.user_data.pop("awaiting_otp", None)
            await update.message.reply_text(
                formatter.stepup_already_verified(result.get("remaining", 1))
            )
            return
        if status == "invalid":
            await update.message.reply_text(
                formatter.stepup_invalid_code(result.get("remaining", 0))
            )
            return
        if status == "expired":
            context.user_data.pop("awaiting_otp", None)
            context.user_data.pop("pending_stepup_action", None)
            await update.message.reply_text(formatter.stepup_code_expired())
            return
        if status == "locked":
            context.user_data.pop("awaiting_otp", None)
            context.user_data.pop("pending_stepup_action", None)
            await update.message.reply_text(formatter.stepup_locked())
            return

        await update.message.reply_text(
            formatter.error("No active verification.", "Retry the action to get a new code.")
        )
        return

    if await nlp_router.handle_pending_action(update, context):
        return

    # Passive email capture after login (if user replies with an email)
    if is_authenticated and validators.validate_email(text) and not models.get_user_email(uid):
        if models.set_user_email(uid, text):
            await update.message.reply_text(
                f"✅ Email updated\n\n"
                f"Address: {text}\n\n"
                "You will receive security alerts at this email if suspicious activity is detected."
            )
        else:
            await update.message.reply_text(
                formatter.error("Failed to update email.", "Please try again later.")
            )
        return

    if await nlp_router.handle_nlp_message(update, context):
        return

    if not is_authenticated:
        await update.message.reply_text(formatter.login_required())
        return


def register_handlers(app: Application) -> None:
    # ── Core commands ─────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("menu",     cmd_menu))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("tool",     cmd_tool))

    # ── Navigation ────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("info",     cmd_info))
    app.add_handler(CommandHandler("browse",   cmd_info))      # alias
    app.add_handler(CommandHandler("ls",       cmd_info))      # alias
    app.add_handler(CommandHandler("cd",       cmd_cd))
    app.add_handler(CommandHandler("back",     cmd_cd))        # /back → /cd (no args = go back)
    app.add_handler(CommandHandler("pwd",      cmd_pwd))

    # ── File operations ───────────────────────────────────────────────────────
    app.add_handler(CommandHandler("download", cmd_download))
    app.add_handler(CommandHandler("dl",       cmd_download))  # alias
    app.add_handler(CommandHandler("more",     cmd_more))
    app.add_handler(CommandHandler("search",   cmd_search))
    app.add_handler(CommandHandler("upload",   cmd_upload))
    app.add_handler(CommandHandler("cancel",   cmd_cancel))

    # ── Management ────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("rename",   cmd_rename))
    app.add_handler(CommandHandler("move",     cmd_move))
    app.add_handler(CommandHandler("delete",   cmd_delete))
    app.add_handler(CommandHandler("mkdir",    cmd_mkdir))
    app.add_handler(CommandHandler("index",    cmd_index))
    app.add_handler(CommandHandler("create_folder", cmd_mkdir))  # alias
    app.add_handler(CommandHandler("zip",      cmd_zip))

    # ── Account ───────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("logout",   cmd_logout))
    app.add_handler(CommandHandler("email",    cmd_email))
    app.add_handler(CommandHandler("verify",   cmd_verify))
    app.add_handler(CommandHandler("clear",    cmd_clear))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ── Guided text input (email / OTP) ───────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    # ── File uploads ──────────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, handle_file_upload))

    # ── Global error handler ───────────────────────────────────────────────────
    app.add_error_handler(handle_error)

    logger.info("All handlers registered successfully.")
