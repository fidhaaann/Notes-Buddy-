"""Bounded, concurrency-safe in-memory dialogue repository."""

from __future__ import annotations

import math
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable

from application.dialogue.errors import InvalidDialogueValue, VersionConflict
from application.dialogue.models import ClientSessionIdentity, DialogueSession


class InMemoryDialogueSessionRepository:
    """LRU repository suitable for the current single-process async runtime."""

    def __init__(
        self,
        *,
        max_sessions: int = 5000,
        session_ttl_seconds: float = 24 * 60 * 60,
        clock: Callable[[], float],
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if (
            not isinstance(max_sessions, int)
            or isinstance(max_sessions, bool)
            or max_sessions <= 0
        ):
            raise InvalidDialogueValue("max_sessions must be positive")
        if (
            not isinstance(session_ttl_seconds, (int, float))
            or not math.isfinite(session_ttl_seconds)
            or session_ttl_seconds <= 0
        ):
            raise InvalidDialogueValue("session_ttl_seconds must be positive")
        self._max_sessions = max_sessions
        self._session_ttl_seconds = float(session_ttl_seconds)
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._sessions: OrderedDict[
            ClientSessionIdentity, DialogueSession
        ] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def session_ttl_seconds(self) -> float:
        return self._session_ttl_seconds

    def get(self, session_key: ClientSessionIdentity) -> DialogueSession | None:
        with self._lock:
            session = self._sessions.get(session_key)
            if session is None:
                return None
            if session.is_expired(self._clock()):
                del self._sessions[session_key]
                return None
            self._sessions.move_to_end(session_key)
            return session

    def save(
        self,
        session: DialogueSession,
        expected_version: int | None = None,
    ) -> DialogueSession:
        with self._lock:
            current = self._sessions.get(session.session_key)
            if current is None:
                if expected_version not in (None, 0):
                    raise VersionConflict(
                        "cannot update a missing dialogue session"
                    )
                if session.state_version != 1:
                    raise VersionConflict(
                        "a new dialogue session must start at version 1"
                    )
            else:
                if current.session_id != session.session_id:
                    raise VersionConflict(
                        "a session identity cannot be overwritten by another session"
                    )
                if (
                    expected_version is not None
                    and expected_version != current.state_version
                ):
                    raise VersionConflict(
                        f"expected version {expected_version}, "
                        f"found {current.state_version}"
                    )
                if session.state_version != current.state_version + 1:
                    raise VersionConflict(
                        "an updated session must increment state_version exactly once"
                    )
            self._sessions[session.session_key] = session
            self._sessions.move_to_end(session.session_key)
            self._evict_overflow_locked()
            return session

    def delete(self, session_key: ClientSessionIdentity) -> bool:
        with self._lock:
            return self._sessions.pop(session_key, None) is not None

    def get_or_create(
        self,
        session_key: ClientSessionIdentity,
        now: float,
    ) -> DialogueSession:
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise InvalidDialogueValue("now must be a finite timestamp")
        with self._lock:
            current = self._sessions.get(session_key)
            if current is not None and not current.is_expired(now):
                self._sessions.move_to_end(session_key)
                return current
            if current is not None:
                del self._sessions[session_key]
            self._cleanup_expired_locked(now)
            session = DialogueSession(
                session_id=self._id_factory(),
                session_key=session_key,
                principal_id=session_key.principal_id,
                created_at=now,
                updated_at=now,
                expires_at=now + self._session_ttl_seconds,
            )
            self._sessions[session_key] = session
            self._sessions.move_to_end(session_key)
            self._evict_overflow_locked()
            return session

    def cleanup_expired(self, now: float) -> int:
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise InvalidDialogueValue("now must be a finite timestamp")
        with self._lock:
            return self._cleanup_expired_locked(now)

    def count(self) -> int:
        with self._lock:
            self._cleanup_expired_locked(self._clock())
            return len(self._sessions)

    def _cleanup_expired_locked(self, now: float) -> int:
        expired_keys = [
            key for key, session in self._sessions.items() if session.is_expired(now)
        ]
        for key in expired_keys:
            del self._sessions[key]
        return len(expired_keys)

    def _evict_overflow_locked(self) -> None:
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)
