"""Typed dialogue state and repository foundation."""

from application.dialogue.errors import (
    AmbiguousSelection,
    DialogueError,
    ExpiredContext,
    InvalidDialogueValue,
    InvalidSelection,
    NavigationLoop,
    SessionNotFound,
    StaleResultSet,
    VersionConflict,
)
from application.dialogue.memory_repository import InMemoryDialogueSessionRepository
from application.dialogue.models import (
    ActiveResultSet,
    ClientSessionIdentity,
    DialogueSession,
    DialogueState,
    ExperienceMode,
    FileSelectionBehavior,
    FolderLocation,
    ItemKind,
    ResultItem,
    SelectedItemReference,
)
from application.dialogue.repository import DialogueSessionRepository
from application.dialogue.service import DialogueSessionService

__all__ = [
    "ActiveResultSet",
    "AmbiguousSelection",
    "ClientSessionIdentity",
    "DialogueError",
    "DialogueSession",
    "DialogueSessionRepository",
    "DialogueSessionService",
    "DialogueState",
    "ExperienceMode",
    "ExpiredContext",
    "FileSelectionBehavior",
    "FolderLocation",
    "InMemoryDialogueSessionRepository",
    "InvalidDialogueValue",
    "InvalidSelection",
    "ItemKind",
    "NavigationLoop",
    "ResultItem",
    "SelectedItemReference",
    "SessionNotFound",
    "StaleResultSet",
    "VersionConflict",
]
