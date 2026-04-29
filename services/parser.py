"""
services/parser.py
Utility helpers for parsing command arguments from Telegram messages.
"""


def parse_args(text: str, command: str) -> list[str]:
    """
    Strip the /command prefix and return remaining tokens.
    e.g. parse_args('/rename old.txt new.txt', '/rename') -> ['old.txt', 'new.txt']
    """
    stripped = text.replace(command, "", 1).strip()
    return stripped.split() if stripped else []


def format_file_list(files: list[dict]) -> str:
    """Return a human-readable numbered list of Drive file dicts."""
    if not files:
        return "📭 No files found."
    lines = []
    for i, f in enumerate(files, 1):
        size = f.get("size", "")
        size_str = f" ({_human_size(int(size))})" if size else ""
        lines.append(f"{i}. 📄 {f['name']}{size_str}")
    return "\n".join(lines)


def format_folder_list(folders: list[dict]) -> str:
    """Return a human-readable numbered list of Drive folder dicts."""
    if not folders:
        return "📭 No folders found."
    lines = [f"{i}. 📁 {f['name']}" for i, f in enumerate(folders, 1)]
    return "\n".join(lines)


def _human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
