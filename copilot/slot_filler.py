"""
copilot/slot_filler.py
Multi-turn slot filling engine.

Defines required and optional slots per intent. When a user's message
provides an intent but is missing required information, the slot filler
generates a natural clarification question and tracks the pending state.

The bot NEVER guesses missing information — it always asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ── Slot definitions per intent ───────────────────────────────────────────────

@dataclass
class SlotSpec:
    """Definition of a required slot."""
    name: str
    prompt: str        # Natural question to ask
    entity_key: str    # Key in LLMResult.entities to check


# Required slots for each intent. If any are missing, we ask.
_REQUIRED_SLOTS: dict[str, list[SlotSpec]] = {
    "mkdir": [
        SlotSpec("folder_name", "What would you like to name the folder?", "folder_name"),
    ],
    "rename": [
        SlotSpec("new_name", "What would you like the new name to be?", "new_name"),
    ],
    "move": [
        SlotSpec("target_folder", "Where should I move it?", "target_folder"),
    ],
    "search": [
        SlotSpec("query", "What are you looking for?", "query"),
    ],
    "open_folder": [
        SlotSpec("folder_name", "Which folder should I open?", "folder_name"),
    ],
    "email": [
        SlotSpec("email", "What email address would you like to use?", "email"),
    ],
    "verify": [
        SlotSpec("otp", "Please enter the 6-digit verification code.", "otp"),
    ],
}


# ── Slot Fill Result ──────────────────────────────────────────────────────────

@dataclass
class SlotFillResult:
    """Result of checking slot completeness."""
    complete: bool
    missing_slot: Optional[str] = None
    prompt: Optional[str] = None
    pending_state: Optional[dict] = None


# ── Public API ────────────────────────────────────────────────────────────────

def check_slots(intent: str, entities: dict) -> SlotFillResult:
    """Check if all required slots are filled for the given intent.
    
    Returns SlotFillResult. If complete=True, all required info is available.
    If complete=False, missing_slot and prompt indicate what to ask.
    """
    specs = _REQUIRED_SLOTS.get(intent)
    if not specs:
        return SlotFillResult(complete=True)

    for spec in specs:
        value = entities.get(spec.entity_key)
        if not value or not str(value).strip():
            return SlotFillResult(
                complete=False,
                missing_slot=spec.name,
                prompt=spec.prompt,
                pending_state={
                    "intent": intent,
                    "entities": entities,
                    "awaiting_slot": spec.name,
                    "entity_key": spec.entity_key,
                },
            )

    return SlotFillResult(complete=True)


def fill_pending_slot(pending_state: dict, user_text: str) -> dict:
    """Fill a pending slot with the user's response.
    
    Returns the updated entities dict.
    """
    entities = dict(pending_state.get("entities", {}))
    entity_key = pending_state.get("entity_key", "")
    if entity_key:
        entities[entity_key] = user_text.strip()
    return entities


def get_pending_intent(user_data: dict) -> Optional[dict]:
    """Get any pending slot-fill state from user_data."""
    return user_data.get("_pending_slots")


def set_pending(user_data: dict, pending_state: dict) -> None:
    """Store pending slot-fill state in user_data."""
    user_data["_pending_slots"] = pending_state


def clear_pending(user_data: dict) -> None:
    """Clear pending slot-fill state."""
    user_data.pop("_pending_slots", None)


def has_pending(user_data: dict) -> bool:
    """Check if there's a pending slot-fill state."""
    return "_pending_slots" in user_data
