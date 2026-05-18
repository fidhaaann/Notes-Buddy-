"""
bot/formatter.py
Centralized, professional message formatting system.

All bot responses are generated here. Every message follows a consistent
visual structure: concise, terminal-inspired, minimal emoji usage.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from bot.nav import IndexedItem, FOLDER_MIME


# ── File type icon mapping ────────────────────────────────────────────────────

def _file_icon(mime_type: str) -> str:
    """Return a single icon character for a given MIME type."""
    if not mime_type:
        return "○"
    m = mime_type.lower()
    if "folder" in m:                                       return "▸"
    if m.startswith("image/"):                              return "◈"
    if m.startswith("video/"):                              return "▶"
    if m.startswith("audio/"):                              return "♪"
    if "pdf" in m:                                          return "▪"
    if "spreadsheet" in m or "excel" in m or "csv" in m:    return "▦"
    if "presentation" in m or "powerpoint" in m:            return "▧"
    if "document" in m or "word" in m or "msword" in m:     return "▫"
    if "zip" in m or "compressed" in m or "rar" in m:       return "▣"
    if "text/plain" in m:                                   return "▭"
    if any(x in m for x in ("javascript", "python", "json", "html", "xml", "code")):
        return "◇"
    return "○"


def _file_type_label(mime_type: str) -> str:
    """Return a human-readable file type label."""
    if not mime_type:
        return "Unknown"
    m = mime_type.lower()
    if "folder" in m:               return "Folder"
    if "pdf" in m:                  return "PDF"
    if "png" in m:                  return "PNG Image"
    if "jpeg" in m or "jpg" in m:   return "JPEG Image"
    if "gif" in m:                  return "GIF Image"
    if "svg" in m:                  return "SVG Image"
    if m.startswith("image/"):      return "Image"
    if "mp4" in m:                  return "MP4 Video"
    if "mov" in m or "quicktime" in m: return "MOV Video"
    if m.startswith("video/"):      return "Video"
    if m.startswith("audio/"):      return "Audio"
    if "spreadsheet" in m or "excel" in m: return "Spreadsheet"
    if "presentation" in m or "powerpoint" in m: return "Presentation"
    if "document" in m or "word" in m or "msword" in m: return "Document"
    if "zip" in m:                  return "ZIP Archive"
    if "rar" in m:                  return "RAR Archive"
    if "compressed" in m:           return "Archive"
    if "text/plain" in m:           return "Text File"
    if "json" in m:                 return "JSON"
    if "python" in m:               return "Python Script"
    if "javascript" in m:           return "JavaScript"
    if "html" in m:                 return "HTML"
    return mime_type.split("/")[-1].upper() if "/" in mime_type else "File"


# ── Welcome / Auth ────────────────────────────────────────────────────────────

def welcome_unauthenticated() -> str:
    return (
        "🔐 Authentication Required\n"
        "\n"
        "Please log in to connect your Google Drive account."
    )


def oauth_scope_warning() -> str:
    return (
        "⚠️ Permission Notice\n"
        "\n"
        "Logging in grants this bot access to:\n"
        "  • View all files and folders\n"
        "  • Upload, download, and modify files\n"
        "  • Delete files and create folders\n"
        "\n"
        "You can revoke access anytime with /logout."
    )


def welcome_authenticated() -> str:
    return (
        "✅ Welcome Back\n"
        "\n"
        "NotesBuddy is connected to your Google Drive.\n"
        "\n"
        "Capabilities:\n"
        "  • Browse folders\n"
        "  • Download files\n"
        "  • Upload files\n"
        "  • Search items\n"
        "  • Navigate directories\n"
        "  • Generate ZIP archives"
    )


def login_successful() -> str:
    return (
        "✅ Login Successful\n"
        "\n"
        "NotesBuddy is now connected to your Google Drive.\n"
        "\n"
        "Capabilities:\n"
        "  • Browse folders\n"
        "  • Download files\n"
        "  • Upload files\n"
        "  • Search items\n"
        "  • Navigate directories\n"
        "  • Generate ZIP archives"
    )


def login_required() -> str:
    return (
        "🔒 Not Authenticated\n"
        "\n"
        "You need to connect your Google account first.\n"
        "Use /start to begin authentication."
    )


def logout_successful() -> str:
    return (
        "✅ Logout Successful\n"
        "\n"
        "Your Google Drive account has been disconnected securely."
    )


# ── Directory listing (hierarchical) ─────────────────────────────────────────

def directory_listing(
    path: str,
    index_map: dict[str, IndexedItem],
    folders: list[dict],
    files: list[dict],
) -> str:
    """
    Format the current directory contents with hierarchical indexing.

    Output:
      📍 Current Location: Home > Notes

      📂 Directories
      [1]  Notes
      [2]  Photos

      📄 Files
      [3]  readme.txt
      [4]  report.pdf
    """
    lines = [f"📍 Current Location: {path}", ""]

    if not folders and not files:
        lines.append("  This directory is empty.")
        lines.append("")
        lines.append("  Send any file to upload it here,")
        lines.append("  or use /cd to navigate elsewhere.")
        return "\n".join(lines)

    # Collect folder indices and file indices
    folder_items = [(idx, item) for idx, item in sorted(index_map.items(), key=_sort_index) if item.is_folder and "." not in idx]
    file_items = [(idx, item) for idx, item in sorted(index_map.items(), key=_sort_index) if not item.is_folder and "." not in idx]

    # Sub-items (children of folders shown in expanded view)
    child_items: dict[str, list[tuple[str, IndexedItem]]] = {}
    for idx, item in sorted(index_map.items(), key=_sort_index):
        if "." in idx:
            parent = idx.rsplit(".", 1)[0]
            child_items.setdefault(parent, []).append((idx, item))

    if folder_items:
        lines.append("📂 Directories")
        lines.append("")
        for idx, item in folder_items:
            icon = "▸"
            lines.append(f"  [{idx}]  {icon} {item.name}")
            # Show children if expanded
            if idx in child_items:
                for cidx, citem in child_items[idx]:
                    cicon = _file_icon(citem.mime_type)
                    lines.append(f"    [{cidx}]  {cicon} {citem.name}")
        lines.append("")

    if file_items:
        lines.append("📄 Files")
        lines.append("")
        for idx, item in file_items:
            icon = _file_icon(item.mime_type)
            lines.append(f"  [{idx}]  {icon} {item.name}")
        lines.append("")

    lines.append("─" * 34)
    lines.append("  /cd <n>  enter   /download <n>")
    lines.append("  /more <n>  info  /cd  go back")

    return "\n".join(lines)


def _sort_index(pair: tuple[str, IndexedItem]) -> list[int]:
    """Sort index strings numerically: '1' < '2' < '1.1' < '1.2' < '10'."""
    idx = pair[0]
    return [int(x) for x in idx.split(".")]


# ── File metadata ─────────────────────────────────────────────────────────────

def file_info(meta: Dict) -> str:
    """Detailed metadata view for /more command."""
    from services.parser import human_size

    name = meta.get("name", "Unknown")
    mime = meta.get("mimeType", "Unknown")
    size_raw = int(meta["size"]) if meta.get("size") else 0
    size = human_size(size_raw) if size_raw else "Unknown"
    created = _format_date(meta.get("createdTime", ""))
    modified = _format_date(meta.get("modifiedTime", created))
    path = meta.get("_path", "")
    file_type = _file_type_label(mime)

    lines = [
        "📄 File Information",
        "",
        f"  Name:      {name}",
        f"  Type:      {file_type}",
        f"  Size:      {size}",
        f"  Modified:  {modified}",
        f"  MIME:      {mime}",
    ]
    if path:
        lines.append(f"  Path:      {path}")

    return "\n".join(lines)


def _format_date(iso_str: str) -> str:
    """Convert ISO date string to clean format."""
    if not iso_str:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_str[:10] if len(iso_str) >= 10 else iso_str


# ── Success / Error / Status ──────────────────────────────────────────────────

def success(action: str, filename: Optional[str] = None, location: Optional[str] = None) -> str:
    lines = [f"✅ {action}"]
    if filename:
        lines.append(f"\n  📄 File: {filename}")
    if location:
        lines.append(f"  📂 Location: {location}")
    return "\n".join(lines)


def error(reason: str, suggestion: Optional[str] = None) -> str:
    lines = [
        "❌ Error",
        "",
        f"  {reason}",
    ]
    if suggestion:
        lines.append("")
        lines.append(f"  Suggestion: {suggestion}")
    return "\n".join(lines)


def processing(action: str = "Processing") -> str:
    return f"⏳ {action}...\n\n  Please wait."


def confirm_action(action: str, item_name: str) -> str:
    return (
        f"⚠️ Confirm {action}\n"
        f"\n"
        f"  Item: {item_name}\n"
        f"\n"
        f"  This action cannot be undone."
    )


# ── Upload ────────────────────────────────────────────────────────────────────

def upload_mode_enabled() -> str:
    return (
        "📤 Upload Mode\n"
        "\n"
        "  Send a document, image, or video to upload.\n"
        "  It will be saved to your current directory.\n"
        "\n"
        "  Type /cancel to exit upload mode."
    )


def upload_confirm(filename: str, destination: str) -> str:
    return (
        "⚠️ Confirm Upload\n"
        "\n"
        f"  File: {filename}\n"
        f"  Destination: {destination}\n"
        "\n"
        "  Proceed?"
    )


def upload_success(filename: str, location: str) -> str:
    return (
        "✅ Upload Successful\n"
        "\n"
        f"  📄 File: {filename}\n"
        f"  📂 Location: {location}"
    )


# ── Search ────────────────────────────────────────────────────────────────────

def search_results(keyword: str, files: List[Dict]) -> str:
    if not files:
        return (
            f"🔍 No Results\n"
            f"\n"
            f"  No items matched \"{keyword}\".\n"
            f"\n"
            f"  Suggestion: Try a different keyword or use /info."
        )

    lines = [f"🔍 Results for \"{keyword}\"", ""]
    for i, f in enumerate(files, 1):
        icon = _file_icon(f.get("mimeType", ""))
        lines.append(f"  [{i}]  {icon} {f['name']}")
    return "\n".join(lines)


# ── ZIP ───────────────────────────────────────────────────────────────────────

def zip_preparing(count: int, size_str: str) -> str:
    return (
        "📦 Preparing Archive\n"
        "\n"
        f"  Files: {count}\n"
        f"  Estimated size: {size_str}\n"
        "\n"
        "  ⏳ Creating..."
    )


def zip_ready(filename: str, count: int) -> str:
    return (
        "✅ Archive Ready\n"
        "\n"
        f"  📦 {filename}\n"
        f"  Files included: {count}"
    )


# ── Download ──────────────────────────────────────────────────────────────────

def download_progress(filename: str, size_str: str) -> str:
    return (
        "⏳ Downloading\n"
        "\n"
        f"  📄 {filename}\n"
        f"  Size: {size_str}\n"
        "\n"
        "  Please wait..."
    )


def download_too_large(filename: str, size_str: str, view_link: str = "", content_link: str = "") -> str:
    lines = [
        "📄 File Too Large",
        "",
        f"  {filename}",
        f"  Size: {size_str}",
        "",
        "  Telegram limits file transfers to 50 MB.",
        "  Access this file directly from Google Drive:",
    ]
    if view_link:
        lines.append(f"\n  🔗 View: {view_link}")
    if content_link:
        lines.append(f"  ⬇️  Download: {content_link}")
    return "\n".join(lines)


# ── Navigation helpers ────────────────────────────────────────────────────────

def current_path(path: str) -> str:
    return (
        "📍 Current Path\n"
        "\n"
        f"  {path}"
    )


# ── Tools / Help ──────────────────────────────────────────────────────────────

def tools_menu() -> str:
    return (
        "🛠️ Keywords & Abilities\n"
        "\n"
        "Navigation\n"
        "  /info         List current directory\n"
        "  /cd <n>       Enter folder by index\n"
        "  /cd           Go back one level\n"
        "  /pwd          Show current path\n"
        "\n"
        "File Operations\n"
        "  /download <n> Download file by index\n"
        "  /more <n>     View file metadata\n"
        "  /search <q>   Search all files\n"
        "  /upload       Enter upload mode\n"
        "  /zip <q>      Download matching files as ZIP\n"
        "\n"
        "Management\n"
        "  /rename <old> <new>    Rename a file\n"
        "  /delete <name>        Delete a file\n"
        "  /move <file> <folder> Move a file\n"
        "  /mkdir <name>         Create folder\n"
        "\n"
        "Account\n"
        "  /logout       Disconnect Google Drive\n"
        "  /clear        Clear chat messages\n"
        "\n"
        "Help\n"
        "  /help         Show this guide\n"
        "  /tool         Show this guide\n"
    )


def main_menu() -> str:
    return "📌 Main Menu\n\nSelect an option below."


def already_home() -> str:
    return "You are already in the home page."
