"""
copilot/greeting.py
Fast greeting and chitchat handler — no LLM call needed.

Detects greetings, thanks, farewells, and common small talk via regex/set
matching and returns natural, contextual responses instantly.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class GreetingResult:
    """Result of greeting detection."""
    matched: bool
    response: str = ""
    category: str = ""  # "greeting", "farewell", "thanks", "smalltalk"


# ── Pattern sets ──────────────────────────────────────────────────────────────

_GREETING_PATTERNS: set[str] = {
    "hello", "hi", "hey", "hola", "howdy", "yo",
    "hii", "hiii", "hiiii", "helo", "helo",
    "heya", "heyo", "sup", "wassup", "whatsup",
}

_GREETING_PHRASES: list[re.Pattern[str]] = [
    re.compile(r"^(good\s+)?(morning|afternoon|evening|day)", re.IGNORECASE),
    re.compile(r"^what'?s\s+up", re.IGNORECASE),
    re.compile(r"^how\s+(are\s+you|r\s+u|is\s+it\s+going|do\s+you\s+do)", re.IGNORECASE),
    re.compile(r"^how'?s\s+(it\s+going|everything|things|life)", re.IGNORECASE),
    re.compile(r"^(greetings|salutations)", re.IGNORECASE),
]

_FAREWELL_PATTERNS: set[str] = {
    "bye", "goodbye", "goodnight", "cya", "see ya",
    "later", "peace", "ttyl", "gn",
}

_FAREWELL_PHRASES: list[re.Pattern[str]] = [
    re.compile(r"^good\s*night", re.IGNORECASE),
    re.compile(r"^see\s+you\s+(later|tomorrow|around|soon)", re.IGNORECASE),
    re.compile(r"^(take\s+care|have\s+a\s+good)", re.IGNORECASE),
    re.compile(r"^(g2g|gtg|gotta\s+go)", re.IGNORECASE),
]

_THANKS_PATTERNS: set[str] = {
    "thanks", "thank you", "thankyou", "thx", "ty",
    "appreciated", "much appreciated", "cheers",
    "thanks a lot", "thank you so much", "thanks a ton",
    "tysm", "tyvm",
}

_THANKS_PHRASES: list[re.Pattern[str]] = [
    re.compile(r"^thank(s|\s+you)", re.IGNORECASE),
    re.compile(r"^(much\s+)?appreciated", re.IGNORECASE),
    re.compile(r"^(great|awesome|perfect|nice|cool|wonderful)(\s+work)?[.!]*$", re.IGNORECASE),
]

_SMALLTALK_PHRASES: list[re.Pattern[str]] = [
    re.compile(r"^who\s+are\s+you", re.IGNORECASE),
    re.compile(r"^what\s+are\s+you", re.IGNORECASE),
    re.compile(r"^are\s+you\s+(a\s+bot|real|human|ai)", re.IGNORECASE),
    re.compile(r"^(lol|haha|hehe|lmao|😂|😁|👋|🙂)", re.IGNORECASE),
    re.compile(r"^(ok|okay|alright|sure|fine|got\s+it|understood|k)\.?$", re.IGNORECASE),
]


# ── Response pools ────────────────────────────────────────────────────────────

_GREETING_RESPONSES: list[str] = [
    "Hello 👋\n\nHow can I help today?\n\nYou can ask things like:\n• Show my DBMS notes\n• Find module 2 PDFs\n• Upload a file\n• Open Semester 4 folder",
    "Hey there 👋\n\nWhat can I do for you?\n\nTry:\n• Search for notes\n• Browse your files\n• Download a document\n• Create a folder",
    "Hi! 👋\n\nReady to help with your Drive.\n\nJust tell me what you need:\n• Find notes by topic\n• Open a folder\n• Upload or download files\n• Organize your files",
]

_MORNING_RESPONSES: list[str] = [
    "Good morning! ☀️\n\nWhat would you like to work on today?\n\nYou can ask me to:\n• Show your recent notes\n• Find study materials\n• Browse your folders",
    "Morning! ☀️\n\nReady to help with your Drive.\n\nTry: \"show my recent files\" or \"find DBMS notes\"",
]

_EVENING_RESPONSES: list[str] = [
    "Good evening! 🌙\n\nNeed help finding something?\n\nJust tell me what you're looking for.",
    "Evening! 🌙\n\nHow can I help?\n\nTry: \"show my notes\" or \"find module 2 PDF\"",
]

_FAREWELL_RESPONSES: list[str] = [
    "Goodbye! 👋\n\nFeel free to come back anytime you need help with your Drive.",
    "See you later! 👋\n\nI'll be here whenever you need your files.",
    "Take care! 👋\n\nYour Drive is just a message away.",
]

_GOODNIGHT_RESPONSES: list[str] = [
    "Good night! 🌙\n\nHave a great rest. I'll be here when you need me.",
    "Night! 🌙\n\nSee you tomorrow.",
]

_THANKS_RESPONSES: list[str] = [
    "You're welcome! 😊\n\nLet me know if you need anything else.",
    "Happy to help! 😊\n\nJust ask if you need anything.",
    "Anytime! 😊\n\nI'm here whenever you need help with your Drive.",
    "Glad I could help! 😊\n\nFeel free to ask anything else.",
]

_SMALLTALK_IDENTITY_RESPONSE: str = (
    "I'm NotesBuddy — your personal Google Drive assistant! 🤖\n"
    "\n"
    "I can help you:\n"
    "• Find and organize your files\n"
    "• Search notes by topic\n"
    "• Upload, download, and manage files\n"
    "• Navigate your Drive folders\n"
    "\n"
    "Just tell me what you need in plain English!"
)

_ACKNOWLEDGMENT_RESPONSES: list[str] = [
    "Got it! Let me know what you'd like to do next. 👍",
    "Alright! Need anything else? 👍",
    "Sure thing! What's next? 👍",
]


# ── Public API ────────────────────────────────────────────────────────────────

def detect_greeting(text: str) -> GreetingResult:
    """Detect if text is a greeting/chitchat and return an appropriate response.
    
    Returns GreetingResult with matched=False if text is not chitchat.
    This function is designed to be fast (regex + set lookup, no LLM call).
    """
    cleaned = text.strip().rstrip("!?.").strip()
    lowered = cleaned.lower()

    # ── Greetings ─────────────────────────────────────────────────────────
    if lowered in _GREETING_PATTERNS:
        return GreetingResult(
            matched=True,
            response=random.choice(_GREETING_RESPONSES),
            category="greeting",
        )

    for pattern in _GREETING_PHRASES:
        if pattern.search(cleaned):
            # Check for morning/evening specificity
            if re.search(r"morning", lowered):
                return GreetingResult(True, random.choice(_MORNING_RESPONSES), "greeting")
            if re.search(r"(evening|night)", lowered):
                return GreetingResult(True, random.choice(_EVENING_RESPONSES), "greeting")
            return GreetingResult(True, random.choice(_GREETING_RESPONSES), "greeting")

    # ── Farewells ─────────────────────────────────────────────────────────
    if lowered in _FAREWELL_PATTERNS:
        if "night" in lowered or lowered == "gn":
            return GreetingResult(True, random.choice(_GOODNIGHT_RESPONSES), "farewell")
        return GreetingResult(True, random.choice(_FAREWELL_RESPONSES), "farewell")

    for pattern in _FAREWELL_PHRASES:
        if pattern.search(cleaned):
            if re.search(r"night", lowered):
                return GreetingResult(True, random.choice(_GOODNIGHT_RESPONSES), "farewell")
            return GreetingResult(True, random.choice(_FAREWELL_RESPONSES), "farewell")

    # ── Thanks ────────────────────────────────────────────────────────────
    if lowered in _THANKS_PATTERNS:
        return GreetingResult(True, random.choice(_THANKS_RESPONSES), "thanks")

    for pattern in _THANKS_PHRASES:
        if pattern.search(cleaned):
            return GreetingResult(True, random.choice(_THANKS_RESPONSES), "thanks")

    # ── Small talk ────────────────────────────────────────────────────────
    for pattern in _SMALLTALK_PHRASES:
        if pattern.search(cleaned):
            if re.search(r"who|what|are\s+you", lowered):
                return GreetingResult(True, _SMALLTALK_IDENTITY_RESPONSE, "smalltalk")
            return GreetingResult(True, random.choice(_ACKNOWLEDGMENT_RESPONSES), "smalltalk")

    return GreetingResult(matched=False)
