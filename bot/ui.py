"""
bot/ui.py
Inline keyboard builders for all interactive bot menus.

Design principles:
  - Use inline URL buttons for OAuth (no raw URLs in chat)
  - Clean, minimal button layouts
  - Consistent navigation shortcuts
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from bot import nav

FOLDER_MIME = "application/vnd.google-apps.folder"
MAX_INLINE_ITEMS = 8


# ── Start / Auth keyboards ───────────────────────────────────────────────────

def login_keyboard(auth_url: str) -> InlineKeyboardMarkup:
    """OAuth login button — opens URL directly, no raw link in chat."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Login with Google", url=auth_url)],
    ])


def post_login_keyboard() -> InlineKeyboardMarkup:
    """Main action keyboard shown after successful login."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Browse Files", callback_data="nav:browse"),
         InlineKeyboardButton("Search Notes", callback_data="nav:search")],
        [InlineKeyboardButton("Upload File", callback_data="nav:upload"),
         InlineKeyboardButton("Recent Files", callback_data="nav:recent")],
        [InlineKeyboardButton("Security Center", callback_data="nav:security"),
         InlineKeyboardButton("Help", callback_data="nav:help")],
    ])


def start_keyboard(is_authenticated: bool, auth_url: str = "") -> InlineKeyboardMarkup:
    """Shown on /start."""
    if is_authenticated:
        return post_login_keyboard()
    if auth_url:
        return login_keyboard(auth_url)
    # Fallback: button that triggers login flow
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Login with Google", callback_data="nav:login")],
    ])


# ── Main menu ─────────────────────────────────────────────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Browse Files", callback_data="nav:browse"),
         InlineKeyboardButton("Search Notes", callback_data="nav:search")],
        [InlineKeyboardButton("Upload File", callback_data="nav:upload"),
         InlineKeyboardButton("Recent Files", callback_data="nav:recent")],
        [InlineKeyboardButton("Security Center", callback_data="nav:security"),
         InlineKeyboardButton("Help", callback_data="nav:help")],
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


def browse_items_keyboard(index_map: dict[str, nav.IndexedItem], is_root: bool = True) -> InlineKeyboardMarkup:
    """Inline-first listing actions for folders/files with navigation controls."""
    rows = []
    count = 0
    for idx in sorted(index_map.keys(), key=lambda x: int(x) if x.isdigit() else 999):
        item = index_map[idx]
        if count >= MAX_INLINE_ITEMS:
            break
        if item.is_folder:
            action = InlineKeyboardButton("Open", callback_data=f"item:open:{item.id}")
        else:
            action = InlineKeyboardButton("Download", callback_data=f"file:download:{item.id}")
        details = InlineKeyboardButton("Details", callback_data=f"item:info:{item.id}")
        rows.append([action, details])
        count += 1

    nav_rows = browse_keyboard(is_root=is_root).inline_keyboard
    rows.extend(nav_rows)
    return InlineKeyboardMarkup(rows)


def results_keyboard(index_map: dict[str, nav.IndexedItem]) -> InlineKeyboardMarkup:
    """Inline actions for search/recent/favorites result lists."""
    rows = []
    count = 0
    for idx in sorted(index_map.keys(), key=lambda x: int(x) if x.isdigit() else 999):
        item = index_map[idx]
        if count >= MAX_INLINE_ITEMS:
            break
        action = InlineKeyboardButton("Download", callback_data=f"file:download:{item.id}")
        details = InlineKeyboardButton("Details", callback_data=f"item:info:{item.id}")
        rows.append([action, details])
        count += 1
    rows.append([InlineKeyboardButton("📋 Menu", callback_data="nav:menu")])
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


def folder_actions_keyboard(folder_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Open", callback_data=f"item:open:{folder_id}"),
         InlineKeyboardButton("ℹ️ Details", callback_data=f"item:info:{folder_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
         InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
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


# ── Step-up verification keyboards ────────────────────────────────────────────

def stepup_resend_keyboard(action: str) -> InlineKeyboardMarkup:
    """Shown when waiting for OTP — allows resending."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Resend Code", callback_data=f"stepup:resend:{action}"),
         InlineKeyboardButton("❌ Cancel", callback_data="nav:menu")],
    ])


def stepup_email_entry_keyboard() -> InlineKeyboardMarkup:
    """Shown when waiting for email input."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="nav:menu")],
    ])


def security_setup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Enable Alerts", callback_data="security:enable"),
         InlineKeyboardButton("Skip", callback_data="security:skip")],
    ])


def security_email_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Skip Email", callback_data="security:skip_email")],
    ])


def security_center_keyboard(telegram_on: bool, email_on: bool, mode: str) -> InlineKeyboardMarkup:
    t_label = "Telegram Alerts: On ✅" if telegram_on else "Telegram Alerts: Off ❌"
    e_label = "Email Alerts: On ✅" if email_on else "Email Alerts: Off ❌"
    mode_label = "Mode: Guided" if mode == "guided" else "Mode: Expert" if mode == "expert" else "Mode: Adaptive"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t_label, callback_data="security:toggle:telegram")],
        [InlineKeyboardButton(e_label, callback_data="security:toggle:email")],
        [InlineKeyboardButton(mode_label, callback_data="security:mode")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav:menu")],
    ])
