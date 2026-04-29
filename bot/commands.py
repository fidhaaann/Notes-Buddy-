"""
bot/commands.py
Pure command handler coroutines for all Telegram bot commands.
Uses plain text (no parse_mode) to avoid Markdown escaping issues.
"""

import io

import logging

from telegram import Update
from telegram.ext import ContextTypes

from drive import auth as drive_auth
from drive import drive_service as ds
from services import parser as p
from services.zip_service import create_zip

logger = logging.getLogger(__name__)

# Per-user breadcrumb stack: list of (folder_id, folder_name)
# Root is represented as ("root", "🏠 Home")
_folder_stack: dict[int, list[tuple[str, str]]] = {}


def _uid(update: Update) -> int:
    return update.effective_user.id


def _stack(uid: int) -> list[tuple[str, str]]:
    if uid not in _folder_stack:
        _folder_stack[uid] = [("root", "🏠 Home")]
    return _folder_stack[uid]


def _folder(uid: int) -> str:
    return _stack(uid)[-1][0]


def _folder_name(uid: int) -> str:
    return _stack(uid)[-1][1]


def _breadcrumb(uid: int) -> str:
    return " > ".join(name for _, name in _stack(uid))


# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "👋 Welcome to Google Drive Bot!\n\n"
        "Use /login to connect your Google account.\n\n"
        "Available commands:\n"
        "/folders — list folders in current location\n"
        "/open <folder> — enter a folder\n"
        "/cd — go back to previous folder\n"
        "/pwd — show current folder path\n"
        "/list — list files in current folder\n"
        "/get <filename> — download a file\n"
        "/search <keyword> — search files\n"
        "/rename <old> <new> — rename a file\n"
        "/delete <filename> — delete a file\n"
        "/zip <keyword> — download matching files as ZIP\n"
        "/logout — disconnect your account"
    )
    await update.message.reply_text(msg)


# ─────────────────────────────────────────────────────────────────────────────
# /login
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    try:
        url = drive_auth.get_auth_url(uid)
        await update.message.reply_text(
            f"🔐 Click the link below to authorize access:\n\n{url}\n\n"
            "After authorizing, the bot will confirm automatically."
        )
    except FileNotFoundError:
        await update.message.reply_text(
            "❌ credentials.json not found. Please place your Google OAuth credentials file "
            "in the project root."
        )


# ─────────────────────────────────────────────────────────────────────────────
# /logout
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from db.models import delete_user
    uid = _uid(update)
    delete_user(uid)
    _folder_stack.pop(uid, None)
    await update.message.reply_text("✅ Logged out. Your tokens have been deleted.")


# ─────────────────────────────────────────────────────────────────────────────
# /folders
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_folders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    try:
        folders = ds.list_folders(uid, parent_id=_folder(uid))
        location = _breadcrumb(uid)
        await update.message.reply_text(
            f"📍 Location: {location}\n\n"
            f"📁 Folders:\n\n{p.format_folder_list(folders)}"
        )
    except PermissionError as e:
        await update.message.reply_text(f"🔒 {e}")
    except Exception as e:
        logger.exception("cmd_folders error")
        await update.message.reply_text(f"❌ Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /open <folder_name>
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(update.message.text, "/open")
    if not args:
        await update.message.reply_text("Usage: /open <folder_name>")
        return
    folder_name = " ".join(args)
    try:
        folder = ds.open_folder(uid, folder_name, parent_id=_folder(uid))
        if folder:
            _stack(uid).append((folder["id"], folder["name"]))
            await update.message.reply_text(
                f"📂 Opened: {folder['name']}\n"
                f"📍 Path: {_breadcrumb(uid)}"
            )
        else:
            await update.message.reply_text(f"❌ Folder '{folder_name}' not found.")
    except PermissionError as e:
        await update.message.reply_text(f"🔒 {e}")
    except Exception as e:
        logger.exception("cmd_open error")
        await update.message.reply_text(f"❌ Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /cd  (go back one level)
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_cd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    stack = _stack(uid)
    if len(stack) <= 1:
        await update.message.reply_text("🏠 Already at Home. Can't go further back.")
        return
    stack.pop()
    await update.message.reply_text(
        f"⬆️ Went back.\n"
        f"📍 Now at: {_breadcrumb(uid)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /pwd  (show current path)
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_pwd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    await update.message.reply_text(f"📍 Current location: {_breadcrumb(uid)}")


# ─────────────────────────────────────────────────────────────────────────────
# /list
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    try:
        files = ds.list_files(uid, parent_id=_folder(uid))
        location = _breadcrumb(uid)
        await update.message.reply_text(
            f"📍 Location: {location}\n\n"
            f"📜 Files:\n\n{p.format_file_list(files)}"
        )
    except PermissionError as e:
        await update.message.reply_text(f"🔒 {e}")
    except Exception as e:
        logger.exception("cmd_list error")
        await update.message.reply_text(f"❌ Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /get <filename>
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_get(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(update.message.text, "/get")
    if not args:
        await update.message.reply_text("Usage: /get <filename>")
        return
    filename = " ".join(args)
    try:
        file_meta = ds.find_file_by_name(uid, filename)
        if not file_meta:
            await update.message.reply_text(f"❌ File '{filename}' not found.")
            return
        await update.message.reply_text(f"⬇️ Downloading {filename}...")
        file_bytes, fname = ds.download_file(uid, file_meta["id"])
        await update.message.reply_document(
            document=io.BytesIO(file_bytes),
            filename=fname,
        )
    except PermissionError as e:
        await update.message.reply_text(f"🔒 {e}")
    except Exception as e:
        logger.exception("cmd_get error")
        await update.message.reply_text(f"❌ Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /search <keyword>
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(update.message.text, "/search")
    if not args:
        await update.message.reply_text("Usage: /search <keyword>")
        return
    keyword = " ".join(args)
    try:
        files = ds.search_files(uid, keyword)
        await update.message.reply_text(
            f"🔍 Search results for '{keyword}':\n\n{p.format_file_list(files)}"
        )
    except PermissionError as e:
        await update.message.reply_text(f"🔒 {e}")
    except Exception as e:
        logger.exception("cmd_search error")
        await update.message.reply_text(f"❌ Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /rename <old_name> <new_name>
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(update.message.text, "/rename")
    if len(args) < 2:
        await update.message.reply_text("Usage: /rename <old_name> <new_name>")
        return
    old_name, new_name = args[0], " ".join(args[1:])
    try:
        file_meta = ds.find_file_by_name(uid, old_name)
        if not file_meta:
            await update.message.reply_text(f"❌ File '{old_name}' not found.")
            return
        updated = ds.rename_file(uid, file_meta["id"], new_name)
        await update.message.reply_text(f"✏️ Renamed to: {updated['name']}")
    except PermissionError as e:
        await update.message.reply_text(f"🔒 {e}")
    except Exception as e:
        logger.exception("cmd_rename error")
        await update.message.reply_text(f"❌ Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /delete <filename>
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(update.message.text, "/delete")
    if not args:
        await update.message.reply_text("Usage: /delete <filename>")
        return
    filename = " ".join(args)
    try:
        file_meta = ds.find_file_by_name(uid, filename)
        if not file_meta:
            await update.message.reply_text(f"❌ File '{filename}' not found.")
            return
        ds.delete_file(uid, file_meta["id"])
        await update.message.reply_text(f"🗑️ Deleted: {filename}")
    except PermissionError as e:
        await update.message.reply_text(f"🔒 {e}")
    except Exception as e:
        logger.exception("cmd_delete error")
        await update.message.reply_text(f"❌ Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /zip <keyword>
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    args = p.parse_args(update.message.text, "/zip")
    if not args:
        await update.message.reply_text("Usage: /zip <keyword>")
        return
    keyword = " ".join(args)
    try:
        files = ds.search_files(uid, keyword)
        if not files:
            await update.message.reply_text(f"❌ No files found matching '{keyword}'.")
            return

        await update.message.reply_text(f"📦 Found {len(files)} file(s). Creating ZIP...")

        collected: list[tuple[bytes, str]] = []
        for f in files:
            try:
                file_bytes, fname = ds.download_file(uid, f["id"])
                collected.append((file_bytes, fname))
            except Exception:
                logger.warning("Could not download %s for ZIP", f["name"])

        if not collected:
            await update.message.reply_text("❌ Could not download any files.")
            return

        zip_bytes = create_zip(collected)
        zip_name = f"{keyword}_files.zip"
        await update.message.reply_document(
            document=io.BytesIO(zip_bytes),
            filename=zip_name,
            caption=f"📦 {len(collected)} file(s) zipped as {zip_name}",
        )
    except PermissionError as e:
        await update.message.reply_text(f"🔒 {e}")
    except Exception as e:
        logger.exception("cmd_zip error")
        await update.message.reply_text(f"❌ Error: {e}")
