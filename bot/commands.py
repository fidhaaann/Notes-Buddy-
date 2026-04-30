"""
bot/commands.py
All /command handlers. Uses nav.py for state, formatter.py for messages,
and ui.py for keyboards. No circular imports.
"""

import io
import logging

from telegram import Update
from telegram.ext import ContextTypes

from drive import auth as drive_auth
from drive import drive_service as ds
from bot import formatter, ui
from bot import nav
from services import parser as p
from services.zip_service import create_zip
from db import models

logger = logging.getLogger(__name__)


def _uid(update: Update) -> int:
    return update.effective_user.id


# ─────────────────────────────────────────────────────────────────────────────
# Shared browse renderer (safe for both message and callback contexts)
# ─────────────────────────────────────────────────────────────────────────────

async def _send_browse(uid: int, send_fn) -> None:
    """
    Fetch current folder contents and send/edit via send_fn(text, reply_markup).
    send_fn is either update.message.reply_text or query.edit_message_text.
    """
    try:
        fid  = nav.current_folder_id(uid)
        name = nav.current_folder_name(uid)
        path = nav.breadcrumb(uid)

        folders = ds.list_folders(uid, parent_id=fid)
        files   = ds.list_files(uid, parent_id=fid)

        text   = formatter.file_listing(name, path, files, folders)
        markup = ui.browse_keyboard(folders + files, is_root=(fid == "root"))

        await send_fn(text, markup)
    except PermissionError:
        await send_fn(formatter.login_required(), None)
    except Exception as e:
        logger.exception("browse error")
        await send_fn(formatter.error(str(e)), None)


# ─────────────────────────────────────────────────────────────────────────────
# /start  /menu
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    # Check if the user is already authenticated
    from db.models import get_user
    user = get_user(uid)
    is_auth = bool(user and user["token"])

    if is_auth:
        text = (
            "👋 Welcome back!\n\n"
            "Your Google Drive is connected. Use the menu below."
        )
    else:
        text = (
            "👋 Welcome to *Google Drive Bot*\n\n"
            "Manage your Google Drive files directly from Telegram.\n"
            "Connect your account to get started."
        )

    await update.message.reply_text(
        text,
        reply_markup=ui.start_keyboard(is_auth),
        parse_mode="Markdown",
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        formatter.main_menu(),
        reply_markup=ui.main_menu_keyboard(),
    )


async def cmd_tools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        formatter.tools_menu(),
        reply_markup=ui.back_to_menu_keyboard(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# /login  /logout
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    try:
        url = drive_auth.get_auth_url(uid)
        await update.message.reply_text(
            f"🔐 Authorize Access\n\n"
            f"Click the link below to connect your Google account:\n{url}\n\n"
            f"Once authorized, return here and tap the button below.",
            reply_markup=ui.main_menu_keyboard(),
        )
    except FileNotFoundError:
        await update.message.reply_text(
            formatter.error(
                "credentials.json not found",
                "Place your Google OAuth credentials file in the project root directory.",
            )
        )


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    models.delete_user(uid)
    nav.clear_user(uid)
    await update.message.reply_text(
        formatter.success("Logout"),
        reply_markup=ui.main_menu_keyboard(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# /browse  /back
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_browse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)

    async def send(text, markup):
        if markup:
            await update.message.reply_text(text, reply_markup=markup)
        else:
            await update.message.reply_text(text)

    await _send_browse(uid, send)


async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    went_back = nav.pop_folder(uid)
    if not went_back:
        await update.message.reply_text("🏠 Already at Home folder.")
        return
    await cmd_browse(update, context)


# ─────────────────────────────────────────────────────────────────────────────
# /search
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = _uid(update)
    args = p.parse_args(update.message.text, "/search")
    if not args:
        await update.message.reply_text("Usage: /search <keyword>")
        return
    keyword = " ".join(args)
    try:
        files = ds.search_files(uid, keyword)
        await update.message.reply_text(
            formatter.search_results(keyword, files),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        await update.message.reply_text(formatter.error(str(e)))


# ─────────────────────────────────────────────────────────────────────────────
# /recent  /favorites
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    try:
        files = ds.get_recent_files(uid)
        await update.message.reply_text(
            formatter.search_results("Recent Files", files),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        await update.message.reply_text(formatter.error(str(e)))


async def cmd_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid     = _uid(update)
    fav_ids = models.get_favorites(uid)
    files   = []
    for fid in fav_ids:
        try:
            files.append(ds.get_file_metadata(uid, fid))
        except Exception:
            continue
    await update.message.reply_text(
        formatter.search_results("⭐ Favorites", files),
        reply_markup=ui.back_to_menu_keyboard(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# /info  /rename  /move  /delete  /create_folder
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = _uid(update)
    args = p.parse_args(update.message.text, "/info")
    if not args:
        await update.message.reply_text("Usage: /info <filename>")
        return
    filename = " ".join(args)
    try:
        fm = ds.find_file_by_name(uid, filename)
        if not fm:
            await update.message.reply_text(
                formatter.error(f"File '{filename}' not found.", "Use /search to locate it.")
            )
            return
        meta = ds.get_file_metadata(uid, fm["id"])
        await update.message.reply_text(
            formatter.file_info(meta),
            reply_markup=ui.file_actions_keyboard(fm["id"], models.is_favorite(uid, fm["id"])),
        )
    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        await update.message.reply_text(formatter.error(str(e)))


async def cmd_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = _uid(update)
    args = p.parse_args(update.message.text, "/rename")
    if len(args) < 2:
        await update.message.reply_text("Usage: /rename <old_name> <new_name>")
        return
    old_name = args[0]
    new_name = " ".join(args[1:])
    try:
        fm = ds.find_file_by_name(uid, old_name)
        if not fm:
            await update.message.reply_text(
                formatter.error(f"File '{old_name}' not found.", "Use /search to locate it.")
            )
            return
        updated = ds.rename_file(uid, fm["id"], new_name)
        await update.message.reply_text(
            formatter.success("Rename", updated["name"]),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        await update.message.reply_text(formatter.error(str(e)))


async def cmd_move(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = _uid(update)
    args = p.parse_args(update.message.text, "/move")
    if len(args) < 2:
        await update.message.reply_text("Usage: /move <file_name> <folder_name>")
        return
    file_name   = args[0]
    folder_name = " ".join(args[1:])
    try:
        fm = ds.find_file_by_name(uid, file_name)
        if not fm:
            await update.message.reply_text(
                formatter.error(f"File '{file_name}' not found.", "Use /search to locate it.")
            )
            return
        tf = ds.open_folder(uid, folder_name)
        if not tf:
            await update.message.reply_text(
                formatter.error(f"Folder '{folder_name}' not found.")
            )
            return
        ds.move_file(uid, fm["id"], tf["id"])
        await update.message.reply_text(
            formatter.success("Move", file_name, folder_name),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        await update.message.reply_text(formatter.error(str(e)))


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = _uid(update)
    args = p.parse_args(update.message.text, "/delete")
    if not args:
        await update.message.reply_text("Usage: /delete <filename>")
        return
    filename = " ".join(args)
    try:
        fm = ds.find_file_by_name(uid, filename)
        if not fm:
            await update.message.reply_text(
                formatter.error(f"File '{filename}' not found.", "Use /search to locate it.")
            )
            return
        await update.message.reply_text(
            formatter.confirm_action("Delete", filename),
            reply_markup=ui.confirm_keyboard("delete", fm["id"]),
        )
    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        await update.message.reply_text(formatter.error(str(e)))


async def cmd_create_folder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = _uid(update)
    args = p.parse_args(update.message.text, "/create_folder")
    if not args:
        await update.message.reply_text("Usage: /create_folder <name>")
        return
    name = " ".join(args)
    try:
        created = ds.create_folder(uid, name, parent_id=nav.current_folder_id(uid))
        await update.message.reply_text(
            formatter.success("Folder Created", created["name"], nav.breadcrumb(uid)),
            reply_markup=ui.back_to_menu_keyboard(),
        )
    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        await update.message.reply_text(formatter.error(str(e)))


# ─────────────────────────────────────────────────────────────────────────────
# /zip
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = _uid(update)
    args = p.parse_args(update.message.text, "/zip")
    if not args:
        await update.message.reply_text("Usage: /zip <keyword>")
        return
    keyword = " ".join(args)
    try:
        files = ds.search_files(uid, keyword)
        if not files:
            await update.message.reply_text(
                formatter.error(f"No files matched '{keyword}'.", "Try a different keyword.")
            )
            return

        total = sum(int(f.get("size", 0)) for f in files)
        size_str = p.human_size(total) if total else "Unknown"
        await update.message.reply_text(formatter.zip_preparing(len(files), size_str))

        collected: list[tuple[bytes, str]] = []
        for f in files:
            try:
                file_bytes, fname = ds.download_file(uid, f["id"])
                collected.append((file_bytes, fname))
            except Exception:
                logger.warning("Skipping %s in ZIP — download failed", f["name"])

        if not collected:
            await update.message.reply_text(
                formatter.error("Could not download any files for the archive.")
            )
            return

        zip_bytes = create_zip(collected)
        zip_name  = f"{keyword}_files.zip"
        await update.message.reply_document(
            document=io.BytesIO(zip_bytes),
            filename=zip_name,
            caption=formatter.zip_ready(zip_name, len(collected)),
        )
    except PermissionError:
        await update.message.reply_text(formatter.login_required())
    except Exception as e:
        logger.exception("cmd_zip error")
        await update.message.reply_text(formatter.error(str(e)))
