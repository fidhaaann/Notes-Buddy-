"""Lightweight per-user NLP context stored in user_data."""

from __future__ import annotations

import time
from typing import Optional

from security import limits


def get_state(user_data: dict) -> dict:
    state = user_data.get("nlp_state")
    if not isinstance(state, dict):
        state = {"updated_at": 0}
        user_data["nlp_state"] = state
    return state


def set_state(user_data: dict, **kwargs) -> None:
    state = get_state(user_data)
    state.update(kwargs)
    state["updated_at"] = time.time()


def is_expired(user_data: dict) -> bool:
    state = get_state(user_data)
    updated = state.get("updated_at", 0)
    return time.time() - updated > limits.NLP_CONTEXT_TTL_SECONDS


def clear_state(user_data: dict) -> None:
    user_data.pop("nlp_state", None)


# ── Conversation history helpers (copilot) ────────────────────────────────────

def add_turn(user_data: dict, role: str, content: str, intent: str = "") -> None:
    """Add a conversation turn to session history."""
    history = user_data.setdefault("_conv_history", [])
    history.append({
        "role": role,
        "content": content[:500],
        "intent": intent,
        "ts": time.time(),
    })
    # Trim to max turns
    max_entries = getattr(limits, "COPILOT_MEMORY_TURNS", 10) * 2
    if len(history) > max_entries:
        user_data["_conv_history"] = history[-max_entries:]


def get_history(user_data: dict, limit: int = 10) -> list[dict[str, str]]:
    """Get recent conversation history formatted for the LLM."""
    history = user_data.get("_conv_history", [])
    ttl = getattr(limits, "COPILOT_MEMORY_TTL", 900)
    now = time.time()
    # Filter by TTL
    valid = [h for h in history if now - h.get("ts", 0) < ttl]
    recent = valid[-limit:]
    return [{"role": h["role"], "content": h["content"]} for h in recent]


def set_last_results(user_data: dict, results: list[dict], label: str = "") -> None:
    """Store the last file listing results for reference resolution."""
    state = get_state(user_data)
    state["last_results"] = results[:25]
    state["last_results_label"] = label
    state["updated_at"] = time.time()


def get_last_results(user_data: dict) -> list[dict]:
    """Get the last file listing results."""
    state = get_state(user_data)
    return state.get("last_results", [])


def resolve_result_reference(user_data: dict, ref: str) -> Optional[dict]:
    """Resolve an index reference (e.g. '2') to a file from last results."""
    results = get_last_results(user_data)
    if not results:
        return None
    try:
        idx = int(ref) - 1
        if 0 <= idx < len(results):
            return results[idx]
    except (ValueError, TypeError):
        pass
    return None

