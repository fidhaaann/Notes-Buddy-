"""Persistence seam for typed dialogue sessions."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from application.dialogue.models import ClientSessionIdentity, DialogueSession


@runtime_checkable
class DialogueSessionRepository(Protocol):
    """Provider-neutral session repository with optimistic version support."""

    def get(self, session_key: ClientSessionIdentity) -> DialogueSession | None:
        ...

    def save(
        self,
        session: DialogueSession,
        expected_version: int | None = None,
    ) -> DialogueSession:
        ...

    def delete(self, session_key: ClientSessionIdentity) -> bool:
        ...

    def get_or_create(
        self,
        session_key: ClientSessionIdentity,
        now: float,
    ) -> DialogueSession:
        ...

    def cleanup_expired(self, now: float) -> int:
        ...

    def count(self) -> int:
        ...
