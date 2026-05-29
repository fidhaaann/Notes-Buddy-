"""Intent types and structures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class IntentType(str, Enum):
    BROWSE = "browse"
    OPEN_FOLDER = "open_folder"
    BACK = "back"
    PWD = "pwd"
    SEARCH = "search"
    DOWNLOAD = "download"
    UPLOAD = "upload"
    INFO = "info"
    DELETE = "delete"
    RENAME = "rename"
    MOVE = "move"
    INDEX = "index"
    HELP = "help"
    UNKNOWN = "unknown"


@dataclass
class Intent:
    intent: IntentType
    confidence: float
    raw_text: str
    query: Optional[str] = None
    index: Optional[str] = None
    target_name: Optional[str] = None
    needs_confirmation: bool = False
