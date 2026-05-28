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
    if "shortcut" in m:                                     return "↪"
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
    if "shortcut" in m:            return "Shortcut"
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


def email_setup_prompt() -> str:
    return (
        "📧 Security Alerts\n"
        "\n"
        "Set your email to receive threat notifications.\n"
        "Reply with your email now (e.g. you@example.com)\n"
        "or use /email you@example.com\n"
        "\n"
        "This helps protect your Drive from unusual activity."
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
            icon = _file_icon(item.mime_type)
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
    lines.append("  /more <n>  info  /delete <n>")
    lines.append("  /cd  go back")

    return "\n".join(lines)


def partial_browse_warning(error_count: int, truncated: bool, used_fallback: bool = False) -> str:
    lines = [
        "❌ Unable to Fully Load Drive Structure",
        "",
        "Some folders or files could not be accessed safely.",
        "Accessible items have been loaded successfully.",
    ]
    if truncated:
        lines.extend(["", "Some large folders were truncated to keep browsing stable."])
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


def confirm_delete_preview(meta: Dict, index: str | None = None) -> str:
    """Confirmation message with a quick preview before deletion."""
    from services.parser import human_size

    name = meta.get("name", "Unknown")
    mime = meta.get("mimeType", "Unknown")
    size_raw = int(meta["size"]) if meta.get("size") else 0
    size = human_size(size_raw) if size_raw else "Unknown"
    modified = _format_date(meta.get("modifiedTime", ""))
    file_type = _file_type_label(mime)
    path = meta.get("_path", "")

    lines = [
        "⚠️ Confirm Delete",
        "",
        f"  Name:     {name}",
        f"  Type:     {file_type}",
        f"  Size:     {size}",
        f"  Modified: {modified}",
    ]
    if index:
        lines.insert(2, f"  Index:    [{index}]")
    if path:
        lines.append(f"  Path:     {path}")
    lines.extend(["", "  This action cannot be undone."])
    return "\n".join(lines)


def stepup_email_required(action: str) -> str:
    return (
        "🔐 Verification Required\n"
        "\n"
        f"  To {action}, set your email first.\n"
        "  Reply with your email now (e.g. you@example.com)\n"
        "  or use /email you@example.com\n"
        "\n"
        "  This protects your account from unauthorized actions."
    )


def stepup_code_sent(action: str, email: str, ttl: int) -> str:
    return (
        "🔐 Verification Required\n"
        "\n"
        f"  To {action}, enter the code we sent to:\n"
        f"  {email}\n"
        "\n"
        "  Reply with the 6-digit code\n"
        "  or use /verify <code>\n"
        f"  Code expires in {ttl} minutes."
    )


def stepup_code_pending(action: str, email: str, retry_after: int) -> str:
    return (
        "🔐 Verification Required\n"
        "\n"
        f"  A code was already sent to:\n"
        f"  {email}\n"
        "\n"
        "  Reply with the 6-digit code\n"
        "  or use /verify <code>\n"
        f"  You can request a new code in {retry_after} seconds."
    )


def stepup_email_failed() -> str:
    return (
        "❌ Error\n"
        "\n"
        "  Email verification is not configured.\n"
        "  Contact the administrator."
    )


def stepup_verified(window_min: int) -> str:
    return (
        "✅ Verification Complete\n"
        "\n"
        f"  You're verified for the next {window_min} minutes.\n"
        "  Please retry your action."
    )


def stepup_invalid_code(remaining: int) -> str:
    return (
        "❌ Invalid Code\n"
        "\n"
        f"  Attempts remaining: {remaining}\n"
        "  Please try again."
    )


def stepup_code_expired() -> str:
    return (
        "❌ Code Expired\n"
        "\n"
        "  Request a new code by retrying the action."
    )


def stepup_locked() -> str:
    return (
        "❌ Too Many Attempts\n"
        "\n"
        "  Please request a new code by retrying the action."
    )


def stepup_already_verified(remaining: int) -> str:
    return (
        "✅ Already Verified\n"
        "\n"
        f"  You are verified for ~{remaining} more minutes."
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
    """Legacy search results formatter. Use search_results_indexed() instead."""
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


def search_results_indexed(keyword: str, index_map: dict[str, IndexedItem]) -> str:
    """Format search results with proper IndexedItem index mapping."""
    if not index_map:
        return (
            f"🔍 No Results for \"{keyword}\"\n"
            f"\n"
            f"  No items matched.\n"
            f"\n"
            f"  Suggestion: Try a different keyword or use /info."
        )

    lines = [f"🔍 Search Results for \"{keyword}\"", ""]
    
    # Sort indices numerically (1, 2, 3, ...)
    for idx in sorted(index_map.keys(), key=lambda x: int(x)):
        item = index_map[idx]
        icon = _file_icon(item.mime_type)
        lines.append(f"  [{idx}]  {icon} {item.name}")
    
    lines.append("")
    lines.append("─" * 34)
    lines.append("  /download <n>  /more <n>")
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


def task_queued(action: str, target: str, size_str: str | None, job_id: str) -> str:
    lines = [
        f"🧾 {action} Queued",
        "",
        f"  ID: {job_id[:8]}",
    ]
    if target:
        lines.append(f"  Item: {target}")
    if size_str:
        lines.append(f"  Size: {size_str}")
    lines.append("")
    lines.append("  You will receive the result shortly.")
    return "\n".join(lines)


def task_running(task_type: str) -> str:
    label = "Processing" if task_type else "Working"
    return f"⏳ {label}...\n\n  Please wait."


def task_complete(action: str, filename: str | None = None) -> str:
    lines = [f"✅ {action} Complete"]
    if filename:
        lines.append("")
        lines.append(f"  📄 {filename}")
    return "\n".join(lines)


def task_failed(action: str) -> str:
    return (
        f"❌ {action} Failed\n"
        "\n"
        "  Please try again or use /help."
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
        "  /rename <n> <new>     Rename by index\n"
        "  /delete <n>           Delete by index\n"
        "  /move <f> <d>         Move file to folder by index\n"
        "  /mkdir <name>         Create folder\n"
        "\n"
        "Account\n"
        "  /logout       Disconnect Google Drive\n"
        "  /email <addr> Set email for security alerts\n"
        "  /verify <otp> Confirm a sensitive action\n"
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
