"""
bot/ui.py
Inline keyboard builders for all interactive bot menus.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

FOLDER_MIME = "application/vnd.google-apps.folder"


# ── File type emoji mapping ────────────────────────────────────────────────────

def file_emoji(mime_type: str) -> str:
    """Return an appropriate emoji for a file based on its MIME type."""
    if not mime_type:
        return "📎"
    m = mime_type.lower()
    if "folder" in m:                                   return "📁"
    if m.startswith("image/"):                          return "🖼️"
    if m.startswith("video/"):                          return "🎬"
    if m.startswith("audio/"):                          return "🎵"
    if "pdf" in m:                                      return "📕"
    if "spreadsheet" in m or "excel" in m or "csv" in m: return "📊"
    if "presentation" in m or "powerpoint" in m:        return "📑"
    if "document" in m or "word" in m or "msword" in m: return "📝"
    if "zip" in m or "compressed" in m or "rar" in m:   return "📦"
    if "text/plain" in m:                               return "📄"
    if any(x in m for x in ("javascript", "python", "json", "html", "xml", "code")): return "💻"
    return "📎"


# ── Keyboards ─────────────────────────────────────────────────────────────────

def start_keyboard(is_authenticated: bool) -> InlineKeyboardMarkup:
    """Shown on /start — login button if not authenticated, menu if authenticated."""
    if is_authenticated:
        return main_menu_keyboard()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Connect Google Drive", callback_data="nav:login")],
        [InlineKeyboardButton("🛠 What can this bot do?", callback_data="nav:tools")],
    ])


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Browse Files",   callback_data="nav:browse"),
         InlineKeyboardButton("🔍 Search",         callback_data="nav:search")],
        [InlineKeyboardButton("📌 Recent Files",   callback_data="nav:recent"),
         InlineKeyboardButton("⭐ Favorites",       callback_data="nav:favorites")],
        [InlineKeyboardButton("🛠 Tools & Commands", callback_data="nav:tools"),
         InlineKeyboardButton("🔓 Logout",          callback_data="nav:logout")],
    ])


def browse_keyboard(items: list, is_root: bool) -> InlineKeyboardMarkup:
    """
    items: list of Drive file/folder dicts with 'id', 'name', 'mimeType'
    Folders and files get distinct emojis based on type.
    """
    rows = []
    folders = [i for i in items if i.get("mimeType") == FOLDER_MIME]
    files   = [i for i in items if i.get("mimeType") != FOLDER_MIME]

    for f in folders:
        emoji = "📁"
        rows.append([InlineKeyboardButton(
            f"{emoji} {f['name']}", callback_data=f"folder:open:{f['id']}:{f['name']}"
        )])

    for f in files:
        emoji = file_emoji(f.get("mimeType", ""))
        rows.append([InlineKeyboardButton(
            f"{emoji} {f['name']}", callback_data=f"file:view:{f['id']}"
        )])

    nav = []
    if not is_root:
        nav.append(InlineKeyboardButton("⬅️ Back", callback_data="nav:back"))
    nav.append(InlineKeyboardButton("🏠 Home", callback_data="nav:home"))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton("📋 Menu",          callback_data="nav:menu"),
        InlineKeyboardButton("📁 New Folder",    callback_data="nav:create_folder"),
    ])

    return InlineKeyboardMarkup(rows)


def file_actions_keyboard(file_id: str, is_fav: bool = False) -> InlineKeyboardMarkup:
    fav_label = "⭐ Unfavorite" if is_fav else "☆ Favorite"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️ Download", callback_data=f"file:download:{file_id}"),
         InlineKeyboardButton("ℹ️ Info",     callback_data=f"file:info:{file_id}")],
        [InlineKeyboardButton(fav_label,     callback_data=f"file:fav:{file_id}"),
         InlineKeyboardButton("❌ Delete",   callback_data=f"file:delete:{file_id}")],
        [InlineKeyboardButton("⬅️ Back",     callback_data="nav:back"),
         InlineKeyboardButton("🏠 Home",     callback_data="nav:home")],
    ])


def confirm_keyboard(action: str, target_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"confirm:{action}:{target_id}"),
        InlineKeyboardButton("❌ Cancel",  callback_data="nav:menu"),
    ]])


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Main Menu", callback_data="nav:menu"),
    ]])
