"""Lightweight per-user NLP context stored in user_data."""

from __future__ import annotations

import time

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
