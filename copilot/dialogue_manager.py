"""
copilot/dialogue_manager.py
Dedicated dialogue manager pipeline for NotesBuddy V2.

Pipeline:
Incoming Message
→ Selection Resolver
→ Pending Task Resolver
→ Reference Resolver
→ Dialogue Manager
→ Intent Detection
→ Execution
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from bot import formatter, nav
from copilot import llm as copilot_llm
from copilot import response_gen, slot_filler
from copilot.greeting import detect_greeting
from nlp import context as nlp_context
from nlp import intents as intent_types
from nlp import normalize
from nlp import router as nlp_router


_ACTION_WORDS = {
    "download", "open", "enter", "details", "info", "delete", "remove", "rename",
    "move", "copy", "share", "zip", "upload", "search", "find", "show", "browse",
    "back", "home", "menu", "help",
}

_SELECTION_ONLY_RE = re.compile(
    r"^(?:the\s+)?(?:(\d{1,2})|(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last))"
    r"(?:\s+(one|file|folder))?$",
    re.IGNORECASE,
)

_REFERENCE_ONLY_RE = re.compile(
    r"^(?:that|this|the)\s+(?:one|file|folder)$",
    re.IGNORECASE,
)


@dataclass
class SelectionResult:
    item: nav.IndexedItem
    action: intent_types.IntentType


def _has_action_words(text: str) -> bool:
    tokens = text.split()
    return any(t in _ACTION_WORDS for t in tokens)


def _resolve_last_item(uid: int, user_data: dict, want_folder: Optional[bool] = None) -> Optional[nav.IndexedItem]:
    state = nlp_context.get_state(user_data)
    last_id = state.get("last_item_id")
    if not last_id:
        return None
    if want_folder is not None and state.get("last_item_is_folder") != want_folder:
        return None
    view = nav.get_active_view(uid)
    if view:
        for item in view.index_map.values():
            if item.id == last_id:
                return item
    return None


def _resolve_selection(uid: int, user_data: dict, text: str) -> Optional[SelectionResult]:
    """Resolve direct selections like '1', 'first', 'that file'."""
    normalized = normalize.normalize_text(text)
    if not normalized or _has_action_words(normalized):
        return None

    if nav.is_view_expired(uid):
        return None

    match = _SELECTION_ONLY_RE.match(normalized)
    if match:
        ref = match.group(1) or match.group(2) or ""
        item = nav.resolve_smart(uid, ref)
        if item:
            action = intent_types.IntentType.OPEN_FOLDER if item.is_folder else intent_types.IntentType.DOWNLOAD
            return SelectionResult(item=item, action=action)
        return None

    if _REFERENCE_ONLY_RE.match(normalized):
        want_folder = "folder" in normalized
        want_file = "file" in normalized
        item = _resolve_last_item(uid, user_data, want_folder=True if want_folder else None)
        if not item and want_file:
            item = _resolve_last_item(uid, user_data, want_folder=False)
        if not item:
            item = _resolve_last_item(uid, user_data)
        if item:
            action = intent_types.IntentType.OPEN_FOLDER if item.is_folder else intent_types.IntentType.DOWNLOAD
            return SelectionResult(item=item, action=action)
    return None


async def _execute_selection(update, context, selection: SelectionResult) -> bool:
    intent = intent_types.Intent(
        intent=selection.action,
        confidence=0.95,
        raw_text=selection.item.full_index or selection.item.name,
        index=selection.item.full_index or None,
        is_fresh_query=False,
        source="selection",
    )
    return await nlp_router.execute_intent(update, context, intent)


async def handle_message(update, context, text: str) -> bool:
    """Main dialogue manager entry point. Returns True if handled."""
    if not update.message:
        return False
    assert context.user_data is not None
    uid = update.effective_user.id

    # ── Selection resolver ────────────────────────────────────────────────
    selection = _resolve_selection(uid, context.user_data, text)
    if selection:
        return await _execute_selection(update, context, selection)

    # ── Pending task resolver (slot fill) ─────────────────────────────────
    if slot_filler.has_pending(context.user_data):
        pending = slot_filler.get_pending_intent(context.user_data)
        if pending:
            entities = slot_filler.fill_pending_slot(pending, text)
            slot_filler.clear_pending(context.user_data)
            try:
                intent_type = intent_types.IntentType(pending["intent"])
            except ValueError:
                intent_type = intent_types.IntentType.UNKNOWN
            filled_intent = intent_types.Intent(
                intent=intent_type,
                confidence=0.95,
                raw_text=text,
                query=entities.get("query"),
                target_name=entities.get("folder_name") or entities.get("new_name") or entities.get("target_folder"),
                email=entities.get("email"),
                otp=entities.get("otp"),
                index=entities.get("index_ref"),
                source="llm",
                is_fresh_query=(nlp_context.classify_query(text) == nlp_context.QueryType.FRESH_QUERY),
            )
            return await nlp_router.execute_intent(update, context, filled_intent)

    # ── Pending action resolver (confirm/rename/move/etc.) ─────────────────
    if await nlp_router.handle_pending_action(update, context):
        return True

    # ── Reference resolver (pronoun-only references) ──────────────────────
    if _REFERENCE_ONLY_RE.match(normalize.normalize_text(text)):
        item = _resolve_last_item(uid, context.user_data)
        if item:
            action = intent_types.IntentType.OPEN_FOLDER if item.is_folder else intent_types.IntentType.DOWNLOAD
            return await _execute_selection(update, context, SelectionResult(item=item, action=action))

    # ── Dialogue Manager → LLM intent detection ───────────────────────────
    if await _handle_llm_intent(update, context, text):
        return True

    # ── Keyword NLP fallback ───────────────────────────────────────────────
    if await nlp_router.handle_nlp_message(update, context):
        return True

    return False


async def _handle_llm_intent(update, context, text: str) -> bool:
    """LLM-driven intent extraction with slot fill + execution."""
    assert context.user_data is not None
    uid = update.effective_user.id

    # Fast greeting check
    greeting = detect_greeting(text)
    if greeting.matched:
        nlp_context.add_turn(context.user_data, "user", text, "greeting")
        await update.message.reply_text(greeting.response)
        nlp_context.add_turn(context.user_data, "assistant", greeting.response, "greeting")
        return True

    # LLM availability
    if not copilot_llm.is_available():
        return False

    # Build context for LLM
    history = nlp_context.get_history(context.user_data, limit=10)
    context_parts = []
    current_path = nav.breadcrumb(uid)
    if current_path:
        context_parts.append(f"Current folder: {current_path}")
    active_results, active_query = nlp_context.get_active_results(context.user_data)
    if active_results:
        result_names = [r.get("name", "file") for r in active_results[:5]]
        context_parts.append(f"Last shown files: {', '.join(result_names)}")
        if active_query:
            context_parts.append(f"Last search query: {active_query}")
    user_context = "; ".join(context_parts)

    result = await copilot_llm.extract_intent(
        user_message=text,
        conversation_history=history,
        user_context=user_context,
    )
    if not result.success:
        return False

    # Record conversation turn
    nlp_context.add_turn(context.user_data, "user", text, result.intent)

    # Chitchat/off-topic handling
    if result.is_chitchat and result.chitchat_response:
        resp = result.chitchat_response
        await update.message.reply_text(resp)
        nlp_context.add_turn(context.user_data, "assistant", resp, "greeting")
        return True

    if result.is_off_topic:
        resp = formatter.off_topic_response("", result.redirect_suggestion)
        await update.message.reply_text(resp)
        nlp_context.add_turn(context.user_data, "assistant", resp, "off_topic")
        return True

    # Map to IntentType
    try:
        intent_type = intent_types.IntentType(result.intent)
    except ValueError:
        return False

    # Slot filling
    slot_result = slot_filler.check_slots(result.intent, result.entities)
    if not slot_result.complete:
        prompt = result.clarification or slot_result.prompt or "Could you provide more details?"
        slot_filler.set_pending(context.user_data, {
            "intent": result.intent,
            "entities": result.entities,
            "awaiting_slot": slot_result.missing_slot,
            "entity_key": slot_result.pending_state.get("entity_key", "") if slot_result.pending_state else "",
        })
        resp = formatter.copilot_clarify(prompt)
        await update.message.reply_text(resp)
        nlp_context.add_turn(context.user_data, "assistant", resp, "slot_fill")
        return True

    # Build intent and execute
    entities = result.entities
    is_fresh = nlp_context.classify_query(text) == nlp_context.QueryType.FRESH_QUERY
    query = entities.get("query") or (normalize.normalize_text(text) if intent_type == intent_types.IntentType.SEARCH else None)
    intent = intent_types.Intent(
        intent=intent_type,
        confidence=result.confidence,
        raw_text=text,
        query=query,
        index=entities.get("index_ref"),
        target_name=entities.get("folder_name") or entities.get("new_name") or entities.get("target_folder"),
        email=entities.get("email"),
        otp=entities.get("otp"),
        bulk=result.bulk,
        file_type_hint=entities.get("file_type"),
        suggested_actions=result.suggested_actions or response_gen.action_suggestions_for_intent(result.intent),
        source="llm",
        is_fresh_query=is_fresh,
        search_scope="entire_drive",
    )

    return await nlp_router.execute_intent(update, context, intent)
