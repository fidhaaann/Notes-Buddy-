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
