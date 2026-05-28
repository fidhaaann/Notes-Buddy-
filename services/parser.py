"""
services/parser.py
Utility helpers for parsing command arguments and formatting output.
"""


def parse_args(text: str, command: str) -> list[str]:
    """
    Strip the /command prefix (and any @botname suffix) and return remaining tokens.
    e.g. parse_args('/rename old.txt new.txt', '/rename') -> ['old.txt', 'new.txt']
    """
    # Handle /command@botname format
    text = text.split("@")[0] if "@" in text.split()[0] else text
    stripped = text.replace(command, "", 1).strip()
    return stripped.split() if stripped else []


def parse_command_text(text: str) -> list[str]:
    """Split a command text into arguments without requiring a command name."""
    if not text:
        return []
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return []
    return parts[1].split()


def human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
