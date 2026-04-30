"""
bot/formatter.py
Centralized message formatting system for the Google Drive bot.
All bot responses must use these templates.
"""

from datetime import datetime
from typing import List, Dict, Optional


def success(action: str, filename: Optional[str] = None, location: Optional[str] = None) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"✅ {action} Completed\n\n"
    if filename:
        msg += f"📄 File: {filename}\n"
    if location:
        msg += f"📂 Location: {location}\n"
    msg += f"🕒 Time: {now}"
    return msg


def error(reason: str, suggestion: Optional[str] = None) -> str:
    msg = f"❌ Action Failed\n\nReason: {reason}\n"
    if suggestion:
        msg += f"Suggestion: {suggestion}"
    return msg


def processing() -> str:
    return "⏳ Processing Request...\n\nPlease wait while we complete your operation."


EMOJI_LEGEND = (
    "📁 Folder  │  🖼️ Image  │  🎬 Video  │  🎵 Audio\n"
    "📕 PDF  │  📊 Sheet  │  📑 Slides  │  📝 Doc\n"
    "📦 Archive  │  💻 Code  │  📄 Text  │  📎 Other"
)


def file_listing(folder_name: str, path: str, files: List[Dict], folders: List[Dict]) -> str:
    msg = f"📍 Path: {path}\n"
    msg += f"📂 {folder_name}\n"
    msg += f"{'─' * 30}\n"

    if not files and not folders:
        msg += "\n📭 This folder is empty.\n\nTip: Send any file to upload it here."
        return msg

    if folders:
        msg += f"\n📁 Folders ({len(folders)})\n"
        for f in folders:
            msg += f"  • {f['name']}\n"

    if files:
        msg += f"\n📄 Files ({len(files)})\n"
        for f in files:
            msg += f"  • {f['name']}\n"

    msg += f"\n{'─' * 30}\n"
    msg += f"🔑 Icon Guide:\n{EMOJI_LEGEND}"

    return msg.strip()


def empty_state(location: str) -> str:
    return f"📍 Path: {location}\n\n📭 No files found in this location.\nSuggestion: Use /search to locate a file."


def search_results(keyword: str, files: List[Dict]) -> str:
    if not files:
        return f'🔍 No results found for "{keyword}"\n\nSuggestion: Try a different keyword or use /browse to navigate.'
    msg = f'🔍 Results for "{keyword}"\n\n'
    for f in files:
        msg += f"📄 {f['name']}\n"
    return msg.strip()


def zip_preparing(count: int, size_str: str) -> str:
    return (
        "📦 Preparing ZIP Archive\n\n"
        f"Files matched: {count}\n"
        f"Estimated size: {size_str}\n\n"
        "⏳ Creating archive..."
    )


def zip_ready(filename: str, count: int) -> str:
    return (
        "✅ ZIP Ready\n\n"
        f"📦 {filename}\n"
        f"📊 Files included: {count}"
    )


def main_menu() -> str:
    return (
        "📌 Main Menu\n\n"
        "Select an option below to get started."
    )


def confirm_action(action: str, item_name: str) -> str:
    return (
        f"⚠️ Confirm Action\n\n"
        f"Action: {action}\n"
        f"Item: {item_name}\n\n"
        "Are you sure? This cannot be undone."
    )


def file_info(meta: Dict) -> str:
    from services.parser import human_size
    size = human_size(int(meta["size"])) if meta.get("size") else "Unknown"
    created = meta.get("createdTime", "Unknown")
    mime = meta.get("mimeType", "Unknown")
    return (
        "ℹ️ File Information\n\n"
        f"📄 Name: {meta['name']}\n"
        f"📏 Size: {size}\n"
        f"🗂 Type: {mime}\n"
        f"🕒 Created: {created}"
    )


def login_required() -> str:
    return (
        "🔒 Not Authenticated\n\n"
        "You need to connect your Google account first.\n"
        "Tap the button below or use /login to authorize access."
    )


def tools_menu() -> str:
    return (
        "🛠 Bot Capabilities\n\n"
        "📂 Navigation\n"
        "  /browse — Browse files & folders\n"
        "  /back — Go to previous folder\n"
        "  /menu — Main navigation hub\n\n"
        "🔍 Discovery\n"
        "  /search <keyword> — Search all files\n"
        "  /recent — Recently accessed files\n"
        "  /favorites — Your starred files\n\n"
        "📄 File Actions\n"
        "  /info <filename> — View file details\n"
        "  /rename <old> <new> — Rename a file\n"
        "  /delete <filename> — Delete a file\n"
        "  /move <file> <folder> — Move a file\n\n"
        "📁 Folder Actions\n"
        "  /create_folder <name> — Create new folder\n\n"
        "📦 Bulk Actions\n"
        "  /zip <keyword> — Download matching files as ZIP\n\n"
        "⬆️ Upload\n"
        "  Send any file directly to this chat\n\n"
        "🔐 Account\n"
        "  /login — Connect Google Drive\n"
        "  /logout — Disconnect account\n"
    )
