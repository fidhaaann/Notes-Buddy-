"""Intent types and structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class IntentType(str, Enum):
    START = "start"
    LOGIN = "login"
    LOGOUT = "logout"
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
    COPY = "copy"
    SHARE = "share"
    ZIP = "zip"
    MKDIR = "mkdir"
    FAVORITE = "favorite"
    UNFAVORITE = "unfavorite"
    FAVORITES = "favorites"
    RECENT = "recent"
    MENU = "menu"
    TOOL = "tool"
    EMAIL = "email"
    VERIFY = "verify"
    CANCEL = "cancel"
    CLEAR = "clear"
    INDEX = "index"
    HELP = "help"
    GREETING = "greeting"
    OFF_TOPIC = "off_topic"
    UNKNOWN = "unknown"


@dataclass
class Intent:
    intent: IntentType
    confidence: float
    raw_text: str
    query: Optional[str] = None
    index: Optional[str] = None
    target_name: Optional[str] = None
    email: Optional[str] = None
    otp: Optional[str] = None
    needs_confirmation: bool = False
    bulk: bool = False
    action: Optional[str] = None
    file_type_hint: Optional[str] = None
    suggested_actions: list[str] = field(default_factory=list)
    source: str = "keyword"  # "keyword" or "llm"
    is_fresh_query: bool = True  # True = new search, False = follow-up reference
    search_scope: Optional[str] = None  # "entire_drive", "current_folder", folder name, or type filter

