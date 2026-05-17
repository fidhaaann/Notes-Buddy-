"""
bot/ui.py
Inline keyboard builders for all interactive bot menus.

Design principles:
  - Use inline URL buttons for OAuth (no raw URLs in chat)
  - Clean, minimal button layouts
  - Consistent navigation shortcuts
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

FOLDER_MIME = "application/vnd.google-apps.folder"


# ── Start / Auth keyboards ───────────────────────────────────────────────────

def login_keyboard(auth_url: str) -> InlineKeyboardMarkup:
    """OAuth login button — opens URL directly, no raw link in chat."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Login with Google", url=auth_url)],
    ])


def post_login_keyboard() -> InlineKeyboardMarkup:
    """Main action keyboard shown after successful login."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Browse",        callback_data="nav:browse"),
         InlineKeyboardButton("⬆️ Upload",         callback_data="nav:upload")],
        [InlineKeyboardButton("🔍 Search",         callback_data="nav:search"),
         InlineKeyboardButton("📌 Current Path",   callback_data="nav:pwd")],
        [InlineKeyboardButton("⚙️ More",            callback_data="nav:tools")],
    ])


def start_keyboard(is_authenticated: bool, auth_url: str = "") -> InlineKeyboardMarkup:
    """Shown on /start."""
    if is_authenticated:
        return post_login_keyboard()
    if auth_url:
        return login_keyboard(auth_url)
    # Fallback: button that triggers login flow
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Login with Google", callback_data="nav:login")],
    ])


# ── Main menu ─────────────────────────────────────────────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Browse",        callback_data="nav:browse"),
         InlineKeyboardButton("🔍 Search",         callback_data="nav:search")],
        [InlineKeyboardButton("⬆️ Upload",         callback_data="nav:upload"),
         InlineKeyboardButton("📌 Current Path",   callback_data="nav:pwd")],
        [InlineKeyboardButton("⚙️ Commands",       callback_data="nav:tools"),
         InlineKeyboardButton("🔓 Logout",         callback_data="nav:logout")],
    ])


# ── Browse / Navigation keyboards ────────────────────────────────────────────

def browse_keyboard(is_root: bool = True) -> InlineKeyboardMarkup:
    """Navigation buttons shown below directory listings."""
    rows = []

    nav_row = []
    if not is_root:
        nav_row.append(InlineKeyboardButton("⬅️ Back", callback_data="nav:back"))
    nav_row.append(InlineKeyboardButton("🏠 Home", callback_data="nav:home"))
    nav_row.append(InlineKeyboardButton("🔄 Refresh", callback_data="nav:refresh"))
    rows.append(nav_row)

    rows.append([
        InlineKeyboardButton("📋 Menu",       callback_data="nav:menu"),
        InlineKeyboardButton("📁 New Folder", callback_data="nav:mkdir"),
    ])

    return InlineKeyboardMarkup(rows)


# ── File action keyboard ─────────────────────────────────────────────────────

def file_actions_keyboard(file_id: str, is_fav: bool = False) -> InlineKeyboardMarkup:
    fav_label = "★ Unfavorite" if is_fav else "☆ Favorite"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️ Download", callback_data=f"file:download:{file_id}"),
         InlineKeyboardButton("ℹ️ Details",   callback_data=f"file:info:{file_id}")],
        [InlineKeyboardButton(fav_label,     callback_data=f"file:fav:{file_id}"),
         InlineKeyboardButton("🗑 Delete",    callback_data=f"file:delete:{file_id}")],
        [InlineKeyboardButton("⬅️ Back",      callback_data="nav:back"),
         InlineKeyboardButton("🏠 Home",      callback_data="nav:home")],
    ])


# ── Confirmation keyboards ───────────────────────────────────────────────────

def confirm_keyboard(action: str, target_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data=f"confirm:{action}:{target_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data="nav:menu"),
    ]])


def upload_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data="upload:confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="upload:cancel"),
    ]])


# ── Utility keyboards ────────────────────────────────────────────────────────

def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Main Menu", callback_data="nav:menu"),
    ]])
