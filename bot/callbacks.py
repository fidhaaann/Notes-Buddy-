"""
bot/callbacks.py
Handles ALL inline keyboard button presses (CallbackQueryHandler).

Callback data format:  "<namespace>:<action>[:<arg1>[:<arg2>]]"
  nav:browse / nav:back / nav:home / nav:menu / nav:recent
  nav:favorites / nav:search / nav:upload_help / nav:login
  nav:logout / nav:tools / nav:create_folder
  folder:open:<id>:<name>
  file:view:<id> / file:download:<id> / file:info:<id>
  file:fav:<id>  / file:delete:<id>
  confirm:delete:<id>
"""

import io
import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from drive import drive_service as ds
from bot import formatter, ui, nav
from db import models
from services.parser import human_size

logger = logging.getLogger(__name__)

# Telegram bot API hard limit (bytes). Use 45 MB to stay safely below 50 MB.
TELEGRAM_LIMIT = 45 * 1024 * 1024


# ── helpers ───────────────────────────────────────────────────────────────────

async def _edit(query, text: str, markup=None) -> None:
    """Safely edit the current message."""
    kwargs = {"text": text}
    if markup:
        kwargs["reply_markup"] = markup
    await query.edit_message_text(**kwargs)


async def _send_browse(uid: int, query) -> None:
    """Fetch current folder contents and edit the callback message."""
    try:
        fid     = nav.current_folder_id(uid)
        name    = nav.current_folder_name(uid)
        path    = nav.breadcrumb(uid)
        folders = ds.list_folders(uid, parent_id=fid)
        files   = ds.list_files(uid, parent_id=fid)
        text    = formatter.file_listing(name, path, files, folders)
        markup  = ui.browse_keyboard(folders + files, is_root=(fid == "root"))
        await _edit(query, text, markup)
    except PermissionError:
        await _edit(query, formatter.login_required(), ui.back_to_menu_keyboard())
    except Exception as e:
        logger.exception("_send_browse error")
        await _edit(query, formatter.error(str(e)), ui.back_to_menu_keyboard())


# ── main dispatcher ───────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    uid   = update.effective_user.id
    data  = query.data or ""

    await query.answer()

    parts = data.split(":")
    ns    = parts[0]

    # ── Navigation ────────────────────────────────────────────────────────────

    if ns == "nav":
        action = parts[1] if len(parts) > 1 else ""

        if action == "home":
            nav.go_home(uid)
            await _send_browse(uid, query)

        elif action == "back":
            nav.pop_folder(uid)
            await _send_browse(uid, query)

        elif action == "browse":
            await _send_browse(uid, query)

        elif action == "menu":
            await _edit(query, formatter.main_menu(), ui.main_menu_keyboard())

        elif action == "recent":
            try:
                files = ds.get_recent_files(uid)
                await _edit(query, formatter.search_results("Recent Files", files), ui.back_to_menu_keyboard())
            except PermissionError:
                await _edit(query, formatter.login_required(), ui.back_to_menu_keyboard())
            except Exception as e:
                await _edit(query, formatter.error(str(e)), ui.back_to_menu_keyboard())

        elif action == "favorites":
            fav_ids = models.get_favorites(uid)
            files   = []
            for fid in fav_ids:
                try:
                    files.append(ds.get_file_metadata(uid, fid))
                except Exception:
                    continue
            await _edit(query, formatter.search_results("⭐ Favorites", files), ui.back_to_menu_keyboard())

        elif action == "search":
            await _edit(
                query,
                "🔍 Search Files\n\nSend your search keyword as:\n/search <keyword>",
                ui.back_to_menu_keyboard(),
            )

        elif action == "upload_help":
            await _edit(
                query,
                "⬆️ Upload a File\n\nSimply send any file directly to this chat.\nIt will be uploaded to your current Drive folder.",
                ui.back_to_menu_keyboard(),
            )

        elif action == "login":
            from drive import auth as drive_auth
            try:
                url = drive_auth.get_auth_url(uid)
                await _edit(
                    query,
                    f"🔐 Authorize Access\n\nClick the link below to connect your Google account:\n{url}\n\nOnce authorized, return here and use the menu.",
                    ui.back_to_menu_keyboard(),
                )
            except FileNotFoundError:
                await _edit(query, formatter.error("credentials.json not found", "Place it in the project root."), ui.back_to_menu_keyboard())

        elif action == "logout":
            models.delete_user(uid)
            nav.go_home(uid)
            await _edit(
                query,
                "✅ Logged out successfully.\n\nYour Google Drive has been disconnected.",
                ui.start_keyboard(False),
            )

        elif action == "tools":
            await _edit(query, formatter.tools_menu(), ui.back_to_menu_keyboard())

        elif action == "create_folder":
            await _edit(
                query,
                "📁 Create New Folder\n\nUse the command below:\n\n/create_folder <folder name>",
                ui.back_to_menu_keyboard(),
            )

        elif action == "clear":
            chat_id = update.effective_chat.id
            msg_id  = query.message.message_id
            deleted = 0
            for i in range(msg_id, max(msg_id - 100, 0), -1):
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=i)
                    deleted += 1
                except Exception:
                    continue
            confirm = await context.bot.send_message(
                chat_id=chat_id,
                text=f"🧹 Cleared {deleted} messages.",
            )
            await asyncio.sleep(3)
            try:
                await confirm.delete()
            except Exception:
                pass

        return

    # ── Folder ────────────────────────────────────────────────────────────────

    if ns == "folder":
        action = parts[1] if len(parts) > 1 else ""

        if action == "open" and len(parts) >= 4:
            folder_id   = parts[2]
            folder_name = ":".join(parts[3:])
            nav.push_folder(uid, folder_id, folder_name)
            await _send_browse(uid, query)

        return

    # ── File ──────────────────────────────────────────────────────────────────

    if ns == "file":
        action  = parts[1] if len(parts) > 1 else ""
        file_id = parts[2] if len(parts) > 2 else ""

        if action == "view":
            try:
                meta   = ds.get_file_metadata(uid, file_id)
                is_fav = models.is_favorite(uid, file_id)
                await _edit(query, formatter.file_info(meta), ui.file_actions_keyboard(file_id, is_fav))
            except Exception as e:
                await _edit(query, formatter.error(str(e)), ui.back_to_menu_keyboard())

        elif action == "download":
            await _handle_download(uid, file_id, query, context, update)

        elif action == "info":
            try:
                meta   = ds.get_file_metadata(uid, file_id)
                is_fav = models.is_favorite(uid, file_id)
                await _edit(query, formatter.file_info(meta), ui.file_actions_keyboard(file_id, is_fav))
            except Exception as e:
                await _edit(query, formatter.error(str(e)), ui.back_to_menu_keyboard())

        elif action == "fav":
            try:
                if models.is_favorite(uid, file_id):
                    models.remove_favorite(uid, file_id)
                    label = "Removed from Favorites"
                else:
                    models.add_favorite(uid, file_id)
                    label = "Added to Favorites"
                meta = ds.get_file_metadata(uid, file_id)
                await _edit(query, formatter.success(label, meta.get("name")), ui.back_to_menu_keyboard())
            except Exception as e:
                await _edit(query, formatter.error(str(e)), ui.back_to_menu_keyboard())

        elif action == "delete":
            try:
                meta = ds.get_file_metadata(uid, file_id)
                await _edit(
                    query,
                    formatter.confirm_action("Delete", meta.get("name", file_id)),
                    ui.confirm_keyboard("delete", file_id),
                )
            except Exception as e:
                await _edit(query, formatter.error(str(e)), ui.back_to_menu_keyboard())

        return

    # ── Confirmation ──────────────────────────────────────────────────────────

    if ns == "confirm":
        action  = parts[1] if len(parts) > 1 else ""
        file_id = parts[2] if len(parts) > 2 else ""

        if action == "delete":
            try:
                ds.delete_file(uid, file_id)
                await _edit(query, formatter.success("Deletion"), ui.back_to_menu_keyboard())
            except Exception as e:
                await _edit(query, formatter.error(str(e)), ui.back_to_menu_keyboard())

        return

    logger.warning("Unhandled callback data: %s", data)


# ── Download handler (extracted for clarity) ──────────────────────────────────

async def _handle_download(uid: int, file_id: str, query, context, update) -> None:
    """
    Download a file from Drive and send it to Telegram.
    - Checks size first to avoid timeouts.
    - Files > 45 MB get a direct Google Drive link instead.
    - Downloads run in a thread pool so the event loop is never blocked.
    """
    try:
        # Step 1: get metadata (fast, no download)
        meta      = ds.get_file_metadata(uid, file_id)
        fname     = meta.get("name", "file")
        mime_type = meta.get("mimeType", "")
        size_raw  = int(meta["size"]) if meta.get("size") else 0
        size_str  = human_size(size_raw) if size_raw else "Unknown"

        # Step 2: if too large for Telegram, send Drive links
        if size_raw > TELEGRAM_LIMIT:
            view_link    = meta.get("webViewLink", "")
            content_link = meta.get("webContentLink", "")
            msg = (
                f"📁 File Too Large for Telegram\n\n"
                f"📄 {fname}\n"
                f"📏 Size: {size_str}\n\n"
                f"Telegram bots can only send files up to 45 MB.\n"
                f"Access this file directly from Google Drive:\n"
            )
            if view_link:
                msg += f"\n🔗 Open in Drive:\n{view_link}\n"
            if content_link:
                msg += f"\n⬇️ Direct Download:\n{content_link}\n"
            await _edit(query, msg, ui.back_to_menu_keyboard())
            return

        # Step 3: show progress, then download in background thread
        await _edit(
            query,
            f"⏳ Downloading...\n\n"
            f"📄 {fname}\n"
            f"📏 Size: {size_str}\n\n"
            f"Please wait — this runs in the background.",
        )

        file_bytes, downloaded_name = await asyncio.to_thread(ds.download_file, uid, file_id)

        # Step 4: send to Telegram with generous timeouts
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=io.BytesIO(file_bytes),
            filename=downloaded_name,
            read_timeout=180,
            write_timeout=180,
            connect_timeout=30,
        )

        await _edit(query, formatter.success("Download", downloaded_name), ui.back_to_menu_keyboard())

    except Exception as e:
        logger.exception("Download failed for file_id=%s", file_id)
        await _edit(
            query,
            formatter.error(str(e), "Try again or open the file directly in Google Drive."),
            ui.back_to_menu_keyboard(),
        )
