"""
bot/handlers.py
Registers all handlers with the Application.
Called once from main.py.
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
    cmd_tools,
    cmd_login,
    cmd_logout,
    cmd_browse,
    cmd_back,
    cmd_search,
    cmd_recent,
    cmd_favorites,
    cmd_info,
    cmd_rename,
    cmd_move,
    cmd_delete,
    cmd_zip,
    cmd_create_folder,
)
from bot.callbacks import handle_callback
from bot import formatter, nav

logger = logging.getLogger(__name__)


async def handle_file_upload(update, context) -> None:
    """Uploads any document sent to the bot into the user's current Drive folder."""
    from drive.drive_service import upload_file

    uid = update.effective_user.id
    doc = update.message.document

    if not doc:
        return

    try:
        await update.message.reply_text(formatter.processing())
        tg_file    = await context.bot.get_file(doc.file_id)
        file_bytes = await tg_file.download_as_bytearray()

        fid      = nav.current_folder_id(uid)
        uploaded = upload_file(uid, bytes(file_bytes), doc.file_name, parent_id=fid)

        await update.message.reply_text(
            formatter.success("Upload", uploaded["name"], nav.breadcrumb(uid))
        )
    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("File upload error")
        await update.message.reply_text(
            formatter.error("Upload failed", str(e))
        )


def register_handlers(app: Application) -> None:
    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",         cmd_start))
    app.add_handler(CommandHandler("menu",          cmd_menu))
    app.add_handler(CommandHandler("tools",         cmd_tools))
    app.add_handler(CommandHandler("login",         cmd_login))
    app.add_handler(CommandHandler("logout",        cmd_logout))

    # Navigation
    app.add_handler(CommandHandler("browse",        cmd_browse))
    app.add_handler(CommandHandler("folders",       cmd_browse))   # alias
    app.add_handler(CommandHandler("list",          cmd_browse))   # alias
    app.add_handler(CommandHandler("back",          cmd_back))
    app.add_handler(CommandHandler("cd",            cmd_back))     # alias

    # File operations
    app.add_handler(CommandHandler("search",        cmd_search))
    app.add_handler(CommandHandler("recent",        cmd_recent))
    app.add_handler(CommandHandler("favorites",     cmd_favorites))
    app.add_handler(CommandHandler("info",          cmd_info))
    app.add_handler(CommandHandler("rename",        cmd_rename))
    app.add_handler(CommandHandler("move",          cmd_move))
    app.add_handler(CommandHandler("delete",        cmd_delete))
    app.add_handler(CommandHandler("zip",           cmd_zip))
    app.add_handler(CommandHandler("create_folder", cmd_create_folder))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ── File uploads ──────────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file_upload))

    logger.info("All handlers registered successfully.")
