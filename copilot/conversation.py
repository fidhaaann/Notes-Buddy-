"""
copilot/conversation.py
Session-scoped conversation memory for contextual understanding.

Stores recent message pairs so the bot can resolve references like
"the second one", "that file", "download it", etc.

Memory is:
  - Session-scoped (stored in context.user_data)
  - Lightweight (only text summaries, not full API responses)
  - Automatically expired by TTL
  - Limited to N turns to control LLM token usage
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConversationTurn:
    """A single conversation turn."""
    role: str        # "user" or "assistant"
    content: str     # message text
    intent: str      # resolved intent type (e.g. "search", "download")
    timestamp: float # time.time()


@dataclass
class ConversationMemory:
    """Session-scoped conversation memory."""
    turns: list[ConversationTurn] = field(default_factory=list)
    last_results: list[dict] = field(default_factory=list)  # files from last search/browse
    last_result_label: str = ""  # "Search: dbms" or "Folder: Notes"
    created_at: float = field(default_factory=time.time)

    def add_user_turn(self, content: str, intent: str = "") -> None:
        """Record a user message."""
        self.turns.append(ConversationTurn(
            role="user",
            content=content[:500],  # truncate for memory efficiency
            intent=intent,
            timestamp=time.time(),
        ))
        self._trim()

    def add_assistant_turn(self, content: str, intent: str = "") -> None:
        """Record a bot response."""
        self.turns.append(ConversationTurn(
            role="assistant",
            content=content[:500],
            intent=intent,
            timestamp=time.time(),
        ))
        self._trim()

    def set_results(self, results: list[dict], label: str = "") -> None:
        """Store the last file listing results for reference resolution."""
        self.last_results = results[:25]  # cap to save memory
        self.last_result_label = label

    def get_history_for_llm(self, limit: int = 10) -> list[dict[str, str]]:
        """Get recent conversation history formatted for the LLM.
        
        Returns list of {"role": "user"|"assistant", "content": str}.
        """
        recent = self.turns[-limit:]
        return [
            {"role": turn.role, "content": turn.content}
            for turn in recent
        ]

    def get_context_summary(self) -> str:
        """Generate a context string for the LLM prompt.
        
        Includes info about recent results and last actions.
        """
        parts: list[str] = []

        if self.last_results:
            result_names = [r.get("name", "file") for r in self.last_results[:5]]
            parts.append(f"Last shown files: {', '.join(result_names)}")
            if self.last_result_label:
                parts.append(f"From: {self.last_result_label}")

        # Summarize recent intents
        recent_intents = [
            t.intent for t in self.turns[-4:]
            if t.role == "user" and t.intent
        ]
        if recent_intents:
            parts.append(f"Recent actions: {', '.join(recent_intents)}")

        return "; ".join(parts) if parts else ""

    def resolve_index_reference(self, ref: str) -> Optional[dict]:
        """Resolve an index reference like '2', 'second one' to a file from last results."""
        if not self.last_results:
            return None
        try:
            idx = int(ref) - 1  # 1-indexed to 0-indexed
            if 0 <= idx < len(self.last_results):
                return self.last_results[idx]
        except (ValueError, TypeError):
            pass
        return None

    def is_expired(self, ttl_seconds: int = 900) -> bool:
        """Check if the conversation memory has expired."""
        if not self.turns:
            return False
        last_ts = self.turns[-1].timestamp
        return time.time() - last_ts > ttl_seconds

    def _trim(self) -> None:
        """Keep only the most recent turns to prevent unbounded growth."""
        from security import limits
        max_turns = getattr(limits, "COPILOT_MEMORY_TURNS", 10)
        # Each user+assistant pair is 2 turns, so keep 2x
        max_entries = max_turns * 2
        if len(self.turns) > max_entries:
            self.turns = self.turns[-max_entries:]


# ── user_data helpers ─────────────────────────────────────────────────────────

_MEMORY_KEY = "_copilot_memory"


def get_memory(user_data: dict) -> ConversationMemory:
    """Get or create conversation memory from user_data."""
    from security import limits
    ttl = getattr(limits, "COPILOT_MEMORY_TTL", 900)

    memory = user_data.get(_MEMORY_KEY)
    if not isinstance(memory, ConversationMemory):
        memory = ConversationMemory()
        user_data[_MEMORY_KEY] = memory
    elif memory.is_expired(ttl):
        memory = ConversationMemory()
        user_data[_MEMORY_KEY] = memory
    return memory


def clear_memory(user_data: dict) -> None:
    """Clear conversation memory."""
    user_data.pop(_MEMORY_KEY, None)
