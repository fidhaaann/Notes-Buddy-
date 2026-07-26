"""Focused state transitions for typed dialogue sessions."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Iterable
from dataclasses import replace

from application.dialogue.errors import (
    ExpiredContext,
    InvalidDialogueValue,
    InvalidSelection,
    NavigationLoop,
    SessionNotFound,
    StaleResultSet,
)
from application.dialogue.models import (
    ActiveResultSet,
    ClientSessionIdentity,
    DialogueSession,
    DialogueState,
    ExperienceMode,
    FileSelectionBehavior,
    FolderLocation,
    ResultItem,
    SelectedItemReference,
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
