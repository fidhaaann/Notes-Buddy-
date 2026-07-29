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
    InvalidDialogueTransition,
    ExpiredContext,
    InvalidSelection,
    NoPendingOperation,
    OperationAlreadyConsumed,
    OperationCancelled,
    OperationExpired,
    PendingOperationExists,
    SessionNotFound,
    SlotExpired,
    StaleResultSet,
    VersionConflict,
)
from application.dialogue.memory_repository import (
    InMemoryDialogueSessionRepository,
)
from application.dialogue.models import (
    ClientSessionIdentity,
    CreateFolderParameters,
    FileSelectionBehavior,
    ItemKind,
    OperationRiskLevel,
    OperationTargetSnapshot,
    OperationType,
    PendingOperationStatus,
)
from application.dialogue.service import DialogueSessionService
from bot import formatter, nav, ui
from security import limits
from security import validators

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
_CANCELLATION_RE = re.compile(
    r"^(?:cancel|stop|never\s+mind|nevermind|forget\s+it|no|abort)$",
    re.IGNORECASE,
)
_CONSEQUENTIAL_START_RE = re.compile(
    r"^(?:create|make|new|mkdir|rename|delete|move|copy|share|upload|zip)\b",
    re.IGNORECASE,
)

_TERMINAL_OPERATION_STATUSES = {
    PendingOperationStatus.SUCCEEDED,
    PendingOperationStatus.FAILED,
    PendingOperationStatus.CANCELLED,
    PendingOperationStatus.EXPIRED,
    PendingOperationStatus.CONSUMED,
}


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
        operation_ttl_seconds=limits.PENDING_OPERATION_TTL_SECONDS,
        slot_ttl_seconds=limits.SLOT_REQUEST_TTL_SECONDS,
        confirmation_ttl_seconds=limits.CONFIRMATION_TTL_SECONDS,
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


def is_exact_cancellation(text: str) -> bool:
    """Recognize only a complete, conservative cancellation response."""
    normalized = " ".join(text.strip().split())
    return bool(_CANCELLATION_RE.fullmatch(normalized))


def _safe_folder_name(value: str) -> str | None:
    cleaned = validators.sanitize_text(value)
    if not cleaned:
        return None
    safe = validators.sanitize_filename(cleaned, max_len=200)
    if safe == "unnamed_file" and cleaned != "unnamed_file":
        return None
    return safe


def _authenticated_session(update, context):
    service = get_dialogue_service(context)
    identity = telegram_session_identity_from_update(update)
    if service is None or identity is None:
        return service, identity, None
    session = service.get_or_create_session(identity)
    account_id = compatibility_account_id(identity.principal_id)
    if session.account_id != account_id:
        session = service.set_account(identity, account_id)
    return service, identity, session


async def begin_create_folder_dialogue(
    update,
    context,
    folder_name: str | None,
) -> bool:
    """Plan and, when complete, execute one typed CREATE_FOLDER operation."""
    message = getattr(update, "message", None)
    user = getattr(update, "effective_user", None)
    if message is None or user is None:
        return False
    service, identity, session = _authenticated_session(update, context)
    if service is None or identity is None or session is None:
        await message.reply_text(
            formatter.error(
                "I can't prepare that action right now.",
                "Please try again.",
            )
        )
        return True
    safe_name = _safe_folder_name(folder_name) if folder_name is not None else None
    if folder_name is not None and safe_name is None:
        await message.reply_text(
            formatter.error(
                "Folder name cannot be empty.",
                "Choose a short, valid folder name.",
            )
        )
        return True
    try:
        planned = service.begin_operation(
            identity,
            operation_type=OperationType.CREATE_FOLDER,
            parameters=CreateFolderParameters(
                folder_name=safe_name,
                parent_folder_id=nav.current_folder_id(user.id),
                parent_folder_name_snapshot=nav.current_folder_name(user.id),
            ),
            targets=(
                OperationTargetSnapshot(
                    item_id=nav.current_folder_id(user.id),
                    name_snapshot=nav.current_folder_name(user.id),
                    item_kind=ItemKind.FOLDER,
                ),
            ),
            risk_level=OperationRiskLevel.LOW,
            expected_version=session.state_version,
        )
        operation = planned.pending_operation
        assert operation is not None
        if safe_name is None:
            service.request_slot(
                identity,
                operation_id=operation.operation_id,
                slot_name="folder_name",
                expected_type="string",
                prompt_key="create_folder.folder_name",
                expected_version=planned.state_version,
            )
            await message.reply_text(
                formatter.nlp_clarify("What should I call it?"),
                reply_markup=ui.typed_pending_cancel_keyboard(),
            )
            return True
        await _execute_create_folder_operation(update, context, service, identity)
        return True
    except PendingOperationExists:
        await message.reply_text(
            "You already have an unfinished action. "
            "Complete it or cancel it first."
        )
        return True
    except VersionConflict:
        await message.reply_text(
            "That action changed while I was preparing it. Please try again."
        )
        return True
    except Exception:
        logger.exception("typed_create_folder_begin_failed")
        await message.reply_text(
            formatter.error(
                "Could not prepare folder creation.",
                "Please try again.",
            )
        )
        return True


async def handle_typed_pending_dialogue(update, context) -> bool:
    """Handle expiry, exact cancellation, or a typed slot before legacy state."""
    message = getattr(update, "message", None)
    text = getattr(message, "text", None)
    if message is None or not isinstance(text, str):
        return False
    service = get_dialogue_service(context)
    identity = telegram_session_identity_from_update(update)
    if service is None or identity is None:
        return False
    try:
        session = service.get_session(identity)
    except SessionNotFound:
        return False
    operation = session.pending_operation
    if operation is None or operation.status in _TERMINAL_OPERATION_STATUSES:
        return False
    try:
        refreshed = service.expire_pending(
            identity,
            expected_version=session.state_version,
        )
        if (
            refreshed.pending_operation is not None
            and refreshed.pending_operation.status
            is PendingOperationStatus.EXPIRED
        ):
            await message.reply_text(
                "That action has expired. Please start it again."
            )
            return True
        session = refreshed
        operation = session.pending_operation
        assert operation is not None
        if is_exact_cancellation(text):
            service.cancel_pending(
                identity,
                expected_version=session.state_version,
            )
            await message.reply_text("Cancelled. No folder was created.")
            return True
        slot = session.slot_request
        if (
            operation.operation_type is OperationType.CREATE_FOLDER
            and operation.status is PendingOperationStatus.AWAITING_SLOT
            and slot is not None
        ):
            if _CONSEQUENTIAL_START_RE.match(text.strip()):
                await message.reply_text(
                    "You already have an unfinished action. "
                    "Complete it or cancel it first."
                )
                return True
            safe_name = _safe_folder_name(text)
            if safe_name is None:
                await message.reply_text(
                    formatter.error(
                        "Folder name cannot be empty.",
                        "Choose a short, valid folder name.",
                    )
                )
                return True
            filled = service.fill_slot(
                identity,
                operation_id=operation.operation_id,
                slot_request_id=slot.slot_request_id,
                value=safe_name,
                expected_version=session.state_version,
            )
            await _execute_create_folder_operation(
                update,
                context,
                service,
                identity,
                expected_version=filled.state_version,
            )
            return True
        return False
    except (SlotExpired, OperationExpired):
        await message.reply_text(
            "That action has expired. Please start it again."
        )
        return True
    except (OperationAlreadyConsumed, OperationCancelled):
        await message.reply_text(
            "That action is no longer active. Please start it again."
        )
        return True
    except VersionConflict:
        await message.reply_text(
            "That action changed while I was processing it. Please try again."
        )
        return True
    except Exception:
        logger.exception("typed_pending_dialogue_failed")
        await message.reply_text(
            formatter.error(
                "I couldn't continue that action safely.",
                "Please cancel it and start again.",
            )
        )
        return True


def cancel_typed_pending_work(update, context) -> bool:
    """Cancel typed work for `/cancel`; legacy cleanup remains with the command."""
    service = get_dialogue_service(context)
    identity = telegram_session_identity_from_update(update)
    if service is None or identity is None:
        return False
    try:
        session = service.get_session(identity)
        operation = session.pending_operation
        if operation is None or operation.status in _TERMINAL_OPERATION_STATUSES:
            return False
        service.cancel_pending(
            identity,
            expected_version=session.state_version,
        )
        return True
    except (SessionNotFound, NoPendingOperation):
        return False
    except (OperationExpired, OperationAlreadyConsumed):
        return True


def cancel_typed_pending_callback_work(update, context) -> bool:
    """Cancel only the calling session's active typed operation for a callback."""
    service = get_dialogue_service(context)
    identity = telegram_session_identity_from_update(update)
    if service is None or identity is None:
        return False
    try:
        session = service.get_session(identity)
        operation = session.pending_operation
        if operation is None or operation.status in _TERMINAL_OPERATION_STATUSES:
            return False
        refreshed = service.expire_pending(
            identity,
            expected_version=session.state_version,
        )
        operation = refreshed.pending_operation
        if operation is None or operation.status in _TERMINAL_OPERATION_STATUSES:
            return False
        service.cancel_pending(
            identity,
            expected_version=refreshed.state_version,
        )
        return True
    except (
        SessionNotFound,
        NoPendingOperation,
        OperationExpired,
        OperationAlreadyConsumed,
        OperationCancelled,
        VersionConflict,
    ):
        return False
    except Exception:
        logger.exception("typed_callback_cancellation_failed")
        return False


async def _execute_create_folder_operation(
    update,
    context,
    service: DialogueSessionService,
    identity: ClientSessionIdentity,
    *,
    expected_version: int | None = None,
) -> None:
    """Cross the single-use boundary before invoking the existing Drive call."""
    session = service.get_session(identity)
    operation = session.pending_operation
    if operation is None:
        raise NoPendingOperation("CREATE_FOLDER operation is missing")
    executing = service.consume_operation(
        identity,
        operation_id=operation.operation_id,
        expected_version=(
            session.state_version
            if expected_version is None
            else expected_version
        ),
    )
    executing_operation = executing.pending_operation
    assert executing_operation is not None
    parameters = executing_operation.parameters
    assert parameters.folder_name is not None
    try:
        from drive import drive_service as drive_service

        created = await drive_service.create_folder_async(
            update.effective_user.id,
            parameters.folder_name,
            parent_id=parameters.parent_folder_id,
        )
    except Exception:
        try:
            service.fail_operation(
                identity,
                operation_id=executing_operation.operation_id,
            )
        except Exception:
            logger.exception("typed_create_folder_failure_state_failed")
        logger.exception("typed_create_folder_provider_failed")
        await update.message.reply_text(
            formatter.error(
                "Could not create folder.",
                "Try a different name or start again.",
            )
        )
        return
    service.complete_operation(
        identity,
        operation_id=executing_operation.operation_id,
    )
    await update.message.reply_text(
        formatter.success(
            "Folder Created",
            created.get("name", parameters.folder_name),
            nav.breadcrumb(update.effective_user.id),
        ),
        reply_markup=ui.back_to_menu_keyboard(),
    )


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
