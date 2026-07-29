"""Transport-neutral errors raised by the dialogue foundation."""


class DialogueError(Exception):
    """Base class for safe, expected dialogue-state failures."""


class InvalidDialogueValue(DialogueError, ValueError):
    """A dialogue model value violates its invariant."""


class InvalidSelection(DialogueError):
    """A selection is not valid for the active result set."""


class AmbiguousSelection(DialogueError):
    """A reference cannot be resolved to exactly one result item."""


class ExpiredContext(DialogueError):
    """The referenced session or result set has expired."""


class StaleResultSet(DialogueError):
    """The caller referenced a replaced result-set ID or version."""


class SessionNotFound(DialogueError):
    """No dialogue session exists for the supplied identity."""


class VersionConflict(DialogueError):
    """Optimistic state-version validation failed."""


class NavigationLoop(DialogueError):
    """A folder transition would create a loop in the active path."""


class PendingOperationExists(DialogueError):
    """A non-terminal operation already owns the dialogue session."""


class NoPendingOperation(DialogueError):
    """The dialogue session has no pending operation."""


class InvalidDialogueTransition(DialogueError):
    """The requested operation-state transition is not allowed."""


class SlotExpired(DialogueError):
    """The active slot request has expired."""


class ConfirmationExpired(DialogueError):
    """The active confirmation request has expired."""


class ConfirmationMismatch(DialogueError):
    """A confirmation does not belong to the supplied operation."""


class ConfirmationAlreadyConsumed(DialogueError):
    """A confirmation was already resolved or consumed."""


class OperationAlreadyConsumed(DialogueError):
    """An operation has already crossed its single-use execution boundary."""


class OperationCancelled(DialogueError):
    """The pending operation was cancelled."""


class OperationExpired(DialogueError):
    """The pending operation has expired."""
