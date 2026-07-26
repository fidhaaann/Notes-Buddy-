"""Telegram-facing integration for typed dialogue selection state."""

from __future__ import annotations

import logging
import re
import time

from application.dialogue.compat import (
    active_view_to_result_items,
    folder_stack_to_locations,
    result_item_to_indexed_item,
    telegram_identity,
)
from application.dialogue.errors import (
    ExpiredContext,
    InvalidSelection,
    SessionNotFound,
    StaleResultSet,
    VersionConflict,
)
from application.dialogue.memory_repository import (
    InMemoryDialogueSessionRepository,
)
from application.dialogue.models import (
    ClientSessionIdentity,
    FileSelectionBehavior,
    ItemKind,
)
from application.dialogue.service import DialogueSessionService
from bot import formatter, nav, ui
from security import limits

logger = logging.getLogger(__name__)

DIALOGUE_SERVICE_KEY = "dialogue_service"
DEFAULT_SESSION_TTL_SECONDS = 24 * 60 * 60

_PURE_SELECTION_RE = re.compile(
    r"^(?:"
    r"\d+"
    r"|(?:the\s+)?"
    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last)"
    r"(?:\s+(?:one|item|file|folder))?"
    r")$",
    re.IGNORECASE,
)


def initialize_dialogue_service(
    application,
    *,
    clock=time.monotonic,
    session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
    result_ttl_seconds: float = limits.NLP_CONTEXT_TTL_SECONDS,
) -> DialogueSessionService:
    """Create or return the one dialogue service stored on a PTB application."""
    existing = application.bot_data.get(DIALOGUE_SERVICE_KEY)
    if isinstance(existing, DialogueSessionService):
        return existing
    repository = InMemoryDialogueSessionRepository(
        max_sessions=nav.MAX_USERS,
        session_ttl_seconds=session_ttl_seconds,
        clock=clock,
    )
    service = DialogueSessionService(
        repository,
        clock=clock,
        session_ttl_seconds=session_ttl_seconds,
        result_ttl_seconds=result_ttl_seconds,
    )
    application.bot_data[DIALOGUE_SERVICE_KEY] = service
    return service


def get_dialogue_service(context) -> DialogueSessionService | None:
    """Read the injected process-scoped service without creating a singleton."""
    bot_data = getattr(context, "bot_data", None)
    if bot_data is None:
        application = getattr(context, "application", None)
        bot_data = getattr(application, "bot_data", None)
    if not isinstance(bot_data, dict):
        return None
    service = bot_data.get(DIALOGUE_SERVICE_KEY)
    return service if isinstance(service, DialogueSessionService) else None


def telegram_session_identity_from_update(
    update,
) -> ClientSessionIdentity | None:
    """Extract scalar Telegram identity values before entering the core."""
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    if user is None or chat is None:
        return None
    user_id = getattr(user, "id", None)
    chat_id = getattr(chat, "id", None)
    if user_id is None or chat_id is None:
        return None
    message = (
        getattr(update, "effective_message", None)
        or getattr(update, "message", None)
    )
    thread_id = getattr(message, "message_thread_id", None)
    return telegram_identity(user_id, chat_id, thread_id)


def compatibility_account_id(principal_id: str) -> str:
    """Stable local account key; it does not claim Google account metadata."""
    return f"telegram-principal:{principal_id}"


def publish_active_view_to_dialogue(
    update,
    context,
    *,
    authenticated: bool,
) -> tuple[str, int] | None:
    """Mirror the current legacy view; rollout failures remain non-fatal."""
    if not authenticated:
        return None
    service: DialogueSessionService | None = None
    identity: ClientSessionIdentity | None = None
    try:
        service = get_dialogue_service(context)
        identity = telegram_session_identity_from_update(update)
        user = getattr(update, "effective_user", None)
        if service is None or identity is None or user is None:
            logger.warning("dialogue_view_mirror_dependency_missing")
            return None
        uid = user.id
        view = nav.get_active_view(uid)
        if view is None:
            return None

        session = service.get_or_create_session(identity)
        account_id = compatibility_account_id(identity.principal_id)
        if session.account_id != account_id:
            session = service.set_account(identity, account_id)

        path = folder_stack_to_locations(nav.get_folder_stack(uid))
        session = service.synchronize_folder_path(identity, path)
        items = active_view_to_result_items(view, account_id=account_id)
        session = service.replace_active_results(
            identity,
            source=view.view_type,
            items=items,
            query=view.metadata.get("keyword"),
            scope=view.metadata.get("scope"),
            folder_id=view.metadata.get("folder_id"),
            originating_request_id=str(getattr(update, "update_id", "")) or None,
        )
        active = session.active_result_set
        if active is None:
            return None
        return active.result_set_id, active.version
    except Exception:
        if service is not None and identity is not None:
            try:
                service.clear_active_results(identity)
            except Exception:
                logger.error("dialogue_view_mirror_clear_failed")
        logger.warning(
            "dialogue_view_mirror_failed",
            exc_info=True,
        )
        return None


def parse_pure_selection(text: str) -> str | None:
    """Return an exact selection token, never an action/query containing digits."""
    normalized = " ".join(text.strip().split())
    return normalized if _PURE_SELECTION_RE.fullmatch(normalized) else None


async def handle_active_result_selection(
    update,
    context,
    *,
    authenticated: bool,
) -> bool:
    """Resolve and execute an exact selection before Copilot or keyword NLP."""
    message = getattr(update, "message", None)
    text = getattr(message, "text", None)
    if message is None or not isinstance(text, str):
        return False
    selection = parse_pure_selection(text)
    if selection is None:
        return False
    if not authenticated:
        await message.reply_text(formatter.login_required())
        return True

    service = get_dialogue_service(context)
    identity = telegram_session_identity_from_update(update)
    if service is None:
        logger.error("dialogue_service_missing")
        await message.reply_text(
            formatter.error(
                "I can't use that list right now.",
                "Please refresh the list and try again.",
            )
        )
        return True
    if identity is None:
        await message.reply_text(
            formatter.error(
                "I don't have an active list to choose from.",
                "Ask me to browse or search first.",
            )
        )
        return True

    try:
        session = service.get_session(identity)
        active = session.active_result_set
        if active is None:
            raise SessionNotFound("session has no active result set")
        item = service.resolve_selection(
            identity,
            selection,
            result_set_id=active.result_set_id,
            result_set_version=active.version,
        )
        selected_session = service.select_item(
            identity,
            selection,
            result_set_id=active.result_set_id,
            result_set_version=active.version,
        )
    except ExpiredContext:
        await message.reply_text(
            "That list has expired. Please refresh or search again."
        )
        return True
    except StaleResultSet:
        await message.reply_text(
            "That list has changed. Please choose from the latest displayed list."
        )
        return True
    except InvalidSelection:
        await message.reply_text(
            "That number is not in the current list. "
            "Please choose one of the displayed items."
        )
        return True
    except SessionNotFound:
        await message.reply_text(
            "I don't have an active list to choose from. "
            "Ask me to browse or search first."
        )
        return True
    except VersionConflict:
        await message.reply_text(
            "That list changed while I was processing your choice. "
            "Please choose again."
        )
        return True
    except Exception:
        logger.exception("dialogue_selection_resolution_failed")
        await message.reply_text(
            formatter.error(
                "I couldn't use that selection safely.",
                "Please refresh the list and try again.",
            )
        )
        return True

    legacy_item = result_item_to_indexed_item(item, path=nav.breadcrumb(update.effective_user.id))
    try:
        from nlp import router as nlp_router

        is_folder = item.item_kind is ItemKind.FOLDER or (
            item.item_kind is ItemKind.SHORTCUT
            and item.shortcut_target_kind is ItemKind.FOLDER
        )
        if is_folder:
            await nlp_router.open_resolved_folder(
                update,
                context,
                legacy_item,
            )
            return True

        behavior = selected_session.file_selection_behavior
        if behavior is FileSelectionBehavior.DOWNLOAD:
            await nlp_router.download_resolved_item(update, context, legacy_item)
        elif behavior is FileSelectionBehavior.ASK:
            await message.reply_text(
                f"Choose an action for {item.name_snapshot}.",
                reply_markup=ui.selection_file_actions_keyboard(item.item_id),
            )
        else:
            await nlp_router.show_resolved_item_details(
                update,
                context,
                legacy_item,
            )
        return True
    except Exception:
        logger.exception("dialogue_selection_action_failed")
        await message.reply_text(
            formatter.error(
                "I found that item, but couldn't complete the action.",
                "Please try again or refresh the list.",
            )
        )
        return True
