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
    cmd_clear,
)
from bot.callbacks import handle_callback
from bot import formatter, nav, ui

logger = logging.getLogger(__name__)

# Max upload size (20 MB — Telegram bot API limit for file downloads)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


async def handle_file_upload(update, context) -> None:
    """
    Handles document, image, and video messages.

    Uploads directly to the current Drive folder.

    Security:
      - Validates file size before downloading
      - Sanitizes filename
      - Checks authentication
    """
    from drive.drive_service import upload_file, _sanitize_filename

    uid = update.effective_user.id
    if not update.message:
        return

    doc = update.message.document
    video = update.message.video
    photo = update.message.photo[-1] if update.message.photo else None

    if not doc and not video and not photo:
        return

    # Check authentication first
    from bot.commands import _is_authenticated
    if not _is_authenticated(uid):
        await update.message.reply_text(formatter.login_required())
        return

    # Validate file size before downloading (defense in depth)
    file_size = None
    file_id = ""
    file_name = ""

    if doc:
        file_id = doc.file_id
        file_size = doc.file_size
        file_name = doc.file_name or "unnamed_file"
    elif video:
        file_id = video.file_id
        file_size = video.file_size
        file_name = video.file_name or f"video_{video.file_unique_id}.mp4"
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

    # Sanitize filename
    safe_filename = _sanitize_filename(file_name)
    try:
        tg_file = await context.bot.get_file(file_id)
        file_bytes = await tg_file.download_as_bytearray()

        # Direct upload (no confirmation)
        await update.message.reply_text(formatter.processing("Uploading"))
        fid = nav.current_folder_id(uid)
        uploaded = upload_file(uid, bytes(file_bytes), safe_filename, parent_id=fid)
        await update.message.reply_text(
            formatter.upload_success(uploaded["name"], nav.breadcrumb(uid)),
            reply_markup=ui.post_login_keyboard(),
        )

    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("File upload error")
        await update.message.reply_text(
            formatter.error("Upload failed.", "Try again or check your connection.")
        )


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
    app.add_handler(CommandHandler("create_folder", cmd_mkdir))  # alias
    app.add_handler(CommandHandler("zip",      cmd_zip))

    # ── Account ───────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("logout",   cmd_logout))
    app.add_handler(CommandHandler("email",    cmd_email))
    app.add_handler(CommandHandler("clear",    cmd_clear))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ── File uploads ──────────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, handle_file_upload))

    logger.info("All handlers registered successfully.")
