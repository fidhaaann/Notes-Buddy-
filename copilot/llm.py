"""
copilot/llm.py
Gemini LLM integration for intent extraction and conversational understanding.

The LLM NEVER executes operations. It only:
  - Understands user intent
  - Extracts entities (filenames, folders, types)
  - Identifies missing slots
  - Generates clarification questions
  - Produces suggested next actions

All actual operations are validated and executed by the backend.

Security:
  - System prompt is hardcoded (not user-modifiable)
  - User messages truncated to COPILOT_MAX_PROMPT_CHARS
  - Structured JSON output only (no free-text commands)
  - Rate-limited to stay within Gemini free tier
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Lazy import for google.generativeai ───────────────────────────────────────
_genai = None
_model = None
_last_call_ts: float = 0.0


def _get_genai():
    global _genai
    if _genai is None:
        try:
            import google.generativeai as genai
            _genai = genai
            api_key = os.environ.get("GEMINI_API_KEY", "")
            if api_key:
                _genai.configure(api_key=api_key)
            else:
                logger.warning("GEMINI_API_KEY not set — copilot LLM disabled.")
                _genai = None
        except ImportError:
            logger.warning("google-generativeai not installed — copilot LLM disabled.")
            _genai = None
    return _genai


def _get_model():
    global _model
    if _model is None:
        genai = _get_genai()
        if genai is None:
            return None
        from security import limits
        model_name = getattr(limits, "GEMINI_MODEL", "gemini-2.0-flash")
        _model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=_SYSTEM_PROMPT,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.2,
                "max_output_tokens": 512,
            },
        )
    return _model


# ── System Prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are NotesBuddy, a personal Google Drive assistant for students.

Your ONLY job is to understand what the user wants and return a structured JSON response.
You NEVER execute operations, access files, or make changes. The backend handles all actions.

RULES:
1. You must ALWAYS respond with valid JSON matching the schema below.
2. NEVER invent files, folders, or search results. Set is_chitchat=true for casual chat.
3. If the user asks something unrelated to Google Drive / file management, set is_off_topic=true and suggest searching their Drive instead.
4. For file type requests, map common words: "notes" → pdf/docx/pptx, "videos" → mp4/mkv/mov/webm/avi, "pictures"/"images"/"photos" → jpg/jpeg/png/webp/gif, "presentations"/"slides" → ppt/pptx, "recordings" → video, "documents" → doc/docx.
5. Resolve ordinal references: "the second one" → index_ref="2", "first file" → index_ref="1".
6. If a required slot is missing, list it in missing_slots and provide a natural clarification question.
7. Keep chitchat_response and clarification concise (1-2 sentences max).
8. suggested_actions should be 2-4 short action phrases the user might want next.

INTENTS (use exactly these values):
start, login, logout, browse, open_folder, back, pwd, search, download, upload,
info, delete, rename, move, copy, share, zip, mkdir, favorite, unfavorite,
favorites, recent, menu, tool, email, verify, cancel, clear, index, help, greeting, off_topic

JSON SCHEMA:
{
  "is_chitchat": boolean,
  "chitchat_response": string or null,
  "intent": string (one of the INTENTS above),
  "confidence": number (0.0 to 1.0),
  "entities": {
    "query": string or null,
    "file_type": string or null,
    "folder_name": string or null,
    "new_name": string or null,
    "index_ref": string or null,
    "email": string or null,
    "otp": string or null,
    "target_folder": string or null
  },
  "missing_slots": [string] or [],
  "clarification": string or null,
  "suggested_actions": [string] or [],
  "is_off_topic": boolean,
  "redirect_suggestion": string or null,
  "bulk": boolean
}"""


# ── Output dataclass ──────────────────────────────────────────────────────────

@dataclass
class LLMResult:
    """Structured output from the Gemini LLM."""
    success: bool = False
    is_chitchat: bool = False
    chitchat_response: str = ""
    intent: str = "unknown"
    confidence: float = 0.0
    entities: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    clarification: str = ""
    suggested_actions: list[str] = field(default_factory=list)
    is_off_topic: bool = False
    redirect_suggestion: str = ""
    bulk: bool = False
    error: str = ""


# ── Rate limiting ─────────────────────────────────────────────────────────────

def _check_rate_limit() -> bool:
    """Returns True if we can make a call. Simple per-minute throttle."""
    global _last_call_ts
    from security import limits
    rpm_limit = getattr(limits, "COPILOT_RATE_LIMIT_RPM", 14)
    min_interval = 60.0 / rpm_limit  # ~4.3 seconds between calls
    now = time.monotonic()
    if now - _last_call_ts < min_interval:
        return False
    _last_call_ts = now
    return True


# ── Public API ────────────────────────────────────────────────────────────────

def is_available() -> bool:
    """Check if the LLM is configured and available."""
    from security import limits
    if not getattr(limits, "COPILOT_ENABLED", True):
        return False
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return False
    return True


async def extract_intent(
    user_message: str,
    conversation_history: list[dict[str, str]] | None = None,
    user_context: str = "",
) -> LLMResult:
    """Extract intent and entities from a user message using Gemini.

    Args:
        user_message: The raw user message text.
        conversation_history: Recent turns as [{"role": "user"|"assistant", "content": str}].
        user_context: Additional context string (current folder, recent files, etc.).

    Returns:
        LLMResult with parsed intent, entities, and suggestions.
        If Gemini is unavailable, returns LLMResult(success=False).
    """
    import asyncio
    from security import limits

    if not is_available():
        return LLMResult(success=False, error="LLM not available")

    if not _check_rate_limit():
        return LLMResult(success=False, error="Rate limited")

    model = _get_model()
    if model is None:
        return LLMResult(success=False, error="Model not initialized")

    # Truncate user message for safety
    max_chars = getattr(limits, "COPILOT_MAX_PROMPT_CHARS", 2000)
    safe_message = user_message[:max_chars]

    # Build conversation content for the API
    contents = []

    # Add conversation history (limited)
    max_turns = getattr(limits, "COPILOT_MEMORY_TURNS", 10)
    if conversation_history:
        for turn in conversation_history[-max_turns:]:
            role = "user" if turn.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [turn.get("content", "")]})

    # Build current user prompt with context
    user_prompt_parts = []
    if user_context:
        user_prompt_parts.append(f"[Context: {user_context}]")
    user_prompt_parts.append(safe_message)
    contents.append({"role": "user", "parts": ["\n".join(user_prompt_parts)]})

    try:
        response = await asyncio.to_thread(
            model.generate_content,
            contents,
        )

        if not response or not response.text:
            return LLMResult(success=False, error="Empty response from Gemini")

        return _parse_response(response.text)

    except Exception as exc:
        logger.warning("gemini_call_failed: %s", str(exc)[:200])
        return LLMResult(success=False, error=str(exc)[:200])


def _parse_response(raw_text: str) -> LLMResult:
    """Parse Gemini's JSON response into an LLMResult."""
    try:
        # Handle potential markdown code fences
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)

        entities = data.get("entities") or {}
        # Normalize entity values
        clean_entities: dict[str, Any] = {}
        for key in ("query", "file_type", "folder_name", "new_name",
                     "index_ref", "email", "otp", "target_folder"):
            val = entities.get(key)
            if val and isinstance(val, str) and val.strip():
                clean_entities[key] = val.strip()

        return LLMResult(
            success=True,
            is_chitchat=bool(data.get("is_chitchat", False)),
            chitchat_response=str(data.get("chitchat_response") or ""),
            intent=str(data.get("intent", "unknown")).lower(),
            confidence=min(1.0, max(0.0, float(data.get("confidence", 0.5)))),
            entities=clean_entities,
            missing_slots=list(data.get("missing_slots") or []),
            clarification=str(data.get("clarification") or ""),
            suggested_actions=list(data.get("suggested_actions") or []),
            is_off_topic=bool(data.get("is_off_topic", False)),
            redirect_suggestion=str(data.get("redirect_suggestion") or ""),
            bulk=bool(data.get("bulk", False)),
        )

    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning("gemini_parse_failed: %s — raw: %s", exc, raw_text[:200])
        return LLMResult(success=False, error=f"Parse error: {exc}")
