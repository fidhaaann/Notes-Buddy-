"""Immutable, transport-neutral dialogue state models."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from application.dialogue.errors import (
    ExpiredContext,
    InvalidDialogueValue,
    InvalidSelection,
)

MAX_IDENTIFIER_LENGTH: Final = 256
MAX_NAME_LENGTH: Final = 512
MAX_SOURCE_LENGTH: Final = 128

_ORDINAL_WORDS: Final[dict[str, int]] = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
    "sixth": 6,
    "6th": 6,
    "seventh": 7,
    "7th": 7,
    "eighth": 8,
    "8th": 8,
    "ninth": 9,
    "9th": 9,
    "tenth": 10,
    "10th": 10,
}


def _validate_text(value: str, field_name: str, maximum: int) -> None:
    if not isinstance(value, str):
        raise InvalidDialogueValue(f"{field_name} must be a string")
    if not value.strip():
        raise InvalidDialogueValue(f"{field_name} must not be empty")
    if len(value) > maximum:
        raise InvalidDialogueValue(
            f"{field_name} must be at most {maximum} characters"
        )
    if any(ord(character) < 32 for character in value):
        raise InvalidDialogueValue(f"{field_name} must not contain control characters")


def _validate_optional_text(
    value: str | None,
    field_name: str,
    maximum: int = MAX_IDENTIFIER_LENGTH,
) -> None:
    if value is not None:
        _validate_text(value, field_name, maximum)


def _validate_timestamp(value: float, field_name: str) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidDialogueValue(f"{field_name} must be a finite timestamp")


class DialogueState(str, Enum):
    READY = "ready"
    AWAITING_SLOT = "awaiting_slot"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_STEP_UP = "awaiting_step_up"
    AWAITING_UPLOAD = "awaiting_upload"
    JOB_QUEUED = "job_queued"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ExperienceMode(str, Enum):
    GUIDED = "guided"
    EXPERT = "expert"


class FileSelectionBehavior(str, Enum):
    SHOW_DETAILS = "show_details"
    DOWNLOAD = "download"
    ASK = "ask"


class ItemKind(str, Enum):
    FILE = "file"
    FOLDER = "folder"
    SHORTCUT = "shortcut"


class OperationType(str, Enum):
    CREATE_FOLDER = "create_folder"
    RENAME = "rename"
    MOVE = "move"
    DELETE = "delete"
    SHARE = "share"
    UPLOAD = "upload"
    COPY = "copy"
    ZIP = "zip"


class OperationRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PendingOperationStatus(str, Enum):
    DRAFT = "draft"
    AWAITING_SLOT = "awaiting_slot"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_STEP_UP = "awaiting_step_up"
    READY = "ready"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class ConfirmationStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class OperationTargetSnapshot:
    item_id: str
    name_snapshot: str
    item_kind: ItemKind

    def __post_init__(self) -> None:
        _validate_text(self.item_id, "item_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.name_snapshot, "name_snapshot", MAX_NAME_LENGTH)
        if not isinstance(self.item_kind, ItemKind):
            raise InvalidDialogueValue("item_kind must be an ItemKind")


@dataclass(frozen=True, slots=True)
class CreateFolderParameters:
    folder_name: str | None
    parent_folder_id: str
    parent_folder_name_snapshot: str | None = None

    def __post_init__(self) -> None:
        _validate_optional_text(self.folder_name, "folder_name", 200)
        _validate_text(
            self.parent_folder_id,
            "parent_folder_id",
            MAX_IDENTIFIER_LENGTH,
        )
        _validate_optional_text(
            self.parent_folder_name_snapshot,
            "parent_folder_name_snapshot",
            MAX_NAME_LENGTH,
        )


@dataclass(frozen=True, slots=True)
class PendingOperation:
    operation_id: str
    operation_type: OperationType
    principal_id: str
    account_id: str
    session_id: str
    source_result_set_id: str | None
    source_result_set_version: int | None
    targets: tuple[OperationTargetSnapshot, ...]
    parameters: CreateFolderParameters
    risk_level: OperationRiskLevel
    status: PendingOperationStatus
    idempotency_key: str
    created_at: float
    expires_at: float

    def __post_init__(self) -> None:
        _validate_text(self.operation_id, "operation_id", MAX_IDENTIFIER_LENGTH)
        if not isinstance(self.operation_type, OperationType):
            raise InvalidDialogueValue("operation_type must be an OperationType")
        _validate_text(self.principal_id, "principal_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.account_id, "account_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.session_id, "session_id", MAX_IDENTIFIER_LENGTH)
        _validate_optional_text(
            self.source_result_set_id,
            "source_result_set_id",
        )
        if self.source_result_set_version is not None and (
            not isinstance(self.source_result_set_version, int)
            or isinstance(self.source_result_set_version, bool)
            or self.source_result_set_version <= 0
        ):
            raise InvalidDialogueValue(
                "source_result_set_version must be positive"
            )
        if not isinstance(self.targets, tuple) or not all(
            isinstance(target, OperationTargetSnapshot)
            for target in self.targets
        ):
            raise InvalidDialogueValue(
                "targets must be an immutable tuple of snapshots"
            )
        if not isinstance(self.parameters, CreateFolderParameters):
            raise InvalidDialogueValue(
                "parameters must be CreateFolderParameters"
            )
        if not isinstance(self.risk_level, OperationRiskLevel):
            raise InvalidDialogueValue(
                "risk_level must be an OperationRiskLevel"
            )
        if not isinstance(self.status, PendingOperationStatus):
            raise InvalidDialogueValue(
                "status must be a PendingOperationStatus"
            )
        _validate_text(
            self.idempotency_key,
            "idempotency_key",
            MAX_IDENTIFIER_LENGTH,
        )
        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise InvalidDialogueValue("expires_at must be later than created_at")

    def is_expired(self, now: float) -> bool:
        _validate_timestamp(now, "now")
        return now >= self.expires_at


@dataclass(frozen=True, slots=True)
class SlotRequest:
    slot_request_id: str
    operation_id: str
    slot_name: str
    expected_type: str
    prompt_key: str
    attempts: int
    created_at: float
    expires_at: float

    def __post_init__(self) -> None:
        _validate_text(
            self.slot_request_id,
            "slot_request_id",
            MAX_IDENTIFIER_LENGTH,
        )
        _validate_text(self.operation_id, "operation_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.slot_name, "slot_name", 128)
        _validate_text(self.expected_type, "expected_type", 64)
        _validate_text(self.prompt_key, "prompt_key", 128)
        if (
            not isinstance(self.attempts, int)
            or isinstance(self.attempts, bool)
            or self.attempts < 0
        ):
            raise InvalidDialogueValue("attempts must be non-negative")
        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise InvalidDialogueValue("expires_at must be later than created_at")

    def is_expired(self, now: float) -> bool:
        _validate_timestamp(now, "now")
        return now >= self.expires_at


@dataclass(frozen=True, slots=True)
class ConfirmationRequest:
    confirmation_id: str
    operation_id: str
    principal_id: str
    session_id: str
    operation_summary: str
    target_snapshots: tuple[OperationTargetSnapshot, ...]
    consequence: str
    reversible: bool
    created_at: float
    expires_at: float
    status: ConfirmationStatus = ConfirmationStatus.PENDING

    def __post_init__(self) -> None:
        _validate_text(
            self.confirmation_id,
            "confirmation_id",
            MAX_IDENTIFIER_LENGTH,
        )
        _validate_text(self.operation_id, "operation_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.principal_id, "principal_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.session_id, "session_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.operation_summary, "operation_summary", 1024)
        if not isinstance(self.target_snapshots, tuple) or not all(
            isinstance(target, OperationTargetSnapshot)
            for target in self.target_snapshots
        ):
            raise InvalidDialogueValue(
                "target_snapshots must be an immutable tuple"
            )
        _validate_text(self.consequence, "consequence", 1024)
        if not isinstance(self.reversible, bool):
            raise InvalidDialogueValue("reversible must be a boolean")
        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise InvalidDialogueValue("expires_at must be later than created_at")
        if not isinstance(self.status, ConfirmationStatus):
            raise InvalidDialogueValue(
                "status must be a ConfirmationStatus"
            )

    def is_expired(self, now: float) -> bool:
        _validate_timestamp(now, "now")
        return now >= self.expires_at


@dataclass(frozen=True, slots=True)
class ClientSessionIdentity:
    """Stable principal plus one isolated client conversation."""

    principal_id: str
    client_type: str
    conversation_id: str
    thread_id: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.principal_id, "principal_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.client_type, "client_type", 64)
        _validate_text(self.conversation_id, "conversation_id", MAX_IDENTIFIER_LENGTH)
        _validate_optional_text(self.thread_id, "thread_id")


@dataclass(frozen=True, slots=True)
class FolderLocation:
    item_id: str
    name: str
    parent_id: str | None = None
    shared_drive_id: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.item_id, "item_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.name, "name", MAX_NAME_LENGTH)
        _validate_optional_text(self.parent_id, "parent_id")
        _validate_optional_text(self.shared_drive_id, "shared_drive_id")

    @classmethod
    def home(cls) -> FolderLocation:
        return cls(item_id="root", name="Home")


@dataclass(frozen=True, slots=True)
class ResultItem:
    ordinal: int
    item_id: str
    account_id: str
    name_snapshot: str
    item_kind: ItemKind
    mime_type: str | None = None
    parent_ids: tuple[str, ...] = ()
    is_shortcut: bool = False
    shortcut_target_id: str | None = None
    shortcut_target_kind: ItemKind | None = None
    modified_at: float | None = None
    size_bytes: int | None = None
    source: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool):
            raise InvalidDialogueValue("ordinal must be an integer")
        if self.ordinal <= 0:
            raise InvalidDialogueValue("ordinal must be positive")
        _validate_text(self.item_id, "item_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.account_id, "account_id", MAX_IDENTIFIER_LENGTH)
        _validate_text(self.name_snapshot, "name_snapshot", MAX_NAME_LENGTH)
        if not isinstance(self.item_kind, ItemKind):
            raise InvalidDialogueValue("item_kind must be an ItemKind")
        _validate_optional_text(self.mime_type, "mime_type", MAX_SOURCE_LENGTH)
        if not isinstance(self.parent_ids, tuple):
            raise InvalidDialogueValue("parent_ids must be an immutable tuple")
        for parent_id in self.parent_ids:
            _validate_text(parent_id, "parent_id", MAX_IDENTIFIER_LENGTH)
        if self.item_kind is ItemKind.SHORTCUT and not self.is_shortcut:
            raise InvalidDialogueValue("shortcut items must set is_shortcut")
        if self.is_shortcut and self.item_kind is not ItemKind.SHORTCUT:
            raise InvalidDialogueValue("is_shortcut requires SHORTCUT item_kind")
        _validate_optional_text(self.shortcut_target_id, "shortcut_target_id")
        if (
            self.shortcut_target_kind is not None
            and not isinstance(self.shortcut_target_kind, ItemKind)
        ):
            raise InvalidDialogueValue("shortcut_target_kind must be an ItemKind")
        if self.modified_at is not None:
            _validate_timestamp(self.modified_at, "modified_at")
        if self.size_bytes is not None:
            if (
                not isinstance(self.size_bytes, int)
                or isinstance(self.size_bytes, bool)
                or self.size_bytes < 0
            ):
                raise InvalidDialogueValue("size_bytes must be a non-negative integer")
        _validate_optional_text(self.source, "source", MAX_SOURCE_LENGTH)
        if not isinstance(self.capabilities, frozenset):
            raise InvalidDialogueValue("capabilities must be an immutable frozenset")
        for capability in self.capabilities:
            _validate_text(capability, "capability", 128)


@dataclass(frozen=True, slots=True)
class ActiveResultSet:
    result_set_id: str
    version: int
    source: str
    items: tuple[ResultItem, ...]
    created_at: float
    expires_at: float
    query: str | None = None
    scope: str | None = None
    folder_id: str | None = None
    originating_request_id: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.result_set_id, "result_set_id", MAX_IDENTIFIER_LENGTH)
        if not isinstance(self.version, int) or isinstance(self.version, bool):
            raise InvalidDialogueValue("version must be an integer")
        if self.version <= 0:
            raise InvalidDialogueValue("version must be positive")
        _validate_text(self.source, "source", MAX_SOURCE_LENGTH)
        if not isinstance(self.items, tuple):
            raise InvalidDialogueValue("items must be an immutable ordered tuple")
        ordinals = [item.ordinal for item in self.items]
        if len(ordinals) != len(set(ordinals)):
            raise InvalidDialogueValue("result-set ordinals must be unique")
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise InvalidDialogueValue(
                "item IDs must be unique within one active result set"
            )
        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise InvalidDialogueValue("expires_at must be later than created_at")
        _validate_optional_text(self.query, "query", 2048)
        _validate_optional_text(self.scope, "scope", MAX_SOURCE_LENGTH)
        _validate_optional_text(self.folder_id, "folder_id")
        _validate_optional_text(
            self.originating_request_id,
            "originating_request_id",
        )

    @property
    def item_count(self) -> int:
        return len(self.items)

    def is_expired(self, now: float) -> bool:
        _validate_timestamp(now, "now")
        return now >= self.expires_at

    def resolve(self, selection: int | str, now: float) -> ResultItem:
        if self.is_expired(now):
            raise ExpiredContext("active result set has expired")
        ordinal = self._parse_ordinal(selection)
        for item in self.items:
            if item.ordinal == ordinal:
                return item
        raise InvalidSelection(
            f"selection {ordinal} is outside the active result set"
        )

    def _parse_ordinal(self, selection: int | str) -> int:
        if isinstance(selection, bool):
            raise InvalidSelection("selection must be a positive ordinal")
        if isinstance(selection, int):
            ordinal = selection
        elif isinstance(selection, str):
            normalized = " ".join(selection.strip().lower().split())
            if normalized in {"last", "last one", "the last", "the last one"}:
                if not self.items:
                    raise InvalidSelection("the active result set is empty")
                return self.items[-1].ordinal
            if normalized.startswith("the "):
                normalized = normalized[4:]
            if normalized.endswith(" one"):
                normalized = normalized[:-4]
            if normalized.isdigit():
                ordinal = int(normalized)
            else:
                ordinal = _ORDINAL_WORDS.get(normalized, 0)
        else:
            raise InvalidSelection("selection must be an integer or ordinal word")
        if ordinal <= 0:
            raise InvalidSelection("selection must be a positive ordinal")
        return ordinal


@dataclass(frozen=True, slots=True)
class SelectedItemReference:
    result_set_id: str
    result_set_version: int
    ordinal: int
    item_id: str
    selected_at: float

    def __post_init__(self) -> None:
        _validate_text(self.result_set_id, "result_set_id", MAX_IDENTIFIER_LENGTH)
        if (
            not isinstance(self.result_set_version, int)
            or isinstance(self.result_set_version, bool)
            or self.result_set_version <= 0
        ):
            raise InvalidDialogueValue("result_set_version must be positive")
        if (
            not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or self.ordinal <= 0
        ):
            raise InvalidDialogueValue("ordinal must be positive")
        _validate_text(self.item_id, "item_id", MAX_IDENTIFIER_LENGTH)
        _validate_timestamp(self.selected_at, "selected_at")


@dataclass(frozen=True, slots=True)
class DialogueSession:
    session_id: str
    session_key: ClientSessionIdentity
    principal_id: str
    created_at: float
    updated_at: float
    expires_at: float
    account_id: str | None = None
    state: DialogueState = DialogueState.READY
    current_folder: FolderLocation = field(default_factory=FolderLocation.home)
    folder_history: tuple[FolderLocation, ...] = ()
    active_result_set: ActiveResultSet | None = None
    last_selected_item: SelectedItemReference | None = None
    pending_operation: PendingOperation | None = None
    slot_request: SlotRequest | None = None
    confirmation: ConfirmationRequest | None = None
    consumed_operation_ids: tuple[str, ...] = ()
    experience_mode: ExperienceMode = ExperienceMode.GUIDED
    file_selection_behavior: FileSelectionBehavior = (
        FileSelectionBehavior.SHOW_DETAILS
    )
    state_version: int = 1

    def __post_init__(self) -> None:
        _validate_text(self.session_id, "session_id", MAX_IDENTIFIER_LENGTH)
        if not isinstance(self.session_key, ClientSessionIdentity):
            raise InvalidDialogueValue(
                "session_key must be a ClientSessionIdentity"
            )
        _validate_text(self.principal_id, "principal_id", MAX_IDENTIFIER_LENGTH)
        if self.principal_id != self.session_key.principal_id:
            raise InvalidDialogueValue(
                "principal_id must agree with session identity"
            )
        _validate_optional_text(self.account_id, "account_id")
        if not isinstance(self.state, DialogueState):
            raise InvalidDialogueValue("state must be a DialogueState")
        if not isinstance(self.current_folder, FolderLocation):
            raise InvalidDialogueValue("current_folder must be a FolderLocation")
        if not isinstance(self.folder_history, tuple):
            raise InvalidDialogueValue("folder_history must be an immutable tuple")
        if not all(
            isinstance(location, FolderLocation)
            for location in self.folder_history
        ):
            raise InvalidDialogueValue(
                "folder_history must contain FolderLocation values"
            )
        if (
            self.active_result_set is not None
            and not isinstance(self.active_result_set, ActiveResultSet)
        ):
            raise InvalidDialogueValue(
                "active_result_set must be an ActiveResultSet"
            )
        if (
            self.last_selected_item is not None
            and not isinstance(self.last_selected_item, SelectedItemReference)
        ):
            raise InvalidDialogueValue(
                "last_selected_item must be a SelectedItemReference"
            )
        if (
            self.pending_operation is not None
            and not isinstance(self.pending_operation, PendingOperation)
        ):
            raise InvalidDialogueValue(
                "pending_operation must be a PendingOperation"
            )
        if (
            self.slot_request is not None
            and not isinstance(self.slot_request, SlotRequest)
        ):
            raise InvalidDialogueValue("slot_request must be a SlotRequest")
        if (
            self.confirmation is not None
            and not isinstance(self.confirmation, ConfirmationRequest)
        ):
            raise InvalidDialogueValue(
                "confirmation must be a ConfirmationRequest"
            )
        if not isinstance(self.consumed_operation_ids, tuple):
            raise InvalidDialogueValue(
                "consumed_operation_ids must be an immutable tuple"
            )
        for operation_id in self.consumed_operation_ids:
            _validate_text(
                operation_id,
                "consumed_operation_id",
                MAX_IDENTIFIER_LENGTH,
            )
        if not isinstance(self.experience_mode, ExperienceMode):
            raise InvalidDialogueValue("experience_mode must be an ExperienceMode")
        if not isinstance(self.file_selection_behavior, FileSelectionBehavior):
            raise InvalidDialogueValue(
                "file_selection_behavior must be a FileSelectionBehavior"
            )
        if (
            not isinstance(self.state_version, int)
            or isinstance(self.state_version, bool)
            or self.state_version <= 0
        ):
            raise InvalidDialogueValue("state_version must be positive")
        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.updated_at, "updated_at")
        _validate_timestamp(self.expires_at, "expires_at")
        if self.updated_at < self.created_at:
            raise InvalidDialogueValue("updated_at must not precede created_at")
        if (
            self.state is not DialogueState.EXPIRED
            and self.expires_at <= self.updated_at
        ):
            raise InvalidDialogueValue("expires_at must be later than updated_at")

    def is_expired(self, now: float) -> bool:
        _validate_timestamp(now, "now")
        return now >= self.expires_at
