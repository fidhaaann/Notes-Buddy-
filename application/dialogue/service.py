"""Focused state transitions for typed dialogue sessions."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Iterable
from dataclasses import replace

from application.dialogue.errors import (
    ConfirmationAlreadyConsumed,
    ConfirmationExpired,
    ConfirmationMismatch,
    ExpiredContext,
    InvalidDialogueTransition,
    InvalidDialogueValue,
    InvalidSelection,
    NavigationLoop,
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
from application.dialogue.models import (
    ActiveResultSet,
    ClientSessionIdentity,
    ConfirmationRequest,
    ConfirmationStatus,
    CreateFolderParameters,
    DialogueSession,
    DialogueState,
    ExperienceMode,
    FileSelectionBehavior,
    FolderLocation,
    OperationRiskLevel,
    OperationTargetSnapshot,
    OperationType,
    PendingOperation,
    PendingOperationStatus,
    ResultItem,
    SelectedItemReference,
    SlotRequest,
)
from application.dialogue.repository import DialogueSessionRepository


class DialogueSessionService:
    """Owns immutable dialogue transitions; performs no external operations."""

    def __init__(
        self,
        repository: DialogueSessionRepository,
        *,
        clock: Callable[[], float],
        session_ttl_seconds: float = 24 * 60 * 60,
        result_ttl_seconds: float = 15 * 60,
        operation_ttl_seconds: float = 15 * 60,
        slot_ttl_seconds: float = 10 * 60,
        confirmation_ttl_seconds: float = 5 * 60,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._session_ttl_seconds = self._positive_duration(
            session_ttl_seconds,
            "session_ttl_seconds",
        )
        self._result_ttl_seconds = self._positive_duration(
            result_ttl_seconds,
            "result_ttl_seconds",
        )
        self._operation_ttl_seconds = self._positive_duration(
            operation_ttl_seconds,
            "operation_ttl_seconds",
        )
        self._slot_ttl_seconds = self._positive_duration(
            slot_ttl_seconds,
            "slot_ttl_seconds",
        )
        self._confirmation_ttl_seconds = self._positive_duration(
            confirmation_ttl_seconds,
            "confirmation_ttl_seconds",
        )
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def get_or_create_session(
        self,
        session_key: ClientSessionIdentity,
    ) -> DialogueSession:
        return self._repository.get_or_create(session_key, self._now())

    def get_session(
        self,
        session_key: ClientSessionIdentity,
    ) -> DialogueSession:
        session = self._repository.get(session_key)
        if session is None:
            raise SessionNotFound("dialogue session does not exist")
        if session.is_expired(self._now()):
            self._repository.delete(session_key)
            raise ExpiredContext("dialogue session has expired")
        return session

    def set_account(
        self,
        session_key: ClientSessionIdentity,
        account_id: str | None,
    ) -> DialogueSession:
        session = self.get_session(session_key)
        return self._mutate(session, account_id=account_id)

    def set_experience_mode(
        self,
        session_key: ClientSessionIdentity,
        mode: ExperienceMode,
    ) -> DialogueSession:
        if not isinstance(mode, ExperienceMode):
            raise InvalidDialogueValue("mode must be an ExperienceMode")
        session = self.get_session(session_key)
        return self._mutate(session, experience_mode=mode)

    def set_file_selection_behavior(
        self,
        session_key: ClientSessionIdentity,
        behavior: FileSelectionBehavior,
    ) -> DialogueSession:
        if not isinstance(behavior, FileSelectionBehavior):
            raise InvalidDialogueValue(
                "behavior must be a FileSelectionBehavior"
            )
        session = self.get_session(session_key)
        return self._mutate(session, file_selection_behavior=behavior)

    def push_folder(
        self,
        session_key: ClientSessionIdentity,
        folder: FolderLocation,
    ) -> DialogueSession:
        if not isinstance(folder, FolderLocation):
            raise InvalidDialogueValue("folder must be a FolderLocation")
        session = self.get_session(session_key)
        path_ids = {
            location.item_id
            for location in (*session.folder_history, session.current_folder)
        }
        if folder.item_id in path_ids:
            raise NavigationLoop(
                f"folder {folder.item_id!r} is already in the active path"
            )
        return self._mutate(
            session,
            current_folder=folder,
            folder_history=(*session.folder_history, session.current_folder),
            active_result_set=None,
        )

    def pop_folder(
        self,
        session_key: ClientSessionIdentity,
    ) -> DialogueSession:
        session = self.get_session(session_key)
        if not session.folder_history:
            return session
        return self._mutate(
            session,
            current_folder=session.folder_history[-1],
            folder_history=session.folder_history[:-1],
            active_result_set=None,
        )

    def go_home(
        self,
        session_key: ClientSessionIdentity,
    ) -> DialogueSession:
        session = self.get_session(session_key)
        return self._mutate(
            session,
            current_folder=FolderLocation.home(),
            folder_history=(),
            active_result_set=None,
        )

    def synchronize_folder_path(
        self,
        session_key: ClientSessionIdentity,
        locations: Iterable[FolderLocation],
    ) -> DialogueSession:
        """Replace the typed path from a trusted compatibility snapshot."""
        path = tuple(locations)
        if not path:
            raise InvalidDialogueValue("folder path must contain at least Home")
        if not all(isinstance(location, FolderLocation) for location in path):
            raise InvalidDialogueValue(
                "folder path must contain FolderLocation values"
            )
        item_ids = [location.item_id for location in path]
        if len(item_ids) != len(set(item_ids)):
            raise NavigationLoop("folder path contains a duplicate folder ID")
        session = self.get_session(session_key)
        desired_current = path[-1]
        desired_history = path[:-1]
        if (
            session.current_folder == desired_current
            and session.folder_history == desired_history
        ):
            return session
        return self._mutate(
            session,
            current_folder=desired_current,
            folder_history=desired_history,
            active_result_set=None,
        )

    def replace_active_results(
        self,
        session_key: ClientSessionIdentity,
        *,
        source: str,
        items: Iterable[ResultItem],
        query: str | None = None,
        scope: str | None = None,
        folder_id: str | None = None,
        originating_request_id: str | None = None,
    ) -> DialogueSession:
        session = self.get_session(session_key)
        now = self._now()
        previous_version = (
            session.active_result_set.version
            if session.active_result_set is not None
            else 0
        )
        result_set = ActiveResultSet(
            result_set_id=self._id_factory(),
            version=previous_version + 1,
            source=source,
            items=tuple(items),
            created_at=now,
            expires_at=now + self._result_ttl_seconds,
            query=query,
            scope=scope,
            folder_id=folder_id,
            originating_request_id=originating_request_id,
        )
        return self._mutate_at(
            session,
            now,
            active_result_set=result_set,
        )

    def clear_active_results(
        self,
        session_key: ClientSessionIdentity,
    ) -> DialogueSession:
        session = self.get_session(session_key)
        return self._mutate(session, active_result_set=None)

    def resolve_selection(
        self,
        session_key: ClientSessionIdentity,
        selection: int | str,
        *,
        result_set_id: str | None = None,
        result_set_version: int | None = None,
    ) -> ResultItem:
        session = self.get_session(session_key)
        active = session.active_result_set
        if active is None:
            raise InvalidSelection("dialogue session has no active result set")
        self._validate_result_reference(
            active,
            result_set_id=result_set_id,
            result_set_version=result_set_version,
        )
        return active.resolve(selection, self._now())

    def select_item(
        self,
        session_key: ClientSessionIdentity,
        selection: int | str,
        *,
        result_set_id: str | None = None,
        result_set_version: int | None = None,
    ) -> DialogueSession:
        session = self.get_session(session_key)
        active = session.active_result_set
        if active is None:
            raise InvalidSelection("dialogue session has no active result set")
        self._validate_result_reference(
            active,
            result_set_id=result_set_id,
            result_set_version=result_set_version,
        )
        now = self._now()
        item = active.resolve(selection, now)
        selected = SelectedItemReference(
            result_set_id=active.result_set_id,
            result_set_version=active.version,
            ordinal=item.ordinal,
            item_id=item.item_id,
            selected_at=now,
        )
        return self._mutate_at(session, now, last_selected_item=selected)

    def begin_operation(
        self,
        session_key: ClientSessionIdentity,
        *,
        operation_type: OperationType,
        parameters: CreateFolderParameters,
        targets: Iterable[OperationTargetSnapshot] = (),
        risk_level: OperationRiskLevel = OperationRiskLevel.LOW,
        source_result_set_id: str | None = None,
        source_result_set_version: int | None = None,
        expected_version: int | None = None,
    ) -> DialogueSession:
        if operation_type is not OperationType.CREATE_FOLDER:
            raise InvalidDialogueTransition(
                "only CREATE_FOLDER workflow logic is implemented"
            )
        if not isinstance(parameters, CreateFolderParameters):
            raise InvalidDialogueValue(
                "parameters must be CreateFolderParameters"
            )
        session = self._versioned_session(session_key, expected_version)
        current = session.pending_operation
        now = self._now()
        if current is not None and (
            current.is_expired(now)
            or (
                session.slot_request is not None
                and session.slot_request.is_expired(now)
            )
            or (
                session.confirmation is not None
                and session.confirmation.status is ConfirmationStatus.PENDING
                and session.confirmation.is_expired(now)
            )
        ):
            session = self._expire_operation(session, now)
            current = session.pending_operation
        if current is not None and current.status not in {
            PendingOperationStatus.SUCCEEDED,
            PendingOperationStatus.FAILED,
            PendingOperationStatus.CANCELLED,
            PendingOperationStatus.EXPIRED,
            PendingOperationStatus.CONSUMED,
        }:
            raise PendingOperationExists("an unfinished operation already exists")
        if session.account_id is None:
            raise InvalidDialogueTransition(
                "an authenticated account is required for an operation"
            )
        operation_id = self._id_factory()
        status = (
            PendingOperationStatus.DRAFT
            if parameters.folder_name is None
            else PendingOperationStatus.READY
        )
        operation = PendingOperation(
            operation_id=operation_id,
            operation_type=operation_type,
            principal_id=session.principal_id,
            account_id=session.account_id,
            session_id=session.session_id,
            source_result_set_id=source_result_set_id,
            source_result_set_version=source_result_set_version,
            targets=tuple(targets),
            parameters=parameters,
            risk_level=risk_level,
            status=status,
            idempotency_key=f"{session.session_id}:{operation_id}",
            created_at=now,
            expires_at=now + self._operation_ttl_seconds,
        )
        return self._mutate_at(
            session,
            now,
            pending_operation=operation,
            slot_request=None,
            confirmation=None,
            state=DialogueState.READY,
        )

    def request_slot(
        self,
        session_key: ClientSessionIdentity,
        *,
        operation_id: str,
        slot_name: str,
        expected_type: str,
        prompt_key: str,
        expected_version: int | None = None,
    ) -> DialogueSession:
        session = self._versioned_session(session_key, expected_version)
        operation = self._require_operation(session, operation_id)
        self._ensure_operation_live(session, operation)
        if operation.status not in {
            PendingOperationStatus.DRAFT,
            PendingOperationStatus.AWAITING_SLOT,
        }:
            raise InvalidDialogueTransition(
                "operation cannot request a slot in its current state"
            )
        now = self._now()
        request = SlotRequest(
            slot_request_id=self._id_factory(),
            operation_id=operation.operation_id,
            slot_name=slot_name,
            expected_type=expected_type,
            prompt_key=prompt_key,
            attempts=0,
            created_at=now,
            expires_at=min(
                operation.expires_at,
                now + self._slot_ttl_seconds,
            ),
        )
        return self._mutate_at(
            session,
            now,
            pending_operation=replace(
                operation,
                status=PendingOperationStatus.AWAITING_SLOT,
            ),
            slot_request=request,
            confirmation=None,
            state=DialogueState.AWAITING_SLOT,
        )

    def fill_slot(
        self,
        session_key: ClientSessionIdentity,
        *,
        operation_id: str,
        slot_request_id: str,
        value: str,
        expected_version: int | None = None,
    ) -> DialogueSession:
        session = self._versioned_session(session_key, expected_version)
        operation = self._require_operation(session, operation_id)
        self._ensure_operation_live(session, operation)
        request = session.slot_request
        if request is None or request.operation_id != operation.operation_id:
            raise InvalidDialogueTransition("operation has no matching slot")
        if request.slot_request_id != slot_request_id:
            raise InvalidDialogueTransition("slot request has been replaced")
        now = self._now()
        if request.is_expired(now):
            self._expire_operation(session, now)
            raise SlotExpired("slot request has expired")
        if (
            operation.status is not PendingOperationStatus.AWAITING_SLOT
            or request.slot_name != "folder_name"
            or request.expected_type != "string"
        ):
            raise InvalidDialogueTransition("slot cannot be filled")
        folder_name = value.strip() if isinstance(value, str) else value
        parameters = replace(
            operation.parameters,
            folder_name=folder_name,
        )
        return self._mutate_at(
            session,
            now,
            pending_operation=replace(
                operation,
                parameters=parameters,
                status=PendingOperationStatus.READY,
            ),
            slot_request=None,
            confirmation=None,
            state=DialogueState.READY,
        )

    def create_confirmation(
        self,
        session_key: ClientSessionIdentity,
        *,
        operation_id: str,
        operation_summary: str,
        consequence: str,
        reversible: bool,
        expected_version: int | None = None,
    ) -> DialogueSession:
        session = self._versioned_session(session_key, expected_version)
        operation = self._require_operation(session, operation_id)
        self._ensure_operation_live(session, operation)
        if operation.status is not PendingOperationStatus.READY:
            raise InvalidDialogueTransition(
                "only a ready operation can request confirmation"
            )
        now = self._now()
        confirmation = ConfirmationRequest(
            confirmation_id=self._id_factory(),
            operation_id=operation.operation_id,
            principal_id=session.principal_id,
            session_id=session.session_id,
            operation_summary=operation_summary,
            target_snapshots=operation.targets,
            consequence=consequence,
            reversible=reversible,
            created_at=now,
            expires_at=min(
                operation.expires_at,
                now + self._confirmation_ttl_seconds,
            ),
        )
        return self._mutate_at(
            session,
            now,
            pending_operation=replace(
                operation,
                status=PendingOperationStatus.AWAITING_CONFIRMATION,
            ),
            confirmation=confirmation,
            state=DialogueState.AWAITING_CONFIRMATION,
        )

    def confirm_operation(
        self,
        session_key: ClientSessionIdentity,
        *,
        operation_id: str,
        confirmation_id: str,
        expected_version: int | None = None,
    ) -> DialogueSession:
        session = self._versioned_session(session_key, expected_version)
        operation = self._require_operation(session, operation_id)
        self._ensure_operation_live(session, operation)
        confirmation = self._require_confirmation(
            session,
            operation_id,
            confirmation_id,
        )
        now = self._now()
        if confirmation.is_expired(now):
            self._expire_operation(session, now)
            raise ConfirmationExpired("confirmation has expired")
        if confirmation.status is not ConfirmationStatus.PENDING:
            raise ConfirmationAlreadyConsumed(
                "confirmation has already been resolved"
            )
        if operation.status is not PendingOperationStatus.AWAITING_CONFIRMATION:
            raise InvalidDialogueTransition(
                "operation is not awaiting confirmation"
            )
        return self._mutate_at(
            session,
            now,
            pending_operation=replace(
                operation,
                status=PendingOperationStatus.READY,
            ),
            confirmation=replace(
                confirmation,
                status=ConfirmationStatus.CONFIRMED,
            ),
            state=DialogueState.READY,
        )

    def deny_operation(
        self,
        session_key: ClientSessionIdentity,
        *,
        operation_id: str,
        confirmation_id: str,
        expected_version: int | None = None,
    ) -> DialogueSession:
        session = self._versioned_session(session_key, expected_version)
        operation = self._require_operation(session, operation_id)
        confirmation = self._require_confirmation(
            session,
            operation_id,
            confirmation_id,
        )
        if confirmation.status is not ConfirmationStatus.PENDING:
            raise ConfirmationAlreadyConsumed(
                "confirmation has already been resolved"
            )
        now = self._now()
        if confirmation.is_expired(now):
            self._expire_operation(session, now)
            raise ConfirmationExpired("confirmation has expired")
        return self._mutate_at(
            session,
            now,
            pending_operation=replace(
                operation,
                status=PendingOperationStatus.CANCELLED,
            ),
            slot_request=None,
            confirmation=replace(
                confirmation,
                status=ConfirmationStatus.DENIED,
            ),
            state=DialogueState.READY,
        )

    def cancel_pending(
        self,
        session_key: ClientSessionIdentity,
        *,
        expected_version: int | None = None,
    ) -> DialogueSession:
        session = self._versioned_session(session_key, expected_version)
        operation = session.pending_operation
        if operation is None:
            raise NoPendingOperation("dialogue session has no pending operation")
        self._ensure_operation_live(session, operation)
        if operation.status in {
            PendingOperationStatus.EXECUTING,
            PendingOperationStatus.SUCCEEDED,
            PendingOperationStatus.CONSUMED,
        }:
            raise OperationAlreadyConsumed(
                "operation can no longer be cancelled"
            )
        confirmation = session.confirmation
        if (
            confirmation is not None
            and confirmation.status is ConfirmationStatus.PENDING
        ):
            confirmation = replace(
                confirmation,
                status=ConfirmationStatus.DENIED,
            )
        return self._mutate(
            session,
            pending_operation=replace(
                operation,
                status=PendingOperationStatus.CANCELLED,
            ),
            slot_request=None,
            confirmation=confirmation,
            state=DialogueState.READY,
        )

    def expire_pending(
        self,
        session_key: ClientSessionIdentity,
        *,
        expected_version: int | None = None,
    ) -> DialogueSession:
        session = self._versioned_session(session_key, expected_version)
        operation = session.pending_operation
        if operation is None:
            return session
        now = self._now()
        slot_expired = (
            session.slot_request is not None
            and session.slot_request.is_expired(now)
        )
        confirmation_expired = (
            session.confirmation is not None
            and session.confirmation.status is ConfirmationStatus.PENDING
            and session.confirmation.is_expired(now)
        )
        if operation.is_expired(now) or slot_expired or confirmation_expired:
            return self._expire_operation(session, now)
        return session

    def consume_operation(
        self,
        session_key: ClientSessionIdentity,
        *,
        operation_id: str,
        expected_version: int | None = None,
    ) -> DialogueSession:
        session = self._versioned_session(session_key, expected_version)
        if operation_id in session.consumed_operation_ids:
            raise OperationAlreadyConsumed("operation has already been consumed")
        operation = self._require_operation(session, operation_id)
        self._ensure_operation_live(session, operation)
        if operation.status is not PendingOperationStatus.READY:
            if operation.status in {
                PendingOperationStatus.EXECUTING,
                PendingOperationStatus.SUCCEEDED,
                PendingOperationStatus.CONSUMED,
            }:
                raise OperationAlreadyConsumed(
                    "operation has already crossed execution"
                )
            raise InvalidDialogueTransition("operation is not ready")
        confirmation = session.confirmation
        if confirmation is not None:
            if confirmation.operation_id != operation.operation_id:
                raise ConfirmationMismatch(
                    "confirmation belongs to another operation"
                )
            if confirmation.status is not ConfirmationStatus.CONFIRMED:
                raise InvalidDialogueTransition(
                    "operation confirmation is not complete"
                )
            confirmation = replace(
                confirmation,
                status=ConfirmationStatus.CONSUMED,
            )
        return self._mutate(
            session,
            pending_operation=replace(
                operation,
                status=PendingOperationStatus.EXECUTING,
            ),
            confirmation=confirmation,
            state=DialogueState.READY,
        )

    def complete_operation(
        self,
        session_key: ClientSessionIdentity,
        *,
        operation_id: str,
        expected_version: int | None = None,
    ) -> DialogueSession:
        session = self._versioned_session(session_key, expected_version)
        if operation_id in session.consumed_operation_ids:
            raise OperationAlreadyConsumed("operation has already been completed")
        operation = self._require_operation(session, operation_id)
        if operation.status is not PendingOperationStatus.EXECUTING:
            raise InvalidDialogueTransition("operation is not executing")
        consumed = (*session.consumed_operation_ids, operation.operation_id)[-32:]
        return self._mutate(
            session,
            pending_operation=None,
            slot_request=None,
            confirmation=None,
            consumed_operation_ids=consumed,
            state=DialogueState.READY,
        )

    def fail_operation(
        self,
        session_key: ClientSessionIdentity,
        *,
        operation_id: str,
        expected_version: int | None = None,
    ) -> DialogueSession:
        session = self._versioned_session(session_key, expected_version)
        operation = self._require_operation(session, operation_id)
        if operation.status is not PendingOperationStatus.EXECUTING:
            raise InvalidDialogueTransition("operation is not executing")
        return self._mutate(
            session,
            pending_operation=replace(
                operation,
                status=PendingOperationStatus.FAILED,
            ),
            slot_request=None,
            confirmation=None,
            state=DialogueState.READY,
        )

    def expire_session(
        self,
        session_key: ClientSessionIdentity,
    ) -> DialogueSession:
        session = self.get_session(session_key)
        now = self._now()
        expired = replace(
            session,
            state=DialogueState.EXPIRED,
            active_result_set=None,
            state_version=session.state_version + 1,
            updated_at=now,
            expires_at=now,
        )
        return self._repository.save(
            expired,
            expected_version=session.state_version,
        )

    def cancel_session_state(
        self,
        session_key: ClientSessionIdentity,
    ) -> DialogueSession:
        session = self.get_session(session_key)
        return self._mutate(
            session,
            state=DialogueState.CANCELLED,
            active_result_set=None,
        )

    def _mutate(self, session: DialogueSession, **changes) -> DialogueSession:
        return self._mutate_at(session, self._now(), **changes)

    def _mutate_at(
        self,
        session: DialogueSession,
        now: float,
        **changes,
    ) -> DialogueSession:
        updated = replace(
            session,
            **changes,
            state_version=session.state_version + 1,
            updated_at=now,
            expires_at=now + self._session_ttl_seconds,
        )
        return self._repository.save(
            updated,
            expected_version=session.state_version,
        )

    def _versioned_session(
        self,
        session_key: ClientSessionIdentity,
        expected_version: int | None,
    ) -> DialogueSession:
        session = self.get_session(session_key)
        if (
            expected_version is not None
            and expected_version != session.state_version
        ):
            raise VersionConflict(
                f"expected version {expected_version}, "
                f"found {session.state_version}"
            )
        return session

    @staticmethod
    def _require_operation(
        session: DialogueSession,
        operation_id: str,
    ) -> PendingOperation:
        operation = session.pending_operation
        if operation is None:
            if operation_id in session.consumed_operation_ids:
                raise OperationAlreadyConsumed(
                    "operation has already been consumed"
                )
            raise NoPendingOperation("dialogue session has no pending operation")
        if operation.operation_id != operation_id:
            raise InvalidDialogueTransition(
                "operation identity does not match"
            )
        if operation.principal_id != session.principal_id:
            raise InvalidDialogueTransition(
                "operation principal does not match"
            )
        if operation.session_id != session.session_id:
            raise InvalidDialogueTransition("operation session does not match")
        if operation.status is PendingOperationStatus.CANCELLED:
            raise OperationCancelled("operation was cancelled")
        if operation.status is PendingOperationStatus.EXPIRED:
            raise OperationExpired("operation has expired")
        return operation

    def _ensure_operation_live(
        self,
        session: DialogueSession,
        operation: PendingOperation,
    ) -> None:
        now = self._now()
        if operation.is_expired(now):
            self._expire_operation(session, now)
            raise OperationExpired("operation has expired")

    @staticmethod
    def _require_confirmation(
        session: DialogueSession,
        operation_id: str,
        confirmation_id: str,
    ) -> ConfirmationRequest:
        confirmation = session.confirmation
        if confirmation is None:
            raise ConfirmationMismatch("operation has no confirmation")
        if (
            confirmation.operation_id != operation_id
            or confirmation.confirmation_id != confirmation_id
            or confirmation.principal_id != session.principal_id
            or confirmation.session_id != session.session_id
        ):
            raise ConfirmationMismatch("confirmation identity does not match")
        return confirmation

    def _expire_operation(
        self,
        session: DialogueSession,
        now: float,
    ) -> DialogueSession:
        operation = session.pending_operation
        confirmation = session.confirmation
        if operation is None:
            return session
        if (
            confirmation is not None
            and confirmation.status is ConfirmationStatus.PENDING
        ):
            confirmation = replace(
                confirmation,
                status=ConfirmationStatus.EXPIRED,
            )
        return self._mutate_at(
            session,
            now,
            pending_operation=replace(
                operation,
                status=PendingOperationStatus.EXPIRED,
            ),
            slot_request=None,
            confirmation=confirmation,
            state=DialogueState.READY,
        )

    @staticmethod
    def _validate_result_reference(
        active: ActiveResultSet,
        *,
        result_set_id: str | None,
        result_set_version: int | None,
    ) -> None:
        if result_set_id is not None and result_set_id != active.result_set_id:
            raise StaleResultSet("result-set ID has been replaced")
        if (
            result_set_version is not None
            and result_set_version != active.version
        ):
            raise StaleResultSet("result-set version has been replaced")

    def _now(self) -> float:
        now = self._clock()
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise InvalidDialogueValue("clock must return a finite timestamp")
        return float(now)

    @staticmethod
    def _positive_duration(value: float, field_name: str) -> float:
        if (
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise InvalidDialogueValue(f"{field_name} must be positive")
        return float(value)
