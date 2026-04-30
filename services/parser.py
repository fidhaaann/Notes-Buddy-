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


def human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# Legacy alias kept for backwards compatibility
_human_size = human_size
