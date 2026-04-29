"""
bot/handlers.py
Wires up all handlers (commands + file upload + inline callbacks).
Called once from main.py to register everything with the Application.
"""

import logging

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.commands import (
    cmd_start,
    cmd_login,
    cmd_logout,
    cmd_folders,
    cmd_open,
    cmd_cd,
    cmd_pwd,
    cmd_list,
    cmd_get,
    cmd_search,
    cmd_rename,
    cmd_delete,
    cmd_zip,
)

logger = logging.getLogger(__name__)


async def handle_file_upload(update, context):
    """
    Handles any document sent to the bot and uploads it to Google Drive.
    """
    from drive.drive_service import upload_file
    from bot.commands import _folder

    uid = update.effective_user.id
    doc = update.message.document

    if not doc:
        return

    try:
        await update.message.reply_text(f"⬆️ Uploading {doc.file_name}...")
        tg_file = await context.bot.get_file(doc.file_id)
        file_bytes = await tg_file.download_as_bytearray()
        uploaded = upload_file(uid, bytes(file_bytes), doc.file_name, parent_id=_folder(uid))
        await update.message.reply_text(
            f"✅ Uploaded {uploaded['name']} to Google Drive."
        )
    except PermissionError as e:
        await update.message.reply_text(f"🔒 {e}")
    except Exception as e:
        logger.exception("File upload error")
        await update.message.reply_text(f"❌ Upload failed: {e}")


def register_handlers(app: Application) -> None:
    # Command handlers
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("login",   cmd_login))
    app.add_handler(CommandHandler("logout",  cmd_logout))
    app.add_handler(CommandHandler("folders", cmd_folders))
    app.add_handler(CommandHandler("open",    cmd_open))
    app.add_handler(CommandHandler("cd",      cmd_cd))
    app.add_handler(CommandHandler("pwd",     cmd_pwd))
    app.add_handler(CommandHandler("list",    cmd_list))
    app.add_handler(CommandHandler("get",     cmd_get))
    app.add_handler(CommandHandler("search",  cmd_search))
    app.add_handler(CommandHandler("rename",  cmd_rename))
    app.add_handler(CommandHandler("delete",  cmd_delete))
    app.add_handler(CommandHandler("zip",     cmd_zip))

    # Document / file upload handler
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file_upload))

    logger.info("All handlers registered.")
