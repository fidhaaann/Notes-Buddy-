"""
copilot/dialogue.py
Dialogue Manager — the V2 "Digital Butler" brain.

Architecture:
  Incoming Message
    ↓
  Selection Resolver (resolve "1", "first", "that file")
    ↓
  Pending Task Resolver (handle unfinished tasks)
    ↓
  Reference Resolver (resolve "this", "that", "it")
    ↓
  Dialogue Manager (manage context, confirmations, follow-ups)
    ↓
  Intent Detection (NLP or LLM)
    ↓
  Execution

The Dialogue Manager:
  - Manages unfinished tasks
  - Manages selections from previous results
  - Manages confirmations and follow-up questions
  - Manages context across turns
  - Reduces user effort by inferring missing information
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bot import nav
from nlp import context as nlp_context
from nlp import normalize

logger = logging.getLogger(__name__)

# ── Selection patterns ────────────────────────────────────────────────────────

_ORDINAL_MAP = {
    "first": "1", "1st": "1",
    "second": "2", "2nd": "2",
    "third": "3", "3rd": "3",
    "fourth": "4", "4th": "4",
    "fifth": "5", "5th": "5",
    "sixth": "6", "6th": "6",
    "seventh": "7", "7th": "7",
    "eighth": "8", "8th": "8",
    "ninth": "9", "9th": "9",
    "tenth": "10", "10th": "10",
    "last": "-1",
    "last one": "-1",
    "the last": "-1",
    "the last one": "-1",
}

# Patterns that are JUST a selection reference (no action attached)
_PURE_SELECTION_RE = re.compile(
    r"^(?:the\s+)?"
    r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last)"
    r"(?:\s+(?:one|file|folder|item|document))?$",
    re.IGNORECASE,
)

_PURE_NUMBER_RE = re.compile(r"^\d{1,2}$")

# Confirmation patterns
_CONFIRM_PATTERNS = {"yes", "confirm", "ok", "proceed", "sure", "do it", "go ahead", "yep", "yeah", "y"}
_DENY_PATTERNS = {"no", "cancel", "stop", "abort", "never mind", "nah", "nope", "n", "don't", "dont"}


def resolve_selection(uid: int, text: str) -> Optional[nav.IndexedItem]:
    """Resolve a pure selection reference against the active view.

    This runs BEFORE NLP/intent detection. If the user's message is
    purely a selection (e.g. "1", "first", "the second one"), we resolve
    it directly against the active view.

    Returns the selected IndexedItem, or None if not a pure selection.
    """
    cleaned = text.strip().lower()

    # Check for pure number: "1", "2", etc.
    if _PURE_NUMBER_RE.match(cleaned):
        return nav.resolve_index(uid, cleaned)

    # Check for ordinal: "first", "second", "last one", etc.
    ordinal_match = _PURE_SELECTION_RE.match(cleaned)
    if ordinal_match:
        word = ordinal_match.group(1).lower()
        idx = _ORDINAL_MAP.get(word)
        if idx:
            return nav.resolve_smart(uid, word)

    # Check bare ordinals without "one"
    if cleaned in _ORDINAL_MAP:
        idx_str = _ORDINAL_MAP[cleaned]
        return nav.resolve_smart(uid, cleaned)

    # "that file", "that one", "this one" → last remembered item
    if cleaned in {"that file", "that one", "this one", "this file", "that", "this"}:
        return None  # Let the reference resolver handle these

    return None


def is_pure_selection(text: str) -> bool:
    """Check if text is a pure selection reference (no action verb)."""
    cleaned = text.strip().lower()
    if _PURE_NUMBER_RE.match(cleaned):
        return True
    if _PURE_SELECTION_RE.match(cleaned):
        return True
    if cleaned in _ORDINAL_MAP:
        return True
    return False


def is_confirmation(text: str) -> bool:
    """Check if user's message is a confirmation response."""
    cleaned = text.strip().lower().rstrip("!?.")
    return cleaned in _CONFIRM_PATTERNS


def is_denial(text: str) -> bool:
    """Check if user's message is a denial/cancellation response."""
    cleaned = text.strip().lower().rstrip("!?.")
    return cleaned in _DENY_PATTERNS


def get_default_action(item: nav.IndexedItem) -> str:
    """Determine the default action for an item.

    Folder → OPEN
    File → DOWNLOAD or PREVIEW (depending on type)
    """
    if item.is_folder:
        return "open"
    return "download"


def resolve_disambiguation(text: str) -> Optional[str]:
    """Try to understand what the user wants when input is ambiguous.

    If a single word is typed that could be a subject/topic,
    suggest searching for it rather than failing.
    """
    cleaned = text.strip().lower()

    # Skip if it looks like a command or has action words
    action_words = {
        "download", "upload", "search", "find", "show", "open", "enter",
        "delete", "remove", "rename", "move", "copy", "share", "zip",
        "create", "favorite", "help", "menu", "browse", "back", "login",
        "logout", "cancel", "verify", "email", "index", "security",
    }

    tokens = cleaned.split()
    if tokens and tokens[0] in action_words:
        return None

    # If it's a single meaningful word or short phrase, suggest search
    if 1 <= len(tokens) <= 4 and len(cleaned) >= 2:
        return cleaned

    return None
